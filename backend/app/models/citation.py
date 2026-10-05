from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Location(BaseModel):
    """Source location matching data-model.md."""
    model_config = ConfigDict(extra="ignore")

    source_id: str
    page: int | None = None
    page_label: str | None = None
    slide: int | None = None
    bbox: list[float] | None = None
    t_start_s: float | None = None
    t_end_s: float | None = None
    section: str | None = None
    url: str | None = None
    anchor: str | None = None
    sheet: str | None = None
    cell_range: str | None = None

class OpenPdfTarget(BaseModel):
    """Target for opening a PDF viewer at a specific page and optional bounding box."""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["pdf"] = "pdf"
    source_id: str
    page: int
    bbox: list[float] | None = None

class OpenYouTubeTarget(BaseModel):
    """Target for opening a YouTube video at a specific URL with timestamp offset."""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["youtube"] = "youtube"
    url: str

OpenTarget = Annotated[OpenPdfTarget | OpenYouTubeTarget, Field(discriminator="kind")]

class Citation(BaseModel):
    """Source citation pointing to original material."""
    model_config = ConfigDict(extra="ignore")

    chunk_id: str
    loc: Location
    label: str
    open: OpenTarget

class Image(BaseModel):
    """Image reference within a paragraph."""
    model_config = ConfigDict(extra="ignore")

    kind: Literal["extracted", "diagram", "ai_generated", "search_link"]
    url: str
    caption: str
    citation: Citation | None = None

class Paragraph(BaseModel):
    """Generated answer paragraph with supporting citations."""
    model_config = ConfigDict(extra="ignore")

    id: str
    section: str | None = None
    text: str
    citations: list[Citation] = Field(default_factory=list)
    outside_course: bool = False
    images: list[Image] = Field(default_factory=list)
