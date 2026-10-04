from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SourceKind = Literal[
    "pdf", "slides_pdf", "pptx", "docx", "video", "markdown", "html", "xlsx", "web"
]
SourceStatus = Literal["queued", "processing", "ready", "failed"]
NotebookStatus = Literal["empty", "processing", "ready"]


class SourceSummary(BaseModel):
    """Summary of a source document in a notebook."""

    model_config = ConfigDict(extra="ignore")

    source_id: str
    ref_n: int
    title: str
    kind: SourceKind
    status: SourceStatus


class Counts(BaseModel):
    """Aggregate counts for notebook artifacts."""

    model_config = ConfigDict(extra="ignore")

    chunks: int = 0
    items: int = 0
    questions_verified: int = 0


class Notebook(BaseModel):
    """Notebook document matching data-model.md plus id."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    owner_uid: str
    is_demo: bool
    status: NotebookStatus
    sources_summary: list[SourceSummary] = Field(default_factory=list)
    counts: Counts = Field(default_factory=Counts)
    created_at: datetime


class NotebookCreate(BaseModel):
    """Payload for creating a new notebook."""

    model_config = ConfigDict(extra="forbid")

    name: str

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("name must be a string")
        stripped = v.strip()
        if not (1 <= len(stripped) <= 100):
            raise ValueError("name must be between 1 and 100 characters")
        return stripped


class NotebookList(BaseModel):
    """Paginated list of notebooks."""

    model_config = ConfigDict(extra="ignore")

    items: list[Notebook]
    next_cursor: str | None = None
