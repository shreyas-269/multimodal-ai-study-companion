from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.citation import Location, Paragraph


class Refs(BaseModel):
    """Explicit references filter for search (accepted and ignored until S5)."""
    model_config = ConfigDict(extra="forbid")
    sources: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)

class AskRequest(BaseModel):
    """Request payload for POST /v1/notebooks/{nb}/ask."""
    model_config = ConfigDict(extra="forbid")
    question: str
    topic_id: str | None = None  # accepted and ignored until S5
    refs: Refs | None = None     # accepted and ignored until S5
    allow_outside: bool = False
    pasted_images: list[str] = Field(default_factory=list)

    @field_validator("question", mode="before")
    @classmethod
    def validate_question(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("question must be a string")
        stripped = v.strip()
        if not (1 <= len(stripped) <= 2000):
            raise ValueError("question must be between 1 and 2000 characters")
        return stripped

class ContextChunk(BaseModel):
    """Retrieved chunk context returned alongside the answer."""
    model_config = ConfigDict(extra="ignore")
    chunk_id: str
    text: str
    loc: Location
    score: float

class AskResponse(BaseModel):
    """Response envelope for POST /v1/notebooks/{nb}/ask matching api-contract.md."""
    model_config = ConfigDict(extra="ignore")
    paragraphs: list[Paragraph]
    context: list[ContextChunk]
    model: str
    latency_ms: int
