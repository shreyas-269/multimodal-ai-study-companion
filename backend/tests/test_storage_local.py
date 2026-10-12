"""Tests for storage backends: local filesystem, S3 stub, GCS offline, and factory."""

from unittest.mock import MagicMock

import google.cloud.storage as gcs_module
import pytest

from app.config import get_settings
from app.storage import (
    GCSStorageBackend,
    LocalStorageBackend,
    S3StorageBackend,
    StorageBackend,
    _local_path,
    delete_prefix,
    get_object_size,
    get_storage_backend,
    get_storage_client,
    stream_byte_range,
    stream_object,
    upload_bytes,
)

DEMO = "notebooks/nb_demo_6041/sources"


@pytest.fixture
def course_dir(tmp_path, monkeypatch):
    """A fake course folder with the real layout, and settings pointed at it."""
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_local_dir", str(tmp_path))
    get_storage_backend.cache_clear()
    yield tmp_path
    get_storage_backend.cache_clear()


# T01: Demo source ID translation on read
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


# T02: Non-demo path resolution under root
def test_non_demo_path_resolves_under_root(course_dir):
    path = "notebooks/nb_user_1/sources/src_abc/original.pdf"
    assert _local_path(path) == (course_dir / path).resolve()


# T03: Unknown demo source path resolution under root
def test_unknown_demo_source_is_not_mapped(course_dir):
    path = f"{DEMO}/src_unknown/original.pdf"
    assert _local_path(path) == (course_dir / path).resolve()


# T04: Relative directory traversal attacks
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


# T05: Absolute path traversal attacks
@pytest.mark.parametrize("abs_path", ["/secret.pdf", "C:/secret.pdf"])
def test_absolute_path_traversal_refused(course_dir, abs_path):
    with pytest.raises((ValueError, FileNotFoundError)):
        _local_path(abs_path)


# T06: Symlink escape refusal (wrapped for Windows privilege safety)
def test_symlink_escape_is_refused(course_dir, tmp_path_factory):
    outside_dir = tmp_path_factory.mktemp("outside")
    outside_file = outside_dir / "secret.pdf"
    outside_file.write_bytes(b"secret")
    symlink_path = course_dir / "link_to_outside.pdf"
    try:
        symlink_path.symlink_to(outside_file)
    except OSError:
        pytest.skip("Symlink creation requires administrative privileges on Windows")
    with pytest.raises((ValueError, FileNotFoundError)):
        _local_path("link_to_outside.pdf")


# T07: Root directory resolution refused
@pytest.mark.parametrize("root_path", ["", ".", "./"])
def test_root_path_resolution_refused(course_dir, root_path):
    with pytest.raises(FileNotFoundError):
        _local_path(root_path)


# T08: upload_bytes creates nested parent directories
def test_upload_bytes_creates_directories(course_dir):
    path = "new_sub/folder/file.pdf"
    upload_bytes(path, b"test-pdf")
    target = course_dir / path
    assert target.is_file()
    assert target.read_bytes() == b"test-pdf"


# T09: upload_bytes overwrites existing files cleanly
def test_upload_bytes_overwrites_existing(course_dir):
    path = "existing.pdf"
    upload_bytes(path, b"first")
    upload_bytes(path, b"second")
    assert (course_dir / path).read_bytes() == b"second"


# T10: upload_bytes does not translate demo IDs on write
def test_upload_bytes_does_not_translate_demo_id(course_dir):
    demo_path = f"{DEMO}/src_textbook/original.pdf"
    upload_bytes(demo_path, b"demo-bytes")
    assert (course_dir / demo_path).is_file()
    assert (course_dir / demo_path).read_bytes() == b"demo-bytes"
    assert not (course_dir / "textbook" / "textbook.pdf").exists()


# T11: upload_bytes accepts custom content_type without error
def test_upload_bytes_custom_content_type(course_dir):
    upload_bytes("notes.txt", b"notes", content_type="text/plain")
    assert (course_dir / "notes.txt").read_bytes() == b"notes"


# T12: get_object_size returns exact byte size
def test_get_object_size_success(course_dir):
    (course_dir / "test.pdf").write_bytes(b"12345")
    assert get_object_size("test.pdf") == 5


