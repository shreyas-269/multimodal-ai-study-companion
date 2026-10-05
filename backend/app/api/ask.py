import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.access import get_readable_notebook
from app.chat.answer import answer_question
from app.config import get_settings
from app.models.ask import AskRequest, AskResponse, ContextChunk
from app.models.notebook import Notebook
from app.retrieval.search import search

router = APIRouter(tags=["ask"])


@router.post("/notebooks/{nb}/ask", name="post")
def post(
    nb: str,
    request: AskRequest,
    notebook: Annotated[Notebook, Depends(get_readable_notebook)],
) -> AskResponse:
    """Stateless source-grounded question answering over a notebook."""
    start_time = time.perf_counter()

    if request.pasted_images:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid", "message": "Pasted images aren't supported yet."},
        )

    if not any(s.status == "ready" for s in notebook.sources_summary):
        raise HTTPException(
            status_code=409,
            detail={"code": "not_ready", "message": "This notebook has no processed sources yet."},
        )

    chunks = search(notebook, request.question)
    paragraphs = answer_question(
        notebook=notebook,
        question=request.question,
        chunks=chunks,
        allow_outside=request.allow_outside,
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
    return AskResponse(
        paragraphs=paragraphs,
        context=context,
        model=get_settings().gemini_model,
        latency_ms=latency_ms,
    )
