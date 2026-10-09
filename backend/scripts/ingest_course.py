"""Ingest course materials into Firestore and Storage.

Run from backend/:
    python scripts/ingest_course.py
    python -m scripts.ingest_course
"""

import argparse
import csv
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Ensure backend/ is in sys.path when running the script by path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import (  # noqa: E402
    chunks_collection_path,
    get_db,
    source_storage_original_pdf_path,
    sources_collection_path,
)
from app.db.notebooks import (  # noqa: E402
    DEMO_NOTEBOOK_ID,
    derive_notebook_status,
    rebuild_notebook_status_and_summary,
    upsert_demo_notebook,
)
from app.db.sources import (  # noqa: E402
    delete_chunks_beyond_seq,
    get_source_snapshot,
    set_source_ready,
    upsert_source_processing,
    write_chunks_batch,
)
from app.db.topics import write_topics_batch  # noqa: E402
from app.embeddings import embed_passages, embed_query  # noqa: E402
from app.ingestion.pdf import parse_pdf  # noqa: E402
from app.ingestion.syllabus import create_other_topic, parse_syllabus  # noqa: E402
from app.ingestion.topics import (  # noqa: E402
    group_topic_locations,
    parse_topics_column,
    resolve_chunk_topic,
)
from app.ingestion.video import ingest_video_source  # noqa: E402
from app.storage import upload_bytes  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("ingest_course")


def verify_emulator_preconditions() -> None:
    """Refuse to run against production; must be on emulator with demo project."""
    project_id = os.environ.get("FIREBASE_PROJECT_ID", "")
    if not project_id.startswith("demo-"):
        logger.error(
            "Safety violation: FIREBASE_PROJECT_ID=%r does not start with demo-",
            project_id,
        )
        sys.exit(2)

    for var in ["FIRESTORE_EMULATOR_HOST", "STORAGE_EMULATOR_HOST"]:
        if not os.environ.get(var):
            logger.error("Safety violation: Missing emulator host variable %s", var)
            sys.exit(2)


def derive_source_id(file_rel: str) -> str:
    """Generate deterministic source ID from relative file path with src_ prefix."""
    stem = Path(file_rel).stem.lower()
    clean_stem = re.sub(r"[^a-z0-9]", "_", stem)
    return f"src_{clean_stem}"


def ingest_syllabus_source(
    course_dir: Path,
    notebook_id: str,
    ref_n: int,
    file_rel: str,
    title: str,
    licence: str | None,
    attribution: str | None,
    force: bool = False,
    **_: Any,
) -> bool:
    """Upload and record syllabus as a markdown source document."""
    file_path = course_dir / file_rel
    if not file_path.exists():
        logger.error("Syllabus file not found: %s", file_path)
        return False

    source_id = derive_source_id(file_rel)
    src_snap = get_source_snapshot(notebook_id, source_id)
    if src_snap.exists and not force:
        src_data = src_snap.to_dict() or {}
        if src_data.get("status") == "ready" and src_data.get("ingest_version") == 1:
            print(f"  {title}: skip (ready; use --force to re-ingest)")
            return True

    syllabus_text = file_path.read_text(encoding="utf-8")
    storage_path = f"notebooks/{notebook_id}/sources/{source_id}/original.md"
    upload_bytes(storage_path, syllabus_text.encode("utf-8"))

    upsert_source_processing(
        nb=notebook_id,
        source_id=source_id,
        ref_n=ref_n,
        title=title,
        kind="markdown",
        role="syllabus",
        filename=Path(file_rel).name,
        storage_path=storage_path,
        viewer_path=None,
        page_count=None,
        page_labels=[],
        licence_pages=[],
        slide_grid=None,
        licence=licence,
        attribution=attribution,
    )
    set_source_ready(notebook_id, source_id)
    print(f"  {title}: ready (syllabus)")
    return True


