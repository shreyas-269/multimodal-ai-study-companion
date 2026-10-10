import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.models.citation import Location
from app.models.notebook import Notebook, SourceSummary
from app.retrieval.search import RetrievedChunk, search, select_top_chunks


def _make_chunk(
    chunk_id: str,
    is_video: bool,
    score: float,
    topic_id: str | None = None,
) -> RetrievedChunk:
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
        topic_id=topic_id,
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


def _mock_doc_snap(doc_id: str, data: dict):
    snap = MagicMock()
    snap.id = doc_id
    snap.to_dict.return_value = data
    return snap


def test_search_topic_boost_feeds_top_10(monkeypatch):
    """2.1 Boosted order feeds the top 10."""
    search_mod = sys.modules["app.retrieval.search"]
    monkeypatch.setattr(search_mod, "embed_query", lambda q: [0.1] * 10)
    mock_db = MagicMock()
    mock_coll = MagicMock()
    mock_vquery = MagicMock()
    mock_coll.find_nearest.return_value = mock_vquery
    mock_db.collection.return_value = mock_coll
    monkeypatch.setattr(search_mod, "get_db", lambda: mock_db)

    # 12 candidates: 10 off-topic with scores 0.89 down to 0.80.
    # 2 on-topic at positions 11 and 12 with scores 0.79 and 0.78 (gap < 0.05 from 0.82 and 0.81).
    # With boost +0.05, on-topic scores become 0.84 and 0.83, boosting them into top 10!
    docs = []
    for i in range(10):
        # distance = 1.0 - score
        score = 0.89 - i * 0.01
        docs.append(_mock_doc_snap(f"off_{i}", {
            "source_id": "src_ready",
            "distance": round(1.0 - score, 4),
            "text": f"Off text {i}",
            "loc": {"source_id": "src_ready", "page": 1},
            "topic_id": None,
        }))
    # T1: score 0.79 -> boosted 0.84 (beats off_6: 0.83)
    docs.append(_mock_doc_snap("topic_1", {
        "source_id": "src_ready",
        "distance": round(1.0 - 0.79, 4),
        "text": "Topic text 1",
        "loc": {"source_id": "src_ready", "page": 2},
        "topic_id": "t2",
    }))
    # T2: score 0.78 -> boosted 0.83 (beats off_7: 0.82)
    docs.append(_mock_doc_snap("topic_2", {
        "source_id": "src_ready",
        "distance": round(1.0 - 0.78, 4),
        "text": "Topic text 2",
        "loc": {"source_id": "src_ready", "page": 3},
        "topic_id": "t2",
    }))
    mock_vquery.get.return_value = docs

    nb = Notebook(
        id="nb_test",
        name="NB",
        owner_uid="uid_test",
        is_demo=False,
        status="ready",
        created_at=datetime.now(UTC),
        sources_summary=[
            SourceSummary(source_id="src_ready", ref_n=1, title="S1", kind="pdf", status="ready")
        ],
    )

    results = search(nb, "question", topic_id="t2")
    assert len(results) == 10
    result_ids = [c.chunk_id for c in results]
    assert "topic_1" in result_ids
    assert "topic_2" in result_ids
    # Off-topic 8 and 9 were pushed out
    assert "off_8" not in result_ids
    assert "off_9" not in result_ids


def test_search_video_cap_preserved_with_topic_boost(monkeypatch):
    """2.2 Video cap preserved: 8 topic video chunks and 6 PDF chunks; capped at 5 video."""
    search_mod = sys.modules["app.retrieval.search"]
    monkeypatch.setattr(search_mod, "embed_query", lambda q: [0.1] * 10)
    mock_db = MagicMock()
    mock_coll = MagicMock()
    mock_vquery = MagicMock()
    mock_coll.find_nearest.return_value = mock_vquery
    mock_db.collection.return_value = mock_coll
    monkeypatch.setattr(search_mod, "get_db", lambda: mock_db)

    # 8 video chunks all on-topic t2 with higher scores (0.90 down to 0.83)
    docs = []
    for i in range(8):
        score = 0.90 - i * 0.01
        docs.append(_mock_doc_snap(f"v_{i}", {
            "source_id": "src_ready",
            "distance": round(1.0 - score, 4),
            "text": f"Video {i}",
            "loc": {
                "source_id": "src_ready",
                "t_start_s": float(i * 10),
                "t_end_s": float(i * 10 + 5),
            },
            "topic_id": "t2",
        }))
    # 6 PDF chunks (scores 0.80 down to 0.75)
    for i in range(6):
        score = 0.80 - i * 0.01
        docs.append(_mock_doc_snap(f"p_{i}", {
            "source_id": "src_ready",
            "distance": round(1.0 - score, 4),
            "text": f"PDF {i}",
            "loc": {"source_id": "src_ready", "page": i + 1},
            "topic_id": None,
        }))
    mock_vquery.get.return_value = docs

    nb = Notebook(
        id="nb_test",
        name="NB",
        owner_uid="uid_test",
        is_demo=False,
        status="ready",
        created_at=datetime.now(UTC),
        sources_summary=[
            SourceSummary(source_id="src_ready", ref_n=1, title="S1", kind="video", status="ready")
        ],
    )

    results = search(nb, "question", topic_id="t2")
    video_results = [c for c in results if c.loc.t_start_s is not None]
    pdf_results = [c for c in results if c.loc.t_start_s is None]
    # Even though all 8 videos have higher scores and are boosted, video cap is strictly 5
    assert len(video_results) == 5
    assert len(pdf_results) == 5
    assert len(results) == 10
