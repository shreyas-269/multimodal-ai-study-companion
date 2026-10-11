import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from google.cloud import firestore

from app.api.access import get_readable_notebook
from app.auth import CurrentUser
from app.coach import record_quiz_answer
from app.db.client import get_db
from app.db.coach import coach_storage, get_study_coach_enabled
from app.db.members import default_member_data
from app.db.paths import (
    attempt_path,
    member_path,
    quiz_path,
    quizzes_collection_path,
)
from app.db.questions import bank_counts, get_question, get_questions, list_verified_questions
from app.db.quizzes import get_attempts, get_quiz_snapshot
from app.models.question import QuestionOptionOut, QuestionOut
from app.models.quiz import (
    QuestionBankResponse,
    QuizAnswerRecord,
    QuizAnswerRequest,
    QuizAnswerResponse,
    QuizCreate,
    QuizOut,
    QuizSummary,
    TopicSummaryItem,
)
from app.questions.quiz import QuizInputError, build_feedback, grade, select_questions

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/notebooks/{nb}/question-bank", tags=["question_bank"], name="get")
def get_question_bank(
    nb: str,
    current_user: CurrentUser,
) -> QuestionBankResponse:
    get_readable_notebook(nb, current_user)
    items = bank_counts(nb)
    total_verified = sum(item.verified for item in items)
    return QuestionBankResponse(
        items=items,
        total_verified=total_verified,
        next_cursor=None,
    )


@router.post("/notebooks/{nb}/quizzes", status_code=201, tags=["quizzes"], name="create")
def create(
    nb: str,
    body: QuizCreate,
    current_user: CurrentUser,
) -> QuizOut:
    get_readable_notebook(nb, current_user)

    if body.mode != "chosen":
        raise HTTPException(
            422,
            detail={"code": "invalid", "message": "Only chosen quizzes are available for now."},
        )

    if not body.topic_ids or not (1 <= len(body.topic_ids) <= 6):
        raise HTTPException(
            422,
            detail={"code": "invalid", "message": "topic_ids must contain between 1 and 6 topics."},
        )

    for t in body.topic_ids:
        if not re.fullmatch(r"t[1-6]|other", t):
            raise HTTPException(
                422,
                detail={"code": "invalid", "message": f"Invalid topic ID: '{t}'."},
            )

    topic_ids = list(dict.fromkeys(body.topic_ids))

    candidates_by_topic = {}
    candidate_map = {}
    total_candidates = 0
    for tid in topic_ids:
        cands = list_verified_questions(nb, tid, limit=100)
        candidates_by_topic[tid] = cands
        for qid, q in cands:
            candidate_map[qid] = q
        total_candidates += len(cands)

    if total_candidates == 0:
        raise HTTPException(
            409,
            detail={"code": "not_ready", "message": "No verified questions for these topics yet."},
        )

    db = get_db()
    transaction = db.transaction()

    @firestore.transactional
    def create_tx(tx):
        member_ref = db.document(member_path(nb, current_user.uid))
        member_snap = member_ref.get(transaction=tx)
        if not member_snap.exists:
            is_new = True
            seen_ids = set()
        else:
            is_new = False
            seen_ids = set(member_snap.to_dict().get("seen_question_ids", []))

        selected_ids = select_questions(candidates_by_topic, topic_ids, seen_ids, count=body.count)

        quiz_ref = db.collection(quizzes_collection_path(nb, current_user.uid)).document()
        question_topics = {qid: candidate_map[qid].topic_id for qid in selected_ids}

        if is_new:
            defaults = default_member_data()
            defaults["seen_question_ids"] = selected_ids
            tx.set(member_ref, defaults)
        else:
            tx.update(member_ref, {"seen_question_ids": firestore.ArrayUnion(selected_ids)})

        quiz_data = {
            "mode": "chosen",
            "topic_ids": topic_ids,
            "question_ids": selected_ids,
            "question_topics": question_topics,
            "position": 0,
            "status": "in_progress",
            "score": None,
            "report": None,
            "created_at": firestore.SERVER_TIMESTAMP,
        }
        tx.set(quiz_ref, quiz_data)
        return quiz_ref.id, selected_ids

    quiz_id, selected_ids = create_tx(transaction)

    questions_out = [
        QuestionOut(
            id=qid,
            type=candidate_map[qid].type,
            topic_id=candidate_map[qid].topic_id,
            difficulty=candidate_map[qid].difficulty,
            stem=candidate_map[qid].stem,
            options=[
                QuestionOptionOut(id=opt.id, text=opt.text)
                for opt in candidate_map[qid].options
            ]
            if candidate_map[qid].type == "mcq"
            else None,
        )
        for qid in selected_ids
    ]

    return QuizOut(
        id=quiz_id,
        mode="chosen",
        topic_ids=topic_ids,
        status="in_progress",
        questions=questions_out,
        answers=[],
        summary=None,
        created_at=datetime.now(UTC),
    )


