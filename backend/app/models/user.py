from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserFormat(BaseModel):
    """User response formatting instructions."""

    model_config = ConfigDict(extra="forbid")

    custom_instructions: str | None = Field(default=None, max_length=2000)


class User(BaseModel):
    """User profile document matching data-model.md plus id."""

    model_config = ConfigDict(extra="ignore")

    id: str
    email: str | None = None
    display_name: str | None = None
    is_guest: bool = False
    study_coach: bool | None = None
    format: UserFormat = Field(default_factory=UserFormat)
    created_at: datetime


class UserPatch(BaseModel):
    """Payload for partial user update."""

    model_config = ConfigDict(extra="forbid")

    study_coach: bool | None = None
    format: UserFormat | None = None

    @field_validator("format", mode="before")
    @classmethod
    def format_not_null(cls, v: Any) -> Any:
        if v is None:
            raise ValueError("format cannot be null")
        return v
