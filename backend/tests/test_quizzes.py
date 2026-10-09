import concurrent.futures
import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.db import get_db, notebook_path
from app.db.paths import attempt_path, member_path, question_path, quiz_path
from app.db.questions import put_question
from app.main import app
from app.models.citation import Citation, Location, OpenPdfTarget
from app.models.question import (
    QuestionAnswer,
    QuestionOption,
    QuestionVerification,
    StoredQuestion,
)
from tests.conftest import create_emulator_user


def setup_test_notebook(
    nb_id: str, owner_uid: str, name: str, is_demo: bool = False
) -> None:
    db = get_db()
    db.document(notebook_path(nb_id)).set(
        {
            "name": name,
            "owner_uid": owner_uid,
            "is_demo": is_demo,
            "status": "ready",
            "sources_summary": [],
            "counts": {"chunks": 0, "questions_verified": 0},
            "created_at": datetime.now(UTC),
        }
    )


def make_question(
    qid: str,
    qtype: str,
    topic_id: str,
    status: str = "verified",
    difficulty: int = 1,
    stem: str = "Test stem",
    options: list[QuestionOption] | None = None,
    answer: QuestionAnswer | None = None,
    explanation: str = "Test explanation",
    citations: list[Citation] | None = None,
    solution_code: str | None = None,
) -> StoredQuestion:
    if options is None and qtype == "mcq":
        options = [
            QuestionOption(id="a", text="Option A", misconception="Misconception A"),
            QuestionOption(id="b", text="Option B", misconception="Misconception B"),
        ]
    if answer is None:
        if qtype == "mcq":
            answer = QuestionAnswer(option_id="a")
        elif qtype == "numerical":
            answer = QuestionAnswer(value="42")
        else:
            answer = QuestionAnswer(model_answer="model")

    return StoredQuestion(
        type=qtype,
        topic_id=topic_id,
        difficulty=difficulty,
        stem=stem,
        options=options or [],
        answer=answer,
        rubric=[],
        explanation=explanation,
        citations=citations or [
            Citation(
                chunk_id="chunk_1",
                loc=Location(source_id="src_1", page=1),
                label="p. 1",
                open=OpenPdfTarget(source_id="src_1", page=1),
            )
        ],
        verification=QuestionVerification(method="auto", passed=True, detail="ok"),
        status=status,
        batch_id="batch_1",
        solution_code=solution_code,
        created_at=datetime.now(UTC),
    )


def test_quiz_operation_ids_pinned():
    client = TestClient(app)
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    paths = schema.get("paths", {})

    expected_ops = {
        "/v1/notebooks/{nb}/question-bank": {"get": "question_bank_get"},
        "/v1/notebooks/{nb}/quizzes": {"post": "quizzes_create"},
        "/v1/notebooks/{nb}/quizzes/{q}": {"get": "quizzes_get"},
        "/v1/notebooks/{nb}/quizzes/{q}/answers": {"post": "quizzes_answer"},
        "/v1/notebooks/{nb}/quizzes/{q}/finish": {"post": "quizzes_finish"},
    }

    for path, methods in expected_ops.items():
        assert path in paths, f"Missing path: {path}"
        for method, expected_op_id in methods.items():
            actual_op_id = paths[path].get(method, {}).get("operationId")
            msg = (
                f"Expected operationId '{expected_op_id}' "
                f"for {method.upper()} {path}, got '{actual_op_id}'"
            )
            assert actual_op_id == expected_op_id, msg


