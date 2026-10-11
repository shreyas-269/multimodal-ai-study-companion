from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TopicProgressOut(BaseModel):
    """Progress for one topic."""

    model_config = ConfigDict(extra="ignore")

    topic_id: str
    checked: bool
    p_known: float | None = None
    mastered: bool | None = None
    n_obs: int | None = None
    last_updated: datetime | None = None


class ProgressOut(BaseModel):
    """Overall progress for a notebook."""

    model_config = ConfigDict(extra="ignore")

    study_coach: bool
    items: list[TopicProgressOut]
    next_cursor: None = None


class NeedsWorkItemOut(BaseModel):
    """Topic needing work."""

    model_config = ConfigDict(extra="ignore")

    topic_id: str
    p_known: float | None = None
    chat_signals: int | None = None


class NeedsWorkOut(BaseModel):
    """Topics needing work in a notebook."""

    model_config = ConfigDict(extra="ignore")

    study_coach: bool
    items: list[NeedsWorkItemOut]
    next_cursor: None = None


class CheckedIn(BaseModel):
    """Request payload to set a topic checkbox state."""

    model_config = ConfigDict(extra="forbid", strict=True)

    checked: bool


class CheckedOut(BaseModel):
    """Response payload after setting a topic checkbox state."""

    model_config = ConfigDict(extra="ignore")

    topic_id: str
    checked: bool
