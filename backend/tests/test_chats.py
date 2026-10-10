import inspect
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
from google.cloud.firestore_v1.vector import Vector
from google.genai import errors
from tenacity import wait_none

from app.api import chats
from app.config import get_settings
from app.db import chunk_path, get_db, notebook_path, topic_path, user_path
from app.db.chats import get_message_snapshot
from app.db.members import get_member_snapshot, member_path
from app.db.paths import (
    chat_path,
    messages_collection_path,
)
from app.embeddings import embed_passages
from app.llm import generate
from app.main import app
from tests.conftest import create_emulator_user

client = TestClient(app)


def create_test_notebook(token: str) -> str:
    """Helper to create a fresh notebook."""
    res = client.post(
        "/v1/notebooks",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": f"Chat Test NB {uuid4().hex[:6]}"},
    )
    assert res.status_code == 201
    return res.json()["id"]


def seed_notebook_source_and_chunks(
    nb_id: str,
    source_id: str,
    title: str,
    chunks_text: list[str],
    status: str = "ready",
) -> list[str]:
    """Seed notebook with source metadata and embedded chunks in Firestore."""
    db = get_db()
    nb_ref = db.document(notebook_path(nb_id))
    nb_doc = nb_ref.get()
    data = nb_doc.to_dict() or {}
    summary = list(data.get("sources_summary", []))
    summary.append({
        "source_id": source_id,
        "ref_n": len(summary) + 1,
        "title": title,
        "kind": "pdf",
        "status": status,
    })
    counts = dict(data.get("counts", {}))
    counts["chunks"] = counts.get("chunks", 0) + len(chunks_text)
    nb_ref.update({
        "sources_summary": summary,
        "status": "ready" if status == "ready" else data.get("status", "empty"),
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


def test_chats_create_with_topic(user_tracker, notebook_tracker):
    """Test 1: Create chat with topic_id defaults name to topic's name and ensures member."""
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    db = get_db()
    db.document(topic_path(nb_id, "t1")).set({
        "name": "Probability Basics",
        "order": 1,
        "summary": "Basics of probability",
        "is_other": False,
        "prerequisite_ids": [],
        "locations": [],
    })

    res = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"topic_id": "t1"},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "Probability Basics"
    assert data["topic_id"] == "t1"
    assert data["message_count"] == 0

    assert get_member_snapshot(nb_id, uid).exists


def test_chats_create_without_topic(user_tracker, notebook_tracker):
    """Test 2: Create chat without topic_id defaults name to 'Whole notebook'."""
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    res = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "Whole notebook"
    assert data["topic_id"] is None
    assert data["message_count"] == 0


def test_chats_create_validation_errors(user_tracker, notebook_tracker):
    """Test 3: Validation errors for topic_id, name, and extra fields."""
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    # topic_id == "t7" -> 422 code invalid
    res = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"topic_id": "t7"},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid"

    # topic_id == "t2\n" -> 422 code invalid
    res = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"topic_id": "t2\n"},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid"

    # topic_id == "t2" when notebook has no topic t2 -> 422 with message
    res = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"topic_id": "t2"},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid"
    assert res.json()["error"]["message"] == "This notebook has no topic t2."

    # name == "a" * 101 -> 422 code invalid
    res = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "a" * 101},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid"

    # name == "   " -> 422 code invalid
    res = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "   "},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid"

    # Extra field {"unknown": 123} -> 422 code invalid
    res = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"unknown": 123},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid"


