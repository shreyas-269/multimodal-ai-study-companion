import google.api_core.exceptions
from fastapi import APIRouter
from firebase_admin import firestore

from app.auth import CurrentUser
from app.db import get_db, user_path
from app.models.user import User, UserPatch

router = APIRouter(tags=["me"])


@router.get("/me")
def get(current_user: CurrentUser) -> User:
    """Get current user profile, creating it on first call."""
    db = get_db()
    ref = db.document(user_path(current_user.uid))
    doc = ref.get()

    if not doc.exists:
        initial_data = {
            "email": current_user.email,
            "display_name": None,
            "is_guest": current_user.is_guest,
            "study_coach": None,
            "format": {"custom_instructions": None},
            "created_at": firestore.SERVER_TIMESTAMP,
        }
        try:
            ref.create(initial_data)
        except google.api_core.exceptions.AlreadyExists:
            pass
        doc = ref.get()

    data = doc.to_dict() or {}
    return User.model_validate({"id": current_user.uid, **data})


@router.patch("/me")
def patch(body: UserPatch, current_user: CurrentUser) -> User:
    """Update current user profile fields."""
    db = get_db()
    ref = db.document(user_path(current_user.uid))
    doc = ref.get()

    if not doc.exists:
        initial_data = {
            "email": current_user.email,
            "display_name": None,
            "is_guest": current_user.is_guest,
            "study_coach": None,
            "format": {"custom_instructions": None},
            "created_at": firestore.SERVER_TIMESTAMP,
        }
        try:
            ref.create(initial_data)
        except google.api_core.exceptions.AlreadyExists:
            pass

    updates = {}
    if body.study_coach is not None:
        updates["study_coach"] = body.study_coach
    if body.format is not None:
        updates["format.custom_instructions"] = body.format.custom_instructions

    if updates:
        ref.update(updates)

    updated_doc = ref.get()
    data = updated_doc.to_dict() or {}
    return User.model_validate({"id": current_user.uid, **data})