def test_question_bank_counts_verified_and_rejected(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Test Notebook")

    # Add verified and rejected questions
    put_question(nb_id, "q1", make_question("q1", "mcq", "t1", status="verified"))
    put_question(nb_id, "q2", make_question("q2", "mcq", "t1", status="rejected"))
    put_question(nb_id, "q3", make_question("q3", "numerical", "t1", status="verified"))
    put_question(nb_id, "q4", make_question("q4", "short", "t2", status="verified"))

    client = TestClient(app)
    res = client.get(
        f"/v1/notebooks/{nb_id}/question-bank",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total_verified"] == 3
    items = data["items"]
    # t1 mcq: 1 verified, 1 rejected
    # t1 numerical: 1 verified, 0 rejected
    # t2 short: 1 verified, 0 rejected
    assert len(items) == 3
    assert items[0] == {"topic_id": "t1", "type": "mcq", "verified": 1, "rejected": 1}
    assert items[1] == {"topic_id": "t1", "type": "numerical", "verified": 1, "rejected": 0}
    assert items[2] == {"topic_id": "t2", "type": "short", "verified": 1, "rejected": 0}


def test_question_bank_counts_empty_notebook(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Empty Notebook")

    client = TestClient(app)
    res = client.get(
        f"/v1/notebooks/{nb_id}/question-bank",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["items"] == []
    assert data["total_verified"] == 0


def test_quiz_create_success_mixed_types(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Quiz Notebook")

    put_question(nb_id, "q1", make_question("q1", "mcq", "t1"))
    put_question(nb_id, "q2", make_question("q2", "numerical", "t1"))

    client = TestClient(app)
    res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 2},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["mode"] == "chosen"
    assert data["status"] == "in_progress"
    assert len(data["questions"]) == 2
    types = {q["type"] for q in data["questions"]}
    assert types == {"mcq", "numerical"}


def test_quiz_create_and_get_leak_test(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Leak Test Notebook")

    put_question(
        nb_id,
        "q_secret",
        make_question(
            "q_secret",
            "numerical",
            "t1",
            answer=QuestionAnswer(value="31/977"),
            explanation="EXPLAIN-MARKER-Q2",
            solution_code="answer = 31/977",
        ),
    )

    client = TestClient(app)
    create_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    assert create_res.status_code == 201
    create_text = create_res.text
    assert "31/977" not in create_text
    assert "EXPLAIN-MARKER-Q2" not in create_text
    assert "answer = 31/977" not in create_text

    quiz_id = create_res.json()["id"]
    get_res = client.get(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_res.status_code == 200
    get_text = get_res.text
    assert "31/977" not in get_text
    assert "EXPLAIN-MARKER-Q2" not in get_text
    assert "answer = 31/977" not in get_text


def test_quiz_seen_questions_excluded_next_quiz(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Seen Exclusion")

    put_question(nb_id, "q1", make_question("q1", "mcq", "t1"))
    put_question(nb_id, "q2", make_question("q2", "mcq", "t1"))
    put_question(nb_id, "q3", make_question("q3", "mcq", "t1"))

    client = TestClient(app)
    # Quiz 1 picks 2 questions
    res1 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 2},
    )
    assert res1.status_code == 201
    q1_ids = {q["id"] for q in res1.json()["questions"]}
    assert len(q1_ids) == 2

    # Quiz 2 picks 1 question: must be the unseen one
    res2 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    assert res2.status_code == 201
    q2_ids = {q["id"] for q in res2.json()["questions"]}
    assert len(q2_ids) == 1
    assert q2_ids.isdisjoint(q1_ids)


def test_quiz_fill_from_seen_when_unseen_run_out(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Fill Seen")

    put_question(nb_id, "q1", make_question("q1", "mcq", "t1"))
    put_question(nb_id, "q2", make_question("q2", "mcq", "t1"))

    client = TestClient(app)
    # Quiz 1 consumes both
    res1 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 2},
    )
    assert res1.status_code == 201

    # Quiz 2 requests 2 questions: both are seen, backfills from seen pool
    res2 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 2},
    )
    assert res2.status_code == 201
    assert len(res2.json()["questions"]) == 2


def test_quiz_create_409_when_no_verified_questions(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Empty Verified")

    # Add only a short question which is filtered out
    put_question(nb_id, "q_short", make_question("q_short", "short", "t1"))

    client = TestClient(app)
    res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 2},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "not_ready"

    # Verify zero writes: member doc was not created
    db = get_db()
    member_snap = db.document(member_path(nb_id, uid)).get()
    assert not member_snap.exists


def test_quiz_create_422_adaptive_mode(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Adaptive")

    client = TestClient(app)
    res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "adaptive", "topic_ids": ["t1"], "count": 2},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid"


def test_quiz_create_422_bad_topic_id(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Bad Topic")

    client = TestClient(app)
    res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["invalid_topic"], "count": 2},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid"


def test_quiz_create_422_count_bounds(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Bounds")

    client = TestClient(app)
    # count 0
    res0 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 0},
    )
    assert res0.status_code == 422

    # count 11
    res11 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 11},
    )
    assert res11.status_code == 422


