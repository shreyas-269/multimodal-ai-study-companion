from app.chat.citations import build_citation
from app.models.citation import Location


def test_build_citation_dropped_when_no_page():
    loc = Location(source_id="src_1", page=None)
    citation = build_citation(chunk_id="src_1-00000", loc=loc, title="Some Source")
    assert citation is None


def test_build_citation_standard_page_label():
    loc = Location(source_id="src_tb", page=70, page_label="137")
    citation = build_citation(chunk_id="src_tb-00001", loc=loc, title="Grinstead & Snell")
    assert citation is not None
    assert citation.label == "Grinstead & Snell p. 137"
    assert citation.open.kind == "pdf"
    assert citation.open.source_id == "src_tb"
    assert citation.open.page == 70
    assert citation.open.bbox is None


def test_build_citation_fallback_physical_page():
    loc = Location(source_id="src_rec", page=2, page_label=None)
    citation = build_citation(chunk_id="src_rec-00002", loc=loc, title="R03 solutions")
    assert citation is not None
    assert citation.label == "R03 solutions p. 2"
    assert citation.open.page == 2
    assert citation.open.bbox is None


def test_build_citation_slide_chunk():
    bbox = [19.5, 23.2, 304.5, 390.8]
    loc = Location(source_id="src_sl", page=2, page_label=None, slide=5, bbox=bbox)
    citation = build_citation(chunk_id="src_sl-00004", loc=loc, title="L03 slides")
    assert citation is not None
    assert citation.label == "L03 slides p. 2 (slide 5)"
    assert citation.open.page == 2
    assert citation.open.bbox == bbox


def test_build_citation_non_slide_drops_bbox():
    """Non-slide chunk ignores bbox on open target."""
    loc = Location(source_id="src_doc", page=1, page_label="10", slide=None, bbox=[1, 2, 3, 4])
    citation = build_citation(chunk_id="src_doc-00000", loc=loc, title="Doc")
    assert citation is not None
    assert citation.label == "Doc p. 10"
    assert citation.open.page == 1
    assert citation.open.bbox is None


def test_build_citation_video_under_hour():
    loc = Location(source_id="src_l02", t_start_s=754.2, t_end_s=800.0)
    citation = build_citation(
        chunk_id="src_l02-00010",
        loc=loc,
        title="L02 lecture",
        youtube_id="yt123",
        offset_s=10.0,
    )
    assert citation is not None
    assert citation.label == "L02 lecture, 12:34"
    assert citation.open.kind == "youtube"
    assert citation.open.url == "https://www.youtube.com/watch?v=yt123&t=764s"


def test_build_citation_video_over_hour():
    loc = Location(source_id="src_l03", t_start_s=3754.8, t_end_s=3800.0)
    citation = build_citation(
        chunk_id="src_l03-00050",
        loc=loc,
        title="L03 lecture",
        youtube_id="yt456",
        offset_s=0.0,
    )
    assert citation is not None
    assert citation.label == "L03 lecture, 1:02:34"
    assert citation.open.kind == "youtube"
    assert citation.open.url == "https://www.youtube.com/watch?v=yt456&t=3754s"


def test_build_citation_video_missing_youtube_id_returns_none():
    loc = Location(source_id="src_l02", t_start_s=100.0)
    citation = build_citation(
        chunk_id="src_l02-00001",
        loc=loc,
        title="L02",
        youtube_id=None,
    )
    assert citation is None
