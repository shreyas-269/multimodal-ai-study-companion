import os
from collections.abc import Iterator
from functools import lru_cache

from google.auth.credentials import AnonymousCredentials
from google.cloud import storage

from app.config import get_settings


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
    """Upload raw bytes to a Cloud Storage object path."""
    bucket = get_storage_bucket()
    blob = bucket.blob(path)
    blob.upload_from_string(data, content_type=content_type)


def get_object_size(path: str) -> int:
    """Return size of object in bytes, raising FileNotFoundError if missing."""
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
    bucket = get_storage_bucket()
    blob = bucket.get_blob(path)
    if blob is None:
        raise FileNotFoundError(f"Storage object not found: {path}")

    if blob.size == 0:
        return

    yield from stream_byte_range(path, 0, blob.size - 1, chunk_size=chunk_size)


def delete_prefix(prefix: str) -> None:
    """Delete all objects matching the given prefix."""
    bucket = get_storage_bucket()
    blobs = list(bucket.list_blobs(prefix=prefix))
    if blobs:
        bucket.delete_blobs(blobs)
