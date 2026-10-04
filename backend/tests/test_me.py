from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import create_emulator_user

client = TestClient(app)


def test_me_no_token():
    response = client.get("/v1/me")
    assert response.status_code == 401
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "unauthenticated"


def test_me_non_bearer_token():
    response = client.get("/v1/me", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert response.status_code == 401
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "unauthenticated"


def test_me_empty_bearer_token():
    response = client.get("/v1/me", headers={"Authorization": "Bearer "})
    assert response.status_code == 401
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "unauthenticated"


def test_me_malformed_token():
    response = client.get("/v1/me", headers={"Authorization": "Bearer not.a.valid.jwt"})
    assert response.status_code == 401
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "unauthenticated"


def test_me_first_get_creates_user(user_tracker):
    email = f"test_{uuid4().hex[:8]}@example.com"
    uid, id_token = create_emulator_user(email=email)
    user_tracker.append(uid)

    response = client.get("/v1/me", headers={"Authorization": f"Bearer {id_token}"})
    assert response.status_code == 200
    data = response.json()

    assert data["id"] == uid
    assert data["email"] == email
    assert data["is_guest"] is False
    assert data["study_coach"] is None
    assert data["format"] == {"custom_instructions": None}

    # Verify created_at parses as a valid UTC datetime
    created_at = datetime.fromisoformat(data["created_at"])
    assert created_at.tzinfo in (UTC, None) or created_at.utcoffset().total_seconds() == 0


def test_me_second_get_returns_same_user(user_tracker):
    email = f"test_{uuid4().hex[:8]}@example.com"
    uid, id_token = create_emulator_user(email=email)
    user_tracker.append(uid)

    headers = {"Authorization": f"Bearer {id_token}"}
    res1 = client.get("/v1/me", headers=headers)
    assert res1.status_code == 200

    res2 = client.get("/v1/me", headers=headers)
    assert res2.status_code == 200

    assert res1.json() == res2.json()


def test_me_patch_study_coach(user_tracker):
    email = f"test_{uuid4().hex[:8]}@example.com"
    uid, id_token = create_emulator_user(email=email)
    user_tracker.append(uid)

    headers = {"Authorization": f"Bearer {id_token}"}
    # Initial creation
    res_get = client.get("/v1/me", headers=headers)
    assert res_get.status_code == 200
    assert res_get.json()["study_coach"] is None

    # Patch study_coach to true
    patch_res = client.patch("/v1/me", headers=headers, json={"study_coach": True})
    assert patch_res.status_code == 200
    assert patch_res.json()["study_coach"] is True

    # Re-fetch
    res_after = client.get("/v1/me", headers=headers)
    assert res_after.status_code == 200
    assert res_after.json()["study_coach"] is True


def test_me_patch_format(user_tracker):
    email = f"test_{uuid4().hex[:8]}@example.com"
    uid, id_token = create_emulator_user(email=email)
    user_tracker.append(uid)

    headers = {"Authorization": f"Bearer {id_token}"}
    patch_res = client.patch(
        "/v1/me",
        headers=headers,
        json={"format": {"custom_instructions": "Be concise and clear"}},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["format"]["custom_instructions"] == "Be concise and clear"


def test_me_patch_format_null_rejected(user_tracker):
    email = f"test_{uuid4().hex[:8]}@example.com"
    uid, id_token = create_emulator_user(email=email)
    user_tracker.append(uid)

    headers = {"Authorization": f"Bearer {id_token}"}
    patch_res = client.patch("/v1/me", headers=headers, json={"format": None})
    assert patch_res.status_code == 422
    data = patch_res.json()
    assert "error" in data
    assert data["error"]["code"] == "invalid"


def test_me_patch_unknown_field_rejected(user_tracker):
    email = f"test_{uuid4().hex[:8]}@example.com"
    uid, id_token = create_emulator_user(email=email)
    user_tracker.append(uid)

    headers = {"Authorization": f"Bearer {id_token}"}
    patch_res = client.patch("/v1/me", headers=headers, json={"disallowed_field": 123})
    assert patch_res.status_code == 422
    data = patch_res.json()
    assert "error" in data
    assert data["error"]["code"] == "invalid"


def test_me_guest_user(user_tracker):
    uid, id_token = create_emulator_user(email=None)
    user_tracker.append(uid)

    headers = {"Authorization": f"Bearer {id_token}"}
    response = client.get("/v1/me", headers=headers)
    assert response.status_code == 200
    data = response.json()

    assert data["id"] == uid
    assert data["is_guest"] is True
    assert data["email"] is None


def test_me_operation_ids():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    # Verify exact operation IDs
    assert schema["paths"]["/v1/health"]["get"]["operationId"] == "health_get"
    assert schema["paths"]["/v1/me"]["get"]["operationId"] == "me_get"
    assert schema["paths"]["/v1/me"]["patch"]["operationId"] == "me_patch"
    assert schema["paths"]["/v1/notebooks"]["post"]["operationId"] == "notebooks_create"
    assert schema["paths"]["/v1/notebooks"]["get"]["operationId"] == "notebooks_list"
    assert schema["paths"]["/v1/notebooks/{nb}"]["get"]["operationId"] == "notebooks_get"

    # Verify uniqueness of all operation IDs in openapi.json
    operation_ids = []
    for path, methods in schema.get("paths", {}).items():
        if path.startswith("/_test_"):
            continue
        for _method, operation in methods.items():
            if isinstance(operation, dict) and "operationId" in operation:
                operation_ids.append(operation["operationId"])

    assert len(operation_ids) == len(set(operation_ids)), (
        f"Duplicate operation IDs: {operation_ids}"
    )