def ingest_pdf_source(
    course_dir: Path,
    notebook_id: str,
    ref_n: int,
    file_rel: str,
    kind: str,
    title: str,
    licence: str | None,
    attribution: str | None,
    slide_grid: str | None,
    topics_spec: str,
    youtube_id: str | None,
    offset_s: float | None,
    topic_query_embs: dict[str, list[float]],
    force: bool,
    **_: Any,
) -> bool:
    """Ingest a PDF source with embeddings and chunk topic assignments."""
    t0 = time.time()
    file_path = course_dir / file_rel
    if not file_path.exists():
        logger.error("File not found: %s", file_path)
        return False

    source_id = derive_source_id(file_rel)
    src_snap = get_source_snapshot(notebook_id, source_id)
    if src_snap.exists and not force:
        src_data = src_snap.to_dict() or {}
        if src_data.get("status") == "ready" and src_data.get("ingest_version") == 1:
            print(f"  {title}: skip (ready; use --force to re-ingest)")
            return True

    try:
        pdf_bytes = file_path.read_bytes()
        storage_path = source_storage_original_pdf_path(notebook_id, source_id)
        upload_bytes(storage_path, pdf_bytes)

        parsed = parse_pdf(pdf_bytes, source_id=source_id, slide_grid=slide_grid)
        texts = [c["text"] for c in parsed.chunks]
        embeddings = embed_passages(texts)

        # Assign topic_id to each chunk
        parsed_topic_mapping = parse_topics_column(topics_spec)
        for c, emb in zip(parsed.chunks, embeddings, strict=True):
            page = c["loc"].get("page")
            c["topic_id"] = resolve_chunk_topic(
                page=page,
                chunk_emb=emb,
                mapping=parsed_topic_mapping,
                query_embs=topic_query_embs,
            )

        # Stage 1: write source doc as processing
        upsert_source_processing(
            nb=notebook_id,
            source_id=source_id,
            ref_n=ref_n,
            title=title,
            kind=kind,
            role="content",
            filename=Path(file_rel).name,
            storage_path=storage_path,
            viewer_path=storage_path,
            page_count=parsed.page_count,
            page_labels=parsed.page_labels,
            licence_pages=parsed.licence_pages,
            slide_grid=slide_grid,
            licence=licence,
            attribution=attribution,
            youtube_id=youtube_id,
            offset_s=offset_s,
        )

        # Clean up old chunks beyond new chunk count if force re-ingesting
        if force and src_snap.exists:
            delete_chunks_beyond_seq(notebook_id, source_id, len(parsed.chunks))

        # Write chunks with 384-d vectors and topic_id
        write_chunks_batch(notebook_id, source_id, parsed.chunks, embeddings)

        # Stage 2: mark source ready
        set_source_ready(notebook_id, source_id)

        elapsed = time.time() - t0
        pages_n = parsed.page_count
        chunks_n = len(parsed.chunks)
        print(f"  {title}: {pages_n} pages, {chunks_n} chunks ({elapsed:.1f}s)")
        return True
    except Exception as exc:
        logger.exception("Failed to ingest source %s: %s", title, exc)
        return False


INGEST_DISPATCH: dict[str, Callable[..., bool]] = {
    "pdf": ingest_pdf_source,
    "slides_pdf": ingest_pdf_source,
    "markdown": ingest_syllabus_source,
    "syllabus": ingest_syllabus_source,
    "video": ingest_video_source,
}