def test_quiz_create_422_extra_fields(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Extra Fields")

    client = TestClient(app)
    res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 2, "extra": "forbidden"},
    )
    assert res.status_code == 422


def test_quiz_404_other_user_notebook(notebook_tracker, user_tracker):
    uid1, _ = create_emulator_user()
    user_tracker.append(uid1)
    uid2, token2 = create_emulator_user()
    user_tracker.append(uid2)

    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid1, "User 1 Notebook", is_demo=False)

    client = TestClient(app)
    res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token2}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 2},
    )
    assert res.status_code == 404


def test_quiz_404_other_user_quiz(notebook_tracker, user_tracker):
    uid1, token1 = create_emulator_user()
    user_tracker.append(uid1)
    uid2, token2 = create_emulator_user()
    user_tracker.append(uid2)

    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid1, "Shared Notebook", is_demo=True)
    put_question(nb_id, "q1", make_question("q1", "mcq", "t1"))

    client = TestClient(app)
    # User 1 creates quiz
    res_create = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token1}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = res_create.json()["id"]

    # User 2 tries to GET user 1's quiz
    res_get = client.get(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert res_get.status_code == 404


def test_quiz_demo_notebook_accessible_by_non_owner(notebook_tracker, user_tracker):
    uid1, _ = create_emulator_user()
    user_tracker.append(uid1)
    uid2, token2 = create_emulator_user()
    user_tracker.append(uid2)

    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid1, "Demo Notebook", is_demo=True)
    put_question(nb_id, "q1", make_question("q1", "mcq", "t1"))

    client = TestClient(app)
    res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token2}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    assert res.status_code == 201


def test_quiz_answer_correct_mcq(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "MCQ Correct")
    ans_a = QuestionAnswer(option_id="a")
    put_question(nb_id, "q1", make_question("q1", "mcq", "t1", answer=ans_a))

    client = TestClient(app)
    create_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = create_res.json()["id"]

    ans_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "a", "time_ms": 1500},
    )
    assert ans_res.status_code == 200
    data = ans_res.json()
    assert data["already_answered"] is False
    assert data["feedback"]["verdict"] == "correct"
    assert data["feedback"]["correct_option_id"] == "a"


def test_quiz_answer_wrong_mcq_with_misconception_and_citations(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "MCQ Wrong")
    ans_a = QuestionAnswer(option_id="a")
    put_question(nb_id, "q1", make_question("q1", "mcq", "t1", answer=ans_a))

    client = TestClient(app)
    create_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = create_res.json()["id"]

    ans_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "b", "time_ms": 2000},
    )
    assert ans_res.status_code == 200
    data = ans_res.json()
    assert data["already_answered"] is False
    assert data["feedback"]["verdict"] == "incorrect"
    assert data["feedback"]["misconception"] == "Misconception B"
    assert len(data["feedback"]["citations"]) == 1


def test_quiz_answer_numerical_equivalent_decimal(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Numerical Equivalent")
    ans_num = QuestionAnswer(value="3/8")
    put_question(nb_id, "q1", make_question("q1", "numerical", "t1", answer=ans_num))

    client = TestClient(app)
    create_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = create_res.json()["id"]

    ans_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "0.375", "time_ms": 3000},
    )
    assert ans_res.status_code == 200
    assert ans_res.json()["feedback"]["verdict"] == "correct"


