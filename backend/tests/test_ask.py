import json
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from google.cloud.firestore_v1.vector import Vector
from google.genai import errors
from tenacity import wait_none

from app.config import get_settings
from app.db import chunk_path, get_db, llm_cache_path, notebook_path
from app.db.notebooks import DEMO_NOTEBOOK_ID
from app.embeddings import embed_passages
from app.llm import generate
from app.main import app
from tests.conftest import create_emulator_user

client = TestClient(app)


@pytest.fixture
def cache_tracker():
    """Take a snapshot of llm_cache document IDs at setup, and at teardown delete only new IDs."""
    db = get_db()
    before_ids = {doc.id for doc in db.collection("llm_cache").stream()}
    yield before_ids
    after_ids = {doc.id for doc in db.collection("llm_cache").stream()}
    for doc_id in (after_ids - before_ids):
        try:
            db.document(llm_cache_path(doc_id)).delete()
        except Exception:
            pass


def create_test_notebook(token: str) -> str:
    """Helper to create a fresh notebook."""
    res = client.post(
        "/v1/notebooks",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": f"Ask Test NB {uuid4().hex[:6]}"},
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
    summary = data.get("sources_summary", [])
    summary.append({
        "source_id": source_id,
        "ref_n": len(summary) + 1,
        "title": title,
        "kind": "pdf",
        "status": status,
    })
    counts = data.get("counts", {})
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


def test_ask_exact_operation_id():
    """Verify operationId is exactly ask_post in OpenAPI schema."""
    res = client.get("/openapi.json")
    assert res.status_code == 200
    schema = res.json()
    assert schema["paths"]["/v1/notebooks/{nb}/ask"]["post"]["operationId"] == "ask_post"


def test_ask_happy_path(user_tracker, notebook_tracker, cache_tracker, mock_gemini_client):
    """Seed ready source with chunks; verify answer, citations, context, model, latency."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    chunks_text = [
        "Sample spaces and events form the foundation of probability theory.",
        "Conditional probability is defined as P(A|B) = P(A and B) / P(B).",
        "Independent events satisfy P(A and B) = P(A) * P(B) exactly.",
    ]
    chunk_ids = seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Probability Fundamentals",
        chunks_text=chunks_text,
    )

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({
            "paragraphs": [
                {
                    "text": "Conditional probability builds on sample spaces and independence.",
                    "sources": [1, 3],
                }
            ]
        })
    )

    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"How does conditional probability relate {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["model"] == get_settings().gemini_model
    assert data["latency_ms"] >= 1
    assert len(data["paragraphs"]) == 1
    assert len(data["context"]) == 3

    p0 = data["paragraphs"][0]
    assert p0["id"] == "p1"
    assert p0["section"] is None
    assert p0["outside_course"] is False
    assert len(p0["citations"]) == 2

    # Verify Citation 1 (mapped to chunk [1] -> context[0])
    c1 = p0["citations"][0]
    assert c1["chunk_id"] == data["context"][0]["chunk_id"]
    page1 = data["context"][0]["loc"]["page"]
    assert c1["label"] == f"Probability Fundamentals p. {page1}"
    assert c1["open"]["kind"] == "pdf"
    assert c1["open"]["source_id"] == src_id
    assert c1["open"]["page"] == page1

    # Verify Citation 2 (mapped to chunk [3] -> context[2])
    c2 = p0["citations"][1]
    assert c2["chunk_id"] == data["context"][2]["chunk_id"]
    page3 = data["context"][2]["loc"]["page"]
    assert c2["label"] == f"Probability Fundamentals p. {page3}"
    assert c2["open"]["kind"] == "pdf"
    assert c2["open"]["source_id"] == src_id
    assert c2["open"]["page"] == page3

    # Verify Context chunks
    ctx_ids = [c["chunk_id"] for c in data["context"]]
    assert set(ctx_ids) == set(chunk_ids)
    for c in data["context"]:
        assert "score" in c
        assert "text" in c
        assert "loc" in c


def test_ask_citation_mapping_and_deduplication(
    user_tracker, notebook_tracker, cache_tracker, mock_gemini_client
):
    """Verify duplicate and out-of-bounds sources dropped; empty sources -> outside_course True."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Dedup Source",
        chunks_text=["First chunk content."],
    )

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({
            "paragraphs": [
                {"text": "Paragraph one text.", "sources": [1, 1, 999, 0]},
                {"text": "Paragraph two text from general knowledge.", "sources": []},
            ]
        })
    )

    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"Test question for deduplication {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["paragraphs"]) == 2

    # Paragraph 1 kept only single valid source 1
    p1 = data["paragraphs"][0]
    assert len(p1["citations"]) == 1
    assert p1["citations"][0]["open"]["page"] == 1
    assert p1["outside_course"] is False

    # Paragraph 2 has no citations -> marked outside_course True
    p2 = data["paragraphs"][1]
    assert len(p2["citations"]) == 0
    assert p2["outside_course"] is True


