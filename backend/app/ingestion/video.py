import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from app.db.sources import (
    delete_chunks_beyond_seq,
    fail_source_cleanup,
    get_source_snapshot,
    set_source_ready,
    upsert_source_processing,
    write_video_chunks_batch,
)
from app.embeddings import count_tokens, embed_passages

logger = logging.getLogger("ingest_video")

PREAMBLE_KEYWORDS: list[str] = [
    "creative commons",
    "opencourseware",
    "ocw.mit.edu",
    "your support",
    "donation",
    "additional materials",
    "make a donation",
]


def filter_preamble_segments(
    segments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], float, int]:
    """Drop leading segments containing preamble keywords, stopping at the first without them.

    Never drops a segment starting at 90.0s or later.
    """
    dropped_count = 0
    for seg in segments:
        start = float(seg.get("start", 0.0))
        if start >= 90.0:
            break
        text = str(seg.get("text", "")).lower()
        if any(kw in text for kw in PREAMBLE_KEYWORDS):
            dropped_count += 1
        else:
            break

    dropped_segs = segments[:dropped_count]
    dropped_s = float(dropped_segs[-1]["end"]) if dropped_segs else 0.0
    kept_segments = segments[dropped_count:]
    return kept_segments, dropped_s, dropped_count


def chunk_transcript_segments(
    segments: list[dict[str, Any]],
    source_id: str,
    topic_id: str,
) -> list[dict[str, Any]]:
    """Group consecutive segments into chunks of at most 60s and 400 tokens.

    Never splits a segment. An oversized single segment is kept as a chunk of its own.
    """
    chunks: list[dict[str, Any]] = []
    curr_segs: list[dict[str, Any]] = []
    seq = 0

    def flush(segs_to_flush: list[dict[str, Any]]) -> None:
        nonlocal seq
        if not segs_to_flush:
            return
        joined_text = " ".join(str(s.get("text", "")).strip() for s in segs_to_flush)
        first_start = float(segs_to_flush[0]["start"])
        last_end = float(segs_to_flush[-1]["end"])
        tok_count = count_tokens(joined_text)
        chunk_dict = {
            "id": f"{source_id}-{seq:05d}",
            "source_id": source_id,
            "kind": "transcript",
            "text": joined_text,
            "loc": {
                "source_id": source_id,
                "t_start_s": first_start,
                "t_end_s": last_end,
            },
            "topic_id": topic_id,
            "token_count": tok_count,
            "segments": [
                {
                    "start": float(s["start"]),
                    "end": float(s["end"]),
                    "text": str(s.get("text", "")).strip(),
                }
                for s in segs_to_flush
            ],
        }
        chunks.append(chunk_dict)
        seq += 1

    for seg in segments:
        if not curr_segs:
            # First segment in this chunk
            curr_segs.append(seg)
        else:
            cand = curr_segs + [seg]
            cand_dur = float(cand[-1]["end"]) - float(cand[0]["start"])
            cand_text = " ".join(str(s.get("text", "")).strip() for s in cand)
            cand_tokens = count_tokens(cand_text)
            if cand_dur <= 60.0 and cand_tokens <= 400:
                curr_segs.append(seg)
            else:
                flush(curr_segs)
                curr_segs = [seg]

    flush(curr_segs)
    return chunks


TRANSCRIPT_ERR_MSG = (
    "transcript missing or invalid: run backend/scripts/transcribe_lectures.py"
)


