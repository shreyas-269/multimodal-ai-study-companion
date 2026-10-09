from unittest.mock import patch

from app.chat.answer import refine_video_citation_time


def test_refine_video_citation_time_earliest_within_margin():
    """Verify earliest 2-segment window within margin 0.03 of best window is selected."""
    segments = [
        {"start": 10.0, "end": 15.0, "text": "intro text"},
        {"start": 15.0, "end": 20.0, "text": "target concept explanation"},
        {"start": 20.0, "end": 25.0, "text": "further elaboration on target"},
        {"start": 25.0, "end": 30.0, "text": "closing remarks"},
    ]
    # Windows:
    # 0: start 10.0, text "intro text target concept explanation"
    # 1: start 15.0, text "target concept explanation further elaboration on target"
    # 2: start 20.0, text "further elaboration on target closing remarks"

    # Mock embed_passages to return vectors with controlled cosine similarities
    # Say paragraph embedding is [1, 0, 0]
    # Window 0 sim = 0.82
    # Window 1 sim = 0.89 (within 0.03 of best 0.91)
    # Window 2 sim = 0.91 (best)
    # Since Window 1 has 0.89 >= 0.91 - 0.03, Window 1 (start 15.0) should be chosen over Window 2!
    def mock_embed_passages(texts: list[str], **_: object) -> list[list[float]]:
        # Return 1 vector per text
        res = []
        for t in texts:
            if "intro" in t:
                res.append([0.82, 0.57, 0.0])
            elif "explanation further" in t:
                res.append([0.89, 0.45, 0.0])
            elif "elaboration on target closing" in t:
                res.append([0.91, 0.41, 0.0])
            else:
                # Paragraph
                res.append([1.0, 0.0, 0.0])
        return res

    with patch("app.chat.answer.embed_passages", side_effect=mock_embed_passages):
        refined = refine_video_citation_time(
            p_text="Discussing the target concept",
            segments=segments,
            default_t=10.0,
            margin=0.03,
        )
        assert refined == 15.0


def test_refine_video_citation_time_fallback_paths():
    """Fallback paths return default_t or single segment start."""
    # 1. Empty segments -> returns default_t
    assert refine_video_citation_time("Some text", [], default_t=42.0) == 42.0

    # 2. Single segment -> returns that segment's start
    single_seg = [{"start": 18.5, "end": 23.0, "text": "only one segment"}]
    with patch("app.chat.answer.embed_passages", return_value=[[1.0, 0.0], [1.0, 0.0]]):
        assert refine_video_citation_time("Some text", single_seg, default_t=0.0) == 18.5

    # 3. Exception in embedding -> returns default_t
    with patch("app.chat.answer.embed_passages", side_effect=RuntimeError("embedder error")):
        segments = [
            {"start": 1.0, "end": 2.0, "text": "a"},
            {"start": 2.0, "end": 3.0, "text": "b"},
        ]
        assert refine_video_citation_time("Some text", segments, default_t=1.0) == 1.0
