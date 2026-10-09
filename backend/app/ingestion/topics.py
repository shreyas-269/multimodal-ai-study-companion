import re
from dataclasses import dataclass
from typing import Any

VIDEO_WINDOW_S: int = 300


@dataclass(frozen=True)
class TopicRange:
    start_page: int
    end_page: int
    topic_ids: list[str]


def parse_topics_column(spec: str) -> list[TopicRange] | str:
    """Parse manifest topics column value.

    Returns:
        - str if single topic (e.g. "t1") or empty string
        - list[TopicRange] if range expression (e.g. "2-24=t1;25-69=t4;...")
    """
    spec = spec.strip()
    if not spec:
        return ""
    if ";" not in spec and "=" not in spec:
        return spec

    ranges: list[TopicRange] = []
    parts = [p.strip() for p in spec.split(";") if p.strip()]
    prev_end = 0

    for part in parts:
        match = re.match(r"^([0-9]+)(?:-([0-9]+))?=([A-Za-z0-9_|]+)$", part)
        if not match:
            raise ValueError(f"Invalid topic range specification: {part!r}")
        start_p, end_p, topics_part = match.groups()
        start = int(start_p)
        end = int(end_p) if end_p is not None else start
        if start > end:
            raise ValueError(f"Invalid range start > end: {part!r}")
        if start <= prev_end:
            raise ValueError(f"Overlapping range detected: {part!r} with previous end {prev_end}")
        topic_ids = [t.strip() for t in topics_part.split("|") if t.strip()]
        if not topic_ids:
            raise ValueError(f"No topic IDs found in part: {part!r}")
        ranges.append(TopicRange(start_page=start, end_page=end, topic_ids=topic_ids))
        prev_end = end

    return ranges


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = 0.0
    norm1 = 0.0
    norm2 = 0.0
    for a, b in zip(v1, v2, strict=True):
        dot += a * b
        norm1 += a * a
        norm2 += b * b
    if norm1 <= 0.0 or norm2 <= 0.0:
        return 0.0
    return dot / ((norm1**0.5) * (norm2**0.5))


def resolve_chunk_topic(
    page: int | None,
    chunk_emb: list[float] | None,
    mapping: list[TopicRange] | str,
    query_embs: dict[str, list[float]],
) -> str:
    """Resolve topic ID for a chunk.

    If mapping is a single topic string, returns it.
    If mapping is TopicRange list, checks page against ranges.
    For an 'a|b' range, resolves candidate with highest cosine similarity against query_embs.
    Tie-breaks deterministically by alphabetical topic ID order.
    """
    if isinstance(mapping, str):
        return mapping if mapping else "other"

    if page is None:
        return "other"

    for r in mapping:
        if r.start_page <= page <= r.end_page:
            if len(r.topic_ids) == 1:
                return r.topic_ids[0]
            if not chunk_emb:
                return r.topic_ids[0]
            # Multiple candidates (a|b range)
            best_topic = min(
                r.topic_ids,
                key=lambda t: (-cosine_similarity(chunk_emb, query_embs.get(t, [])), t),
            )
            return best_topic

    return "other"


def _extract_seq(chunk_dict: dict[str, Any]) -> int:
    cid = chunk_dict.get("id") or ""
    parts = cid.split("-")
    try:
        return int(parts[-1])
    except ValueError:
        return 0


def location_key(loc: dict[str, Any]) -> tuple[Any, ...]:
    """Derive grouping key for a location.

    For videos (t_start_s present): (source_id, floor(t_start_s / VIDEO_WINDOW_S)).
    For PDFs/slides: (source_id, page, slide).
    """
    src_id = loc.get("source_id", "")
    if loc.get("t_start_s") is not None:
        return (src_id, int(loc["t_start_s"] // VIDEO_WINDOW_S))
    return (src_id, loc.get("page"), loc.get("slide"))


def group_topic_locations(
    chunks_with_src: list[tuple[dict[str, Any], int]],
) -> list[dict[str, Any]]:
    """Group chunks into topic locations.

    Each entry corresponds to a distinct location_key(loc).
    Retains the chunk with lowest seq in each group.
    Ordered by source ref_n, then page (or t_start_s), then slide.
    """
    # Key: location_key(loc) -> (chunk_dict, ref_n, seq)
    groups: dict[tuple[Any, ...], tuple[dict[str, Any], int, int]] = {}

    for chunk, ref_n in chunks_with_src:
        loc = chunk.get("loc") or {}
        key = location_key(loc)
        seq = _extract_seq(chunk)

        if key not in groups:
            groups[key] = (chunk, ref_n, seq)
        else:
            prev_chunk, prev_ref_n, prev_seq = groups[key]
            if seq < prev_seq:
                groups[key] = (chunk, ref_n, seq)

    # Sort groups by ref_n, page (or t_start_s), slide
    sorted_items = sorted(
        groups.values(),
        key=lambda item: (
            item[1],  # ref_n
            item[0].get("loc", {}).get("page")
            if item[0].get("loc", {}).get("page") is not None
            else item[0].get("loc", {}).get("t_start_s") or 0,
            item[0].get("loc", {}).get("slide") or 0,
        ),
    )

    return [
        {
            "chunk_id": item[0].get("id") or f"{item[0].get('source_id')}-{item[2]:05d}",
            "loc": item[0].get("loc") or {},
        }
        for item in sorted_items
    ]
