def user_path(uid: str) -> str:
    """Return Firestore document path for a user."""
    return f"users/{uid}"
