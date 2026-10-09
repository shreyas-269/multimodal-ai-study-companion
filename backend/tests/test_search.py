from app.models.citation import Location
from app.retrieval.search import RetrievedChunk, select_top_chunks


def _make_chunk(chunk_id: str, is_video: bool, score: float) -> RetrievedChunk:
    if is_video:
        loc = Location(source_id="src_v", t_start_s=100.0, t_end_s=160.0)
    else:
        loc = Location(source_id="src_p", page=1)
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id="src_v" if is_video else "src_p",
        text=f"Text for {chunk_id}",
        loc=loc,
        score=score,
        segments=[],
    )


def test_select_top_chunks_video_cap_and_backfill():
    """8 video and 4 PDF candidates in score order.

    Expect 5 video + 4 PDF + 1 backfilled video = 10.
    """
    candidates = []
    # Interleave 8 video and 4 PDF in descending score order
    # Scores: 0.99 down to 0.88
    # V1(0.99), P1(0.98), V2(0.97), V3(0.96), P2(0.95), V4(0.94),
    # V5(0.93), P3(0.92), V6(0.91), P4(0.90), V7(0.89), V8(0.88)
    items = [
        ("V1", True, 0.99),
        ("P1", False, 0.98),
        ("V2", True, 0.97),
        ("V3", True, 0.96),
        ("P2", False, 0.95),
        ("V4", True, 0.94),
        ("V5", True, 0.93),
        ("P3", False, 0.92),
        ("V6", True, 0.91),
        ("P4", False, 0.90),
        ("V7", True, 0.89),
        ("V8", True, 0.88),
    ]
    for cid, is_vid, sc in items:
        candidates.append(_make_chunk(cid, is_vid, sc))

    selected = select_top_chunks(candidates, max_total=10, max_video=5)
    assert len(selected) == 10

    video_ids = [c.chunk_id for c in selected if c.loc.t_start_s is not None]
    pdf_ids = [c.chunk_id for c in selected if c.loc.t_start_s is None]

    # Exactly 4 PDFs (all available)
    assert pdf_ids == ["P1", "P2", "P3", "P4"]
    # Exactly 6 videos (top 5 + 1 backfilled V6 to reach 10)
    assert video_ids == ["V1", "V2", "V3", "V4", "V5", "V6"]

    # Original score order preserved:
    assert [c.chunk_id for c in selected] == [
        "V1", "P1", "V2", "V3", "P2", "V4", "V5", "P3", "V6", "P4"
    ]


def test_select_top_chunks_pdf_only():
    """PDF-only candidates stay identical to today (first 10 in score order)."""
    candidates = [_make_chunk(f"P{i}", False, 1.0 - i * 0.05) for i in range(15)]
    selected = select_top_chunks(candidates, max_total=10, max_video=5)
    assert len(selected) == 10
    assert [c.chunk_id for c in selected] == [f"P{i}" for i in range(10)]


def test_select_top_chunks_video_only():
    """Video-only candidates with 8 candidates -> returns all 8 (5 cap + 3 backfilled)."""
    candidates = [_make_chunk(f"V{i}", True, 1.0 - i * 0.05) for i in range(8)]
    selected = select_top_chunks(candidates, max_total=10, max_video=5)
    assert len(selected) == 8
    assert [c.chunk_id for c in selected] == [f"V{i}" for i in range(8)]
