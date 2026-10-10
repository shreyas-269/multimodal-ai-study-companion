from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.access import get_readable_notebook, is_valid_notebook_id
from app.auth import CurrentUser
from app.chat.answer import run_answer_pipeline
from app.chat.history import format_chat_history
from app.db.chats import (
    create_chat_doc,
    get_chat_snapshot,
    get_message_snapshot,
    get_recent_chat_messages,
    list_chats_snapshots,
    list_messages_snapshots,
    save_chat_messages_transaction,
)
from app.db.client import get_db
from app.db.members import ensure_member
from app.db.paths import chats_collection_path, messages_collection_path
from app.db.topics import get_topic_snapshot
from app.db.users import get_user_custom_instructions
from app.models.ask import Refs
from app.models.chat import (
    ChatCreate,
    ChatList,
    ChatMessageCreate,
    ChatOut,
    ChatSendOut,
    MessageList,
    MessageOut,
)
from app.models.citation import Paragraph
from app.models.notebook import Notebook

router = APIRouter(tags=["chats"])


@router.post("/notebooks/{nb}/chats", name="create", status_code=status.HTTP_201_CREATED)
def create_chat(
    nb: str,
    body: ChatCreate,
    current_user: CurrentUser,
    notebook: Annotated[Notebook, Depends(get_readable_notebook)],
) -> ChatOut:
    """Create a new chat in a readable notebook."""
    if body.topic_id is not None:
        topic_snap = get_topic_snapshot(nb, body.topic_id)
        if not topic_snap.exists:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid",
                    "message": f"This notebook has no topic {body.topic_id}.",
                },
            )
        topic_data = topic_snap.to_dict() or {}
        default_name = topic_data.get("name", body.topic_id)
    else:
        default_name = "Whole notebook"

    chat_name = body.name if body.name is not None else default_name

    ensure_member(nb, current_user.uid)

    db = get_db()
    chat_id = db.collection(chats_collection_path(nb, current_user.uid)).document().id
    now = datetime.now(UTC)

    chat_data = {
        "name": chat_name,
        "topic_id": body.topic_id,
        "created_at": now,
        "updated_at": now,
        "message_count": 0,
    }
    create_chat_doc(nb, current_user.uid, chat_id, chat_data)

    return ChatOut(
        id=chat_id,
        name=chat_name,
        topic_id=body.topic_id,
        created_at=now,
        updated_at=now,
        message_count=0,
    )


@router.get("/notebooks/{nb}/chats", name="list")
def list_chats(
    nb: str,
    current_user: CurrentUser,
    notebook: Annotated[Notebook, Depends(get_readable_notebook)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> ChatList:
    """List chats for the caller in a readable notebook, ordered by updated_at descending."""
    cursor_snap = None
    if cursor is not None:
        if not is_valid_notebook_id(cursor):
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid", "message": "Invalid cursor format"},
            )
        cursor_snap = get_chat_snapshot(nb, current_user.uid, cursor)
        if not cursor_snap.exists:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid", "message": "Cursor chat not found."},
            )

    raw = list_chats_snapshots(
        nb=nb,
        uid=current_user.uid,
        limit=limit + 1,
        cursor_snap=cursor_snap,
    )
    has_more = len(raw) > limit
    page_snaps = raw[:limit]
    next_cursor = page_snaps[-1].id if has_more and page_snaps else None

    items = []
    for doc in page_snaps:
        d = doc.to_dict() or {}
        items.append(
            ChatOut(
                id=doc.id,
                name=d.get("name", ""),
                topic_id=d.get("topic_id"),
                created_at=d["created_at"],
                updated_at=d["updated_at"],
                message_count=d.get("message_count", 0),
            )
        )

    return ChatList(items=items, next_cursor=next_cursor)


