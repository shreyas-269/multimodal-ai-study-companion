import os
import subprocess
import sys
import uuid
from pathlib import Path

import pymupdf
import pytest

from app.db import (
    chunks_collection_path,
    get_db,
    notebook_path,
    sources_collection_path,
)
from app.db.topics import list_topic_snapshots
from app.storage import delete_prefix
from scripts.ingest_course import ingest_course, verify_emulator_preconditions


def test_verify_emulator_preconditions_safety(monkeypatch):
    """Refuse to run if project ID doesn't start with demo-, exiting with code 2."""
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "real-study-companion")
    with pytest.raises(SystemExit) as exc_info:
        verify_emulator_preconditions()
    assert exc_info.value.code == 2

    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-test")
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    with pytest.raises(SystemExit) as exc_info2:
        verify_emulator_preconditions()
    assert exc_info2.value.code == 2


def _create_synthetic_course(tmp_path: Path) -> Path:
    course_dir = tmp_path / "synthetic_course"
    course_dir.mkdir(parents=True, exist_ok=True)

    # 1. syllabus.md
    syllabus_path = course_dir / "syllabus.md"
    syllabus_path.write_text(
        """# Synthetic Course
1. Basic Probability. Prerequisites: none
2. Conditional Probability. Prerequisites: 1
""",
        encoding="utf-8",
    )

    # 2. 2x2 slide PDF: 2 pages (content + OCW terms)
    slides_pdf = course_dir / "slides.pdf"
    doc_s = pymupdf.open()
    p1_s = doc_s.new_page(width=612, height=792)
    w, h = 285.0, 367.5
    boxes = [
        (19.5, 23.2, 19.5 + w, 23.2 + h),
        (307.5, 23.2, 307.5 + w, 23.2 + h),
        (19.5, 401.2, 19.5 + w, 401.2 + h),
        (307.5, 401.2, 307.5 + w, 401.2 + h),
    ]
    for b in boxes:
        p1_s.draw_rect(pymupdf.Rect(*b))
    p1_s.insert_text(pymupdf.Point(30, 50), "Slide 1: Basic concepts")
    p1_s.insert_text(pymupdf.Point(320, 50), "Slide 2: Sample space")
    p1_s.insert_text(pymupdf.Point(30, 430), "Slide 3: Probability axioms")
    p1_s.insert_text(pymupdf.Point(320, 430), "Slide 4: Summary")
    # Terms page
    p2_s = doc_s.new_page(width=612, height=792)
    p2_s.insert_text(pymupdf.Point(50, 50), "MIT OpenCourseWare ocw.mit.edu/terms 6.041 terms")
    doc_s.save(str(slides_pdf))
    doc_s.close()

    # 3. Plain PDF: 2 pages (content + OCW terms)
    plain_pdf = course_dir / "handout.pdf"
    doc_p = pymupdf.open()
    p1_p = doc_p.new_page(width=612, height=792)
    p1_p.insert_text(
        pymupdf.Point(50, 50), "Handout: Conditional Probability and Bayes theorem notes."
    )
    p2_p = doc_p.new_page(width=612, height=792)
    p2_p.insert_text(pymupdf.Point(50, 50), "MIT OpenCourseWare ocw.mit.edu/terms 6.041 terms")
    doc_p.save(str(plain_pdf))
    doc_p.close()

    # 4. manifest.csv
    manifest_path = course_dir / "manifest.csv"
    manifest_path.write_text(
        """file,type,youtube_id,offset_s,title,licence,attribution,slide_grid,topics,source_url
slides.pdf,pdf,,,Lecture Slides,CC BY-NC-SA 4.0,"MIT",2x2,t1,
handout.pdf,pdf,,,Handout Notes,CC BY-NC-SA 4.0,"MIT",,t2,
syllabus.md,syllabus,,,Synthetic Syllabus,,,,
""",
        encoding="utf-8",
    )

    return course_dir


def test_ingest_course_pipeline_and_idempotency(tmp_path):
    course_dir = _create_synthetic_course(tmp_path)
    test_nb = f"nb_test_ingest_{uuid.uuid4().hex[:8]}"
    db = get_db()

    try:
        # Run 1: fresh ingestion
        success = ingest_course(course_dir, notebook_id=test_nb, force=False)
        assert success is True

        nb_snap = db.document(notebook_path(test_nb)).get()
        assert nb_snap.exists
        nb_data = nb_snap.to_dict()
        assert nb_data["status"] == "ready"
        assert nb_data["is_demo"] is True
        assert "items" not in nb_data["counts"]
        initial_chunks_count = nb_data["counts"]["chunks"]
        assert initial_chunks_count > 0

        # Verify sources and src_ naming format
        sources = list(db.collection(sources_collection_path(test_nb)).stream())
        assert len(sources) == 3  # slides, handout, syllabus
        for s in sources:
            assert s.id.startswith("src_")
            s_data = s.to_dict()
            assert s_data["status"] == "ready"
            assert "job_id" not in s_data

        # Verify sources_summary in notebook is ordered by ref_n
        summary = nb_data.get("sources_summary", [])
        assert len(summary) == 3
        assert [s["ref_n"] for s in summary] == [1, 2, 3]

        # Verify topics
        topics = list_topic_snapshots(test_nb)
        assert len(topics) >= 2
        t1_data = next(t.to_dict() for t in topics if t.id == "t1")
        assert t1_data["location_count"] > 0
        assert len(t1_data["locations"]) == t1_data["location_count"]

        # Run 2: Re-run without force (must skip already-ready and be idempotent)
        success_rerun = ingest_course(course_dir, notebook_id=test_nb, force=False)
        assert success_rerun is True

        nb_snap2 = db.document(notebook_path(test_nb)).get()
        assert nb_snap2.to_dict()["counts"]["chunks"] == initial_chunks_count

        all_chunks = list(db.collection(chunks_collection_path(test_nb)).stream())
        assert len(all_chunks) == initial_chunks_count

        # Run 3: Re-run with force (must not duplicate chunks)
        success_force = ingest_course(course_dir, notebook_id=test_nb, force=True)
        assert success_force is True

        nb_snap3 = db.document(notebook_path(test_nb)).get()
        assert nb_snap3.to_dict()["counts"]["chunks"] == initial_chunks_count

        all_chunks_force = list(db.collection(chunks_collection_path(test_nb)).stream())
        assert len(all_chunks_force) == initial_chunks_count

    finally:
        # Cleanup test notebook documents and storage
        try:
            db.recursive_delete(db.document(notebook_path(test_nb)))
        except Exception:
            pass
        try:
            delete_prefix(f"notebooks/{test_nb}/")
        except Exception:
            pass


def test_ingest_course_run_by_path_preconditions():
    """Run script by path in a subprocess without emulator env; assert code 2."""
    backend_dir = Path(__file__).resolve().parents[1]
    script_path = backend_dir / "scripts" / "ingest_course.py"
    env = dict(os.environ)
    env.pop("FIRESTORE_EMULATOR_HOST", None)
    env.setdefault("FIREBASE_PROJECT_ID", "demo-test")

    proc = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "ModuleNotFoundError" not in proc.stderr

