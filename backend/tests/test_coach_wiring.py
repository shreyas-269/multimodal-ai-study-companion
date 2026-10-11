"""Integration tests for Study Coach wiring in quiz answers and topic chat turns."""

import json
import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
from google.cloud.firestore_v1.vector import Vector
from tenacity import wait_none

from app.db.client import get_db
from app.db.paths import (
    attempt_path,
    chunk_path,
    coach_event_path,
    coach_events_collection_path,
    mastery_path,
    messages_collection_path,
    notebook_path,
    topic_path,
    user_path,
)
from app.db.questions import put_question
from app.embeddings import embed_passages
from app.llm import generate
from app.main import app
from app.models.question import (
    QuestionAnswer,
    QuestionOption,
    QuestionVerification,
    StoredQuestion,
)
from tests.conftest import create_emulator_user

client = TestClient(app)


def _setup_test_notebook(nb_id: str, owner_uid: str) -> None:
    db = get_db()
    db.document(notebook_path(nb_id)).set({
        "name": "Wiring Test NB",
        "owner_uid": owner_uid,
        "is_demo": False,
        "status": "ready",
        "sources_summary": [],
        "counts": {"chunks": 0, "questions_verified": 1},
        "created_at": datetime.now(UTC),
    })


def _setup_topic(nb_id: str, topic_id: str = "t1") -> None:
    db = get_db()
    db.document(topic_path(nb_id, topic_id)).set({
        "name": "Probability Basics",
        "order": 1,
        "summary": "Basics of probability",
        "is_other": False,
        "prerequisite_ids": [],
        "locations": [],
    })


def _setup_question(nb_id: str, qid: str = "q1", topic_id: str = "t1") -> None:
    q = StoredQuestion(
        id=qid,
        type="mcq",
        topic_id=topic_id,
        difficulty=1,
        stem="What is 2+2?",
        options=[
            QuestionOption(id="a", text="4", misconception=None),
            QuestionOption(id="b", text="5", misconception="Off by one"),
        ],
        answer=QuestionAnswer(option_id="a"),
        explanation="4 is correct.",
        citations=[],
        verification=QuestionVerification(method="auto", passed=True, detail="ok"),
        batch_id="batch_1",
        solution_code=None,
        status="verified",
        created_at=datetime.now(UTC),
    )
    put_question(nb_id, qid, q)


def _seed_chunks(nb_id: str, source_id: str, chunks_text: list[str]) -> list[str]:
    db = get_db()
    nb_ref = db.document(notebook_path(nb_id))
    nb_doc = nb_ref.get()
    data = nb_doc.to_dict() or {}
    summary = list(data.get("sources_summary", []))
    summary.append({
        "source_id": source_id,
        "ref_n": len(summary) + 1,
        "title": "Course Notes",
        "kind": "pdf",
        "status": "ready",
    })
    counts = dict(data.get("counts", {}))
    counts["chunks"] = counts.get("chunks", 0) + len(chunks_text)
    nb_ref.update({
        "sources_summary": summary,
        "status": "ready",
        "counts": counts,
    })

    chunk_ids = []
    if chunks_text:
        embeddings = embed_passages(chunks_text)
        for idx, text in enumerate(chunks_text):
            c_id = f"{source_id}-{idx:05d}"
            chunk_ids.append(c_id)
            c_ref = db.document(chunk_path(nb_id, c_id))
            c_ref.set({
                "source_id": source_id,
                "kind": "text",
                "text": text,
                "loc": {
                    "source_id": source_id,
                    "page": idx + 1,
                    "page_label": None,
                    "bbox": [10.0, 20.0, 100.0, 200.0],
                },
                "topic_id": None,
                "embedding": Vector(embeddings[idx]),
                "token_count": len(text.split()),
                "image_path": None,
            })
    return chunk_ids