def test_quiz_answer_422_option_id_not_in_question(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Option Missing")
    put_question(nb_id, "q1", make_question("q1", "mcq", "t1"))

    client = TestClient(app)
    create_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = create_res.json()["id"]

    ans_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "z", "time_ms": 1000},
    )
    assert ans_res.status_code == 422


def test_quiz_answer_422_question_not_in_quiz(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Q Not in Quiz")
    put_question(nb_id, "q1", make_question("q1", "mcq", "t1"))
    put_question(nb_id, "q2", make_question("q2", "mcq", "t1"))

    client = TestClient(app)
    # Quiz only contains q1
    create_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = create_res.json()["id"]

    # Try to answer q2
    ans_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q2", "answer": "a", "time_ms": 1000},
    )
    assert ans_res.status_code == 422


def test_quiz_answer_malformed_stored_numerical_gives_500(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Malformed 500")

    # Put a valid question initially to allow quiz creation
    ans_42 = QuestionAnswer(value="42")
    put_question(nb_id, "q_corrupt", make_question("q_corrupt", "numerical", "t1", answer=ans_42))

    client = TestClient(app, raise_server_exceptions=False)
    create_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = create_res.json()["id"]

    # Corrupt the stored answer.value directly in Firestore
    db = get_db()
    db.document(question_path(nb_id, "q_corrupt")).update({"answer.value": "not_a_valid_number"})

    ans_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q_corrupt", "answer": "42", "time_ms": 1000},
    )
    assert ans_res.status_code == 500


def test_quiz_get_skips_deleted_question_and_summary_counts_it(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Deleted Q")
    put_question(nb_id, "q1", make_question("q1", "mcq", "t1"))
    put_question(nb_id, "q2", make_question("q2", "mcq", "t1"))

    client = TestClient(app)
    create_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 2},
    )
    quiz_id = create_res.json()["id"]

    # Delete q2 from questions collection
    db = get_db()
    db.document(question_path(nb_id, "q2")).delete()

    # Finish the quiz
    finish_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/finish",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert finish_res.status_code == 200
    summary = finish_res.json()
    assert summary["total"] == 2
    assert summary["by_topic"][0]["total"] == 2

    # GET quiz should skip deleted q2 from questions array, but summary still counts it
    get_res = client.get(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert len(get_data["questions"]) == 1
    assert get_data["summary"]["total"] == 2


def test_quiz_answer_after_finish_422(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Finish 422")
    put_question(nb_id, "q1", make_question("q1", "mcq", "t1"))

    client = TestClient(app)
    create_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = create_res.json()["id"]

    # Finish quiz without answering
    client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/finish",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Answering new question after finish gives 422
    ans_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "a", "time_ms": 1000},
    )
    assert ans_res.status_code == 422
    assert ans_res.json()["error"]["message"] == "This quiz is finished."


def test_quiz_answer_repeat_after_finish_returns_stored_feedback(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Repeat After Finish")
    put_question(nb_id, "q1", make_question("q1", "mcq", "t1"))

    client = TestClient(app)
    create_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = create_res.json()["id"]

    # Answer q1 first
    ans1 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "a", "time_ms": 1000},
    )
    assert ans1.status_code == 200

    # Finish quiz
    client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/finish",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Repeat answer after finish: returns stored feedback with already_answered=True
    ans_repeat = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "a", "time_ms": 1000},
    )
    assert ans_repeat.status_code == 200
    assert ans_repeat.json()["already_answered"] is True
    assert ans_repeat.json()["feedback"]["verdict"] == "correct"


