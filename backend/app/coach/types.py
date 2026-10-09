"""Data models and type definitions for Study Coach."""

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

QuestionType = Literal["mcq", "short", "numerical"]
EventKind = Literal["quiz_answer", "chat_signal", "checkbox"]
Outcome = Literal["applied", "duplicate", "coach_off", "ignored_other"]
ProgressLabel = Literal["not_started", "learning", "mastered"]
NeedsWorkReason = Literal["low_mastery", "not_started", "unchecked"]


class CoachInputError(ValueError):
    pass


class TopicInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: str
    name: str
    order: int
    prerequisite_ids: tuple[str, ...] = ()
    is_other: bool = False

    @field_validator("prerequisite_ids", mode="before")
    @classmethod
    def _coerce_prereqs(cls, v: Any) -> Any:
        if isinstance(v, (list, tuple)):
            return tuple(v)
        return v


class MasteryRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    p_known: float = Field(ge=0.0, le=1.0)
    n_obs: int = Field(ge=0)
    last_updated: AwareDatetime


class CoachEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    kind: EventKind
    topic_id: str
    value: float
    created_at: AwareDatetime


class RecordResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    outcome: Outcome
    topic_id: str
    p_known: float | None


class TopicProgress(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    topic_id: str
    name: str
    order: int
    checked: bool
    p_known: float | None
    n_obs: int | None
    last_updated: AwareDatetime | None
    label: ProgressLabel | None


class Progress(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    coach_on: bool
    topics: list[TopicProgress]


class NeedsWorkItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    topic_id: str
    name: str
    reason: NeedsWorkReason
    p_known: float | None
    chat_signals: int | None


class NeedsWork(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    coach_on: bool
    items: list[NeedsWorkItem]