@router.get("/notebooks/{nb}/quizzes/{q}", tags=["quizzes"], name="get")
def get(
    nb: str,
    q: str,
    current_user: CurrentUser,
) -> QuizOut:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", q):
        raise HTTPException(
            404,
            detail={"code": "not_found", "message": "Quiz not found."},
        )

    get_readable_notebook(nb, current_user)

    quiz_snap = get_quiz_snapshot(nb, current_user.uid, q)
    if not quiz_snap.exists:
        raise HTTPException(
            404,
            detail={"code": "not_found", "message": "Quiz not found."},
        )

    quiz_data = quiz_snap.to_dict() or {}
    question_ids = quiz_data.get("question_ids", [])
    topic_ids = quiz_data.get("topic_ids", [])
    question_topics = quiz_data.get("question_topics", {})

    stored_questions = get_questions(nb, question_ids)
    attempt_ids = [f"{q}_{qid}" for qid in question_ids]
    attempts = get_attempts(nb, current_user.uid, attempt_ids)

    questions_out = []
    for qid in question_ids:
        if qid in stored_questions:
            sq = stored_questions[qid]
            questions_out.append(
                QuestionOut(
                    id=qid,
                    type=sq.type,
                    topic_id=sq.topic_id,
                    difficulty=sq.difficulty,
                    stem=sq.stem,
                    options=[QuestionOptionOut(id=opt.id, text=opt.text) for opt in sq.options]
                    if sq.type == "mcq"
                    else None,
                )
            )

    answers_out = []
    for qid in question_ids:
        aid = f"{q}_{qid}"
        if aid in attempts and qid in stored_questions:
            att = attempts[aid]
            fb = build_feedback(stored_questions[qid], att)
            answers_out.append(
                QuizAnswerRecord(
                    question_id=qid,
                    answer=att.get("answer", ""),
                    feedback=fb,
                )
            )

    summary_out: QuizSummary | None = None
    if quiz_data.get("status") == "finished":
        total = len(question_ids)
        answered = len(attempts)
        correct = sum(1 for a in attempts.values() if a.get("correct") is True)
        score = (correct / total) if total > 0 else 0.0

        by_topic = []
        for tid in topic_ids:
            topic_total = sum(1 for qid in question_ids if question_topics.get(qid) == tid)
            topic_answered = sum(1 for a in attempts.values() if a.get("topic_id") == tid)
            topic_correct = sum(
                1
                for a in attempts.values()
                if a.get("topic_id") == tid and a.get("correct") is True
            )
            by_topic.append(
                TopicSummaryItem(
                    topic_id=tid,
                    total=topic_total,
                    answered=topic_answered,
                    correct=topic_correct,
                )
            )

        summary_out = QuizSummary(
            quiz_id=q,
            status="finished",
            total=total,
            answered=answered,
            correct=correct,
            score=score,
            by_topic=by_topic,
            report=None,
        )

    created_at = quiz_data.get("created_at")
    if not isinstance(created_at, datetime):
        created_at = datetime.now(UTC)

    return QuizOut(
        id=q,
        mode=quiz_data.get("mode", "chosen"),
        topic_ids=topic_ids,
        status=quiz_data.get("status", "in_progress"),
        questions=questions_out,
        answers=answers_out,
        summary=summary_out,
        created_at=created_at,
    )


