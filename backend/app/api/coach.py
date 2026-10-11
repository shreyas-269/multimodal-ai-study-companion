import logging
import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.access import get_readable_notebook
from app.auth import CurrentUser
from app.coach import get_progress, get_topics_needing_work, record_checkbox
from app.db.coach import coach_storage, get_study_coach_enabled, set_topic_checked
from app.db.members import ensure_member
from app.db.topics import get_topic_snapshot
from app.models.coach import (
    CheckedIn,
    CheckedOut,
    NeedsWorkItemOut,
    NeedsWorkOut,
    ProgressOut,
    TopicProgressOut,
)
from app.models.notebook import Notebook

router = APIRouter()
logger = logging.getLogger(__name__)


def is_valid_topic_id(value: str) -> bool:
    """Validate topic ID strictly against known pattern (t1..t6 or other)."""
    return bool(re.fullmatch(r"t[1-6]|other", value))


@router.get("/notebooks/{nb}/progress", tags=["progress"], name="get")
def get_progress_endpoint(
    nb: str,
    notebook: Annotated[Notebook, Depends(get_readable_notebook)],
    current_user: CurrentUser,
) -> ProgressOut:
    """Get topic progress for a readable notebook."""
    storage = coach_storage(notebook.id, current_user.uid)
    study_coach = get_study_coach_enabled(current_user.uid)
    now = datetime.now(UTC)

    res = get_progress(
        storage, notebook.id, current_user.uid, coach_on=study_coach, now=now
    )
    items = [
        TopicProgressOut(
            topic_id=tp.topic_id,
            checked=tp.checked,
            p_known=tp.p_known,
            mastered=None if tp.label is None else tp.label == "mastered",
            n_obs=tp.n_obs,
            last_updated=tp.last_updated,
        )
        for tp in res.topics
    ]
    return ProgressOut(study_coach=res.coach_on, items=items, next_cursor=None)


@router.get("/notebooks/{nb}/needs-work", tags=["needs_work"], name="get")
def get_needs_work_endpoint(
    nb: str,
    notebook: Annotated[Notebook, Depends(get_readable_notebook)],
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=6)] = 3,
) -> NeedsWorkOut:
    """Get topics needing work for a readable notebook."""
    storage = coach_storage(notebook.id, current_user.uid)
    study_coach = get_study_coach_enabled(current_user.uid)
    now = datetime.now(UTC)
    res = get_topics_needing_work(
        storage,
        notebook.id,
        current_user.uid,
        coach_on=study_coach,
        now=now,
        limit=limit,
    )
    items = [
        NeedsWorkItemOut(
            topic_id=it.topic_id,
            p_known=it.p_known,
            chat_signals=it.chat_signals,
        )
        for it in res.items
    ]
    return NeedsWorkOut(study_coach=res.coach_on, items=items, next_cursor=None)


@router.put("/notebooks/{nb}/topics/{t}/checked", tags=["topics"], name="put_checked")
def put_topic_checked(
    nb: str,
    t: str,
    body: CheckedIn,
    notebook: Annotated[Notebook, Depends(get_readable_notebook)],
    current_user: CurrentUser,
) -> CheckedOut:
    """Set topic checkbox state."""
    # a. get_readable_notebook is satisfied by dependency.
    # b. Check topic ID strictly before any read.
    if not is_valid_topic_id(t):
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Topic not found"},
        )
    topic_snap = get_topic_snapshot(notebook.id, t)
    if not topic_snap.exists:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Topic not found"},
        )

    # c. ensure_member
    ensure_member(notebook.id, current_user.uid)

    # d & e. Update checked map if needed
    changed = set_topic_checked(notebook.id, current_user.uid, t, body.checked)

    # f. Call record_checkbox if state changed and Study Coach is enabled
    if changed and get_study_coach_enabled(current_user.uid):
        try:
            storage = coach_storage(notebook.id, current_user.uid)
            record_checkbox(
                storage,
                notebook.id,
                current_user.uid,
                topic_id=t,
                checked=body.checked,
                coach_on=True,
                now=datetime.now(UTC),
            )
        except Exception:
            logger.exception("Failed to record checkbox event in Study Coach")

    return CheckedOut(topic_id=t, checked=body.checked)
