from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.retrieval.search import RetrievedChunk

TOPIC_BOOST: float = 0.05


def rerank(
    candidates: list[RetrievedChunk],
    topic_id: str | None,
) -> list[RetrievedChunk]:
    """Rerank candidate chunks by applying a topic boost if topic_id matches.

    Returns a new list without mutating candidates or modifying c.score.
    """
    if topic_id is None:
        return list(candidates)

    return sorted(
        candidates,
        key=lambda c: c.score + (TOPIC_BOOST if c.topic_id == topic_id else 0.0),
        reverse=True,
    )
