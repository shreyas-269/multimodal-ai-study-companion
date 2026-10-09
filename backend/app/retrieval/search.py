from typing import Any

from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector
from pydantic import BaseModel, Field

from app.db import chunks_collection_path, get_db
from app.embeddings import embed_query
from app.models.citation import Location
from app.models.notebook import Notebook


class RetrievedChunk(BaseModel):
    chunk_id: str
    source_id: str
    text: str
    loc: Location
    score: float
    segments: list[dict[str, Any]] = Field(default_factory=list)


def select_top_chunks(
    candidates: list[RetrievedChunk],
    max_total: int = 10,
    max_video: int = 5,
) -> list[RetrievedChunk]:
    """Select up to max_total chunks from ready candidates, capping video chunks at max_video.

    If fewer than max_total non-video chunks exist, backfills with next best video chunks.
    Preserves original candidate score ordering.
    """
    selected: list[RetrievedChunk] = []
    skipped_video: list[RetrievedChunk] = []
    video_count = 0

    for c in candidates:
        is_video = c.loc.t_start_s is not None
        if is_video:
            if video_count < max_video:
                selected.append(c)
                video_count += 1
            else:
                skipped_video.append(c)
        else:
            selected.append(c)

        if len(selected) == max_total:
            break

    if len(selected) < max_total and skipped_video:
        needed = max_total - len(selected)
        backfilled = skipped_video[:needed]
        combined_ids = {id(c) for c in (selected + backfilled)}
        return [c for c in candidates if id(c) in combined_ids]

    return selected


def search(notebook: Notebook, question: str) -> list[RetrievedChunk]:
    ready_source_ids = {s.source_id for s in notebook.sources_summary if s.status == "ready"}
    if not ready_source_ids:
        return []

    query_vec = embed_query(question)
    db = get_db()
    chunks_coll = db.collection(chunks_collection_path(notebook.id))

    vector_query = chunks_coll.find_nearest(
        vector_field="embedding",
        query_vector=Vector(query_vec),
        distance_measure=DistanceMeasure.COSINE,
        limit=40,
        distance_result_field="distance",
    )

    candidates: list[RetrievedChunk] = []
    for doc in vector_query.get():
        data = doc.to_dict() or {}
        source_id = data.get("source_id", "")
        if source_id not in ready_source_ids:
            continue

        distance = float(data.get("distance", 0.0))
        score = 1.0 - distance
        loc_data = data.get("loc") or {}
        loc = Location.model_validate(loc_data)
        segments = data.get("segments") or []

        candidates.append(
            RetrievedChunk(
                chunk_id=doc.id,
                source_id=source_id,
                text=data.get("text", ""),
                loc=loc,
                score=score,
                segments=segments,
            )
        )

    return select_top_chunks(candidates, max_total=10, max_video=5)
