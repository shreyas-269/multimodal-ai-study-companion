import time
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.chat.citations import build_citation
from app.db.sources import get_sources_metadata_by_ids
from app.embeddings import embed_passages
from app.ingestion.topics import cosine_similarity
from app.llm.generate import generate_json_with_model
from app.llm.prompts.ask import PROMPT_VERSION, build_contents, build_system_instruction
from app.models.ask import ContextChunk, Refs
from app.models.citation import Citation, Paragraph
from app.models.notebook import Notebook
from app.retrieval.search import RetrievedChunk, search


class RawParagraph(BaseModel):
    text: str
    sources: list[int] = Field(default_factory=list)


class AskGeminiOutput(BaseModel):
    paragraphs: list[RawParagraph] = Field(default_factory=list)


def refine_video_citation_time(
    p_text: str,
    segments: list[dict[str, Any]],
    default_t: float,
    margin: float = 0.03,
) -> float:
    """Refine video citation start time using 2-segment sliding windows against citing paragraph.

    Embeds the paragraph text and pairs of consecutive segments locally.
    Sets start time to first start of earliest window within margin of best similarity.
    Falls back to default_t on any error or empty segments.
    """
    if not segments:
        return default_t

    try:
        if len(segments) == 1:
            windows = [(float(segments[0]["start"]), str(segments[0].get("text", "")).strip())]
        else:
            windows = []
            for i in range(len(segments) - 1):
                t_start = float(segments[i]["start"])
                txt1 = str(segments[i].get("text", "")).strip()
                txt2 = str(segments[i + 1].get("text", "")).strip()
                windows.append((t_start, f"{txt1} {txt2}"))

        p_emb = embed_passages([p_text])[0]
        w_texts = [w[1] for w in windows]
        w_embs = embed_passages(w_texts)

        sims = [cosine_similarity(p_emb, we) for we in w_embs]
        best_sim = max(sims)
        thresh = best_sim - margin
        earliest_idx = next(i for i, s in enumerate(sims) if s >= thresh)
        return windows[earliest_idx][0]
    except Exception:
        return default_t


def answer_question(
    notebook: Notebook,
    question: str,
    chunks: list[RetrievedChunk],
    allow_outside: bool = False,
) -> tuple[list[Paragraph], str]:
    sources_by_id = {s.source_id: s for s in notebook.sources_summary}
    chunks_data = []
    for idx, c in enumerate(chunks, start=1):
        s = sources_by_id.get(c.source_id)
        s_title = s.title if s else c.source_id
        page = c.loc.page
        t_start_s = c.loc.t_start_s
        chunks_data.append((idx, s_title, page, c.text, t_start_s))

    system_instruction = build_system_instruction(allow_outside)
    contents = build_contents(question, chunks_data)

    cache_key_parts = [
        str(allow_outside).lower(),
        ",".join(c.chunk_id for c in chunks),
        question.strip(),
    ]

    llm_output, model_name = generate_json_with_model(
        prompt_version=PROMPT_VERSION,
        system_instruction=system_instruction,
        contents=contents,
        schema=AskGeminiOutput,
        cache_key_parts=cache_key_parts,
    )

    # Fetch video sources metadata once for video chunks
    video_source_ids = {c.source_id for c in chunks if c.loc.t_start_s is not None}
    video_meta = (
        get_sources_metadata_by_ids(notebook.id, list(video_source_ids))
        if video_source_ids
        else {}
    )

    paragraphs: list[Paragraph] = []
    for p_idx, raw_p in enumerate(llm_output.paragraphs, start=1):
        seen_numbers = set()
        citations: list[Citation] = []
        for n in raw_p.sources:
            if not isinstance(n, int) or n < 1 or n > len(chunks) or n in seen_numbers:
                continue
            seen_numbers.add(n)
            chunk = chunks[n - 1]

            # Drop only chunks with NEITHER a page NOR a t_start_s
            if chunk.loc.page is None and chunk.loc.t_start_s is None:
                continue

            s = sources_by_id.get(chunk.source_id)
            s_title = s.title if s else chunk.source_id

            if chunk.loc.t_start_s is not None:
                src_meta = video_meta.get(chunk.source_id, {})
                yt_id = src_meta.get("youtube_id")
                offset_s = src_meta.get("offset_s")
                if not yt_id:
                    continue
                refined_t = refine_video_citation_time(
                    p_text=raw_p.text,
                    segments=chunk.segments,
                    default_t=chunk.loc.t_start_s,
                    margin=0.03,
                )
                refined_loc = chunk.loc.model_copy(update={"t_start_s": refined_t})
                citation = build_citation(
                    chunk_id=chunk.chunk_id,
                    loc=refined_loc,
                    title=s_title,
                    youtube_id=yt_id,
                    offset_s=offset_s,
                )
            else:
                citation = build_citation(
                    chunk_id=chunk.chunk_id,
                    loc=chunk.loc,
                    title=s_title,
                )

            if citation is not None:
                citations.append(citation)

        outside_course = len(citations) == 0
        paragraphs.append(
            Paragraph(
                id=f"p{p_idx}",
                section=None,
                text=raw_p.text,
                citations=citations,
                outside_course=outside_course,
                images=[],
            )
        )

    return paragraphs, model_name


def run_answer_pipeline(
    notebook: Notebook,
    question: str,
    allow_outside: bool = False,
    refs: Refs | None = None,
) -> tuple[list[Paragraph], list[ContextChunk], str, int]:
    """Execute the complete question answering pipeline with retrieval and timing."""
    start_time = time.perf_counter()

    if not any(s.status == "ready" for s in notebook.sources_summary):
        raise HTTPException(
            status_code=409,
            detail={"code": "not_ready", "message": "This notebook has no processed sources yet."},
        )

    chunks = search(notebook, question)
    paragraphs, model_name = answer_question(
        notebook=notebook,
        question=question,
        chunks=chunks,
        allow_outside=allow_outside,
    )

    context = [
        ContextChunk(
            chunk_id=c.chunk_id,
            text=c.text,
            loc=c.loc,
            score=c.score,
        )
        for c in chunks
    ]

    latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
    return paragraphs, context, model_name, latency_ms