def test_chats_access_control(user_tracker, notebook_tracker, mock_gemini_client):
    """Test 4: Access control for private vs demo notebooks, cross-user isolation."""
    uid_a, token_a = create_emulator_user()
    uid_b, token_b = create_emulator_user()
    user_tracker.extend([uid_a, uid_b])

    nb_a = create_test_notebook(token_a)
    notebook_tracker.append(nb_a)

    res_a = client.post(
        f"/v1/notebooks/{nb_a}/chats",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Chat A"},
    )
    assert res_a.status_code == 201
    c_a = res_a.json()["id"]

    # User B accessing any chat route on User A's notebook receives 404 (never 403)
    res = client.post(
        f"/v1/notebooks/{nb_a}/chats",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Intruder Chat"},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"

    res = client.get(
        f"/v1/notebooks/{nb_a}/chats",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"

    res = client.get(
        f"/v1/notebooks/{nb_a}/chats/{c_a}/messages",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"

    res = client.post(
        f"/v1/notebooks/{nb_a}/chats/{c_a}/messages",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"text": "Hello"},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"

    # Test notebook with is_demo: True (random ID, never nb_demo_6041)
    db = get_db()
    nb_demo = f"nb_demo_test_{uuid4().hex[:8]}"
    db.document(notebook_path(nb_demo)).set({
        "name": "Demo Notebook",
        "owner_uid": uid_a,
        "is_demo": True,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "status": "ready",
        "sources_summary": [],
        "counts": {"sources": 0, "chunks": 0, "notes": 0},
    })
    notebook_tracker.append(nb_demo)

    # User A creates a chat in demo notebook
    res_a_demo = client.post(
        f"/v1/notebooks/{nb_demo}/chats",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "A Demo Chat"},
    )
    assert res_a_demo.status_code == 201
    c_a_demo = res_a_demo.json()["id"]

    # User B can create a chat in demo notebook
    res_b_demo = client.post(
        f"/v1/notebooks/{nb_demo}/chats",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "B Demo Chat"},
    )
    assert res_b_demo.status_code == 201
    c_b_demo = res_b_demo.json()["id"]

    # User B's chats_list contains only c_b_demo
    res_b_list = client.get(
        f"/v1/notebooks/{nb_demo}/chats",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res_b_list.status_code == 200
    b_chats = res_b_list.json()["items"]
    assert len(b_chats) == 1
    assert b_chats[0]["id"] == c_b_demo

    # User B calling GET / POST to User A's chat receives 404
    res_b_get_msg = client.get(
        f"/v1/notebooks/{nb_demo}/chats/{c_a_demo}/messages",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res_b_get_msg.status_code == 404
    assert res_b_get_msg.json()["error"]["message"] == "Chat not found."

    res_b_post_msg = client.post(
        f"/v1/notebooks/{nb_demo}/chats/{c_a_demo}/messages",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"text": "Attacking user A chat"},
    )
    assert res_b_post_msg.status_code == 404
    assert res_b_post_msg.json()["error"]["message"] == "Chat not found."

    # Fake Gemini check: generate_content was never called
    assert not mock_gemini_client.models.generate_content.called


def test_chats_list_ordering_paging_and_cursor(
    user_tracker, notebook_tracker, cache_tracker, mock_gemini_client
):
    """Test 5: Chats list order by updated_at desc, limit paging, send bumps position,
    bad cursor.
    """
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Prob Title",
        chunks_text=["Probability notes and definitions for test."],
    )

    db = get_db()
    res1 = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "C1"},
    )
    c1 = res1.json()["id"]

    res2 = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "C2"},
    )
    c2 = res2.json()["id"]

    res3 = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "C3"},
    )
    c3 = res3.json()["id"]

    now = datetime.now(UTC)
    db.document(chat_path(nb_id, uid, c1)).update({"updated_at": now - timedelta(seconds=20)})
    db.document(chat_path(nb_id, uid, c2)).update({"updated_at": now - timedelta(seconds=10)})
    db.document(chat_path(nb_id, uid, c3)).update({"updated_at": now})

    res_list = client.get(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_list.status_code == 200
    items = res_list.json()["items"]
    assert [i["id"] for i in items] == [c3, c2, c1]

    # Paging limit=1
    p1 = client.get(
        f"/v1/notebooks/{nb_id}/chats?limit=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert p1.status_code == 200
    d1 = p1.json()
    assert [i["id"] for i in d1["items"]] == [c3]
    assert d1["next_cursor"] == c3

    p2 = client.get(
        f"/v1/notebooks/{nb_id}/chats?limit=1&cursor={d1['next_cursor']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert p2.status_code == 200
    d2 = p2.json()
    assert [i["id"] for i in d2["items"]] == [c2]
    assert d2["next_cursor"] == c2

    p3 = client.get(
        f"/v1/notebooks/{nb_id}/chats?limit=1&cursor={d2['next_cursor']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert p3.status_code == 200
    d3 = p3.json()
    assert [i["id"] for i in d3["items"]] == [c1]
    assert d3["next_cursor"] is None

    # Sending to C1 moves it to front
    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Answer for C1", "sources": [1]}]})
    )
    res_send = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c1}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Message to C1"},
    )
    assert res_send.status_code == 200

    res_list_after = client.get(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_list_after.status_code == 200
    assert res_list_after.json()["items"][0]["id"] == c1

    # Invalid cursor format or nonexistent cursor ID -> 422 invalid
    res_bad_fmt = client.get(
        f"/v1/notebooks/{nb_id}/chats?cursor=bad/id",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_bad_fmt.status_code == 422
    assert res_bad_fmt.json()["error"]["code"] == "invalid"

    res_not_found = client.get(
        f"/v1/notebooks/{nb_id}/chats?cursor=nonexistent_cursor_123",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_not_found.status_code == 422
    assert res_not_found.json()["error"]["code"] == "invalid"


def test_chats_send_success_and_seq(
    user_tracker, notebook_tracker, cache_tracker, mock_gemini_client
):
    """Test 6: Send message success, seq counter, invariants, stored context shape."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    chunks_text = ["Sample spaces and events form the foundation of probability theory."]
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Prob Title",
        chunks_text=chunks_text,
    )

    res_c = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Seq Test Chat"},
    )
    assert res_c.status_code == 201
    c_id = res_c.json()["id"]

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({
            "paragraphs": [{"text": "A sample space is all outcomes.", "sources": [1]}]
        })
    )

    # First send
    res1 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "What is sample space?"},
    )
    assert res1.status_code == 200
    d1 = res1.json()

    user_msg_1 = d1["user_message"]
    asst_msg_1 = d1["assistant_message"]
    assert d1["model"] == get_settings().gemini_model
    assert d1["latency_ms"] >= 1
    assert user_msg_1["created_at"] <= asst_msg_1["created_at"]

    db = get_db()
    u1_snap = get_message_snapshot(nb_id, uid, c_id, user_msg_1["id"])
    a1_snap = get_message_snapshot(nb_id, uid, c_id, asst_msg_1["id"])
    assert u1_snap.exists
    assert a1_snap.exists

    u1_data = u1_snap.to_dict()
    a1_data = a1_snap.to_dict()
    assert u1_data["seq"] == 1
    assert u1_data["role"] == "user"
    assert u1_data["text"] == "What is sample space?"
    assert a1_data["seq"] == 2
    assert a1_data["role"] == "assistant"
    assert len(a1_data["paragraphs"]) == 1
    assert len(a1_data["paragraphs"][0]["citations"]) == 1

    for ctx_item in a1_data["context"]:
        assert "chunk_id" in ctx_item
        assert "score" in ctx_item
        assert "text" not in ctx_item
        assert "loc" not in ctx_item

    assert u1_data["created_at"] <= a1_data["created_at"]

    chat_snap1 = db.document(chat_path(nb_id, uid, c_id)).get()
    assert chat_snap1.to_dict()["message_count"] == 2

    # Second send
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "An event is a subset.", "sources": [1]}]})
    )
    res2 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "And what is an event?"},
    )
    assert res2.status_code == 200
    d2 = res2.json()

    user_msg_2 = d2["user_message"]
    asst_msg_2 = d2["assistant_message"]
    u2_snap = get_message_snapshot(nb_id, uid, c_id, user_msg_2["id"])
    a2_snap = get_message_snapshot(nb_id, uid, c_id, asst_msg_2["id"])
    assert u2_snap.to_dict()["seq"] == 3
    assert a2_snap.to_dict()["seq"] == 4

    chat_snap2 = db.document(chat_path(nb_id, uid, c_id)).get()
    assert chat_snap2.to_dict()["message_count"] == 4


def test_chats_list_messages_paging_and_order(
    user_tracker, notebook_tracker, cache_tracker, mock_gemini_client
):
    """Test 7: List messages chronological order, reverse paging with limit=1, cross-chat cursor."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Prob Title",
        chunks_text=["Sample spaces and events."],
    )

    res_c = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "History Chat"},
    )
    c_id = res_c.json()["id"]

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Answer 1", "sources": [1]}]})
    )
    r1 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Question 1"},
    )
    assert r1.status_code == 200

    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Answer 2", "sources": [1]}]})
    )
    r2 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Question 2"},
    )
    assert r2.status_code == 200

    res_all = client.get(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_all.status_code == 200
    all_msgs = res_all.json()["items"]
    assert len(all_msgs) == 4
    assert [m["role"] for m in all_msgs] == ["user", "assistant", "user", "assistant"]
    assert [m["text"] for m in all_msgs] == ["Question 1", None, "Question 2", None]
    for m in all_msgs:
        assert m["context"] is None
        assert "seq" not in m

    msg1_id, msg2_id, msg3_id, msg4_id = [m["id"] for m in all_msgs]

    p1 = client.get(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages?limit=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert p1.status_code == 200
    d1 = p1.json()
    assert [m["id"] for m in d1["items"]] == [msg4_id]
    assert d1["next_cursor"] == msg4_id

    p2 = client.get(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages?limit=1&cursor={d1['next_cursor']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert p2.status_code == 200
    d2 = p2.json()
    assert [m["id"] for m in d2["items"]] == [msg3_id]
    assert d2["next_cursor"] == msg3_id

    p3 = client.get(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages?limit=1&cursor={d2['next_cursor']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert p3.status_code == 200
    d3 = p3.json()
    assert [m["id"] for m in d3["items"]] == [msg2_id]
    assert d3["next_cursor"] == msg2_id

    p4 = client.get(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages?limit=1&cursor={d3['next_cursor']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert p4.status_code == 200
    d4 = p4.json()
    assert [m["id"] for m in d4["items"]] == [msg1_id]
    assert d4["next_cursor"] is None

    joined = d4["items"] + d3["items"] + d2["items"] + d1["items"]
    assert [m["id"] for m in joined] == [m["id"] for m in all_msgs]

    res_bad_c = client.get(
        f"/v1/notebooks/{nb_id}/chats/nonexistent_chat/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_bad_c.status_code == 404

    res_bad_cur = client.get(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages?cursor=bad/id",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_bad_cur.status_code == 422

    # Cross-chat cursor test
    res_c2 = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Second Chat"},
    )
    c2_id = res_c2.json()["id"]
    r_c2_msg = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c2_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Message in Chat 2"},
    )
    c2_msg_id = r_c2_msg.json()["user_message"]["id"]

    res_cross = client.get(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages?cursor={c2_msg_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_cross.status_code == 422
    assert res_cross.json()["error"]["code"] == "invalid"
    assert res_cross.json()["error"]["message"] == "Cursor message not found."


def test_chats_send_failures_save_nothing(
    user_tracker, notebook_tracker, cache_tracker, mock_gemini_client
):
    """Test 8: Failures (409 not_ready, 429 quota, 404 not_found) write zero messages."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    # 1. Notebook with NO ready sources: returns 409 not_ready
    res_c1 = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Not Ready Chat"},
    )
    c1_id = res_c1.json()["id"]

    res_409 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c1_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Hello not ready"},
    )
    assert res_409.status_code == 409
    assert res_409.json()["error"]["code"] == "not_ready"

    db = get_db()
    c1_doc = db.document(chat_path(nb_id, uid, c1_id)).get()
    assert c1_doc.to_dict()["message_count"] == 0
    msgs1 = list(db.collection(messages_collection_path(nb_id, uid, c1_id)).stream())
    assert len(msgs1) == 0

    # Seed ready sources now
    src_id = f"src_{uuid4().hex[:6]}"
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Prob Title",
        chunks_text=["Ready chunk text."],
    )

    # 2. Gemini client raises SDK 429: returns 429 quota_exhausted
    mock_gemini_client.models.generate_content.side_effect = errors.ClientError(
        429,
        {"error": {"code": 429, "message": "Resource exhausted", "status": "RESOURCE_EXHAUSTED"}},
    )
    res_429 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c1_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Hello 429"},
    )
    assert res_429.status_code == 429
    assert res_429.json()["error"]["code"] == "quota_exhausted"

    c1_doc_after_429 = db.document(chat_path(nb_id, uid, c1_id)).get()
    assert c1_doc_after_429.to_dict()["message_count"] == 0
    msgs1_after = list(db.collection(messages_collection_path(nb_id, uid, c1_id)).stream())
    assert len(msgs1_after) == 0

    # 3. Unknown chat ID: returns 404 "Chat not found." with fake client never called
    mock_gemini_client.models.generate_content.reset_mock()
    res_404 = client.post(
        f"/v1/notebooks/{nb_id}/chats/nonexistent_chat_xyz/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Hello 404"},
    )
    assert res_404.status_code == 404
    assert res_404.json()["error"]["message"] == "Chat not found."
    assert not mock_gemini_client.models.generate_content.called


def test_chats_send_validation_errors(user_tracker, notebook_tracker):
    """Test 9: Whitespace text, 2001 chars, reply_to rejected with 422."""
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    res_c = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    c_id = res_c.json()["id"]

    res_empty = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "   "},
    )
    assert res_empty.status_code == 422
    assert res_empty.json()["error"]["code"] == "invalid"

    res_long = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "a" * 2001},
    )
    assert res_long.status_code == 422
    assert res_long.json()["error"]["code"] == "invalid"

    res_reply_to = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Valid question", "reply_to": "m1"},
    )
    assert res_reply_to.status_code == 422
    assert res_reply_to.json()["error"]["code"] == "invalid"


def test_chats_send_cache_hit(
    user_tracker, notebook_tracker, cache_tracker, mock_gemini_client
):
    """Test 10: Repeated message hits cache, skips fake Gemini call."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Prob Title",
        chunks_text=["Cache test chunk content."],
    )

    res_c = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Cache Chat"},
    )
    c_id = res_c.json()["id"]

    question_text = f"Identical question {uuid4().hex[:8]}"

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Cached response text", "sources": [1]}]})
    )

    res1 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": question_text},
    )
    assert res1.status_code == 200
    assert mock_gemini_client.models.generate_content.call_count == 1

    res_c2 = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Cache Chat 2"},
    )
    c2_id = res_c2.json()["id"]

    res2 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c2_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": question_text},
    )
    assert res2.status_code == 200
    # Fresh chat Turn 1 with no history hits cache:
    assert mock_gemini_client.models.generate_content.call_count == 1
    assert (
        res1.json()["assistant_message"]["paragraphs"]
        == res2.json()["assistant_message"]["paragraphs"]
    )

    # Turn 2 to c_id has conversation history, causing a cache miss:
    res3 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": question_text},
    )
    assert res3.status_code == 200
    assert mock_gemini_client.models.generate_content.call_count == 2


def test_cleanup_ask_forbids_pasted_images_and_refs_files(
    user_tracker, notebook_tracker, cache_tracker, mock_gemini_client
):
    """Test 11: /ask forbids pasted_images and refs.files with 422; refs.sources succeeds."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Prob Title",
        chunks_text=["Ask cleanup chunk."],
    )

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Answer for ask", "sources": [1]}]})
    )

    r_img = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "What is ask?", "pasted_images": ["p.png"]},
    )
    assert r_img.status_code == 422
    assert r_img.json()["error"]["code"] == "invalid"

    r_files = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "What is ask?", "refs": {"files": ["f.pdf"]}},
    )
    assert r_files.status_code == 422
    assert r_files.json()["error"]["code"] == "invalid"

    r_ok = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "What is ask?", "refs": {"sources": ["src_1"]}},
    )
    assert r_ok.status_code == 200


def test_chats_operation_ids_and_plain_def():
    """Test 12: Verify OpenAPI operation IDs and that route handlers are synchronous def."""
    res = client.get("/openapi.json")
    assert res.status_code == 200
    schema = res.json()
    paths = schema["paths"]

    assert paths["/v1/notebooks/{nb}/chats"]["post"]["operationId"] == "chats_create"
    assert paths["/v1/notebooks/{nb}/chats"]["get"]["operationId"] == "chats_list"
    assert (
        paths["/v1/notebooks/{nb}/chats/{c}/messages"]["get"]["operationId"]
        == "chats_list_messages"
    )
    assert (
        paths["/v1/notebooks/{nb}/chats/{c}/messages"]["post"]["operationId"]
        == "chats_send"
    )

    assert not inspect.iscoroutinefunction(chats.create_chat)
    assert not inspect.iscoroutinefunction(chats.list_chats)
    assert not inspect.iscoroutinefunction(chats.list_messages)
    assert not inspect.iscoroutinefunction(chats.send_message)


def test_chats_create_preserves_existing_member(user_tracker, notebook_tracker):
    """Test 13: chats_create preserves existing member document without overwrite."""
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    db = get_db()
    db.document(member_path(nb_id, uid)).set({
        "checked": {"t1": True},
        "seen_question_ids": ["q_keep"],
        "diagnostic": {"status": "not_started", "quiz_id": None},
        "created_at": datetime.now(UTC),
    })

    res = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert res.status_code == 201

    mem_snap = get_member_snapshot(nb_id, uid)
    assert mem_snap.exists
    mem_data = mem_snap.to_dict()
    assert mem_data["seen_question_ids"] == ["q_keep"]
    assert mem_data["checked"] == {"t1": True}


def test_get_recent_chat_messages_limit_and_order(user_tracker, notebook_tracker):
    """4.1 Only last 6 messages, oldest first."""
    from app.db.chats import get_recent_chat_messages

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    res_c = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "History Test Chat"},
    )
    c_id = res_c.json()["id"]

    db = get_db()
    coll = db.collection(messages_collection_path(nb_id, uid, c_id))
    for i in range(1, 11):
        coll.document(f"msg_{i:02d}").set({
            "seq": i,
            "role": "user" if i % 2 == 1 else "assistant",
            "text": f"Message text {i}",
            "created_at": datetime.now(UTC),
        })

    recent = get_recent_chat_messages(nb_id, uid, c_id, limit=6)
    assert len(recent) == 6
    texts = [m["text"] for m in recent]
    assert texts == [f"Message text {i}" for i in range(5, 11)]


def test_chats_send_second_message_conversation_block(
    user_tracker, notebook_tracker, mock_gemini_client
):
    """7.1 Second message contains conversation block with Turn 1."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Bayes Source",
        chunks_text=["Bayes theorem details."],
    )

    res_c = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Chat Turn Test"},
    )
    c_id = res_c.json()["id"]

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Bayes relates probabilities.", "sources": [1]}]})
    )
    res1 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "What is Bayes?"},
    )
    assert res1.status_code == 200

    captured_contents = None

    def fake_gen(*args, **kwargs):
        nonlocal captured_contents
        captured_contents = kwargs.get("contents")
        return MagicMock(
            text=json.dumps({"paragraphs": [{"text": "Explained again.", "sources": [1]}]})
        )

    mock_gemini_client.models.generate_content.side_effect = fake_gen
    res2 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Explain that again"},
    )
    assert res2.status_code == 200
    assert captured_contents is not None

    conv_start = captured_contents.find("<<<CONVERSATION>>>")
    conv_end = captured_contents.find("<<<END CONVERSATION>>>")
    assert conv_start != -1
    assert conv_end != -1

    conv_block = captured_contents[conv_start:conv_end]
    assert "Student: What is Bayes?" in conv_block
    assert "Tutor: Bayes relates probabilities." in conv_block
    assert conv_block.find("Student:") < conv_block.find("Tutor:")
    assert "Explain that again" not in conv_block


