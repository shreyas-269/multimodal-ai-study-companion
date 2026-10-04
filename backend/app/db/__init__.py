"""Database access and path helpers package."""

from app.db.client import get_db, init_firebase
from app.db.paths import notebook_path, notebooks_collection_path, user_path

__all__ = [
    "get_db",
    "init_firebase",
    "notebook_path",
    "notebooks_collection_path",
    "user_path",
]