def test_quiz_answer_repeat_returns_already_answered(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Repeat Answer")
    put_question(nb_id, "q1", make_question("q1", "mcq", "t1"))

    client = TestClient(app)
    create_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = create_res.json()["id"]

    ans1 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "a", "time_ms": 1000},
    )
    assert ans1.status_code == 200
    assert ans1.json()["already_answered"] is False

    ans2 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "a", "time_ms": 1000},
    )
    assert ans2.status_code == 200
    assert ans2.json()["already_answered"] is True


def test_quiz_answer_concurrent_single_attempt(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Concurrent Answer")
    put_question(nb_id, "q1", make_question("q1", "mcq", "t1"))

    client = TestClient(app)
    create_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = create_res.json()["id"]

    def submit_answer():
        c = TestClient(app)
        return c.post(
            f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
            headers={"Authorization": f"Bearer {token}"},
            json={"question_id": "q1", "answer": "a", "time_ms": 1000},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(submit_answer) for _ in range(5)]
        results = [f.result() for f in futures]

    assert all(r.status_code == 200 for r in results)
    already_answered_counts = [r.json()["already_answered"] for r in results]
    assert already_answered_counts.count(False) == 1
    assert already_answered_counts.count(True) == 4

    # Assert exactly 1 attempt document and quiz position 1
    db = get_db()
    quiz_snap = db.document(quiz_path(nb_id, uid, quiz_id)).get()
    assert quiz_snap.to_dict()["position"] == 1

    attempt_snap = db.document(attempt_path(nb_id, uid, f"{quiz_id}_q1")).get()
    assert attempt_snap.exists


def test_quiz_finish_summary_and_idempotence(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Finish Idempotent")
    ans_a = QuestionAnswer(option_id="a")
    put_question(nb_id, "q1", make_question("q1", "mcq", "t1", answer=ans_a))
    ans_a2 = QuestionAnswer(option_id="a")
    put_question(nb_id, "q2", make_question("q2", "mcq", "t2", answer=ans_a2))

    client = TestClient(app)
    create_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1", "t2"], "count": 2},
    )
    quiz_id = create_res.json()["id"]

    # Answer only q1 correctly
    client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "a", "time_ms": 1000},
    )

    # Finish 1
    fin1 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/finish",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert fin1.status_code == 200
    s1 = fin1.json()
    assert s1["total"] == 2
    assert s1["answered"] == 1
    assert s1["correct"] == 1
    assert s1["score"] == 0.5
    assert len(s1["by_topic"]) == 2

    # Finish 2 (idempotent)
    fin2 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/finish",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert fin2.status_code == 200
    s2 = fin2.json()
    assert s1 == s2


def test_quiz_get_after_finish_shows_answers_and_summary(notebook_tracker, user_tracker):
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, "Get After Finish")
    put_question(nb_id, "q1", make_question("q1", "mcq", "t1"))

    client = TestClient(app)
    create_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = create_res.json()["id"]

    client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "a", "time_ms": 1000},
    )
    client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/finish",
        headers={"Authorization": f"Bearer {token}"},
    )

    get_res = client.get(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["status"] == "finished"
    assert len(data["answers"]) == 1
    assert data["summary"] is not None
    assert data["summary"]["score"] == 1.0


def test_isolation_random_suffix_notebooks_and_cleanup(notebook_tracker, user_tracker):
    """Verify test uses random suffix, never touches nb_demo_6041, and cleans up only own docs."""
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    suffix = uuid.uuid4().hex[:8]
    nb_id = f"nb_test_{suffix}"
    assert nb_id != "nb_demo_6041"
    notebook_tracker.append(nb_id)
    setup_test_notebook(nb_id, uid, f"Isolation {suffix}")

    db = get_db()
    # Confirm nb_demo_6041 is not being modified
    demo_ref = db.document(notebook_path("nb_demo_6041"))
    demo_snap_before = demo_ref.get()

    put_question(nb_id, "q1", make_question("q1", "mcq", "t1"))

    client = TestClient(app)
    res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    assert res.status_code == 201

    demo_snap_after = demo_ref.get()
    assert demo_snap_before.exists == demo_snap_after.exists
