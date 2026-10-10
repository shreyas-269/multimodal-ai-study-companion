import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.ask import ContextChunk, Refs
from app.models.citation import Paragraph


class ChatCreate(BaseModel):
    """Request body for POST /v1/notebooks/{nb}/chats."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    topic_id: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: object) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("name must be a string")
        stripped = v.strip()
        if not (1 <= len(stripped) <= 100):
            raise ValueError("name must be between 1 and 100 characters")
        return stripped

    @field_validator("topic_id", mode="before")
    @classmethod
    def validate_topic_id(cls, v: object) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("topic_id must be a string")
        if not re.fullmatch(r"^(t[1-6]|other)$", v):
            raise ValueError("topic_id must be t1-t6 or other")
        return v


class ChatOut(BaseModel):
    """Response model for a chat document."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    topic_id: str | None = None
    created_at: datetime
    updated_at: datetime
    message_count: int


class ChatList(BaseModel):
    """Paginated list of chats."""

    model_config = ConfigDict(extra="ignore")

    items: list[ChatOut]
    next_cursor: str | None = None


class ChatMessageCreate(BaseModel):
    """Request body for POST /v1/notebooks/{nb}/chats/{c}/messages."""

    model_config = ConfigDict(extra="forbid")

    text: str
    refs: Refs | None = None
    allow_outside: bool = False

    @field_validator("text", mode="before")
    @classmethod
    def validate_text(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("text must be a string")
        stripped = v.strip()
        if not (1 <= len(stripped) <= 2000):
            raise ValueError("text must be between 1 and 2000 characters")
        return stripped


class MessageOut(BaseModel):
    """Public message model matching data-model.md."""

    model_config = ConfigDict(extra="ignore")

    id: str
    role: Literal["user", "assistant"]
    created_at: datetime
    text: str | None = None
    paragraphs: list[Paragraph] | None = None
    refs: Refs = Field(default_factory=Refs)
    context: list[ContextChunk] | None = None


class MessageList(BaseModel):
    """Paginated list of messages."""

    model_config = ConfigDict(extra="ignore")

    items: list[MessageOut]
    next_cursor: str | None = None


class ChatSendOut(BaseModel):
    """Response envelope for POST /v1/notebooks/{nb}/chats/{c}/messages."""

    model_config = ConfigDict(extra="ignore")

    user_message: MessageOut
    assistant_message: MessageOut
    model: str
    latency_ms: int