def parse_and_chunk_transcript(
    transcript_path: Path,
    source_id: str,
    topic_id: str,
) -> tuple[float, list[dict[str, Any]]]:
    """Parse transcript JSON, validate schema, filter preamble, and chunk segments."""
    if not transcript_path.exists():
        raise ValueError(TRANSCRIPT_ERR_MSG)

    try:
        content = transcript_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as exc:
        raise ValueError(TRANSCRIPT_ERR_MSG) from exc

    if not isinstance(data, dict):
        raise ValueError(TRANSCRIPT_ERR_MSG)

    if data.get("schema_version") != 1:
        raise ValueError(TRANSCRIPT_ERR_MSG)

    if data.get("clip_seconds") is not None:
        raise ValueError(TRANSCRIPT_ERR_MSG)

    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError(TRANSCRIPT_ERR_MSG)

    duration_s = float(data.get("duration_s", raw_segments[-1].get("end", 0.0)))
    kept_segments, dropped_s, dropped_count = filter_preamble_segments(raw_segments)
    logger.info(
        "Preamble for %s: dropped %d segments (%.1fs), kept %d segments",
        source_id,
        dropped_count,
        dropped_s,
        len(kept_segments),
    )

    chunks = chunk_transcript_segments(kept_segments, source_id, topic_id)
    return duration_s, chunks


def ingest_video_source(
    course_dir: Path,
    notebook_id: str,
    ref_n: int,
    file_rel: str,
    kind: str,
    title: str,
    licence: str | None,
    attribution: str | None,
    topics_spec: str,
    youtube_id: str | None,
    offset_s: float | None,
    force: bool = False,
    **_: Any,
) -> bool:
    """Ingest a lecture video source from its transcript JSON."""
    stem = Path(file_rel).stem.lower()
    clean_stem = re.sub(r"[^a-z0-9]", "_", stem)
    source_id = f"src_{clean_stem}"

    src_snap = get_source_snapshot(notebook_id, source_id)
    if src_snap.exists and not force:
        src_data = src_snap.to_dict() or {}
        if src_data.get("status") == "ready" and src_data.get("ingest_version") == 1:
            print(f"  {title}: skip (ready; use --force to re-ingest)")
            return True

    transcript_path = course_dir / "derived" / "transcripts" / f"{Path(file_rel).stem}.json"
    topic_id = topics_spec.strip() if topics_spec else "other"

    try:
        duration_s, chunks = parse_and_chunk_transcript(transcript_path, source_id, topic_id)
    except Exception as exc:
        logger.warning("Transcript validation failed for %s: %s", title, exc)
        upsert_source_processing(
            nb=notebook_id,
            source_id=source_id,
            ref_n=ref_n,
            title=title,
            kind="video",
            role="content",
            filename=Path(file_rel).name,
            storage_path="",
            viewer_path=None,
            page_count=None,
            page_labels=[],
            licence_pages=[],
            slide_grid=None,
            licence=licence,
            attribution=attribution,
            youtube_id=youtube_id,
            offset_s=offset_s,
            duration_s=None,
        )
        fail_source_cleanup(
            nb=notebook_id,
            source_id=source_id,
            stage="transcript",
            error_message=TRANSCRIPT_ERR_MSG,
        )
        print(f"  {title}: failed (transcript missing or invalid)")
        return False

    t0 = time.time()
    try:
        texts = [c["text"] for c in chunks]
        embeddings = embed_passages(texts)

        upsert_source_processing(
            nb=notebook_id,
            source_id=source_id,
            ref_n=ref_n,
            title=title,
            kind="video",
            role="content",
            filename=Path(file_rel).name,
            storage_path="",
            viewer_path=None,
            page_count=None,
            page_labels=[],
            licence_pages=[],
            slide_grid=None,
            licence=licence,
            attribution=attribution,
            youtube_id=youtube_id,
            offset_s=offset_s,
            duration_s=duration_s,
        )

        if force and src_snap.exists:
            delete_chunks_beyond_seq(notebook_id, source_id, len(chunks))

        write_video_chunks_batch(notebook_id, source_id, chunks, embeddings)
        set_source_ready(notebook_id, source_id)

        elapsed = time.time() - t0
        print(f"  {title}: {duration_s:.1f}s, {len(chunks)} chunks ({elapsed:.1f}s)")
        return True
    except Exception as exc:
        logger.exception("Failed to ingest video source %s: %s", title, exc)
        fail_source_cleanup(
            nb=notebook_id,
            source_id=source_id,
            stage="ingest",
            error_message=str(exc),
        )
        return False
