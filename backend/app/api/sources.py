from pathlib import Path
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from fastapi.responses import StreamingResponse

from app.api.access import get_owned_notebook, get_readable_notebook, is_valid_notebook_id
from app.config import MAX_UPLOAD_SIZE_BYTES
from app.db.paths import source_storage_original_pdf_path
from app.db.sources import (
    create_source_transaction,
    fail_source_cleanup,
    finish_source_transaction,
    generate_source_id,
    get_source_snapshot,
    list_sources_snapshots,
    write_chunks_batch,
)
from app.embeddings import embed_passages
from app.ingestion.pdf import parse_pdf
from app.models.notebook import Notebook
from app.models.source import SourceList, SourceOut
from app.storage import get_object_size, stream_byte_range, stream_object, upload_bytes

router = APIRouter(tags=["sources"])


@router.post("/notebooks/{nb}/sources", status_code=202, name="create")
def create(
    nb: str,
    notebook: Annotated[Notebook, Depends(get_owned_notebook)],
    file: Annotated[UploadFile, File()],
    role: Annotated[Literal["content", "syllabus"], Form()] = "content",
) -> SourceOut:
    """Upload and ingest a PDF source document for a notebook."""
    # Read upload counting bytes; enforce 30 MB limit
    content = bytearray()
    while chunk := file.file.read(65536):
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid", "message": "File is larger than 30 MB."},
            )

    pdf_bytes = bytes(content)

    # Magic bytes check in first 1024 bytes
    if b"%PDF-" not in pdf_bytes[:1024]:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid", "message": "Only PDF files are supported for now."},
        )

    # Extract title from basename stripping / and \
    raw_filename = file.filename or "file.pdf"
    basename = raw_filename.replace("\\", "/").split("/")[-1]
    stem = Path(basename).stem.strip()[:200]
    title = stem if stem else "Untitled"
    filename = basename

    source_id = generate_source_id(nb)
    storage_path = source_storage_original_pdf_path(nb, source_id)

    create_source_transaction(
        nb=nb,
        source_id=source_id,
        title=title,
        filename=filename,
        role=role,
        storage_path=storage_path,
    )

    current_stage = "upload"
    try:
        upload_bytes(storage_path, pdf_bytes)

        current_stage = "extract"
        parsed = parse_pdf(pdf_bytes, source_id=source_id, slide_grid=None)

        current_stage = "embed"
        texts = [c["text"] for c in parsed.chunks]
        embeddings = embed_passages(texts)

        current_stage = "save"
        write_chunks_batch(nb, source_id, parsed.chunks, embeddings)

        source_doc = finish_source_transaction(
            nb=nb,
            source_id=source_id,
            page_count=parsed.page_count,
            chunk_count=len(parsed.chunks),
            page_labels=parsed.page_labels,
            licence_pages=parsed.licence_pages,
            slide_grid=None,
            viewer_path=storage_path,
        )
        return SourceOut.from_stored(source_doc)

    except Exception as exc:
        if isinstance(exc, ValueError):
            error_msg = str(exc)
        else:
            error_msg = f"Processing failed at {current_stage}."
        failed_doc = fail_source_cleanup(
            nb=nb,
            source_id=source_id,
            stage=current_stage,
            error_message=error_msg,
        )
        return SourceOut.from_stored(failed_doc)


# Named list_sources so the builtin list isn't shadowed; name="list" keeps the operation ID sources_list (DECISIONS 2026-10-04).  # noqa: E501
@router.get("/notebooks/{nb}/sources", name="list")
def list_sources(
    nb: str,
    notebook: Annotated[Notebook, Depends(get_readable_notebook)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> SourceList:
    """List sources in a notebook ordered by ref_n ascending with cursor pagination."""
    cursor_snapshot = None
    if cursor is not None:
        if not is_valid_notebook_id(cursor):
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid", "message": "Invalid cursor format"},
            )
        cursor_snapshot = get_source_snapshot(nb, cursor)
        if not cursor_snapshot.exists:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid", "message": "Cursor source not found in this notebook"},
            )

    raw = list_sources_snapshots(nb=nb, limit=limit, cursor_snapshot=cursor_snapshot)
    has_more = len(raw) > limit
    items = [SourceOut.from_stored(doc) for doc in raw[:limit]]
    next_cursor = raw[limit - 1].id if has_more else None

    return SourceList(items=items, next_cursor=next_cursor)