def test_w1_quiz_answer_coach_on_writes_mastery_and_event(user_tracker, notebook_tracker):
    """W1: Quiz answer with study_coach: True writes mastery/{t} and coach event."""
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb_id)

    db = get_db()
    db.document(user_path(uid)).set({"study_coach": True})
    _setup_test_notebook(nb_id, uid)
    _setup_topic(nb_id, "t1")
    _setup_question(nb_id, "q1", "t1")

    # Create quiz
    q_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    assert q_res.status_code == 201
    quiz_id = q_res.json()["id"]

    # Answer quiz
    ans_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "a", "time_ms": 1000},
    )
    assert ans_res.status_code == 200
    assert ans_res.json()["already_answered"] is False

    # Check mastery doc
    m_snap = db.document(mastery_path(nb_id, uid, "t1")).get()
    assert m_snap.exists
    m_data = m_snap.to_dict() or {}
    assert m_data["n_obs"] == 1
    assert m_data["p_known"] > 0.0

    # Check coach event doc
    ev_snap = db.document(coach_event_path(nb_id, uid, f"qa_{quiz_id}_q1")).get()
    assert ev_snap.exists
    ev_data = ev_snap.to_dict() or {}
    assert ev_data["kind"] == "quiz_answer"
    assert ev_data["topic_id"] == "t1"
    assert ev_data["value"] == 1.0


def test_w2_quiz_answer_already_answered_leaves_n_obs_one(user_tracker, notebook_tracker):
    """W2: Answering same question again returns already_answered: True and leaves n_obs=1."""
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb_id)

    db = get_db()
    db.document(user_path(uid)).set({"study_coach": True})
    _setup_test_notebook(nb_id, uid)
    _setup_topic(nb_id, "t1")
    _setup_question(nb_id, "q1", "t1")

    q_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = q_res.json()["id"]

    # First answer
    ans_res1 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "a", "time_ms": 1000},
    )
    assert ans_res1.status_code == 200
    assert ans_res1.json()["already_answered"] is False

    # Second answer to same question
    ans_res2 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "a", "time_ms": 1500},
    )
    assert ans_res2.status_code == 200
    assert ans_res2.json()["already_answered"] is True

    m_snap = db.document(mastery_path(nb_id, uid, "t1")).get()
    assert m_snap.exists
    assert m_snap.to_dict()["n_obs"] == 1

    ev_list = list(db.collection(coach_events_collection_path(nb_id, uid)).stream())
    assert len(ev_list) == 1


def test_w3_quiz_answer_repeat_repairs_missing_coach_update(user_tracker, notebook_tracker):
    """W3: Deleting mastery/marker and re-answering recreates mastery document with n_obs=1."""
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb_id)

    db = get_db()
    db.document(user_path(uid)).set({"study_coach": True})
    _setup_test_notebook(nb_id, uid)
    _setup_topic(nb_id, "t1")
    _setup_question(nb_id, "q1", "t1")

    q_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = q_res.json()["id"]

    ans_res1 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "a", "time_ms": 1000},
    )
    assert ans_res1.status_code == 200

    # Delete mastery and event documents to simulate missed update
    db.document(mastery_path(nb_id, uid, "t1")).delete()
    db.document(coach_event_path(nb_id, uid, f"qa_{quiz_id}_q1")).delete()

    assert not db.document(mastery_path(nb_id, uid, "t1")).get().exists
    assert not db.document(coach_event_path(nb_id, uid, f"qa_{quiz_id}_q1")).get().exists

    # Re-answer (already_answered: True)
    ans_res2 = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "a", "time_ms": 1000},
    )
    assert ans_res2.status_code == 200
    assert ans_res2.json()["already_answered"] is True

    # Check mastery was repaired
    m_snap = db.document(mastery_path(nb_id, uid, "t1")).get()
    assert m_snap.exists
    assert m_snap.to_dict()["n_obs"] == 1

    ev_snap = db.document(coach_event_path(nb_id, uid, f"qa_{quiz_id}_q1")).get()
    assert ev_snap.exists


def test_w4_quiz_answer_coach_off_or_null_no_writes(
    user_tracker, notebook_tracker, monkeypatch
):
    """W4: Answering with study_coach: False or None writes no coach data and costs 1 user read."""
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb_id)

    db = get_db()
    db.document(user_path(uid)).set({"study_coach": False})
    _setup_test_notebook(nb_id, uid)
    _setup_topic(nb_id, "t1")
    _setup_question(nb_id, "q1", "t1")

    q_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = q_res.json()["id"]

    import app.api.quizzes as quizzes_module

    spy_enabled_calls = []
    orig_enabled = quizzes_module.get_study_coach_enabled

    def spy_enabled(u):
        spy_enabled_calls.append(u)
        return orig_enabled(u)

    spy_storage_calls = []

    def spy_storage(*args, **kwargs):
        spy_storage_calls.append((args, kwargs))
        return quizzes_module.coach_storage(*args, **kwargs)

    monkeypatch.setattr(quizzes_module, "get_study_coach_enabled", spy_enabled)
    monkeypatch.setattr(quizzes_module, "coach_storage", spy_storage)

    ans_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
        headers={"Authorization": f"Bearer {token}"},
        json={"question_id": "q1", "answer": "a", "time_ms": 1000},
    )
    assert ans_res.status_code == 200

    assert len(spy_enabled_calls) == 1
    assert len(spy_storage_calls) == 0
    assert not db.document(mastery_path(nb_id, uid, "t1")).get().exists
    assert len(list(db.collection(coach_events_collection_path(nb_id, uid)).stream())) == 0


