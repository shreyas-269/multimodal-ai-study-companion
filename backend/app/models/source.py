import math
from datetime import datetime
from typing import Any, Literal

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
    licence: str | None = None
    attribution: str | None = None
    youtube_url: str | None = None
    job_id: str | None = None

    @classmethod
    def from_stored(cls, source: Any) -> "SourceOut":
        """Build SourceOut from a stored source document snapshot, model, or dict."""
        if isinstance(source, BaseModel):
            data = source.model_dump()
        elif hasattr(source, "to_dict") and hasattr(source, "id"):
            data = {"id": source.id, **(source.to_dict() or {})}
        elif isinstance(source, dict):
            data = dict(source)
        elif hasattr(source, "to_dict"):
            data = {"id": getattr(source, "id", None), **(source.to_dict() or {})}
        else:
            raise TypeError(f"Unsupported source type: {type(source)}")

        kind = data.get("kind")
        youtube_id = data.get("youtube_id")
        offset_s = data.get("offset_s")

        youtube_url: str | None = None
        if kind == "video" and youtube_id:
            url = f"https://www.youtube.com/watch?v={youtube_id}"
            if offset_s is not None and math.floor(offset_s) > 0:
                url += f"&t={math.floor(offset_s)}s"
            youtube_url = url

        data.setdefault("id", "")
        data["youtube_url"] = youtube_url
        return cls.model_validate(data)


class SourceList(BaseModel):
    """Paginated list of sources."""

    model_config = ConfigDict(extra="ignore")

    items: list[SourceOut]
    next_cursor: str | None = None