def test_chats_send_retrieval_query_second_message(
    user_tracker, notebook_tracker, mock_gemini_client, monkeypatch
):
    """7.2 Retrieval query embedding for second message uses question + previous user text."""
    import sys
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Source",
        chunks_text=["Content."],
    )

    res_c = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Retrieval Query Test"},
    )
    c_id = res_c.json()["id"]

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Answer 1", "sources": [1]}]})
    )
    res1 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "What is Bayes?"},
    )
    assert res1.status_code == 200

    search_mod = sys.modules["app.retrieval.search"]
    orig_embed = search_mod.embed_query
    captured_queries = []

    def spy_embed(q):
        captured_queries.append(q)
        return orig_embed(q)

    monkeypatch.setattr(search_mod, "embed_query", spy_embed)

    res2 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Explain that again"},
    )
    assert res2.status_code == 200
    assert captured_queries
    assert captured_queries[-1] == "Explain that again\nWhat is Bayes?"


def test_chats_topic_id_reaches_retrieval(user_tracker, notebook_tracker, mock_gemini_client):
    """7.3 Chat topic_id reaches retrieval: on-topic chunk comes first in chats_send."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    db = get_db()
    db.document(f"notebooks/{nb_id}/topics/t2").set({"name": "Bayes Topic"})

    res_c = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Topic Chat", "topic_id": "t2"},
    )
    assert res_c.status_code == 201
    c_id = res_c.json()["id"]

    src_id = f"src_{uuid4().hex[:6]}"
    chunk_ids = seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Source",
        chunks_text=["Off-topic chunk.", "On-topic chunk."],
    )
    import math

    import numpy as np
    from google.cloud.firestore_v1.vector import Vector

    from app.embeddings import embed_query

    question = f"Question {uuid4().hex[:6]}"
    q_arr = np.array(embed_query(question), dtype=float)
    q_norm = q_arr / np.linalg.norm(q_arr)

    rnd = np.ones_like(q_norm)
    orth = rnd - np.dot(rnd, q_norm) * q_norm
    orth = orth / np.linalg.norm(orth)

    v_off = (0.80 * q_norm + math.sqrt(1 - 0.80**2) * orth).tolist()
    v_topic = (0.77 * q_norm + math.sqrt(1 - 0.77**2) * orth).tolist()

    db.document(chunk_path(nb_id, chunk_ids[0])).update({
        "embedding": Vector(v_off),
        "topic_id": None,
    })
    db.document(chunk_path(nb_id, chunk_ids[1])).update({
        "embedding": Vector(v_topic),
        "topic_id": "t2",
    })

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Answer", "sources": [1]}]})
    )

    res = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": question},
    )
    assert res.status_code == 200
    ctx = res.json()["assistant_message"]["context"]
    assert ctx[0]["chunk_id"] == chunk_ids[1]
    assert ctx[1]["chunk_id"] == chunk_ids[0]


def test_chats_custom_instructions_in_chat_prompt(
    user_tracker, notebook_tracker, mock_gemini_client
):
    """7.4 Custom instructions reach the chat prompt."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Source",
        chunks_text=["Source content."],
    )

    db = get_db()
    db.document(user_path(uid)).set({
        "format": {"custom_instructions": "Chat concisely and use bullet points"},
    }, merge=True)

    res_c = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Preferences Chat"},
    )
    c_id = res_c.json()["id"]

    captured_contents = None

    def fake_gen(*args, **kwargs):
        nonlocal captured_contents
        captured_contents = kwargs.get("contents")
        return MagicMock(
            text=json.dumps({"paragraphs": [{"text": "Bullet answer", "sources": [1]}]})
        )

    mock_gemini_client.models.generate_content.side_effect = fake_gen
    res = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Give me an explanation"},
    )
    assert res.status_code == 200
    assert captured_contents is not None
    expected_pref = (
        "<<<STUDENT PREFERENCES>>>\n"
        "Chat concisely and use bullet points\n"
        "<<<END STUDENT PREFERENCES>>>"
    )
    assert expected_pref in captured_contents


