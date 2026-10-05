from pydantic import BaseModel, Field

from app.llm.generate import generate_json
from app.llm.prompts.ask import PROMPT_VERSION, build_contents, build_system_instruction
from app.models.citation import Citation, OpenPdfTarget, Paragraph
from app.models.notebook import Notebook
from app.retrieval.search import RetrievedChunk


class RawParagraph(BaseModel):
    text: str
    sources: list[int] = Field(default_factory=list)


class AskGeminiOutput(BaseModel):
    paragraphs: list[RawParagraph] = Field(default_factory=list)


def answer_question(
    notebook: Notebook,
    question: str,
    chunks: list[RetrievedChunk],
    allow_outside: bool = False,
) -> list[Paragraph]:
    sources_by_id = {s.source_id: s for s in notebook.sources_summary}
    chunks_data = []
    for idx, c in enumerate(chunks, start=1):
        s = sources_by_id.get(c.source_id)
        s_title = s.title if s else c.source_id
        page = c.loc.page
        chunks_data.append((idx, s_title, page, c.text))

    system_instruction = build_system_instruction(allow_outside)
    contents = build_contents(question, chunks_data)

    cache_key_parts = [
        str(allow_outside).lower(),
        ",".join(c.chunk_id for c in chunks),
        question.strip(),
    ]

    llm_output = generate_json(
        prompt_version=PROMPT_VERSION,
        system_instruction=system_instruction,
        contents=contents,
        schema=AskGeminiOutput,
        cache_key_parts=cache_key_parts,
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
            if chunk.loc.page is None:
                continue
            s = sources_by_id.get(chunk.source_id)
            s_title = s.title if s else chunk.source_id
            page = chunk.loc.page
            citations.append(Citation(
                chunk_id=chunk.chunk_id,
                loc=chunk.loc,
                label=f"{s_title} p. {page}",
                open=OpenPdfTarget(
                    kind="pdf",
                    source_id=chunk.source_id,
                    page=page,
                    bbox=chunk.loc.bbox,
                ),
            ))

        outside_course = len(citations) == 0
        paragraphs.append(Paragraph(
            id=f"p{p_idx}",
            section=None,
            text=raw_p.text,
            citations=citations,
            outside_course=outside_course,
            images=[],
        ))

    return paragraphs
