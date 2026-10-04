from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.notebook import SourceKind, SourceStatus

SourceRole = Literal["content", "syllabus"]


class Source(BaseModel):
    """Source document matching data-model.md plus id."""

    model_config = ConfigDict(extra="ignore")

    id: str
    ref_n: int
    title: str
    kind: SourceKind
    role: SourceRole
    filename: str
    storage_path: str
    viewer_path: str
    youtube_id: str | None = None
    offset_s: float | None = None
    duration_s: float | None = None
    page_count: int | None = None
    page_labels: list[str | None] = Field(default_factory=list)
    slide_grid: str | None = None
    licence_pages: list[int] = Field(default_factory=list)
    licence: str | None = None
    attribution: str | None = None
    status: SourceStatus
    stage: str | None = None
    error: str | None = None
    ingest_version: int = 1
    created_at: datetime


class SourceOut(BaseModel):
    """External view of a source matching api-contract.md."""

    model_config = ConfigDict(extra="ignore")

    id: str
    ref_n: int
    title: str
    kind: SourceKind
    role: SourceRole
    status: SourceStatus
    stage: str | None = None
    error: str | None = None
    page_count: int | None = None
    duration_s: float | None = None
    job_id: str | None = None


class SourceList(BaseModel):
    """Paginated list of sources."""

    model_config = ConfigDict(extra="ignore")

    items: list[SourceOut]
    next_cursor: str | None = None
