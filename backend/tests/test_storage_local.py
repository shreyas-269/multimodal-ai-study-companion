"""Tests for STORAGE_BACKEND=local: demo path mapping, traversal guard, size and range reads."""

import pytest

from app.config import get_settings
from app.storage import _local_path, get_object_size, stream_byte_range

DEMO = "notebooks/nb_demo_6041/sources"


@pytest.fixture
def course_dir(tmp_path, monkeypatch):
    """A fake course folder with the real layout, and settings pointed at it."""
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_local_dir", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize(
    ("source_id", "expected"),
    [
        ("src_l01_slides", "slides/L01-slides.pdf"),
        ("src_l06_slides", "slides/L06-slides.pdf"),
        ("src_r01", "recitations/R01.pdf"),
        ("src_r06", "recitations/R06.pdf"),
        ("src_r01_sol", "recitations/R01-sol.pdf"),
        ("src_r06_sol", "recitations/R06-sol.pdf"),
        ("src_textbook", "textbook/textbook.pdf"),
    ],
)
def test_demo_source_maps_to_course_file(course_dir, source_id, expected):
    assert _local_path(f"{DEMO}/{source_id}/original.pdf") == (course_dir / expected).resolve()


def test_non_demo_path_resolves_under_root(course_dir):
    path = "notebooks/nb_user_1/sources/src_abc/original.pdf"
    assert _local_path(path) == (course_dir / path).resolve()


def test_unknown_demo_source_is_not_mapped(course_dir):
    path = f"{DEMO}/src_unknown/original.pdf"
    assert _local_path(path) == (course_dir / path).resolve()


@pytest.mark.parametrize(
    "bad_path",
    [
        "../secret.pdf",
        "slides/../../secret.pdf",
        f"{DEMO}/../../../../secret.pdf",
        f"{DEMO}/src_r01/../../../../../secret.pdf",
    ],
)
def test_traversal_is_refused(course_dir, bad_path):
    with pytest.raises((ValueError, FileNotFoundError)):
        _local_path(bad_path)


def test_get_object_size_for_demo_source(course_dir):
    (course_dir / "recitations").mkdir()
    (course_dir / "recitations" / "R01-sol.pdf").write_bytes(b"x" * 123)
    assert get_object_size(f"{DEMO}/src_r01_sol/original.pdf") == 123


def test_get_object_size_missing_file_raises(course_dir):
    with pytest.raises(FileNotFoundError):
        get_object_size(f"{DEMO}/src_textbook/original.pdf")


def test_stream_byte_range_returns_inclusive_slice(course_dir):
    (course_dir / "slides").mkdir()
    (course_dir / "slides" / "L01-slides.pdf").write_bytes(b"0123456789")
    path = f"{DEMO}/src_l01_slides/original.pdf"

    assert b"".join(stream_byte_range(path, 2, 5)) == b"2345"
    # Small chunk size must still stop exactly at `end`
    assert b"".join(stream_byte_range(path, 0, 9, chunk_size=3)) == b"0123456789"


def test_stream_byte_range_missing_file_raises(course_dir):
    with pytest.raises(FileNotFoundError):
        list(stream_byte_range(f"{DEMO}/src_r03/original.pdf", 0, 10))
