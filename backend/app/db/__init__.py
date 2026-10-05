"""Database access and path helpers package."""

from app.db.client import get_db, init_firebase
from app.db.paths import (
    chunk_path,
    chunks_collection_path,
    llm_cache_collection_path,
    llm_cache_path,
    notebook_path,
    notebooks_collection_path,
    source_path,
    source_storage_original_pdf_path,
    sources_collection_path,
    user_path,
)

__all__ = [
    "chunk_path",
    "chunks_collection_path",
    "get_db",
    "init_firebase",
    "llm_cache_collection_path",
    "llm_cache_path",
    "notebook_path",
    "notebooks_collection_path",
    "source_path",
    "source_storage_original_pdf_path",
    "sources_collection_path",
    "user_path",
]
