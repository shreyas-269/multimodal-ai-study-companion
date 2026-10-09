import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.db import get_db, notebook_path, sources_collection_path
from app.ingestion.video import (
    chunk_transcript_segments,
    filter_preamble_segments,
    parse_and_chunk_transcript,
)
from app.main import app
from app.storage import delete_prefix
from scripts.ingest_course import ingest_course
from tests.conftest import create_emulator_user


def test_filter_preamble_segments():
    """Verify preamble filtering rules, single segment cutoff, and 90s cap."""
    # 1. Multi-segment preamble
    segs = [
        {"start": 0.0, "end": 3.8, "text": "Provided under a Creative Commons license."},
        {"start": 3.8, "end": 10.5, "text": "Your support will help MIT OpenCourseWare."},
        {"start": 10.5, "end": 22.3, "text": "Make a donation at ocw.mit.edu."},
        {"start": 22.3, "end": 26.0, "text": "Welcome to probability."},
    ]
    kept, dropped_s, dropped_cnt = filter_preamble_segments(segs)
    assert dropped_cnt == 3
    assert dropped_s == 22.3
    assert len(kept) == 1
    assert kept[0]["text"] == "Welcome to probability."

    # 2. Single segment preamble cutoff (L02 case)
    l02_segs = [
        {"start": 0.0, "end": 32.0, "text": "Creative Commons license. Your support will help MIT"},
        {"start": 32.0, "end": 36.5, "text": "information is always partial."},
    ]
    kept_l02, dropped_s_l02, dropped_cnt_l02 = filter_preamble_segments(l02_segs)
    assert dropped_cnt_l02 == 1
    assert dropped_s_l02 == 32.0
    assert len(kept_l02) == 1
    assert kept_l02[0]["text"] == "information is always partial."

    # 3. 90s cap (never drop segment starting at 90s or later)
    cap_segs = [
        {"start": 85.0, "end": 89.0, "text": "Visit ocw.mit.edu for more."},
        {"start": 91.0, "end": 95.0, "text": "Please make a donation if you like."},
        {"start": 95.0, "end": 100.0, "text": "Let us continue."},
    ]
    kept_cap, dropped_s_cap, dropped_cnt_cap = filter_preamble_segments(cap_segs)
    assert dropped_cnt_cap == 1
    assert dropped_s_cap == 89.0
    assert len(kept_cap) == 2
    assert kept_cap[0]["start"] == 91.0

    # 4. No preamble keywords
    clean_segs = [{"start": 0.0, "end": 5.0, "text": "Hello world."}]
    kept_clean, dropped_s_clean, dropped_cnt_clean = filter_preamble_segments(clean_segs)
    assert dropped_cnt_clean == 0
    assert dropped_s_clean == 0.0
    assert len(kept_clean) == 1


def test_chunk_transcript_segments_limits():
    """Verify 60s and 400 token limits, and single oversized segment as standalone chunk."""
    # Build 10 short segments of 10s each
    segs = [
        {"start": float(i * 10), "end": float((i + 1) * 10), "text": f"segment {i}"}
        for i in range(15)
    ]
    chunks = chunk_transcript_segments(segs, source_id="src_v1", topic_id="t1")

    # Each chunk should span at most 60s
    for c in chunks:
        dur = c["loc"]["t_end_s"] - c["loc"]["t_start_s"]
        assert dur <= 60.0
        assert c["kind"] == "transcript"
        assert c["source_id"] == "src_v1"
        assert c["topic_id"] == "t1"
        assert c["id"].startswith("src_v1-")
        assert len(c["segments"]) > 0

    # Single oversized segment (>400 tokens) is kept as a chunk of its own without word-splitting
    long_text = "word " * 450
    oversized_seg = [{"start": 0.0, "end": 15.0, "text": long_text}]
    with patch("app.ingestion.video.count_tokens", return_value=450):
        over_chunks = chunk_transcript_segments(oversized_seg, source_id="src_v2", topic_id="t2")
        assert len(over_chunks) == 1
        assert over_chunks[0]["text"] == long_text.strip()
        assert over_chunks[0]["loc"]["t_start_s"] == 0.0
        assert over_chunks[0]["loc"]["t_end_s"] == 15.0


