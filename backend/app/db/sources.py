from typing import Any

from firebase_admin import firestore
from google.cloud.firestore import DocumentSnapshot, Transaction
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.vector import Vector

from app.db.client import get_db
from app.db.paths import (
    chunk_path,
    chunks_collection_path,
    notebook_path,
    source_path,
    sources_collection_path,
)


def generate_source_id(nb: str) -> str:
    """Pre-generate a Firestore auto-ID for a source."""
    db = get_db()
    return db.collection(sources_collection_path(nb)).document().id


def create_source_transaction(
    nb: str,
    source_id: str,
    title: str,
    filename: str,
    role: str,
    storage_path: str,
) -> int:
    """Transaction 1: Create source doc in 'upload' stage and update notebook summary."""
    db = get_db()
    nb_ref = db.document(notebook_path(nb))
    src_ref = db.document(source_path(nb, source_id))

    @firestore.transactional
    def _tx(transaction: Transaction) -> int:
        nb_doc = nb_ref.get(transaction=transaction)
        nb_data = nb_doc.to_dict() or {}
        sources_summary = list(nb_data.get("sources_summary", []))

        ref_n = max([s.get("ref_n", 0) for s in sources_summary], default=0) + 1

        source_data = {
            "ref_n": ref_n,
            "title": title,
            "kind": "pdf",
            "role": role,
            "filename": filename,
            "storage_path": storage_path,
            "viewer_path": storage_path,
            "youtube_id": None,
            "offset_s": None,
            "duration_s": None,
            "page_count": None,
            "page_labels": [],
            "slide_grid": None,
            "licence_pages": [],
            "licence": None,
            "attribution": None,
            "status": "processing",
            "stage": "upload",
            "error": None,
            "ingest_version": 1,
            "created_at": firestore.SERVER_TIMESTAMP,
        }
        transaction.set(src_ref, source_data)

        sources_summary.append(
            {
                "source_id": source_id,
                "ref_n": ref_n,
                "title": title,
                "kind": "pdf",
                "status": "processing",
            }
        )
        transaction.update(
            nb_ref,
            {
                "sources_summary": sources_summary,
                "status": "processing",
            },
        )
        return ref_n

    transaction = db.transaction()
    return _tx(transaction)


def write_chunks_batch(
    nb: str,
    source_id: str,
    chunks: list[dict[str, Any]],
    embeddings: list[list[float]],
) -> None:
    """Write chunk documents with 384-d vectors in batches of at most 500."""
    db = get_db()
    batch_size = 500

    for i in range(0, len(chunks), batch_size):
        batch = db.batch()
        chunk_slice = chunks[i : i + batch_size]
        for seq_idx, chunk in enumerate(chunk_slice, start=i):
            chunk_id = chunk.get("id") or f"{source_id}-{seq_idx:05d}"
            ref = db.document(chunk_path(nb, chunk_id))
            batch.set(
                ref,
                {
                    "source_id": source_id,
                    "kind": "text",
                    "text": chunk["text"],
                    "loc": chunk["loc"],
                    "topic_id": chunk.get("topic_id"),
                    "embedding": Vector(embeddings[seq_idx]),
                    "token_count": chunk["token_count"],
                    "image_path": None,
                },
            )
        batch.commit()


def finish_source_transaction(
    nb: str,
    source_id: str,
    page_count: int,
    chunk_count: int,
    page_labels: list[str | None],
    licence_pages: list[int],
    slide_grid: str | None = None,
    viewer_path: str | None = None,
) -> dict[str, Any]:
    """Transaction 2: Set source ready and recompute notebook status & counts."""
    assert len(page_labels) == page_count, "len(page_labels) must match page_count"
    db = get_db()
    nb_ref = db.document(notebook_path(nb))
    src_ref = db.document(source_path(nb, source_id))

    @firestore.transactional
    def _tx(transaction: Transaction) -> None:
        nb_doc = nb_ref.get(transaction=transaction)
        nb_data = nb_doc.to_dict() or {}
        sources_summary = list(nb_data.get("sources_summary", []))

        # Update matching entry
        for s in sources_summary:
            if s.get("source_id") == source_id:
                s["status"] = "ready"

        # Recompute notebook status
        if any(s.get("status") in ("queued", "processing") for s in sources_summary):
            nb_status = "processing"
        elif any(s.get("status") == "ready" for s in sources_summary):
            nb_status = "ready"
        else:
            nb_status = "empty"

        counts = nb_data.get("counts", {})
        new_chunks = counts.get("chunks", 0) + chunk_count

        transaction.update(
            nb_ref,
            {
                "sources_summary": sources_summary,
                "status": nb_status,
                "counts.chunks": new_chunks,
            },
        )
        src_update: dict[str, Any] = {
            "status": "ready",
            "stage": "done",
            "page_count": page_count,
            "page_labels": page_labels,
            "licence_pages": licence_pages,
            "slide_grid": slide_grid,
        }
        if viewer_path is not None:
            src_update["viewer_path"] = viewer_path
        transaction.update(src_ref, src_update)

    transaction = db.transaction()
    _tx(transaction)

    doc = src_ref.get()
    return {"id": source_id, **(doc.to_dict() or {})}


