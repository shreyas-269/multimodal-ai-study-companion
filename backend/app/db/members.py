from typing import Any

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
