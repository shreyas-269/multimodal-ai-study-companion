from datetime import datetime
from typing import Any

from fastapi import HTTPException
from google.cloud import firestore

from app.db.client import get_db
from app.db.paths import (
    chat_path,
    chats_collection_path,
    message_path,
    messages_collection_path,
)


def create_chat_doc(nb: str, uid: str, chat_id: str, data: dict[str, Any]) -> None:
    """Create a new chat document."""
    db = get_db()
    db.document(chat_path(nb, uid, chat_id)).set(data)


def get_chat_snapshot(nb: str, uid: str, chat_id: str) -> firestore.DocumentSnapshot:
    """Fetch a chat document snapshot."""
    db = get_db()
    return db.document(chat_path(nb, uid, chat_id)).get()


def list_chats_snapshots(
    nb: str,
    uid: str,
    limit: int,
    cursor_snap: firestore.DocumentSnapshot | None = None,
) -> list[firestore.DocumentSnapshot]:
    """List chat snapshots ordered by updated_at descending with cursor pagination."""
    db = get_db()
    query = (
        db.collection(chats_collection_path(nb, uid))
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    if cursor_snap is not None:
        query = query.start_after(cursor_snap)
    return list(query.stream())


def get_message_snapshot(
    nb: str,
    uid: str,
    chat_id: str,
    msg_id: str,
) -> firestore.DocumentSnapshot:
    """Fetch a chat message snapshot."""
    db = get_db()
    return db.document(message_path(nb, uid, chat_id, msg_id)).get()


def list_messages_snapshots(
    nb: str,
    uid: str,
    chat_id: str,
    limit: int,
    cursor_snap: firestore.DocumentSnapshot | None = None,
) -> list[firestore.DocumentSnapshot]:
    """List message snapshots ordered by seq descending with cursor pagination."""
    db = get_db()
    query = (
        db.collection(messages_collection_path(nb, uid, chat_id))
        .order_by("seq", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    if cursor_snap is not None:
        query = query.start_after(cursor_snap)
    return list(query.stream())


def save_chat_messages_transaction(
    nb: str,
    uid: str,
    chat_id: str,
    user_msg_id: str,
    asst_msg_id: str,
    user_msg_data: dict[str, Any],
    asst_msg_data: dict[str, Any],
    asst_created_at: datetime,
) -> tuple[int, int]:
    """Save user and assistant messages transactionally, updating chat count and timestamp."""
    db = get_db()
    chat_ref = db.document(chat_path(nb, uid, chat_id))
    user_msg_ref = db.document(message_path(nb, uid, chat_id, user_msg_id))
    asst_msg_ref = db.document(message_path(nb, uid, chat_id, asst_msg_id))

    transaction = db.transaction()

    @firestore.transactional
    def _tx(tx: firestore.Transaction) -> tuple[int, int]:
        # All reads before all writes
        chat_snap = chat_ref.get(transaction=tx)
        if not chat_snap.exists:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Chat not found."},
            )
        data = chat_snap.to_dict() or {}
        n = data.get("message_count", 0)

        user_data = {**user_msg_data, "seq": n + 1}
        asst_data = {**asst_msg_data, "seq": n + 2}

        tx.set(user_msg_ref, user_data)
        tx.set(asst_msg_ref, asst_data)
        tx.update(
            chat_ref,
            {
                "message_count": n + 2,
                "updated_at": asst_created_at,
            },
        )
        return n + 1, n + 2

    return _tx(transaction)


def get_recent_chat_messages(
    nb: str,
    uid: str,
    chat_id: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Read up to `limit` recent messages with field mask, returned oldest first."""
    db = get_db()
    query = (
        db.collection(messages_collection_path(nb, uid, chat_id))
        .select(["role", "text", "paragraphs"])
        .order_by("seq", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    snaps = list(query.stream())
    # Reverse descending seq order to chronological (oldest first)
    return [doc.to_dict() or {} for doc in reversed(snaps)]