def test_parse_and_chunk_transcript_validation(tmp_path: Path):
    """Verify transcript validation errors."""
    missing_file = tmp_path / "nonexistent.json"
    with pytest.raises(ValueError, match="transcript missing or invalid"):
        parse_and_chunk_transcript(missing_file, "src_v", "t1")

    # Invalid schema version
    bad_schema = tmp_path / "bad_schema.json"
    bad_schema_content = {
        "schema_version": 2,
        "segments": [{"start": 0, "end": 1, "text": "hi"}],
    }
    bad_schema.write_text(json.dumps(bad_schema_content))
    with pytest.raises(ValueError, match="transcript missing or invalid"):
        parse_and_chunk_transcript(bad_schema, "src_v", "t1")

    # Clip trials file (clip_seconds not None)
    clip_file = tmp_path / "clip.json"
    clip_content = {
        "schema_version": 1,
        "clip_seconds": 30,
        "segments": [{"start": 0, "end": 1, "text": "hi"}],
    }
    clip_file.write_text(json.dumps(clip_content))
    with pytest.raises(ValueError, match="transcript missing or invalid"):
        parse_and_chunk_transcript(clip_file, "src_v", "t1")


def test_video_ingest_synthetic_course_with_failure_resilience(tmp_path: Path):
    """Synthetic course with valid L01 and missing L02 transcript.

    Asserts run continues, L02 is marked failed, notebook status is 'ready', and GET returns 200.
    """
    course_dir = tmp_path / "course"
    course_dir.mkdir()

    syllabus = course_dir / "syllabus.md"
    syllabus.write_text("# Syllabus\n1. Topic 1\n2. Topic 2\n", encoding="utf-8")

    # 1-page PDF
    handout = course_dir / "handout.pdf"
    doc = pymupdf.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text(pymupdf.Point(50, 50), "PDF Handout text on probability.")
    doc.save(str(handout))
    doc.close()

    # Derived transcripts directory
    transcripts_dir = course_dir / "derived" / "transcripts"
    transcripts_dir.mkdir(parents=True)

    # Valid L01 transcript
    l01_json = transcripts_dir / "L01.json"
    l01_data = {
        "schema_version": 1,
        "clip_seconds": None,
        "duration_s": 50.0,
        "segments": [
            {"start": 0.0, "end": 5.0, "text": "Creative Commons OpenCourseWare donation."},
            {"start": 5.0, "end": 25.0, "text": "Welcome to lecture one on probability models."},
            {"start": 25.0, "end": 45.0, "text": "Sample spaces and axioms of probability."},
        ],
    }
    l01_json.write_text(json.dumps(l01_data), encoding="utf-8")
    # L02 transcript is deliberately omitted

    manifest = course_dir / "manifest.csv"
    manifest.write_text(
        """file,type,youtube_id,offset_s,title,licence,attribution,slide_grid,topics,source_url
handout.pdf,pdf,,,Handout,CC BY-NC-SA 4.0,"MIT",,t1,
syllabus.md,syllabus,,,Syllabus,,,,
videos/L01.mp4,video,yt_l01_id,0,Lecture 1,CC BY-NC-SA 4.0,"MIT",,t1,
videos/L02.mp4,video,yt_l02_id,0,Lecture 2,CC BY-NC-SA 4.0,"MIT",,t2,
""",
        encoding="utf-8",
    )

    test_nb = f"nb_test_video_{uuid.uuid4().hex[:8]}"
    db = get_db()
    client = TestClient(app)
    uid, token = create_emulator_user()

    try:
        # Run 1: Ingest
        ok = ingest_course(course_dir, notebook_id=test_nb, force=False)
        # Because L02 failed, ingest_course returns False (any_failed is True)
        assert ok is False

        # Notebook doc status must be 'ready' (derived from ready sources)
        nb_snap = db.document(notebook_path(test_nb)).get()
        assert nb_snap.exists
        nb_data = nb_snap.to_dict() or {}
        assert nb_data["status"] == "ready"

        # Verify sources in Firestore
        sources = list(db.collection(sources_collection_path(test_nb)).stream())
        src_map = {s.id: s.to_dict() for s in sources}

        assert "src_l01" in src_map
        assert src_map["src_l01"]["status"] == "ready"
        assert src_map["src_l01"]["youtube_id"] == "yt_l01_id"
        assert src_map["src_l01"]["duration_s"] == 50.0

        assert "src_l02" in src_map
        assert src_map["src_l02"]["status"] == "failed"
        assert "transcript missing or invalid" in src_map["src_l02"]["error"]

        # GET /v1/notebooks/{nb} returns 200 (not 500)
        res = client.get(f"/v1/notebooks/{test_nb}", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        assert res.json()["status"] == "ready"

        # Re-run without force skips L01
        ok_rerun = ingest_course(course_dir, notebook_id=test_nb, force=False)
        assert ok_rerun is False
    finally:
        try:
            db.recursive_delete(db.document(notebook_path(test_nb)))
        except Exception:
            pass
        try:
            delete_prefix(f"notebooks/{test_nb}/")
        except Exception:
            pass
