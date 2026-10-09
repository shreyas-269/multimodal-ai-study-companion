from app.models.citation import Citation, Location, OpenPdfTarget


def build_citation(
    *,
    chunk_id: str,
    loc: Location,
    title: str,
) -> Citation | None:
    """Build a single Citation pointing to original material.

    Chunks without a page are dropped (returns None).
    """
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