@router.get("/notebooks/{nb}/chats/{c}/messages", name="list_messages")
def list_messages(
    nb: str,
    c: str,
    current_user: CurrentUser,
    notebook: Annotated[Notebook, Depends(get_readable_notebook)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, Query()] = None,
) -> MessageList:
    """List messages for a chat, newest pages first, chronological within page."""
    if not is_valid_notebook_id(c):
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Chat not found."},
        )

    chat_snap = get_chat_snapshot(nb, current_user.uid, c)
    if not chat_snap.exists:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Chat not found."},
        )

    cursor_snap = None
    if cursor is not None:
        if not is_valid_notebook_id(cursor):
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid", "message": "Invalid cursor format"},
            )
        cursor_snap = get_message_snapshot(nb, current_user.uid, c, cursor)
        if not cursor_snap.exists:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid", "message": "Cursor message not found."},
            )

    snaps = list_messages_snapshots(
        nb=nb,
        uid=current_user.uid,
        chat_id=c,
        limit=limit + 1,
        cursor_snap=cursor_snap,
    )
    has_older = len(snaps) > limit
    page_snaps = snaps[:limit]
    next_cursor = page_snaps[-1].id if has_older and page_snaps else None

    # Reverse page_snaps to chronological order (oldest first)
    chronological_snaps = list(reversed(page_snaps))
    items = []
    for doc in chronological_snaps:
        d = doc.to_dict() or {}
        role = d.get("role", "user")
        raw_refs = d.get("refs") or {}
        refs_obj = Refs.model_validate(raw_refs)
        if role == "user":
            items.append(
                MessageOut(
                    id=doc.id,
                    role="user",
                    created_at=d["created_at"],
                    text=d.get("text"),
                    paragraphs=None,
                    refs=refs_obj,
                    context=None,
                )
            )
        else:
            raw_paragraphs = d.get("paragraphs")
            paragraphs_obj = (
                [Paragraph.model_validate(p) for p in raw_paragraphs]
                if raw_paragraphs is not None
                else None
            )
            items.append(
                MessageOut(
                    id=doc.id,
                    role="assistant",
                    created_at=d["created_at"],
                    text=None,
                    paragraphs=paragraphs_obj,
                    refs=refs_obj,
                    context=None,
                )
            )

    return MessageList(items=items, next_cursor=next_cursor)


@router.post("/notebooks/{nb}/chats/{c}/messages", name="send")
def send_message(
    nb: str,
    c: str,
    body: ChatMessageCreate,
    current_user: CurrentUser,
    notebook: Annotated[Notebook, Depends(get_readable_notebook)],
) -> ChatSendOut:
    """Send a message to a chat, execute answering pipeline, and save turn transactionally."""
    if not is_valid_notebook_id(c):
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Chat not found."},
        )

    chat_snap = get_chat_snapshot(nb, current_user.uid, c)
    if not chat_snap.exists:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Chat not found."},
        )

    chat_data = chat_snap.to_dict() or {}
    topic_id = chat_data.get("topic_id")

    recent_messages = get_recent_chat_messages(nb, current_user.uid, c, limit=6)
    previous_user_text = None
    for m in reversed(recent_messages):
        if m.get("role") == "user":
            previous_user_text = m.get("text")
            break

    history = format_chat_history(recent_messages)
    custom_instructions = get_user_custom_instructions(current_user.uid)

    user_created_at = datetime.now(UTC)

    paragraphs, context, model_name, latency_ms = run_answer_pipeline(
        notebook=notebook,
        question=body.text,
        allow_outside=body.allow_outside,
        refs=body.refs,
        topic_id=topic_id,
        previous_user_text=previous_user_text,
        history=history,
        custom_instructions=custom_instructions,
    )

    db = get_db()
    messages_coll = db.collection(messages_collection_path(nb, current_user.uid, c))
    user_msg_id = messages_coll.document().id
    asst_msg_id = messages_coll.document().id
    asst_created_at = datetime.now(UTC)

    user_msg_data = {
        "role": "user",
        "created_at": user_created_at,
        "text": body.text,
        "refs": body.refs.model_dump() if body.refs else {"sources": []},
    }
    asst_msg_data = {
        "role": "assistant",
        "created_at": asst_created_at,
        "paragraphs": [p.model_dump() for p in paragraphs],
        "refs": {"sources": body.refs.sources if body.refs else []},
        "context": [{"chunk_id": chunk.chunk_id, "score": chunk.score} for chunk in context],
    }

    save_chat_messages_transaction(
        nb=nb,
        uid=current_user.uid,
        chat_id=c,
        user_msg_id=user_msg_id,
        asst_msg_id=asst_msg_id,
        user_msg_data=user_msg_data,
        asst_msg_data=asst_msg_data,
        asst_created_at=asst_created_at,
    )
    # S7: call coach.record_chat_signal here, after the save has committed (Firestore CoachStorage arrives in S7).  # noqa: E501

    user_msg_out = MessageOut(
        id=user_msg_id,
        role="user",
        created_at=user_created_at,
        text=body.text,
        paragraphs=None,
        refs=body.refs or Refs(),
        context=None,
    )
    asst_msg_out = MessageOut(
        id=asst_msg_id,
        role="assistant",
        created_at=asst_created_at,
        text=None,
        paragraphs=paragraphs,
        refs=Refs(sources=body.refs.sources if body.refs else []),
        context=context,
    )

    return ChatSendOut(
        user_message=user_msg_out,
        assistant_message=asst_msg_out,
        model=model_name,
        latency_ms=latency_ms,
    )