@router.post("/notebooks/{nb}/quizzes/{q}/answers", tags=["quizzes"], name="answer")
def answer(
    nb: str,
    q: str,
    body: QuizAnswerRequest,
    current_user: CurrentUser,
) -> QuizAnswerResponse:
    # a) Validate question_id and q with full-string ID match
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", q):
        raise HTTPException(
            404,
            detail={"code": "not_found", "message": "Quiz not found."},
        )

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", body.question_id):
        raise HTTPException(
            422,
            detail={"code": "invalid", "message": "Invalid question_id format."},
        )

    get_readable_notebook(nb, current_user)

    # b) Fetch question outside the transaction
    question = get_question(nb, body.question_id)
    if question is None:
        raise HTTPException(
            404,
            detail={"code": "not_found", "message": "Question not found."},
        )

    # c) Grade it; input errors are 422
    try:
        grade_result = grade(question, body.answer)
    except QuizInputError as exc:
        raise HTTPException(
            422,
            detail={"code": "invalid", "message": str(exc)},
        ) from exc

    # d) ONE transaction that reads quiz and attempt before any write
    db = get_db()
    transaction = db.transaction()

    quiz_ref = db.document(quiz_path(nb, current_user.uid, q))
    attempt_ref = db.document(attempt_path(nb, current_user.uid, f"{q}_{body.question_id}"))

    @firestore.transactional
    def answer_tx(tx):
        quiz_snap = quiz_ref.get(transaction=tx)
        attempt_snap = attempt_ref.get(transaction=tx)

        if not quiz_snap.exists:
            raise HTTPException(
                404,
                detail={"code": "not_found", "message": "Quiz not found."},
            )

        quiz_data = quiz_snap.to_dict() or {}
        if body.question_id not in quiz_data.get("question_ids", []):
            raise HTTPException(
                422,
                detail={"code": "invalid", "message": "Question not in quiz."},
            )

        # Attempt exists checked BEFORE finished check so retried answer gets stored feedback
        if attempt_snap.exists:
            return attempt_snap.to_dict() or {}, True

        if quiz_data.get("status") == "finished":
            raise HTTPException(
                422,
                detail={"code": "invalid", "message": "This quiz is finished."},
            )

        new_attempt = {
            "question_id": body.question_id,
            "quiz_id": q,
            "topic_id": question.topic_id,
            "type": question.type,
            "answer": grade_result.recorded_answer,
            "correct": grade_result.correct,
            "score": 1.0 if grade_result.correct else 0.0,
            "time_ms": body.time_ms,
            "created_at": firestore.SERVER_TIMESTAMP,
        }
        tx.set(attempt_ref, new_attempt)
        tx.update(quiz_ref, {"position": firestore.Increment(1)})
        return new_attempt, False

    attempt_record, already_answered = answer_tx(transaction)

    try:
        if get_study_coach_enabled(current_user.uid):
            storage = coach_storage(nb, current_user.uid)
            record_quiz_answer(
                storage=storage,
                nb=nb,
                uid=current_user.uid,
                topic_id=attempt_record.get("topic_id", question.topic_id),
                attempt_id=f"{q}_{body.question_id}",
                question_type=attempt_record.get("type", question.type),
                score=float(attempt_record.get("score", 0.0)),
                coach_on=True,
                now=datetime.now(UTC),
            )
    except Exception:
        logger.exception("Failed to record quiz answer in Study Coach")

    feedback = build_feedback(question, attempt_record)

    return QuizAnswerResponse(
        question_id=body.question_id,
        answer=attempt_record.get("answer", grade_result.recorded_answer),
        already_answered=already_answered,
        feedback=feedback,
    )


@router.post("/notebooks/{nb}/quizzes/{q}/finish", tags=["quizzes"], name="finish")
def finish(
    nb: str,
    q: str,
    current_user: CurrentUser,
) -> QuizSummary:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", q):
        raise HTTPException(
            404,
            detail={"code": "not_found", "message": "Quiz not found."},
        )

    get_readable_notebook(nb, current_user)

    db = get_db()
    transaction = db.transaction()
    quiz_ref = db.document(quiz_path(nb, current_user.uid, q))

    @firestore.transactional
    def finish_tx(tx):
        quiz_snap = quiz_ref.get(transaction=tx)
        if not quiz_snap.exists:
            raise HTTPException(
                404,
                detail={"code": "not_found", "message": "Quiz not found."},
            )

        quiz_data = quiz_snap.to_dict() or {}
        question_ids = quiz_data.get("question_ids", [])
        topic_ids = quiz_data.get("topic_ids", [])
        question_topics = quiz_data.get("question_topics", {})

        attempt_refs = [
            db.document(attempt_path(nb, current_user.uid, f"{q}_{qid}"))
            for qid in question_ids
        ]
        attempt_snaps = tx.get_all(attempt_refs)
        existing_attempts = [
            s.to_dict() for s in attempt_snaps if s.exists and s.to_dict() is not None
        ]

        total = len(question_ids)
        answered = len(existing_attempts)
        correct = sum(1 for a in existing_attempts if a.get("correct") is True)
        score = (correct / total) if total > 0 else 0.0

        by_topic = []
        for tid in topic_ids:
            topic_total = sum(1 for qid in question_ids if question_topics.get(qid) == tid)
            topic_answered = sum(1 for a in existing_attempts if a.get("topic_id") == tid)
            topic_correct = sum(
                1
                for a in existing_attempts
                if a.get("topic_id") == tid and a.get("correct") is True
            )
            by_topic.append(
                TopicSummaryItem(
                    topic_id=tid,
                    total=topic_total,
                    answered=topic_answered,
                    correct=topic_correct,
                )
            )

        if quiz_data.get("status") == "in_progress":
            tx.update(
                quiz_ref,
                {
                    "status": "finished",
                    "finished_at": firestore.SERVER_TIMESTAMP,
                    "score": score,
                },
            )

        return QuizSummary(
            quiz_id=q,
            status="finished",
            total=total,
            answered=answered,
            correct=correct,
            score=score,
            by_topic=by_topic,
            report=None,
        )

    return finish_tx(transaction)