def test_ask_excludes_processing_source_chunks(
    user_tracker, notebook_tracker, cache_tracker, mock_gemini_client
):
    """Verify chunks from sources with status 'processing' never appear in context."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_ready = f"src_r_{uuid4().hex[:6]}"
    src_proc = f"src_p_{uuid4().hex[:6]}"
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_ready,
        title="Ready Source",
        chunks_text=["Ready content available."],
        status="ready",
    )
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_proc,
        title="Processing Source",
        chunks_text=["Processing content should not appear."],
        status="processing",
    )

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Answer text.", "sources": [1]}]})
    )

    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"What content is available {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 200
    data = res.json()

    # Context must only contain chunks from src_ready
    for c in data["context"]:
        assert src_ready in c["chunk_id"]
        assert src_proc not in c["chunk_id"]


def test_ask_auth_and_access_errors(
    user_tracker, notebook_tracker, demo_notebook_context, cache_tracker, mock_gemini_client
):
    """Test 401 without token, 404 for foreign notebook, 200 for demo notebook, 409 not ready."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid1, token1 = create_emulator_user()
    uid2, token2 = create_emulator_user()
    user_tracker.extend([uid1, uid2])

    nb1 = create_test_notebook(token1)
    notebook_tracker.append(nb1)

    # 401 unauthenticated
    res = client.post(f"/v1/notebooks/{nb1}/ask", json={"question": "Hello?"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthenticated"

    # 404 not found for another user's notebook
    res = client.post(
        f"/v1/notebooks/{nb1}/ask",
        headers={"Authorization": f"Bearer {token2}"},
        json={"question": "Hello?"},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"

    # 409 not_ready when no source is ready
    res = client.post(
        f"/v1/notebooks/{nb1}/ask",
        headers={"Authorization": f"Bearer {token1}"},
        json={"question": "Hello?"},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "not_ready"
    assert res.json()["error"]["message"] == "This notebook has no processed sources yet."

    # Demo notebook (nb_demo_6041) answers User 2
    db = get_db()
    demo_ref = db.document(notebook_path(DEMO_NOTEBOOK_ID))
    demo_ref.set({
        "name": "MIT OCW 6.041 Demo",
        "owner_uid": "system",
        "is_demo": True,
        "status": "ready",
        "sources_summary": [{
            "source_id": "demo_src",
            "ref_n": 1,
            "title": "Demo Textbook",
            "kind": "pdf",
            "status": "ready",
        }],
        "counts": {"chunks": 1, "items": 0, "questions_verified": 0},
        "created_at": datetime.now(UTC),
    })

    demo_chunk_id = "demo_src-00000"
    db.document(chunk_path(DEMO_NOTEBOOK_ID, demo_chunk_id)).set({
        "source_id": "demo_src",
        "kind": "text",
        "text": "Demo content on probability theory.",
        "loc": {"source_id": "demo_src", "page": 1, "page_label": None, "bbox": None},
        "topic_id": None,
        "embedding": Vector(embed_passages(["Demo content on probability theory."])[0]),
        "token_count": 5,
        "image_path": None,
    })

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Demo answer.", "sources": [1]}]})
    )

    try:
        res = client.post(
            f"/v1/notebooks/{DEMO_NOTEBOOK_ID}/ask",
            headers={"Authorization": f"Bearer {token2}"},
            json={"question": f"What is covered in demo {uuid4().hex[:6]}?"},
        )
        assert res.status_code == 200
        assert len(res.json()["paragraphs"]) == 1
    finally:
        # Clean up chunk seeded under demo notebook
        try:
            db.document(chunk_path(DEMO_NOTEBOOK_ID, demo_chunk_id)).delete()
        except Exception:
            pass


def test_ask_validation_errors(user_tracker, notebook_tracker):
    """Test 422 for empty question, 2001 chars, unknown field, and pasted_images."""
    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    # Empty question
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "   "},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid"

    # 2001 character question
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "a" * 2001},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid"

    # Unknown field rejected
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "Valid question?", "unsupported_field": 123},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid"

    # Non-empty pasted_images rejected
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "Valid question?", "pasted_images": ["users/u/pasted/1.png"]},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid"
    assert res.json()["error"]["message"] == "Pasted images aren't supported yet."


