from fastapi import APIRouter, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import health, me, notebooks, sources
from app.config import get_settings


def custom_generate_unique_id(route: APIRoute) -> str:
    """Generate concise and distinct operation IDs in the format '{tag}_{function_name}'."""
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}_{route.name}"


app = FastAPI(
    title="AI Study Companion Backend",
    version="0.1.0",
    generate_unique_id_function=custom_generate_unique_id,
)

# Unhandled exception middleware (inner: executed before CORS so 500s get CORS headers)
@app.middleware("http")
async def catch_unhandled_exceptions(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "internal_error", "message": "Internal server error"}},
        )


# CORS middleware (outer: wraps unhandled exception middleware)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "Range"],
    expose_headers=["Accept-Ranges", "Content-Range", "Content-Length"],
)


STATUS_CODE_TO_ERROR_CODE = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "not_ready",
    422: "invalid",
    429: "quota_exhausted",
    500: "internal_error",
}


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    default_code = STATUS_CODE_TO_ERROR_CODE.get(exc.status_code, "error")
    headers = getattr(exc, "headers", None)

    if isinstance(exc.detail, dict):
        code = exc.detail.get("code", default_code)
        message = exc.detail.get("message", "Error")
        error_dict = {"code": code, "message": message}
        for k, v in exc.detail.items():
            if k not in ("code", "message"):
                error_dict[k] = v
    else:
        error_dict = {"code": default_code, "message": str(exc.detail)}

    return JSONResponse(
        status_code=exc.status_code,
        content={"error": error_dict},
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    messages = []
    for err in errors:
        loc = " -> ".join(str(part) for part in err.get("loc", []))
        msg = err.get("msg", "Invalid value")
        messages.append(f"{loc}: {msg}" if loc else msg)
    message = "; ".join(messages) if messages else "Validation error"

    return JSONResponse(
        status_code=422,
        content={"error": {"code": "invalid", "message": message}},
    )


# Mount routers under /v1
v1_router = APIRouter(prefix="/v1")
v1_router.include_router(health.router)
v1_router.include_router(me.router)
v1_router.include_router(notebooks.router)
v1_router.include_router(sources.router)
app.include_router(v1_router)
