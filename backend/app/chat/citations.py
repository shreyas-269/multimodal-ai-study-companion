import math

from app.models.citation import Citation, Location, OpenPdfTarget, OpenYouTubeTarget


def format_citation_time(seconds: float) -> str:
    """Format seconds into m:ss under an hour and h:mm:ss from an hour."""
    total_secs = max(0, int(math.floor(seconds)))
    hours = total_secs // 3600
    rem = total_secs % 3600
    mins = rem // 60
    secs = rem % 60
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def build_citation(
    *,
    chunk_id: str,
    loc: Location,
    title: str,
    youtube_id: str | None = None,
    offset_s: float | None = None,
) -> Citation | None:
    """Build a single Citation pointing to original material.

    For video chunks (loc.t_start_s present): creates a YouTube open target.
    Requires youtube_id; returns None if missing.
    Chunks without a page or t_start_s are dropped (returns None).
    """
    if loc.t_start_s is not None:
        if not youtube_id:
            return None
        time_str = format_citation_time(loc.t_start_s)
        label = f"{title}, {time_str}"
        effective_offset = offset_s if offset_s is not None else 0.0
        start_secs = int(effective_offset + math.floor(loc.t_start_s))
        url = f"https://www.youtube.com/watch?v={youtube_id}&t={start_secs}s"
        return Citation(
            chunk_id=chunk_id,
            loc=loc,
            label=label,
            open=OpenYouTubeTarget(kind="youtube", url=url),
        )

    if loc.page is None:
        return None

    page_str = loc.page_label if loc.page_label else str(loc.page)
    label = f"{title} p. {page_str}"
    if loc.slide is not None:
        label += f" (slide {loc.slide})"

    open_target = OpenPdfTarget(
        kind="pdf",
        source_id=loc.source_id,
        page=loc.page,
        bbox=loc.bbox if loc.slide is not None else None,
    )

    return Citation(
        chunk_id=chunk_id,
        loc=loc,
        label=label,
        open=open_target,
    )
