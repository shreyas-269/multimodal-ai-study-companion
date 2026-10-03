from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_not_found():
    response = client.get("/v1/unknown")
    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "not_found"
    assert data["error"]["message"] == "Not Found"


def test_method_not_allowed():
    response = client.post("/v1/health")
    assert response.status_code == 405
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "method_not_allowed"
    assert "message" in data["error"]


def test_validation_error():
    # Register test-only route dynamically so it never appears in production schema or docs
    @app.get("/_test_validation")
    def _test_validation_route(count: int):
        return {"count": count}

    response = client.get("/_test_validation?count=invalid_int")
    assert response.status_code == 422
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "invalid"
    assert "count" in data["error"]["message"]


def test_server_error_and_cors():
    @app.get("/_test_server_error")
    def _test_server_error_route():
        raise RuntimeError("Simulated failure")

    response = client.get("/_test_server_error", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 500
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "internal_error"
    assert data["error"]["message"] == "Internal server error"
    # Ensure unhandled 500 still receives CORS headers
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_quota_exhausted_detail_and_headers():
    @app.get("/_test_quota")
    def _test_quota_route():
        raise HTTPException(
            status_code=429,
            detail={
                "code": "quota_exhausted",
                "message": "Gemini quota hit",
                "retry_after_s": 45,
            },
            headers={"Retry-After": "45"},
        )

    response = client.get("/_test_quota")
    assert response.status_code == 429
    assert response.headers.get("retry-after") == "45"
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "quota_exhausted"
    assert data["error"]["message"] == "Gemini quota hit"
    assert data["error"]["retry_after_s"] == 45


def test_cors_preflight():
    response = client.options(
        "/v1/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "range,content-type,authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    allowed_headers = response.headers.get("access-control-allow-headers", "").lower()
    assert "range" in allowed_headers
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers


def test_docs_load():
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_operation_ids_unique():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    # Health check operationId
    health_op = schema["paths"]["/v1/health"]["get"]["operationId"]
    assert health_op == "health_check"

    # Verify uniqueness of all operationIds
    operation_ids = []
    for path, methods in schema.get("paths", {}).items():
        # Exclude dynamic test-only routes if any leaked into schema
        if path.startswith("/_test_"):
            continue
        for _method, operation in methods.items():
            if isinstance(operation, dict) and "operationId" in operation:
                operation_ids.append(operation["operationId"])

    assert len(operation_ids) == len(set(operation_ids)), (
        f"Duplicate operation IDs: {operation_ids}"
    )
