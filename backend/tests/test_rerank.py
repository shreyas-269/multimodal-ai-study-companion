from app.models.citation import Location
from app.retrieval.rerank import rerank
from app.retrieval.search import RetrievedChunk


def _make_chunk(chunk_id: str, score: float, topic_id: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id="src_1",
        text=f"Text {chunk_id}",
        loc=Location(source_id="src_1", page=1),
        score=score,
        segments=[],
        topic_id=topic_id,
    )


def test_rerank_boost_size_cases():
    """1.1 Boost size, exactly two cases (similarity scores, higher is better)."""
    # Case A: off-topic 0.80, topic 0.76 (gap 0.04).
    # With boost 0.05, topic score is 0.81 > 0.80 -> topic chunk is first.
    c_off_a = _make_chunk("off_a", 0.80, topic_id=None)
    c_topic_a = _make_chunk("topic_a", 0.76, topic_id="t2")
    res_a = rerank([c_off_a, c_topic_a], topic_id="t2")
    assert [c.chunk_id for c in res_a] == ["topic_a", "off_a"]

    # Case B: off-topic 0.80, topic 0.74 (gap 0.06).
    # With boost 0.05, topic score is 0.79 < 0.80 -> off-topic chunk stays first.
    c_off_b = _make_chunk("off_b", 0.80, topic_id=None)
    c_topic_b = _make_chunk("topic_b", 0.74, topic_id="t2")
    res_b = rerank([c_off_b, c_topic_b], topic_id="t2")
    assert [c.chunk_id for c in res_b] == ["off_b", "topic_b"]


def test_rerank_topic_id_none_is_identity():
    """1.3 topic_id=None is the identity."""
    c1 = _make_chunk("c1", 0.70, topic_id="t1")
    c2 = _make_chunk("c2", 0.80, topic_id="t2")
    c3 = _make_chunk("c3", 0.75, topic_id=None)
    orig = [c1, c2, c3]
    res = rerank(orig, topic_id=None)
    assert [c.chunk_id for c in res] == ["c1", "c2", "c3"]


def test_rerank_stable_sort_for_equal_scores():
    """1.4 Stable sort for equal effective scores."""
    # Both off-topic with equal scores 0.80
    c1 = _make_chunk("first", 0.80, topic_id="t1")
    c2 = _make_chunk("second", 0.80, topic_id="t1")
    res = rerank([c1, c2], topic_id="t2")
    assert [c.chunk_id for c in res] == ["first", "second"]

    # One on-topic 0.75 (+0.05 = 0.80) and one off-topic 0.80.
    # When effective scores tie, original order is preserved.
    c_tie1 = _make_chunk("tie1", 0.80, topic_id=None)
    c_tie2 = _make_chunk("tie2", 0.75, topic_id="t2")
    res_tie = rerank([c_tie1, c_tie2], topic_id="t2")
    assert [c.chunk_id for c in res_tie] == ["tie1", "tie2"]


def test_rerank_input_list_unchanged():
    """1.5 Input list is not mutated; return value is a new list instance."""
    c1 = _make_chunk("c1", 0.80, topic_id=None)
    c2 = _make_chunk("c2", 0.76, topic_id="t2")
    orig = [c1, c2]
    res = rerank(orig, topic_id="t2")
    assert res is not orig
    assert orig == [c1, c2]


def test_rerank_context_scores_stay_raw():
    """1.6 RetrievedChunk instances retain their original score attribute."""
    c = _make_chunk("c", 0.76, topic_id="t2")
    res = rerank([c], topic_id="t2")
    assert res[0].score == 0.76