def test_ask_cache_hit_and_immutability(
    user_tracker, notebook_tracker, cache_tracker, mock_gemini_client
):
    """Verify cache hit skips Gemini, writes nothing; allow_outside changes cache key."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Cache Source",
        chunks_text=["Sample text for caching test."],
    )

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Cached response.", "sources": [1]}]})
    )

    db = get_db()
    nb_doc_before = db.document(notebook_path(nb_id)).get()
    nb_dict_before = nb_doc_before.to_dict()

    q_text = f"Cache question test {uuid4().hex[:6]}?"

    # Call 1: Cache miss
    res1 = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": q_text, "allow_outside": False},
    )
    assert res1.status_code == 200
    assert mock_gemini_client.models.generate_content.call_count == 1

    # Find created cache doc from snapshot diff
    cache_docs = list(db.collection("llm_cache").stream())
    new_docs = [d for d in cache_docs if d.id not in cache_tracker]
    assert len(new_docs) == 1
    created_doc = new_docs[0]
    cache_update_time_1 = created_doc.update_time

    # Call 2: Exact same question and params -> Cache hit
    res2 = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": q_text, "allow_outside": False},
    )
    assert res2.status_code == 200
    # Mock must NOT have been called again
    assert mock_gemini_client.models.generate_content.call_count == 1

    cache_doc_after = db.document(llm_cache_path(created_doc.id)).get()
    # Cache document was not re-written
    assert cache_doc_after.update_time == cache_update_time_1

    # Call 3: Different allow_outside -> Cache miss (calls mock again)
    res3 = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": q_text, "allow_outside": True},
    )
    assert res3.status_code == 200
    assert mock_gemini_client.models.generate_content.call_count == 2

    # Teardown of cache_tracker deletes newly added cache docs

    # Immutability: notebook doc unchanged
    nb_doc_after = db.document(notebook_path(nb_id)).get()
    nb_dict_after = nb_doc_after.to_dict()
    assert nb_dict_after["sources_summary"] == nb_dict_before["sources_summary"]
    assert nb_dict_after["counts"] == nb_dict_before["counts"]

    # Statelessness: no documents written to members collection
    members_docs = list(db.collection(f"notebooks/{nb_id}/members").stream())
    assert len(members_docs) == 0


def test_ask_error_handling_and_retries(
    user_tracker, notebook_tracker, cache_tracker, mock_gemini_client
):
    """Verify 503 retry, 400 no retry, 429 quota exhausted with retryDelay, 500 envelope."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    seed_notebook_source_and_chunks(
        nb_id=nb_id,
        source_id=src_id,
        title="Error Test Source",
        chunks_text=["Content for error test."],
    )

    # 1. 503 ServerError on attempt 1, then success on attempt 2
    mock_gemini_client.models.generate_content.side_effect = [
        errors.ServerError(503, {"error": {"message": "Service unavailable"}}),
        MagicMock(text=json.dumps({"paragraphs": [{"text": "Recovered text.", "sources": [1]}]})),
    ]
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"Retry question 1 {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 200
    assert mock_gemini_client.models.generate_content.call_count == 2

    # 2. 400 ClientError: never retried
    mock_gemini_client.models.generate_content.side_effect = errors.ClientError(
        400, {"error": {"message": "Bad request"}}
    )
    mock_gemini_client.models.generate_content.reset_mock()
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"Retry question 2 {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 500  # unhandled client error turns into 500
    assert mock_gemini_client.models.generate_content.call_count == 1

    # 3. 429 ClientError with RetryInfo retryDelay="37s"
    mock_gemini_client.models.generate_content.side_effect = errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Resource exhausted",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "37s",
                    }
                ],
            }
        },
    )
    mock_gemini_client.models.generate_content.reset_mock()
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"Retry question 3 {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 429
    assert res.headers.get("Retry-After") == "37"
    err_body = res.json()["error"]
    assert err_body["code"] == "quota_exhausted"
    assert err_body["retry_after_s"] == 37
    assert "Try again in 37 seconds." in err_body["message"]
    assert mock_gemini_client.models.generate_content.call_count == 1  # Never retried

    # 4. 429 ClientError with fractional retryDelay="1.5s" -> rounded up to 2
    mock_gemini_client.models.generate_content.side_effect = errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Resource exhausted",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "1.5s",
                    }
                ],
            }
        },
    )
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"Retry question 4 {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 429
    assert res.headers.get("Retry-After") == "2"
    assert res.json()["error"]["retry_after_s"] == 2

    # 5. 429 ClientError without RetryInfo -> default 60s
    mock_gemini_client.models.generate_content.side_effect = errors.ClientError(
        429, {"error": {"code": 429, "message": "Resource exhausted"}}
    )
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"Retry question 5 {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 429
    assert res.headers.get("Retry-After") == "60"
    assert res.json()["error"]["retry_after_s"] == 60

    # 6. Unreadable output twice -> 500 envelope with exact message
    mock_gemini_client.models.generate_content.side_effect = [
        MagicMock(text="not json output"),
        MagicMock(text="{bad json}"),
    ]
    mock_gemini_client.models.generate_content.reset_mock()
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"Retry question 6 {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 500
    assert mock_gemini_client.models.generate_content.call_count == 2
    err_body = res.json()["error"]
    assert err_body["code"] == "internal_error"
    assert err_body["message"] == "The AI model returned an unreadable answer. Please try again."

    # 7. 503 ServerError three times in a row -> 503 unavailable with Retry-After: 30
    mock_gemini_client.models.generate_content.side_effect = errors.ServerError(
        503, {"error": {"message": "Service unavailable"}}
    )
    mock_gemini_client.models.generate_content.reset_mock()
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"Retry question 7 {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 503
    assert res.headers.get("Retry-After") == "30"
    err_body = res.json()["error"]
    assert err_body["code"] == "unavailable"
    assert err_body["message"] == "The AI model is busy right now. Please try again in a minute."
    assert mock_gemini_client.models.generate_content.call_count == 3

    # 8. response.text is None twice -> 500 unreadable message
    mock_gemini_client.models.generate_content.side_effect = [
        MagicMock(text=None),
        MagicMock(text=None),
    ]
    mock_gemini_client.models.generate_content.reset_mock()
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"Retry question 8 {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 500
    assert mock_gemini_client.models.generate_content.call_count == 2
    err_body = res.json()["error"]
    assert err_body["code"] == "internal_error"
    assert err_body["message"] == "The AI model returned an unreadable answer. Please try again."

    # 9. Valid JSON that fails schema twice -> 500 unreadable message
    mock_gemini_client.models.generate_content.side_effect = [
        MagicMock(text=json.dumps({"paragraphs": [{"text": "Hello", "sources": "not_a_list"}]})),
        MagicMock(text=json.dumps({"paragraphs": "not_a_list"})),
    ]
    mock_gemini_client.models.generate_content.reset_mock()
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"Retry question 9 {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 500
    assert mock_gemini_client.models.generate_content.call_count == 2
    err_body = res.json()["error"]
    assert err_body["code"] == "internal_error"
    assert err_body["message"] == "The AI model returned an unreadable answer. Please try again."

    # 10. httpx.TimeoutException then success -> 200 with two calls
    mock_gemini_client.models.generate_content.side_effect = [
        httpx.TimeoutException("Read timed out"),
        MagicMock(
            text=json.dumps({"paragraphs": [{"text": "Timeout recovered text.", "sources": [1]}]})
        ),
    ]
    mock_gemini_client.models.generate_content.reset_mock()
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"Retry question 10 {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 200
    assert mock_gemini_client.models.generate_content.call_count == 2

    # 11. httpx.ConnectError then success -> 200 with two calls
    mock_gemini_client.models.generate_content.side_effect = [
        httpx.ConnectError("Connection refused"),
        MagicMock(
            text=json.dumps({"paragraphs": [{"text": "Connect recovered text.", "sources": [1]}]})
        ),
    ]
    mock_gemini_client.models.generate_content.reset_mock()
    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"Retry question 11 {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 200
    assert mock_gemini_client.models.generate_content.call_count == 2