def test_w5_quiz_answer_coach_error_logs_and_returns_200(
    user_tracker, notebook_tracker, monkeypatch, caplog
):
    """W5: Coach exception is caught, logged as ERROR, and answer endpoint still returns 200."""
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb_id)

    db = get_db()
    db.document(user_path(uid)).set({"study_coach": True})
    _setup_test_notebook(nb_id, uid)
    _setup_topic(nb_id, "t1")
    _setup_question(nb_id, "q1", "t1")

    q_res = client.post(
        f"/v1/notebooks/{nb_id}/quizzes",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "chosen", "topic_ids": ["t1"], "count": 1},
    )
    quiz_id = q_res.json()["id"]

    import app.api.quizzes as quizzes_module

    def fail_record_answer(*args, **kwargs):
        raise RuntimeError("Injected coach storage failure")

    monkeypatch.setattr(quizzes_module, "record_quiz_answer", fail_record_answer)

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        ans_res = client.post(
            f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
            headers={"Authorization": f"Bearer {token}"},
            json={"question_id": "q1", "answer": "a", "time_ms": 1000},
        )
    assert ans_res.status_code == 200
    assert ans_res.json()["feedback"]["verdict"] == "correct"

    # Assert attempt doc was saved
    att_snap = db.document(attempt_path(nb_id, uid, f"{quiz_id}_q1")).get()
    assert att_snap.exists

    # Assert ERROR logged
    assert any("Failed to record quiz answer in Study Coach" in r.message for r in caplog.records)

    # Variant 2: get_study_coach_enabled raises
    def fail_get_enabled(*args, **kwargs):
        raise RuntimeError("Injected get_study_coach_enabled failure")

    monkeypatch.setattr(quizzes_module, "get_study_coach_enabled", fail_get_enabled)
    caplog.clear()
    with caplog.at_level(logging.ERROR):
        ans_res2 = client.post(
            f"/v1/notebooks/{nb_id}/quizzes/{quiz_id}/answers",
            headers={"Authorization": f"Bearer {token}"},
            json={"question_id": "q1", "answer": "a", "time_ms": 1000},
        )
    assert ans_res2.status_code == 200
    assert any("Failed to record quiz answer in Study Coach" in r.message for r in caplog.records)


def test_w6_chat_send_coach_on_writes_user_message_signal(
    user_tracker, notebook_tracker, mock_gemini_client
):
    """W6: Topic chat with coach on creates coach_events/cs_{user_msg_id} and none for assistant."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb_id)

    db = get_db()
    db.document(user_path(uid)).set({"study_coach": True})
    _setup_test_notebook(nb_id, uid)
    _setup_topic(nb_id, "t1")
    _seed_chunks(nb_id, "src_1", ["Probability theory is the study of random events."])

    # Create topic chat
    c_res = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"topic_id": "t1"},
    )
    assert c_res.status_code == 201
    chat_id = c_res.json()["id"]

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Probability explanation", "sources": [1]}]})
    )

    send_res = client.post(
        f"/v1/notebooks/{nb_id}/chats/{chat_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Can you explain probability?"},
    )
    assert send_res.status_code == 200
    data = send_res.json()
    user_msg_id = data["user_message"]["id"]
    asst_msg_id = data["assistant_message"]["id"]

    # Check coach event doc for user message
    user_ev_snap = db.document(coach_event_path(nb_id, uid, f"cs_{user_msg_id}")).get()
    assert user_ev_snap.exists
    user_ev_data = user_ev_snap.to_dict() or {}
    assert user_ev_data["kind"] == "chat_signal"
    assert user_ev_data["topic_id"] == "t1"
    assert user_ev_data["value"] == 1.0

    # Check NO coach event for assistant message
    asst_ev_snap = db.document(coach_event_path(nb_id, uid, f"cs_{asst_msg_id}")).get()
    assert not asst_ev_snap.exists


def test_w7_chat_send_whole_notebook_no_writes(
    user_tracker, notebook_tracker, mock_gemini_client, monkeypatch
):
    """W7: Chat with topic_id=None does zero coach reads and writes."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb_id)

    db = get_db()
    db.document(user_path(uid)).set({"study_coach": True})
    _setup_test_notebook(nb_id, uid)
    _seed_chunks(nb_id, "src_1", ["Some notes about the course."])

    c_res = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert c_res.status_code == 201
    chat_id = c_res.json()["id"]

    import app.api.chats as chats_module

    spy_enabled_calls = []
    spy_storage_calls = []

    monkeypatch.setattr(
        chats_module, "get_study_coach_enabled", lambda u: spy_enabled_calls.append(u)
    )
    monkeypatch.setattr(
        chats_module, "coach_storage", lambda *a, **kw: spy_storage_calls.append(a)
    )

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Notebook summary", "sources": [1]}]})
    )

    send_res = client.post(
        f"/v1/notebooks/{nb_id}/chats/{chat_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Hello notebook"},
    )
    assert send_res.status_code == 200

    assert len(spy_enabled_calls) == 0
    assert len(spy_storage_calls) == 0
    assert len(list(db.collection(coach_events_collection_path(nb_id, uid)).stream())) == 0


