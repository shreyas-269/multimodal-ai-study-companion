from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.access import get_readable_notebook
from app.auth import CurrentUser
from app.chat.answer import run_answer_pipeline
from app.db.users import get_user_custom_instructions
from app.models.ask import AskRequest, AskResponse
from app.models.notebook import Notebook

router = APIRouter(tags=["ask"])


@router.post("/notebooks/{nb}/ask", name="post")
def post(
    nb: str,
    request: AskRequest,
    current_user: CurrentUser,
    notebook: Annotated[Notebook, Depends(get_readable_notebook)],
) -> AskResponse:
    """Stateless source-grounded question answering over a notebook."""
    custom_instructions = get_user_custom_instructions(current_user.uid)
    paragraphs, context, model_name, latency_ms = run_answer_pipeline(
        notebook=notebook,
        question=request.question,
        allow_outside=request.allow_outside,
        refs=request.refs,
        topic_id=request.topic_id,
        custom_instructions=custom_instructions,
    )
    return AskResponse(
        paragraphs=paragraphs,
        context=context,
        model=model_name,
        latency_ms=latency_ms,
    )