def test_ask_chunk_without_page_produces_no_citation(
    user_tracker, notebook_tracker, cache_tracker, mock_gemini_client
):
    """Verify that a chunk with loc.page = None produces no citation."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id = f"src_{uuid4().hex[:6]}"
    db = get_db()
    nb_ref = db.document(notebook_path(nb_id))
    nb_ref.update({
        "sources_summary": [{
            "source_id": src_id,
            "ref_n": 1,
            "title": "Pageless Source",
            "kind": "pdf",
            "status": "ready",
        }],
        "status": "ready",
        "counts": {"chunks": 1},
    })
    c_id = f"{src_id}-00000"
    db.document(chunk_path(nb_id, c_id)).set({
        "source_id": src_id,
        "kind": "text",
        "text": "Chunk text without page.",
        "loc": {
            "source_id": src_id,
            "page": None,
            "page_label": None,
            "bbox": None,
        },
        "topic_id": None,
        "embedding": Vector(embed_passages(["Chunk text without page."])[0]),
        "token_count": 5,
        "image_path": None,
    })

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({
            "paragraphs": [
                {"text": "Paragraph citing pageless chunk.", "sources": [1]},
            ]
        })
    )

    res = client.post(
        f"/v1/notebooks/{nb_id}/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": f"Question pageless {uuid4().hex[:6]}?"},
    )
    assert res.status_code == 200
    p = res.json()["paragraphs"][0]
    assert len(p["citations"]) == 0
    assert p["outside_course"] is True
