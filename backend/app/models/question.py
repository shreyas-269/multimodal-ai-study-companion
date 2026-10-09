import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.citation import Citation
from app.questions.numerical import parse_exact

QuestionType = Literal["mcq", "numerical", "short"]
QuestionStatus = Literal["verified", "rejected"]


class QuestionOption(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    text: str
    misconception: str | None = None


class QuestionOptionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str


class QuestionAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    option_id: str | None = None
    value: str | None = None
    model_answer: str | None = None


class RubricItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    point: str
    chunk_id: str


class QuestionVerification(BaseModel):
    model_config = ConfigDict(extra="ignore")

    method: str
    passed: bool
    detail: str


class StoredQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: QuestionType
    topic_id: str
    difficulty: int = Field(ge=1, le=3)
    stem: str
    options: list[QuestionOption] = Field(default_factory=list)
    answer: QuestionAnswer
    rubric: list[RubricItem] = Field(default_factory=list)
    explanation: str
    citations: list[Citation] = Field(default_factory=list)
    verification: QuestionVerification
    status: QuestionStatus
    batch_id: str
    solution_code: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def validate_type_and_answer(self) -> "StoredQuestion":
        if self.type == "mcq":
            if not (2 <= len(self.options) <= 6):
                raise ValueError("MCQ must have between 2 and 6 options")
            opt_ids = [opt.id for opt in self.options]
            if len(opt_ids) != len(set(opt_ids)):
                raise ValueError("MCQ option IDs must be unique")
            for oid in opt_ids:
                if not re.fullmatch(r"[a-z]", oid):
                    raise ValueError(f"Option ID '{oid}' must match [a-z]")
            if not self.answer.option_id:
                raise ValueError("MCQ question must specify answer.option_id")
            if self.answer.option_id not in opt_ids:
                raise ValueError(
                    f"answer.option_id '{self.answer.option_id}' not found in options"
                )
        elif self.type == "numerical":
            if self.options:
                raise ValueError("Numerical question must have empty options")
            if not self.answer.value:
                raise ValueError("Numerical question must specify answer.value")
            if parse_exact(self.answer.value) is None:
                raise ValueError(
                    f"Numerical answer.value '{self.answer.value}' is not an exact integer/fraction"
                )
        elif self.type == "short":
            if self.options:
                raise ValueError("Short answer question must have empty options")
            if not self.answer.model_answer:
                raise ValueError("Short answer question must specify answer.model_answer")
        return self


class QuestionOut(BaseModel):
    """The only question shape sent before answering."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: QuestionType
    topic_id: str
    difficulty: int = Field(ge=1, le=3)
    stem: str
    options: list[QuestionOptionOut] | None = None
