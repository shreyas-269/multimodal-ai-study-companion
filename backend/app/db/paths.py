def user_path(uid: str) -> str:
    """Return Firestore document path for a user."""
    return f"users/{uid}"


def notebooks_collection_path() -> str:
    """Return Firestore collection path for notebooks."""
    return "notebooks"


def notebook_path(nb: str) -> str:
    """Return Firestore document path for a notebook."""
    return f"notebooks/{nb}"
