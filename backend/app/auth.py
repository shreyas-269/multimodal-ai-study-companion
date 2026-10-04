from typing import Annotated

from fastapi import Depends, Header, HTTPException
from firebase_admin import auth
from pydantic import BaseModel

from app.db import init_firebase


class AuthenticatedUser(BaseModel):
    """Authenticated user context extracted from verified Firebase ID token."""

    uid: str
    email: str | None = None
    is_guest: bool = False


def get_current_user(
    authorization: str | None = Header(default=None),
) -> AuthenticatedUser:
    """Verify Firebase Bearer token and return authenticated user context."""
    init_firebase()

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthenticated", "message": "Missing or invalid authorization token"},
        )

    token = authorization[7:].strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthenticated", "message": "Empty authorization token"},
        )

    try:
        decoded_token = auth.verify_id_token(token, clock_skew_seconds=10)
    except auth.InvalidIdTokenError:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthenticated", "message": "Invalid authorization token"},
        ) from None

    uid = decoded_token["uid"]
    email = decoded_token.get("email")
    is_guest = decoded_token.get("firebase", {}).get("sign_in_provider") == "anonymous"

    return AuthenticatedUser(uid=uid, email=email, is_guest=is_guest)


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
