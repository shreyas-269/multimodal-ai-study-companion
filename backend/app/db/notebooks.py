from typing import Any

from firebase_admin import firestore
from google.cloud.firestore import DocumentSnapshot
from google.cloud.firestore_v1.base_query import FieldFilter

from app.db.client import get_db
from app.db.paths import notebook_path, notebooks_collection_path

DEMO_NOTEBOOK_ID = "nb_demo_6041"


def create_notebook(name: str, owner_uid: str) -> dict[str, Any]:
    """Create a new notebook with defaults, reading back the persisted document."""
    db = get_db()
    ref = db.collection(notebooks_collection_path()).document()
    initial_data = {
        "name": name,
        "owner_uid": owner_uid,
        "is_demo": False,
        "status": "empty",
        "sources_summary": [],
        "counts": {
            "chunks": 0,
            "questions_verified": 0,
        },
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    ref.create(initial_data)
    doc = ref.get()
    doc_data = doc.to_dict() or {}
    return {"id": ref.id, **doc_data}


def upsert_demo_notebook(
    notebook_id: str = DEMO_NOTEBOOK_ID,
    name: str = "MIT 6.041 Probability (demo)",
    owner_uid: str = "demo-owner",
) -> dict[str, Any]:
    """Ensure demo notebook document exists with proper initial fields."""
    db = get_db()
    ref = db.document(notebook_path(notebook_id))
    doc = ref.get()
    if not doc.exists:
        initial_data = {
            "name": name,
            "owner_uid": owner_uid,
            "is_demo": True,
            "status": "empty",
            "sources_summary": [],
            "counts": {
                "chunks": 0,
                "questions_verified": 0,
            },
            "created_at": firestore.SERVER_TIMESTAMP,
        }
        ref.set(initial_data)
        doc = ref.get()
    else:
        ref.update({"is_demo": True, "name": name, "owner_uid": owner_uid})
        doc = ref.get()
    return {"id": ref.id, **(doc.to_dict() or {})}


def get_notebook_snapshot(nb: str) -> DocumentSnapshot:
    """Fetch a notebook DocumentSnapshot by ID directly from Firestore."""
    db = get_db()
    return db.document(notebook_path(nb)).get()


def list_owner_notebook_snapshots(
    owner_uid: str,
    limit: int,
    cursor_snapshot: DocumentSnapshot | None = None,
) -> list[DocumentSnapshot]:
    """Query owner's notebooks newest first, fetching limit + 1 items."""
    db = get_db()
    query = (
        db.collection(notebooks_collection_path())
        .where(filter=FieldFilter("owner_uid", "==", owner_uid))
        .order_by("created_at", direction=firestore.Query.DESCENDING)
    )
    if cursor_snapshot is not None:
        query = query.start_after(cursor_snapshot)
    return list(query.limit(limit + 1).stream())


def rebuild_notebook_status_and_summary(
    nb: str,
    sources_summary: list[dict[str, Any]],
    total_chunks: int,
    status: str = "ready",
) -> None:
    """Atomically set sources_summary, counts.chunks, and status on a notebook."""
    db = get_db()
    ref = db.document(notebook_path(nb))
    doc = ref.get()
    doc_data = doc.to_dict() or {}
    counts = doc_data.get("counts", {})
    counts["chunks"] = total_chunks
    counts.pop("items", None)
    ref.update(
        {
            "sources_summary": sources_summary,
            "counts": counts,
            "status": status,
        }
    )
