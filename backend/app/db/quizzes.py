from typing import Any

from google.cloud import firestore

from app.db.client import get_db
from app.db.paths import attempt_path, quiz_path


def get_quiz_snapshot(nb_id: str, uid: str, quiz_id: str) -> firestore.DocumentSnapshot:
    """Fetch quiz document snapshot directly."""
    db = get_db()
    return db.document(quiz_path(nb_id, uid, quiz_id)).get()


def get_attempts(
    nb_id: str, uid: str, attempt_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Fetch attempt documents for given attempt IDs in one batch get_all call."""
    if not attempt_ids:
        return {}
    db = get_db()
    refs = [db.document(attempt_path(nb_id, uid, aid)) for aid in attempt_ids]
    snaps = db.get_all(refs)
    result: dict[str, dict[str, Any]] = {}
    for snap in snaps:
        if snap.exists and snap.to_dict() is not None:
            result[snap.id] = snap.to_dict()
    return result