def test_w8_chat_send_coach_off_no_writes(
    user_tracker, notebook_tracker, mock_gemini_client, monkeypatch
):
    """W8: Topic chat with study_coach: False writes no coach events and costs 1 user read."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb_id)

    db = get_db()
    db.document(user_path(uid)).set({"study_coach": False})
    _setup_test_notebook(nb_id, uid)
    _setup_topic(nb_id, "t1")
    _seed_chunks(nb_id, "src_1", ["Probability notes."])

    c_res = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"topic_id": "t1"},
    )
    assert c_res.status_code == 201
    chat_id = c_res.json()["id"]

    import app.api.chats as chats_module

    spy_enabled_calls = []
    orig_enabled = chats_module.get_study_coach_enabled

    def spy_enabled(u):
        spy_enabled_calls.append(u)
        return orig_enabled(u)

    spy_storage_calls = []

    def spy_storage(*args, **kwargs):
        spy_storage_calls.append((args, kwargs))
        return chats_module.coach_storage(*args, **kwargs)

    monkeypatch.setattr(chats_module, "get_study_coach_enabled", spy_enabled)
    monkeypatch.setattr(chats_module, "coach_storage", spy_storage)

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Probability answer", "sources": [1]}]})
    )

    send_res = client.post(
        f"/v1/notebooks/{nb_id}/chats/{chat_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Question on probability"},
    )
    assert send_res.status_code == 200

    assert len(spy_enabled_calls) == 1
    assert len(spy_storage_calls) == 0
    assert len(list(db.collection(coach_events_collection_path(nb_id, uid)).stream())) == 0


def test_w9_chat_send_coach_error_logs_and_returns_200(
    user_tracker, notebook_tracker, mock_gemini_client, monkeypatch, caplog
):
    """W9: Coach exception during chat is caught and logged; chat returns 200."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb_id)

    db = get_db()
    db.document(user_path(uid)).set({"study_coach": True})
    _setup_test_notebook(nb_id, uid)
    _setup_topic(nb_id, "t1")
    _seed_chunks(nb_id, "src_1", ["Probability notes."])

    c_res = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"topic_id": "t1"},
    )
    assert c_res.status_code == 201
    chat_id = c_res.json()["id"]

    import app.api.chats as chats_module

    def fail_record_chat(*args, **kwargs):
        raise RuntimeError("Injected record_chat_signal failure")

    monkeypatch.setattr(chats_module, "record_chat_signal", fail_record_chat)

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Probability answer", "sources": [1]}]})
    )

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        send_res = client.post(
            f"/v1/notebooks/{nb_id}/chats/{chat_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": "Question on probability"},
        )
    assert send_res.status_code == 200
    data = send_res.json()
    user_msg_id = data["user_message"]["id"]
    asst_msg_id = data["assistant_message"]["id"]

    # Check both messages were saved in chat collection
    user_doc = db.document(f"{messages_collection_path(nb_id, uid, chat_id)}/{user_msg_id}").get()
    asst_doc = db.document(f"{messages_collection_path(nb_id, uid, chat_id)}/{asst_msg_id}").get()
    assert user_doc.exists
    assert asst_doc.exists

    # Check ERROR was logged
    assert any("Failed to record chat signal in Study Coach" in r.message for r in caplog.records)
