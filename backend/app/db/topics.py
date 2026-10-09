from typing import Any

from firebase_admin import firestore
from google.cloud.firestore import DocumentSnapshot

from app.db.client import get_db
from app.db.paths import topic_path, topics_collection_path


def get_topic_snapshot(nb: str, topic_id: str) -> DocumentSnapshot:
    """Fetch a topic DocumentSnapshot directly from Firestore."""
    db = get_db()
    return db.document(topic_path(nb, topic_id)).get()


def list_topic_snapshots(nb: str) -> list[DocumentSnapshot]:
    """Query notebook topics ordered by order ascending."""
    db = get_db()
    coll = db.collection(topics_collection_path(nb))
    return list(coll.order_by("order", direction=firestore.Query.ASCENDING).stream())


def write_topics_batch(nb: str, topics: list[dict[str, Any]]) -> None:
    """Write topic documents in batches of at most 500."""
    if not topics:
        return
    db = get_db()
    batch_size = 500
    for i in range(0, len(topics), batch_size):
        batch = db.batch()
        slice_items = topics[i : i + batch_size]
        for t in slice_items:
            tid = t["id"]
            ref = db.document(topic_path(nb, tid))
            data = {
                "name": t["name"],
                "order": t["order"],
                "summary": t["summary"],
                "prerequisite_ids": t.get("prerequisite_ids", []),
                "is_other": t.get("is_other", False),
                "locations": t.get("locations", []),
                "location_count": t.get("location_count", len(t.get("locations", []))),
            }
            batch.set(ref, data)
        batch.commit()
