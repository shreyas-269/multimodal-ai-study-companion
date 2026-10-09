from typing import Any

from pydantic import BaseModel

from app.models.question import StoredQuestion
from app.models.quiz import Feedback, VerdictType
from app.questions.numerical import grade_numerical


class QuizInputError(Exception):
    """Raised when student answer input fails validation."""
    pass


class GradeResult(BaseModel):
    correct: bool
    verdict: VerdictType
    recorded_answer: str


def select_questions(
    candidates_by_topic: dict[str, list[tuple[str, StoredQuestion]]],
    topic_ids: list[str],
    seen_ids: set[str],
    count: int,
) -> list[str]:
    """Select count questions across topic_ids using round-robin and type balancing.

    Phase 1 draws from unseen questions; phase 2 draws from seen questions if needed.
    """
    mcq_unseen: dict[str, list[tuple[str, StoredQuestion]]] = {}
    num_unseen: dict[str, list[tuple[str, StoredQuestion]]] = {}
    mcq_seen: dict[str, list[tuple[str, StoredQuestion]]] = {}
    num_seen: dict[str, list[tuple[str, StoredQuestion]]] = {}
    mcq_given: dict[str, int] = {}
    num_given: dict[str, int] = {}

    def sort_fn(item: tuple[str, StoredQuestion]) -> tuple[int, str]:
        return (item[1].difficulty, item[0])

    for tid in topic_ids:
        cands = candidates_by_topic.get(tid, [])
        m_u, n_u, m_s, n_s = [], [], [], []
        for qid, q in cands:
            if q.type == "mcq":
                if qid in seen_ids:
                    m_s.append((qid, q))
                else:
                    m_u.append((qid, q))
            elif q.type == "numerical":
                if qid in seen_ids:
                    n_s.append((qid, q))
                else:
                    n_u.append((qid, q))

        # Sort each queue ascending by (difficulty, question_id)
        m_u.sort(key=sort_fn)
        n_u.sort(key=sort_fn)
        m_s.sort(key=sort_fn)
        n_s.sort(key=sort_fn)

        mcq_unseen[tid] = m_u
        num_unseen[tid] = n_u
        mcq_seen[tid] = m_s
        num_seen[tid] = n_s
        mcq_given[tid] = 0
        num_given[tid] = 0

    selected: list[str] = []

    # Phase 1: Unseen questions
    while len(selected) < count:
        added_in_round = False
        for tid in topic_ids:
            if len(selected) == count:
                break
            if not mcq_unseen[tid] and not num_unseen[tid]:
                continue

            # Type preference rule
            if mcq_given[tid] <= num_given[tid]:
                if mcq_unseen[tid]:
                    qid, _ = mcq_unseen[tid].pop(0)
                    mcq_given[tid] += 1
                else:
                    qid, _ = num_unseen[tid].pop(0)
                    num_given[tid] += 1
            else:
                if num_unseen[tid]:
                    qid, _ = num_unseen[tid].pop(0)
                    num_given[tid] += 1
                else:
                    qid, _ = mcq_unseen[tid].pop(0)
                    mcq_given[tid] += 1

            selected.append(qid)
            added_in_round = True
        if not added_in_round:
            break

    # Phase 2: Seen questions
    while len(selected) < count:
        added_in_round = False
        for tid in topic_ids:
            if len(selected) == count:
                break
            if not mcq_seen[tid] and not num_seen[tid]:
                continue

            if mcq_given[tid] <= num_given[tid]:
                if mcq_seen[tid]:
                    qid, _ = mcq_seen[tid].pop(0)
                    mcq_given[tid] += 1
                else:
                    qid, _ = num_seen[tid].pop(0)
                    num_given[tid] += 1
            else:
                if num_seen[tid]:
                    qid, _ = num_seen[tid].pop(0)
                    num_given[tid] += 1
                else:
                    qid, _ = mcq_seen[tid].pop(0)
                    mcq_given[tid] += 1

            selected.append(qid)
            added_in_round = True
        if not added_in_round:
            break

    return selected


def grade(question: StoredQuestion, raw_answer: str) -> GradeResult:
    """Deterministically grade student answer."""
    clean = raw_answer.strip()
    if len(clean) == 0:
        raise QuizInputError("Enter an answer.")
    if len(clean) > 100:
        raise QuizInputError("Answer must be at most 100 characters.")

    if question.type == "mcq":
        valid_options = {opt.id for opt in question.options}
        if clean not in valid_options:
            raise QuizInputError("Choose one of the options.")
        correct = clean == question.answer.option_id
        verdict: VerdictType = "correct" if correct else "incorrect"
        return GradeResult(correct=correct, verdict=verdict, recorded_answer=clean)

    if question.type == "numerical":
        if not question.answer.value:
            raise ValueError("Numerical question missing answer.value")
        correct = grade_numerical(clean, question.answer.value)
        verdict = "correct" if correct else "incorrect"
        return GradeResult(correct=correct, verdict=verdict, recorded_answer=clean)

    raise QuizInputError(f"Unsupported question type for grading: {question.type}")


def build_feedback(question: StoredQuestion, attempt: dict[str, Any]) -> Feedback:
    """Build feedback object for an attempt."""
    correct = attempt.get("correct") is True
    verdict: VerdictType = "correct" if correct else "incorrect"

    correct_answer = ""
    correct_option_id: str | None = None
    if question.type == "mcq":
        correct_option_id = question.answer.option_id
        for opt in question.options:
            if opt.id == question.answer.option_id:
                correct_answer = opt.text
                break
    elif question.type == "numerical":
        correct_answer = question.answer.value or ""

    misconception: str | None = None
    if question.type == "mcq" and not correct:
        given_opt_id = attempt.get("answer")
        for opt in question.options:
            if opt.id == given_opt_id:
                misconception = opt.misconception
                break

    return Feedback(
        verdict=verdict,
        correct_answer=correct_answer,
        correct_option_id=correct_option_id,
        explanation=question.explanation,
        citations=question.citations,
        misconception=misconception,
        rubric_coverage=None,
    )
