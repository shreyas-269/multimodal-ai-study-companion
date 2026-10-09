import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.access import get_readable_notebook
from app.chat.citations import build_citation
from app.db.sources import get_sources_metadata_by_ids
from app.db.topics import get_topic_snapshot, list_topic_snapshots
from app.models.citation import Location
from app.models.notebook import Notebook
from app.models.topic import TopicList, TopicListItem, TopicSourceList

router = APIRouter(tags=["topics"])


def is_valid_topic_id(value: str) -> bool:
    """Validate topic ID strictly against known pattern (t1..t6 or other)."""
    return bool(re.fullmatch(r"t[1-6]|other", value))


@router.get("/notebooks/{nb}/topics", name="list")
def list_topics(
    nb: str,
    notebook: Annotated[Notebook, Depends(get_readable_notebook)],
) -> TopicList:
    """List topics for a readable notebook sorted by order."""
    snaps = list_topic_snapshots(nb)
    items: list[TopicListItem] = []

    for doc in snaps:
        data = doc.to_dict() or {}
        tid = doc.id
        is_other = data.get("is_other", False) or (tid == "other")
        location_count = data.get("location_count", len(data.get("locations", [])))

        # "other" topic is included only if location_count > 0
        if is_other and location_count <= 0:
            continue

        items.append(
            TopicListItem(
                id=tid,
                name=data.get("name", tid),
                order=data.get("order", 999),
                summary=data.get("summary", ""),
                is_other=is_other,
                prerequisite_ids=data.get("prerequisite_ids", []),
                location_count=location_count,
            )
        )

    items.sort(key=lambda t: t.order)
    return TopicList(items=items, next_cursor=None)


@router.get("/notebooks/{nb}/topics/{t}/sources", name="list_sources")
def list_topic_sources(
    nb: str,
    t: str,
    notebook: Annotated[Notebook, Depends(get_readable_notebook)],
) -> TopicSourceList:
    """Return citations for all locations where this topic is covered."""
    if not is_valid_topic_id(t):
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Topic not found"},
        )

    topic_snap = get_topic_snapshot(nb, t)
    if not topic_snap.exists:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Topic not found"},
        )

    topic_data = topic_snap.to_dict() or {}
    locations = topic_data.get("locations", [])

    # Extract source titles from notebook.sources_summary (skip non-ready sources)
    source_titles = {
        s.source_id: s.title
        for s in notebook.sources_summary
        if s.status == "ready"
    }

    # If any location has video (t_start_s), fetch metadata
    # for distinct video source IDs via get_all
    video_source_ids = sorted(
        {
            loc_item.get("loc", {}).get("source_id")
            for loc_item in locations
            if loc_item.get("loc", {}).get("t_start_s") is not None
            and loc_item.get("loc", {}).get("source_id") in source_titles
        }
    )
    video_meta = (
        get_sources_metadata_by_ids(nb, video_source_ids)
        if video_source_ids
        else {}
    )

    citations = []
    for loc_item in locations:
        chunk_id = loc_item.get("chunk_id", "")
        raw_loc = loc_item.get("loc") or {}
        loc = Location.model_validate(raw_loc)
        if loc.source_id not in source_titles:
            continue
        title = source_titles[loc.source_id]

        if loc.t_start_s is not None:
            meta = video_meta.get(loc.source_id, {})
            yt_id = meta.get("youtube_id")
            offset_s = meta.get("offset_s")
            citation = build_citation(
                chunk_id=chunk_id,
                loc=loc,
                title=title,
                youtube_id=yt_id,
                offset_s=offset_s,
            )
        else:
            citation = build_citation(chunk_id=chunk_id, loc=loc, title=title)

        if citation is not None:
            citations.append(citation)

    return TopicSourceList(items=citations, next_cursor=None)