# T13: get_object_size returns 0 for empty file
def test_get_object_size_empty_file(course_dir):
    (course_dir / "empty.pdf").write_bytes(b"")
    assert get_object_size("empty.pdf") == 0


# T14: get_object_size raises FileNotFoundError for missing file
def test_get_object_size_missing_file_raises(course_dir):
    with pytest.raises(FileNotFoundError):
        get_object_size(f"{DEMO}/src_textbook/original.pdf")


# T15: get_object_size raises FileNotFoundError for directory path
def test_get_object_size_directory_raises(course_dir):
    (course_dir / "somedir").mkdir()
    with pytest.raises(FileNotFoundError):
        get_object_size("somedir")


# T16: stream_object chunks file correctly
def test_stream_object_chunking(course_dir):
    (course_dir / "chunked.pdf").write_bytes(b"0123456789")
    chunks = list(stream_object("chunked.pdf", chunk_size=3))
    assert chunks == [b"012", b"345", b"678", b"9"]
    assert b"".join(chunks) == b"0123456789"


# T17: stream_object on empty file yields 0 chunks
def test_stream_object_empty_file(course_dir):
    (course_dir / "empty.pdf").write_bytes(b"")
    assert list(stream_object("empty.pdf")) == []


# T18: stream_object on missing file raises FileNotFoundError
def test_stream_object_missing_file_raises(course_dir):
    with pytest.raises(FileNotFoundError):
        list(stream_object("missing.pdf"))


# T19: stream_byte_range returns inclusive slice
def test_stream_byte_range_inclusive_slice(course_dir):
    (course_dir / "slides").mkdir()
    (course_dir / "slides" / "L01-slides.pdf").write_bytes(b"0123456789")
    path = f"{DEMO}/src_l01_slides/original.pdf"
    assert b"".join(stream_byte_range(path, 2, 5)) == b"2345"


# T20: stream_byte_range respects custom chunk size
def test_stream_byte_range_chunk_boundaries(course_dir):
    (course_dir / "file.pdf").write_bytes(b"0123456789")
    chunks = list(stream_byte_range("file.pdf", 1, 8, chunk_size=3))
    assert chunks == [b"123", b"456", b"78"]
    assert b"".join(chunks) == b"12345678"


# T21: stream_byte_range clamps cleanly past EOF
def test_stream_byte_range_past_eof_clamping(course_dir):
    (course_dir / "file.pdf").write_bytes(b"0123456789")
    assert b"".join(stream_byte_range("file.pdf", 5, 100)) == b"56789"


# T22: stream_byte_range on missing file raises FileNotFoundError
def test_stream_byte_range_missing_raises(course_dir):
    with pytest.raises(FileNotFoundError):
        list(stream_byte_range(f"{DEMO}/src_r03/original.pdf", 0, 10))


# T23: delete_prefix unlinks matching files and preserves others
def test_delete_prefix_matching_files(course_dir):
    (course_dir / "nb_1").mkdir()
    (course_dir / "nb_1" / "a.pdf").write_bytes(b"a")
    (course_dir / "nb_1" / "b.pdf").write_bytes(b"b")
    (course_dir / "nb_2").mkdir()
    (course_dir / "nb_2" / "c.pdf").write_bytes(b"c")

    delete_prefix("nb_1/")
    assert not (course_dir / "nb_1" / "a.pdf").exists()
    assert not (course_dir / "nb_1" / "b.pdf").exists()
    assert (course_dir / "nb_2" / "c.pdf").exists()


# T24: delete_prefix with non-matching prefix leaves all files intact
def test_delete_prefix_non_matching_noop(course_dir):
    (course_dir / "file.pdf").write_bytes(b"data")
    delete_prefix("nonexistent/")
    assert (course_dir / "file.pdf").exists()


# T25: delete_prefix with empty string raises ValueError
def test_delete_prefix_empty_raises_value_error(course_dir):
    with pytest.raises(ValueError, match="Cannot delete with empty prefix"):
        delete_prefix("")


