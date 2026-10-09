from pydantic import BaseModel, ConfigDict, Field

from app.models.citation import Citation, Location


class TopicLocation(BaseModel):
    """Stored reference linking a chunk and location for a topic."""

    model_config = ConfigDict(extra="ignore")

    chunk_id: str
    loc: Location


class Topic(BaseModel):
    """Stored topic document matching data-model.md."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    order: int
    summary: str
    prerequisite_ids: list[str] = Field(default_factory=list)
    is_other: bool = False
    locations: list[TopicLocation] = Field(default_factory=list)
    location_count: int = 0


class TopicListItem(BaseModel):
    """Topic list item matching api-contract.md."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    order: int
    summary: str
    is_other: bool = False
    prerequisite_ids: list[str] = Field(default_factory=list)
    location_count: int = 0


class TopicList(BaseModel):
    """Topic list response shape matching api-contract.md."""

    model_config = ConfigDict(extra="ignore")

    items: list[TopicListItem]
    next_cursor: None = None


class TopicSourceList(BaseModel):
    """Topic sources response shape matching api-contract.md."""

    model_config = ConfigDict(extra="ignore")

    items: list[Citation]
    next_cursor: None = None
