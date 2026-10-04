"""Database access and path helpers package."""

from app.db.client import get_db, init_firebase
from app.db.paths import user_path

__all__ = ["get_db", "init_firebase", "user_path"]
