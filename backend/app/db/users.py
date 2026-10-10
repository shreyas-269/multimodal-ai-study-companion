from app.db import get_db, user_path


def get_user_custom_instructions(uid: str) -> str | None:
    """Read users/{uid}.format.custom_instructions from Firestore. Returns raw stripped text."""
    db = get_db()
    doc = db.document(user_path(uid)).get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    format_data = data.get("format")
    if not isinstance(format_data, dict):
        return None
    inst = format_data.get("custom_instructions")
    if not isinstance(inst, str):
        return None
    stripped = inst.strip()
    return stripped or None