def ingest_course(
    course_dir: Path,
    notebook_id: str = DEMO_NOTEBOOK_ID,
    force: bool = False,
) -> bool:
    """Build or update demo notebook from course directory manifest."""
    verify_emulator_preconditions()

    if not course_dir.exists():
        logger.error("Course directory does not exist: %s", course_dir)
        return False

    manifest_path = course_dir / "manifest.csv"
    if not manifest_path.exists():
        logger.error("Manifest file not found: %s", manifest_path)
        return False

    syllabus_path = course_dir / "syllabus.md"
    if not syllabus_path.exists():
        logger.error("Syllabus file not found: %s", syllabus_path)
        return False

    logger.info("Initializing notebook %s...", notebook_id)
    upsert_demo_notebook(
        notebook_id=notebook_id,
        name="MIT 6.041 Probability (demo)",
        owner_uid="demo-owner",
    )

    # 1. Parse syllabus topics
    syllabus_text = syllabus_path.read_text(encoding="utf-8")
    syllabus_topics = parse_syllabus(syllabus_text)
    other_topic = create_other_topic(order=len(syllabus_topics) + 1)
    all_topics_meta = {t.id: t for t in syllabus_topics}
    all_topics_meta[other_topic.id] = other_topic

    # Precompute query embeddings for candidate topics
    topic_query_embs = {tid: embed_query(t.summary) for tid, t in all_topics_meta.items()}

    # 2. Read manifest rows
    with open(manifest_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    any_failed = False

    for row_idx, row in enumerate(rows, start=1):
        ref_n = row_idx
        file_rel = row.get("file", "").strip()
        ftype = row.get("type", "").strip()
        title = row.get("title", "").strip()
        licence = row.get("licence", "").strip() or None
        attribution = row.get("attribution", "").strip() or None
        slide_grid = row.get("slide_grid", "").strip() or None
        topics_spec = row.get("topics", "").strip()
        youtube_id = row.get("youtube_id", "").strip() or None
        offset_s_val = row.get("offset_s", "").strip()
        offset_s = float(offset_s_val) if offset_s_val else None

        if ftype == "video" or file_rel.endswith(".mp4"):
            kind = "video"
        elif file_rel.startswith("slides/") or slide_grid:
            kind = "slides_pdf"
        elif file_rel.endswith(".md"):
            kind = "markdown"
        else:
            kind = ftype

        handler = INGEST_DISPATCH.get(kind)
        if handler is None:
            print(f"  {title}: skipped (unsupported kind {kind})")
            continue

        shared_kwargs: dict[str, Any] = {
            "course_dir": course_dir,
            "notebook_id": notebook_id,
            "ref_n": ref_n,
            "file_rel": file_rel,
            "kind": kind,
            "title": title,
            "licence": licence,
            "attribution": attribution,
            "slide_grid": slide_grid,
            "topics_spec": topics_spec,
            "youtube_id": youtube_id,
            "offset_s": offset_s,
            "topic_query_embs": topic_query_embs,
            "force": force,
        }
        ok = handler(**shared_kwargs)
        if not ok:
            any_failed = True

    # 3. Rebuild topic documents from all notebook chunks without embeddings
    logger.info("Building topic documents from chunks...")
    db = get_db()
    chunks_coll = db.collection(chunks_collection_path(notebook_id))
    all_chunks = list(chunks_coll.select(["source_id", "loc", "topic_id"]).stream())

    # Map source_id to ref_n and build sources_summary sorted by ref_n
    sources_coll = db.collection(sources_collection_path(notebook_id))
    sources_snaps = list(sources_coll.stream())
    src_ref_map: dict[str, int] = {}
    sources_summary_list = []

    for s in sources_snaps:
        s_data = s.to_dict() or {}
        ref_n_val = s_data.get("ref_n", 999)
        src_ref_map[s.id] = ref_n_val
        sources_summary_list.append(
            {
                "source_id": s.id,
                "ref_n": ref_n_val,
                "title": s_data.get("title", s.id),
                "kind": s_data.get("kind", "pdf"),
                "status": s_data.get("status", "ready"),
            }
        )

    sources_summary_list.sort(key=lambda s: s["ref_n"])

    chunks_by_topic: dict[str, list[tuple[dict[str, Any], int]]] = {
        tid: [] for tid in all_topics_meta
    }

    for cdoc in all_chunks:
        cdata = cdoc.to_dict() or {}
        cdata["id"] = cdoc.id
        tid = cdata.get("topic_id") or "other"
        if tid not in chunks_by_topic:
            chunks_by_topic[tid] = []
        source_id = cdata.get("source_id", "")
        ref_n_val = src_ref_map.get(source_id, 999)
        chunks_by_topic[tid].append((cdata, ref_n_val))

    topics_to_write = []
    for tid, meta in all_topics_meta.items():
        chunk_list = chunks_by_topic.get(tid, [])
        locations = group_topic_locations(chunk_list)
        topics_to_write.append(
            {
                "id": tid,
                "name": meta.name,
                "order": meta.order,
                "summary": meta.summary,
                "prerequisite_ids": meta.prerequisite_ids,
                "is_other": meta.is_other,
                "locations": locations,
                "location_count": len(locations),
            }
        )

    # set() all 7 topic documents
    write_topics_batch(notebook_id, topics_to_write)

    # 4. Atomically set notebook summary, total chunks, and status
    total_chunks = len(all_chunks)
    nb_status = derive_notebook_status(sources_summary_list)
    rebuild_notebook_status_and_summary(
        nb=notebook_id,
        sources_summary=sources_summary_list,
        total_chunks=total_chunks,
        status=nb_status,
    )

    print(
        f"\nIngestion finished for {notebook_id}: {total_chunks} chunks indexed "
        f"across {len(topics_to_write)} topics. Status: {nb_status}."
    )
    return not any_failed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest demo course material into Firestore and Storage."
    )
    parser.add_argument("--notebook-id", default=DEMO_NOTEBOOK_ID, help="Target notebook ID")
    parser.add_argument(
        "--course-dir",
        default=os.environ.get(
            "COURSE_DATA_DIR",
            r"C:\Users\Saksham Chaudhry\course-data\6041",
        ),
        help="Path to course directory",
    )
    parser.add_argument("--force", action="store_true", help="Re-ingest ready sources")
    args = parser.parse_args()

    course_path = Path(args.course_dir)
    success = ingest_course(course_path, notebook_id=args.notebook_id, force=args.force)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