# T26: S3StorageBackend stub methods raise NotImplementedError
def test_s3_backend_stub_methods_raise():
    s3 = S3StorageBackend()
    with pytest.raises(NotImplementedError, match="S3 storage backend is not yet configured"):
        s3.upload_bytes("path", b"data")
    with pytest.raises(NotImplementedError, match="S3 storage backend is not yet configured"):
        s3.get_object_size("path")
    with pytest.raises(NotImplementedError, match="S3 storage backend is not yet configured"):
        list(s3.stream_byte_range("path", 0, 10))
    with pytest.raises(NotImplementedError, match="S3 storage backend is not yet configured"):
        list(s3.stream_object("path"))
    with pytest.raises(NotImplementedError, match="S3 storage backend is not yet configured"):
        s3.delete_prefix("prefix")


# T27: get_storage_backend returns correct provider instances
def test_factory_backend_selection():
    assert isinstance(get_storage_backend("local"), LocalStorageBackend)
    assert isinstance(get_storage_backend("gcs"), GCSStorageBackend)
    assert isinstance(get_storage_backend("s3"), S3StorageBackend)


# T28: get_storage_backend raises ValueError for invalid backend type
def test_factory_invalid_backend_raises():
    with pytest.raises(ValueError, match="Unsupported storage backend: invalid"):
        get_storage_backend("invalid")


# T29: get_storage_backend dynamically resolves configured setting
def test_factory_dynamic_settings_resolution(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    assert isinstance(get_storage_backend(), LocalStorageBackend)
    monkeypatch.setattr(settings, "storage_backend", "gcs")
    assert isinstance(get_storage_backend(), GCSStorageBackend)


# T30: All concrete providers satisfy StorageBackend protocol
def test_storage_backend_protocol_conformance():
    assert isinstance(LocalStorageBackend(), StorageBackend)
    assert isinstance(GCSStorageBackend(), StorageBackend)
    assert isinstance(S3StorageBackend(), StorageBackend)


# T31: GCS safety check raises RuntimeError for demo- project without emulator host
def test_gcs_safety_check_raises_without_emulator(monkeypatch):
    get_storage_client.cache_clear()
    try:
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-safety-test")
        monkeypatch.delenv("STORAGE_EMULATOR_HOST", raising=False)
        settings = get_settings()
        monkeypatch.setattr(settings, "storage_emulator_host", None)
        with pytest.raises(RuntimeError, match="Safety check failed"):
            get_storage_client()
    finally:
        get_storage_client.cache_clear()


# T32: GCS environment variables take precedence over settings fields
def test_gcs_env_precedence_over_settings(monkeypatch):
    get_storage_client.cache_clear()
    try:
        settings = get_settings()
        monkeypatch.setattr(settings, "firebase_project_id", "settings-proj")
        monkeypatch.setattr(settings, "storage_emulator_host", "http://settings-host:9199")
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "env-proj")
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", "http://env-host:9199")

        mock_client_cls = MagicMock()
        monkeypatch.setattr(gcs_module, "Client", mock_client_cls)

        get_storage_client()
        mock_client_cls.assert_called_once()
        kwargs = mock_client_cls.call_args[1]
        assert kwargs.get("project") == "env-proj"
        assert kwargs.get("client_options") == {"api_endpoint": "http://env-host:9199"}
    finally:
        get_storage_client.cache_clear()


# T33: GCS delete_prefix raises ValueError on empty string
def test_gcs_delete_prefix_empty_raises():
    gcs = GCSStorageBackend()
    with pytest.raises(ValueError, match="Cannot delete with empty prefix"):
        gcs.delete_prefix("")


# T34: GCS stream_object yields zero chunks on size-0 blob
def test_gcs_stream_object_zero_length_blob(monkeypatch):
    gcs = GCSStorageBackend()
    mock_blob = MagicMock()
    mock_blob.size = 0
    mock_bucket = MagicMock()
    mock_bucket.get_blob.return_value = mock_blob
    monkeypatch.setattr(gcs, "_get_bucket", lambda: mock_bucket)

    chunks = list(gcs.stream_object("any/path.pdf"))
    assert chunks == []