def delete_chunks_for_source(nb: str, source_id: str) -> None:
    """Delete all chunks belonging to a source."""
    db = get_db()
    chunks_query = db.collection(chunks_collection_path(nb)).where(
        filter=FieldFilter("source_id", "==", source_id)
    )
    docs = list(chunks_query.stream())
    batch_size = 500
    for i in range(0, len(docs), batch_size):
        batch = db.batch()
        for doc in docs[i : i + batch_size]:
            batch.delete(doc.reference)
        batch.commit()


def fail_source_cleanup(
    nb: str,
    source_id: str,
    stage: str,
    error_message: str,
) -> dict[str, Any]:
    """On failure: delete chunks first, then mark source failed transactionally."""
    delete_chunks_for_source(nb, source_id)

    db = get_db()
    nb_ref = db.document(notebook_path(nb))
    src_ref = db.document(source_path(nb, source_id))

    @firestore.transactional
    def _tx(transaction: Transaction) -> None:
        nb_doc = nb_ref.get(transaction=transaction)
        nb_data = nb_doc.to_dict() or {}
        sources_summary = list(nb_data.get("sources_summary", []))

        for s in sources_summary:
            if s.get("source_id") == source_id:
                s["status"] = "failed"

        if any(s.get("status") in ("queued", "processing") for s in sources_summary):
            nb_status = "processing"
        elif any(s.get("status") == "ready" for s in sources_summary):
            nb_status = "ready"
        else:
            nb_status = "empty"

        transaction.update(
            nb_ref,
            {
                "sources_summary": sources_summary,
                "status": nb_status,
            },
        )
        transaction.update(
            src_ref,
            {
                "status": "failed",
                "stage": stage,
                "error": error_message,
            },
        )

    transaction = db.transaction()
    _tx(transaction)

    doc = src_ref.get()
    return {"id": source_id, **(doc.to_dict() or {})}


def get_source_snapshot(nb: str, src: str) -> DocumentSnapshot:
    """Fetch a source DocumentSnapshot by ID directly from Firestore."""
    db = get_db()
    return db.document(source_path(nb, src)).get()


def list_sources_snapshots(
    nb: str,
    limit: int,
    cursor_snapshot: DocumentSnapshot | None = None,
) -> list[DocumentSnapshot]:
    """Query notebook's sources ordered by ref_n ascending, fetching limit + 1 items."""
    db = get_db()
    query = db.collection(sources_collection_path(nb)).order_by(
        "ref_n", direction=firestore.Query.ASCENDING
    )
    if cursor_snapshot is not None:
        query = query.start_after(cursor_snapshot)
    return list(query.limit(limit + 1).stream())


def delete_chunks_beyond_seq(nb: str, source_id: str, new_chunk_count: int) -> int:
    """Delete chunks belonging to a source with seq index >= new_chunk_count."""
    db = get_db()
    chunks_query = db.collection(chunks_collection_path(nb)).where(
        filter=FieldFilter("source_id", "==", source_id)
    )
    docs = list(chunks_query.stream())
    to_delete = []
    for doc in docs:
        parts = doc.id.split("-")
        try:
            seq = int(parts[-1])
            if seq >= new_chunk_count:
                to_delete.append(doc)
        except ValueError:
            pass
    batch_size = 500
    for i in range(0, len(to_delete), batch_size):
        batch = db.batch()
        for doc in to_delete[i : i + batch_size]:
            batch.delete(doc.reference)
        batch.commit()
    return len(to_delete)


def upsert_source_processing(
    nb: str,
    source_id: str,
    ref_n: int,
    title: str,
    kind: str,
    role: str,
    filename: str,
    storage_path: str,
    viewer_path: str | None,
    page_count: int | None,
    page_labels: list[str | None],
    licence_pages: list[int],
    slide_grid: str | None = None,
    licence: str | None = None,
    attribution: str | None = None,
    youtube_id: str | None = None,
    offset_s: float | None = None,
    duration_s: float | None = None,
) -> dict[str, Any]:
    """Upsert a source document with processing status prior to writing chunks."""
    db = get_db()
    src_ref = db.document(source_path(nb, source_id))

    src_doc = src_ref.get()
    had_src = src_doc.exists
    created_at = src_doc.to_dict().get("created_at") if had_src else firestore.SERVER_TIMESTAMP

    source_data = {
        "ref_n": ref_n,
        "title": title,
        "kind": kind,
        "role": role,
        "filename": filename,
        "storage_path": storage_path,
        "viewer_path": viewer_path,
        "youtube_id": youtube_id,
        "offset_s": offset_s,
        "duration_s": duration_s,
        "page_count": page_count,
        "page_labels": page_labels,
        "slide_grid": slide_grid,
        "licence_pages": licence_pages,
        "licence": licence,
        "attribution": attribution,
        "status": "processing",
        "stage": "ingest",
        "error": None,
        "ingest_version": 1,
        "created_at": created_at,
    }
    src_ref.set(source_data)
    return {"id": source_id, **source_data}


def set_source_ready(nb: str, source_id: str) -> None:
    """Mark source document status as ready and stage as done after chunks are written."""
    db = get_db()
    src_ref = db.document(source_path(nb, source_id))
    src_ref.update({"status": "ready", "stage": "done"})

