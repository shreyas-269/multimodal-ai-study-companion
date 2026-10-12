import os
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from google.auth.credentials import AnonymousCredentials
from google.cloud import storage

from app.config import get_settings


def _use_local() -> bool:
    return get_settings().storage_backend == "local"


_DEMO_SOURCES_PREFIX = "notebooks/nb_demo_6041/sources/"


def _demo_source_files() -> dict[str, str]:
    """Demo source ID -> file inside the course folder (Firestore IDs differ from disk names)."""
    files = {"src_textbook": "textbook/textbook.pdf"}
    for n in range(1, 7):
        files[f"src_l0{n}_slides"] = f"slides/L0{n}-slides.pdf"
        files[f"src_r0{n}"] = f"recitations/R0{n}.pdf"
        files[f"src_r0{n}_sol"] = f"recitations/R0{n}-sol.pdf"
    return files


_DEMO_SOURCE_FILES = _demo_source_files()


def _local_path(path: str) -> Path:
    """Map a storage path to a file under STORAGE_LOCAL_DIR, refusing anything outside it.

    Firestore stores demo viewer paths as notebooks/nb_demo_6041/sources/<id>/original.pdf, but
    the course folder uses its own names, so those paths are translated first.
    """
    if path.startswith(_DEMO_SOURCES_PREFIX):
        parts = path[len(_DEMO_SOURCES_PREFIX) :].split("/")
        if len(parts) == 2 and parts[1] == "original.pdf" and parts[0] in _DEMO_SOURCE_FILES:
            path = _DEMO_SOURCE_FILES[parts[0]]
    root = Path(get_settings().storage_local_dir).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise FileNotFoundError(f"Storage path escapes local storage root: {path}")
    return target


@lru_cache
def get_storage_client() -> storage.Client:
    """Initialize Google Cloud Storage client with emulator safety checks."""
    settings = get_settings()
    project_id = os.environ.get("FIREBASE_PROJECT_ID") or settings.firebase_project_id
    storage_emu = os.environ.get("STORAGE_EMULATOR_HOST") or settings.storage_emulator_host

    # Safety check: demo project IDs must be backed by emulators
    if project_id and project_id.startswith("demo-"):
        if not storage_emu:
            raise RuntimeError(
                f"Safety check failed: FIREBASE_PROJECT_ID '{project_id}' starts with 'demo-', "
                f"but STORAGE_EMULATOR_HOST={storage_emu} is missing. "
                "Refusing to run to prevent reaching production Cloud Storage."
            )

    if storage_emu:
        return storage.Client(
            project=project_id,
            credentials=AnonymousCredentials(),
            client_options={"api_endpoint": storage_emu},
        )

    if settings.google_application_credentials:
        return storage.Client.from_service_account_json(
            settings.google_application_credentials,
            project=project_id,
        )

    return storage.Client(project=project_id)


def get_storage_bucket() -> storage.Bucket:
    """Return configured Storage bucket, creating it in emulator mode if absent."""
    settings = get_settings()
    bucket_name = os.environ.get("FIREBASE_STORAGE_BUCKET") or settings.firebase_storage_bucket
    client = get_storage_client()
    bucket = client.bucket(bucket_name)

    storage_emu = os.environ.get("STORAGE_EMULATOR_HOST") or settings.storage_emulator_host
    if storage_emu:
        try:
            if not bucket.exists():
                bucket.create()
        except Exception:
            pass

    return bucket


def upload_bytes(path: str, data: bytes, content_type: str = "application/pdf") -> None:
    """Upload raw bytes to a Cloud Storage object path (or a local file in local mode)."""
    if _use_local():
        target = _local_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return

    bucket = get_storage_bucket()
    blob = bucket.blob(path)
    blob.upload_from_string(data, content_type=content_type)


def get_object_size(path: str) -> int:
    """Return size of object in bytes, raising FileNotFoundError if missing."""
    if _use_local():
        target = _local_path(path)
        if not target.is_file():
            raise FileNotFoundError(f"Storage object not found: {path}")
        return target.stat().st_size

    bucket = get_storage_bucket()
    blob = bucket.get_blob(path)
    if blob is None:
        raise FileNotFoundError(f"Storage object not found: {path}")
    return blob.size


def stream_byte_range(
    path: str,
    start: int,
    end: int,
    chunk_size: int = 1024 * 1024,
) -> Iterator[bytes]:
    """Stream byte range [start, end] inclusive in 1 MB pieces."""
    if _use_local():
        target = _local_path(path)
        if not target.is_file():
            raise FileNotFoundError(f"Storage object not found: {path}")
        with target.open("rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                yield chunk
                remaining -= len(chunk)
        return

    bucket = get_storage_bucket()
    blob = bucket.get_blob(path)
    if blob is None:
        raise FileNotFoundError(f"Storage object not found: {path}")

    curr = start
    while curr <= end:
        chunk_end = min(curr + chunk_size - 1, end)
        chunk = blob.download_as_bytes(start=curr, end=chunk_end)
        if not chunk:
            break
        yield chunk
        curr += len(chunk)


def stream_object(path: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    """Stream entire object in 1 MB pieces without buffering into memory."""
    if _use_local():
        target = _local_path(path)
        if not target.is_file():
            raise FileNotFoundError(f"Storage object not found: {path}")
        with target.open("rb") as f:
            while chunk := f.read(chunk_size):
                yield chunk
        return

    bucket = get_storage_bucket()
    blob = bucket.get_blob(path)
    if blob is None:
        raise FileNotFoundError(f"Storage object not found: {path}")

    if blob.size == 0:
        return

    yield from stream_byte_range(path, 0, blob.size - 1, chunk_size=chunk_size)


def delete_prefix(prefix: str) -> None:
    """Delete all objects matching the given prefix."""
    if _use_local():
        if not prefix:
            raise ValueError("Refusing to delete everything under the local storage root")
        root = Path(get_settings().storage_local_dir).resolve()
        # Same string-prefix meaning as Cloud Storage, not a directory delete
        for file in root.rglob("*"):
            if file.is_file() and file.relative_to(root).as_posix().startswith(prefix):
                file.unlink()
        return

    bucket = get_storage_bucket()
    blobs = list(bucket.list_blobs(prefix=prefix))
    if blobs:
        bucket.delete_blobs(blobs)
