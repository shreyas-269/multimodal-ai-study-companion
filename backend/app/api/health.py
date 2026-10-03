from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
