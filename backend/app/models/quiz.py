from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.citation import Citation
from app.models.question import QuestionOut, QuestionType

QuizMode = Literal["chosen", "adaptive", "diagnostic"]
QuizStatus = Literal["in_progress", "finished"]
VerdictType = Literal["correct", "incorrect", "partial"]


class QuizCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: QuizMode
    topic_ids: list[str] | None = None
    count: int = Field(default=5, ge=1, le=10)


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: VerdictType
    correct_answer: str
    correct_option_id: str | None = None
    explanation: str
    citations: list[Citation] = Field(default_factory=list)
    misconception: str | None = None
    rubric_coverage: list[dict[str, Any]] | None = None


class QuizAnswerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    answer: str
    feedback: Feedback


class TopicSummaryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str
    total: int
    answered: int
    correct: int


class QuizSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quiz_id: str
    status: Literal["finished"]
    total: int
    answered: int
    correct: int
    score: float
    by_topic: list[TopicSummaryItem]
    report: None = None


class QuizOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    mode: QuizMode
    topic_ids: list[str]
    status: QuizStatus
    questions: list[QuestionOut]
    answers: list[QuizAnswerRecord]
    summary: QuizSummary | None = None
    created_at: datetime


class QuizAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    answer: str
    time_ms: int = Field(ge=0, le=86400000)


class QuizAnswerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    answer: str
    already_answered: bool
    feedback: Feedback


class QuestionBankItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str
    type: QuestionType
    verified: int
    rejected: int


class QuestionBankResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[QuestionBankItem]
    total_verified: int
    next_cursor: None = None
