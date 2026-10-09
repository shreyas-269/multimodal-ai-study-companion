import collections
import logging
from typing import Any

from google.cloud.firestore_v1.base_query import FieldFilter

from app.db.client import get_db
from app.db.paths import question_path, questions_collection_path
from app.models.question import QuestionType, StoredQuestion
from app.models.quiz import QuestionBankItem

logger = logging.getLogger(__name__)

SYLLABUS_ORDER = {"t1": 1, "t2": 2, "t3": 3, "t4": 4, "t5": 5, "t6": 6}
TYPE_ORDER = {"mcq": 1, "numerical": 2, "short": 3}


def put_question(nb_id: str, question_id: str, question: StoredQuestion) -> None:
    """Store a question document in Firestore."""
    db = get_db()
    ref = db.document(question_path(nb_id, question_id))
    ref.set(question.model_dump(mode="json"))


def get_question(nb_id: str, question_id: str) -> StoredQuestion | None:
    """Fetch a single question by ID.

    Returns None if the document does not exist.
    Raises ValidationError if the document fails StoredQuestion validation.
    Used by answer endpoint.
    """
    db = get_db()
    snap = db.document(question_path(nb_id, question_id)).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    return StoredQuestion.model_validate(data)


def get_questions(nb_id: str, ids: list[str]) -> dict[str, StoredQuestion]:
    """Fetch multiple questions in a single get_all batch read.

    Skips missing documents and documents failing validation with warning logs.
    Used by GET quiz endpoint.
    """
    if not ids:
        return {}
    db = get_db()
    refs = [db.document(question_path(nb_id, qid)) for qid in ids]
    snaps = db.get_all(refs)
    result: dict[str, StoredQuestion] = {}
    for snap in snaps:
        if not snap.exists:
            logger.warning("Question %s missing from notebook %s", snap.id, nb_id)
            continue
        try:
            data = snap.to_dict() or {}
            result[snap.id] = StoredQuestion.model_validate(data)
        except Exception as exc:
            logger.warning(
                "Question %s in notebook %s failed validation: %s", snap.id, nb_id, exc
            )
            continue
    return result


def list_verified_questions(
    nb_id: str, topic_id: str, limit: int = 100
) -> list[tuple[str, StoredQuestion]]:
    """Query verified questions for a topic, ordered by document ID for stable cut-off.

    Filters in-memory to retain only mcq and numerical types.
    """
    db = get_db()
    coll = db.collection(questions_collection_path(nb_id))
    query = (
        coll.where(filter=FieldFilter("topic_id", "==", topic_id))
        .where(filter=FieldFilter("status", "==", "verified"))
        .order_by("__name__")
        .limit(limit)
    )
    docs = query.stream()
    candidates: list[tuple[str, StoredQuestion]] = []
    for doc in docs:
        try:
            data = doc.to_dict() or {}
            q = StoredQuestion.model_validate(data)
            if q.type in ("mcq", "numerical"):
                candidates.append((doc.id, q))
        except Exception as exc:
            logger.warning(
                "Skipping invalid question %s in notebook %s: %s", doc.id, nb_id, exc
            )
            continue
    return candidates


def bank_counts(nb_id: str) -> list[QuestionBankItem]:
    """Aggregate question-bank counts per topic and type for verified and rejected questions."""
    db = get_db()
    coll = db.collection(questions_collection_path(nb_id))
    docs = coll.select(["topic_id", "type", "status"]).limit(2000).stream()

    counts: dict[tuple[str, QuestionType], dict[str, int]] = collections.defaultdict(
        lambda: {"verified": 0, "rejected": 0}
    )
    for doc in docs:
        data: dict[str, Any] = doc.to_dict() or {}
        tid = data.get("topic_id")
        qtype = data.get("type")
        status = data.get("status")
        if (
            tid
            and qtype in ("mcq", "numerical", "short")
            and status in ("verified", "rejected")
        ):
            counts[(tid, qtype)][status] += 1

    items: list[QuestionBankItem] = []
    for (tid, qtype), stats in counts.items():
        if stats["verified"] + stats["rejected"] > 0:
            items.append(
                QuestionBankItem(
                    topic_id=tid,
                    type=qtype,
                    verified=stats["verified"],
                    rejected=stats["rejected"],
                )
            )

    def sort_key(item: QuestionBankItem) -> tuple[int, Any, int]:
        if item.topic_id in SYLLABUS_ORDER:
            tid_order = (0, SYLLABUS_ORDER[item.topic_id])
        else:
            tid_order = (1, item.topic_id)
        type_order = TYPE_ORDER.get(item.type, 99)
        return (tid_order[0], tid_order[1], type_order)

    items.sort(key=sort_key)
    return items
