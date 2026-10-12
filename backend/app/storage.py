"""Storage backend abstraction and client utilities.

Supports Google Cloud Storage (gcs), local disk filesystem (local), and a stub
for future S3/R2 endpoints (s3), exposing a unified StorageBackend protocol.
"""

import os
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.config import get_settings

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


@runtime_checkable
class StorageBackend(Protocol):
    """Authoritative storage abstraction interface for Groundwork."""

    def upload_bytes(
        self, path: str, data: bytes, content_type: str = "application/pdf"
    ) -> None:
        """Upload raw bytes to the specified object path."""
        ...

    def get_object_size(self, path: str) -> int:
        """Return size of object in bytes.

        Raises:
            FileNotFoundError: If the object does not exist.
        """
        ...

    def stream_byte_range(
        self,
        path: str,
        start: int,
        end: int,
        chunk_size: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        """Stream inclusive byte range [start, end] in pieces up to chunk_size.

        Raises:
            FileNotFoundError: If the object does not exist, raised upon first
                iteration (next()).
        """
        ...

    def stream_object(
        self, path: str, chunk_size: int = 1024 * 1024
    ) -> Iterator[bytes]:
        """Stream entire object in pieces up to chunk_size without buffering into memory.

        Raises:
            FileNotFoundError: If the object does not exist, raised upon first
                iteration (next()).
        """
        ...

    def delete_prefix(self, prefix: str) -> None:
        """Delete all objects matching the given path prefix.

        Raises:
            ValueError: If prefix is empty (to prevent accidental root/bucket wipe).
        """
        ...


@lru_cache
def get_storage_client() -> Any:
    """Initialize Google Cloud Storage client with emulator safety checks."""
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import storage

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


def get_storage_bucket() -> Any:
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


class GCSStorageBackend:
    """StorageBackend implementation backed by Google Cloud Storage."""

    def _get_bucket(self) -> Any:
        return get_storage_bucket()

    def upload_bytes(
        self, path: str, data: bytes, content_type: str = "application/pdf"
    ) -> None:
        bucket = self._get_bucket()
        blob = bucket.blob(path)
        blob.upload_from_string(data, content_type=content_type)

    def get_object_size(self, path: str) -> int:
        bucket = self._get_bucket()
        blob = bucket.get_blob(path)
        if blob is None:
            raise FileNotFoundError(f"Storage object not found: {path}")
        return blob.size

    def stream_byte_range(
        self,
        path: str,
        start: int,
        end: int,
        chunk_size: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        bucket = self._get_bucket()
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

    def stream_object(
        self, path: str, chunk_size: int = 1024 * 1024
    ) -> Iterator[bytes]:
        bucket = self._get_bucket()
        blob = bucket.get_blob(path)
        if blob is None:
            raise FileNotFoundError(f"Storage object not found: {path}")

        if blob.size == 0:
            return

        yield from self.stream_byte_range(path, 0, blob.size - 1, chunk_size=chunk_size)

    def delete_prefix(self, prefix: str) -> None:
        if not prefix:
            raise ValueError("Cannot delete with empty prefix")
        bucket = self._get_bucket()
        blobs = list(bucket.list_blobs(prefix=prefix))
        if blobs:
            bucket.delete_blobs(blobs)


class LocalStorageBackend:
    """StorageBackend implementation backed by the local filesystem."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self._root_dir = Path(root_dir) if root_dir is not None else None

    def _resolve_root(self) -> Path:
        if self._root_dir is not None:
            return self._root_dir.resolve()
        return Path(get_settings().storage_local_dir).resolve()

    def _local_path(self, path: str, for_read: bool = False) -> Path:
        """Map a storage path to a file under STORAGE_LOCAL_DIR, refusing escapes.

        Firestore stores demo viewer paths as notebooks/nb_demo_6041/sources/<id>/original.pdf,
        but the course folder uses its own names, so those paths are translated on read only.
        """
        if for_read and path.startswith(_DEMO_SOURCES_PREFIX):
            parts = path[len(_DEMO_SOURCES_PREFIX) :].split("/")
            if len(parts) == 2 and parts[1] == "original.pdf" and parts[0] in _DEMO_SOURCE_FILES:
                path = _DEMO_SOURCE_FILES[parts[0]]
        root = self._resolve_root()
        try:
            target = (root / path).resolve()
        except (ValueError, OSError) as exc:
            raise FileNotFoundError(f"Storage path resolution failed: {path}") from exc
        if target == root or not target.is_relative_to(root):
            raise FileNotFoundError(f"Storage path escapes local storage root: {path}")
        return target

    def upload_bytes(
        self, path: str, data: bytes, content_type: str = "application/pdf"
    ) -> None:
        target = self._local_path(path, for_read=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get_object_size(self, path: str) -> int:
        target = self._local_path(path, for_read=True)
        if not target.is_file():
            raise FileNotFoundError(f"Storage object not found: {path}")
        return target.stat().st_size

    def stream_byte_range(
        self,
        path: str,
        start: int,
        end: int,
        chunk_size: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        target = self._local_path(path, for_read=True)
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

    def stream_object(
        self, path: str, chunk_size: int = 1024 * 1024
    ) -> Iterator[bytes]:
        target = self._local_path(path, for_read=True)
        if not target.is_file():
            raise FileNotFoundError(f"Storage object not found: {path}")
        with target.open("rb") as f:
            while chunk := f.read(chunk_size):
                yield chunk

    def delete_prefix(self, prefix: str) -> None:
        if not prefix:
            raise ValueError("Cannot delete with empty prefix")
        root = self._resolve_root()
        # Same string-prefix meaning as Cloud Storage, not a directory delete
        for file in root.rglob("*"):
            if file.is_file() and file.relative_to(root).as_posix().startswith(prefix):
                file.unlink()


class S3StorageBackend:
    """Stub S3 storage backend for future AWS/S3-compatible integrations."""

    def upload_bytes(
        self, path: str, data: bytes, content_type: str = "application/pdf"
    ) -> None:
        raise NotImplementedError("S3 storage backend is not yet configured")

    def get_object_size(self, path: str) -> int:
        raise NotImplementedError("S3 storage backend is not yet configured")

    def stream_byte_range(
        self,
        path: str,
        start: int,
        end: int,
        chunk_size: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        if False:
            yield b""
        raise NotImplementedError("S3 storage backend is not yet configured")

    def stream_object(
        self, path: str, chunk_size: int = 1024 * 1024
    ) -> Iterator[bytes]:
        if False:
            yield b""
        raise NotImplementedError("S3 storage backend is not yet configured")

    def delete_prefix(self, prefix: str) -> None:
        raise NotImplementedError("S3 storage backend is not yet configured")


@lru_cache
def _get_backend_instance(backend_type: str) -> StorageBackend:
    if backend_type == "gcs":
        return GCSStorageBackend()
    elif backend_type == "local":
        return LocalStorageBackend()
    elif backend_type == "s3":
        return S3StorageBackend()
    else:
        raise ValueError(f"Unsupported storage backend: {backend_type}")


def get_storage_backend(backend_type: str | None = None) -> StorageBackend:
    """Return a StorageBackend instance for the given or configured backend type."""
    selected = backend_type if backend_type is not None else get_settings().storage_backend
    return _get_backend_instance(selected)


get_storage_backend.cache_clear = _get_backend_instance.cache_clear  # type: ignore[attr-defined]


def _local_path(path: str) -> Path:
    """Backwards-compatible helper delegating to LocalStorageBackend._local_path."""
    backend = get_storage_backend("local")
    if isinstance(backend, LocalStorageBackend):
        return backend._local_path(path, for_read=True)
    return LocalStorageBackend()._local_path(path, for_read=True)


def upload_bytes(path: str, data: bytes, content_type: str = "application/pdf") -> None:
    get_storage_backend().upload_bytes(path=path, data=data, content_type=content_type)


def get_object_size(path: str) -> int:
    return get_storage_backend().get_object_size(path=path)


def stream_byte_range(
    path: str,
    start: int,
    end: int,
    chunk_size: int = 1024 * 1024,
) -> Iterator[bytes]:
    yield from get_storage_backend().stream_byte_range(
        path=path, start=start, end=end, chunk_size=chunk_size
    )


def stream_object(path: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    yield from get_storage_backend().stream_object(path=path, chunk_size=chunk_size)


def delete_prefix(prefix: str) -> None:
    get_storage_backend().delete_prefix(prefix=prefix)
