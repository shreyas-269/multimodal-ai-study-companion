"""Study Coach core module exports."""

from .core import (
    get_progress,
    get_topics_needing_work,
    record_chat_signal,
    record_checkbox,
    record_quiz_answer,
)
from .storage import CoachStorage, InMemoryCoachStorage
from .types import (
    CoachEvent,
    CoachInputError,
    EventKind,
    MasteryRecord,
    NeedsWork,
    NeedsWorkItem,
    NeedsWorkReason,
    Outcome,
    Progress,
    ProgressLabel,
    QuestionType,
    RecordResult,
    TopicInfo,
    TopicProgress,
)

__all__ = [
    "record_quiz_answer",
    "record_chat_signal",
    "record_checkbox",
    "get_topics_needing_work",
    "get_progress",
    "CoachStorage",
    "InMemoryCoachStorage",
    "CoachInputError",
    "TopicInfo",
    "MasteryRecord",
    "CoachEvent",
    "RecordResult",
    "TopicProgress",
    "Progress",
    "NeedsWorkItem",
    "NeedsWork",
    "QuestionType",
    "EventKind",
    "Outcome",
    "ProgressLabel",
    "NeedsWorkReason",
]