def test_ask_and_chats_shared_cache(
    user_tracker, notebook_tracker, cache_tracker, mock_gemini_client
):
    """8.1 First chat message hits /ask cache; 8.2 second turn is a cache miss."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Shared Cache Source",
        chunks_text=["Shared cache chunk content."],
    )

    question_text = f"Identical shared question {uuid4().hex[:8]}"
    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Answer from LLM", "sources": [1]}]})
    )

    # 1. Call /ask (populates cache) -> call_count becomes 1
    res_ask = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": question_text},
    )
    assert res_ask.status_code == 200
    assert mock_gemini_client.models.generate_content.call_count == 1

    # 2. Create chat, send same question as Turn 1 (hits /ask cache) -> call_count remains 1
    res_c = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Shared Cache Chat"},
    )
    c_id = res_c.json()["id"]

    res_chat1 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": question_text},
    )
    assert res_chat1.status_code == 200
    assert mock_gemini_client.models.generate_content.call_count == 1

    # 3. Send Turn 2 in that chat: conversation history changes prompt hash,
    # causing a cache miss -> call_count becomes 2
    res_chat2 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": question_text},
    )
    assert res_chat2.status_code == 200
    assert mock_gemini_client.models.generate_content.call_count == 2


def test_chats_send_history_uses_last_six(
    user_tracker, notebook_tracker, mock_gemini_client
):
    """M6: chats_send includes only the last 6 messages in conversation block."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="History Test Source",
        chunks_text=["Sample text for retrieval."],
    )

    res_c = client.post(
        f"/v1/notebooks/{nb_id}/chats",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "History Limit Test Chat"},
    )
    assert res_c.status_code == 201
    c_id = res_c.json()["id"]

    turns = [
        ("first-question-marker", "first-answer-marker"),
        ("second-question-marker", "second-answer-marker"),
        ("third-question-marker", "third-answer-marker"),
        ("fourth-question-marker", "fourth-answer-marker"),
    ]

    for q_text, a_text in turns:
        mock_gemini_client.models.generate_content.side_effect = None
        mock_gemini_client.models.generate_content.return_value = MagicMock(
            text=json.dumps({"paragraphs": [{"text": a_text, "sources": [1]}]})
        )
        res = client.post(
            f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": q_text},
        )
        assert res.status_code == 200

    captured_contents = None

    def fake_gen(*args, **kwargs):
        nonlocal captured_contents
        captured_contents = kwargs.get("contents")
        return MagicMock(
            text=json.dumps({"paragraphs": [{"text": "fifth-answer-marker", "sources": [1]}]})
        )

    mock_gemini_client.models.generate_content.side_effect = fake_gen
    res5 = client.post(
        f"/v1/notebooks/{nb_id}/chats/{c_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "fifth-question-marker"},
    )
    assert res5.status_code == 200
    assert captured_contents is not None

    conv_start = captured_contents.find("<<<CONVERSATION>>>")
    conv_end = captured_contents.find("<<<END CONVERSATION>>>")
    assert conv_start != -1
    assert conv_end != -1

    conv_block = captured_contents[conv_start + len("<<<CONVERSATION>>>"):conv_end]

    assert "second-question-marker" in conv_block
    assert "second-answer-marker" in conv_block
    assert "third-question-marker" in conv_block
    assert "third-answer-marker" in conv_block
    assert "fourth-question-marker" in conv_block
    assert "fourth-answer-marker" in conv_block

    assert "first-question-marker" not in conv_block
    assert "first-answer-marker" not in conv_block

    assert "fifth-question-marker" not in conv_block

    student_lines = [
        line for line in conv_block.splitlines() if line.startswith("Student:")
    ]
    tutor_lines = [
        line for line in conv_block.splitlines() if line.startswith("Tutor:")
    ]
    assert len(student_lines) == 3
    assert len(tutor_lines) == 3
