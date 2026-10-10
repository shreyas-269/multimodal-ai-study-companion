from typing import Any

from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore

from app.db.client import get_db
from app.db.paths import member_path


def default_member_data() -> dict[str, Any]:
    """Return default document data for a notebook member."""
    return {
        "checked": {},
        "seen_question_ids": [],
        "diagnostic": {"status": "not_started", "quiz_id": None},
        "created_at": firestore.SERVER_TIMESTAMP,
    }


def get_member_snapshot(nb_id: str, uid: str) -> firestore.DocumentSnapshot:
    """Fetch member document snapshot directly."""
    db = get_db()
    return db.document(member_path(nb_id, uid)).get()


def ensure_member(notebook_id: str, uid: str) -> None:
    """Create member document with default data if absent; never overwrites existing document."""
    db = get_db()
    ref = db.document(member_path(notebook_id, uid))
    try:
        ref.create(default_member_data())
    except AlreadyExists:
        pass