@router.get("/notebooks/{nb}/sources/{src}", name="get")
def get(
    nb: str,
    src: str,
    notebook: Annotated[Notebook, Depends(get_readable_notebook)],
) -> SourceOut:
    """Get source document details by ID."""
    if not is_valid_notebook_id(src):
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Source not found"},
        )

    doc = get_source_snapshot(nb, src)
    if not doc.exists:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Source not found"},
        )

    return SourceOut.from_stored(doc)


@router.get("/notebooks/{nb}/sources/{src}/file", name="get_file")
def get_file(
    nb: str,
    src: str,
    notebook: Annotated[Notebook, Depends(get_readable_notebook)],
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> Response:
    """Stream PDF source file with HTTP Range support for viewer."""
    if not is_valid_notebook_id(src):
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Source not found"},
        )

    doc = get_source_snapshot(nb, src)
    if not doc.exists:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Source not found"},
        )

    source_data = doc.to_dict() or {}
    viewer_path = source_data.get("viewer_path")
    if not viewer_path:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "File not found"},
        )

    try:
        total_size = get_object_size(viewer_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "File not found"},
        ) from None

    headers_200 = {
        "Content-Length": str(total_size),
        "Content-Type": "application/pdf",
        "Content-Disposition": "inline",
        "Accept-Ranges": "bytes",
    }

    # If no Range or non-bytes, serve full file
    if not range_header or not range_header.startswith("bytes="):
        return StreamingResponse(
            stream_object(viewer_path),
            status_code=200,
            headers=headers_200,
        )

    spec = range_header[len("bytes=") :].strip()

    # Multiple ranges: serve whole file with 200
    if "," in spec:
        return StreamingResponse(
            stream_object(viewer_path),
            status_code=200,
            headers=headers_200,
        )

    if "-" not in spec:
        return StreamingResponse(
            stream_object(viewer_path),
            status_code=200,
            headers=headers_200,
        )

    parts = spec.split("-", 1)
    start_str, end_str = parts[0].strip(), parts[1].strip()

    if start_str and end_str:
        try:
            a = int(start_str)
            b = int(end_str)
        except ValueError:
            return StreamingResponse(
                stream_object(viewer_path),
                status_code=200,
                headers=headers_200,
            )
        if a < 0 or b < 0 or a > b:
            return StreamingResponse(
                stream_object(viewer_path),
                status_code=200,
                headers=headers_200,
            )
        if a >= total_size:
            return Response(
                status_code=416,
                headers={
                    "Content-Range": f"bytes */{total_size}",
                    "Accept-Ranges": "bytes",
                },
            )
        b = min(b, total_size - 1)

    elif start_str and not end_str:
        try:
            a = int(start_str)
        except ValueError:
            return StreamingResponse(
                stream_object(viewer_path),
                status_code=200,
                headers=headers_200,
            )
        if a < 0:
            return StreamingResponse(
                stream_object(viewer_path),
                status_code=200,
                headers=headers_200,
            )
        if a >= total_size:
            return Response(
                status_code=416,
                headers={
                    "Content-Range": f"bytes */{total_size}",
                    "Accept-Ranges": "bytes",
                },
            )
        b = total_size - 1

    elif not start_str and end_str:
        try:
            n = int(end_str)
        except ValueError:
            return StreamingResponse(
                stream_object(viewer_path),
                status_code=200,
                headers=headers_200,
            )
        if n == 0:
            return Response(
                status_code=416,
                headers={
                    "Content-Range": f"bytes */{total_size}",
                    "Accept-Ranges": "bytes",
                },
            )
        if n < 0:
            return StreamingResponse(
                stream_object(viewer_path),
                status_code=200,
                headers=headers_200,
            )
        if n > total_size:
            a = 0
        else:
            a = total_size - n
        b = total_size - 1

    else:
        return StreamingResponse(
            stream_object(viewer_path),
            status_code=200,
            headers=headers_200,
        )

    length = b - a + 1
    headers_206 = {
        "Content-Range": f"bytes {a}-{b}/{total_size}",
        "Content-Length": str(length),
        "Content-Type": "application/pdf",
        "Content-Disposition": "inline",
        "Accept-Ranges": "bytes",
    }
    return StreamingResponse(
        stream_byte_range(viewer_path, a, b),
        status_code=206,
        headers=headers_206,
    )
