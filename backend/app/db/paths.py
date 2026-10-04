def user_path(uid: str) -> str:
    """Return Firestore document path for a user."""
    return f"users/{uid}"


def notebooks_collection_path() -> str:
    """Return Firestore collection path for notebooks."""
    return "notebooks"


def notebook_path(nb: str) -> str:
    """Return Firestore document path for a notebook."""
    return f"notebooks/{nb}"


def sources_collection_path(nb: str) -> str:
    """Return Firestore collection path for a notebook's sources."""
    return f"notebooks/{nb}/sources"


def source_path(nb: str, src: str) -> str:
    """Return Firestore document path for a source."""
    return f"notebooks/{nb}/sources/{src}"


def chunks_collection_path(nb: str) -> str:
    """Return Firestore collection path for a notebook's chunks."""
    return f"notebooks/{nb}/chunks"


def chunk_path(nb: str, chunk_id: str) -> str:
    """Return Firestore document path for a chunk."""
    return f"notebooks/{nb}/chunks/{chunk_id}"


def source_storage_original_pdf_path(nb: str, src: str) -> str:
    """Return Cloud Storage object path for a source's original PDF."""
    return f"notebooks/{nb}/sources/{src}/original.pdf"
