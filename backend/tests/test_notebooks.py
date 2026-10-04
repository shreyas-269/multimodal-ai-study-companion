from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from firebase_admin import firestore

from app.api.access import get_owned_notebook
from app.auth import AuthenticatedUser
from app.db import get_db, notebook_path
from app.db.notebooks import DEMO_NOTEBOOK_ID
from app.main import app
from tests.conftest import create_emulator_user

client = TestClient(app)


def seed_demo_notebook(owner_uid: str = "seed-owner", name: str = "Demo Course") -> dict[str, Any]:
    """Directly seed nb_demo_6041 in Firestore using Admin SDK."""
    db = get_db()
    data = {
        "name": name,
        "owner_uid": owner_uid,
        "is_demo": True,
        "status": "ready",
        "sources_summary": [],
        "counts": {"chunks": 0, "items": 0, "questions_verified": 0},
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    db.document(notebook_path(DEMO_NOTEBOOK_ID)).set(data)
    return data


def test_notebook_create_no_token():
    response = client.post("/v1/notebooks", json={"name": "Test Notebook"})
    assert response.status_code == 401
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "unauthenticated"


def test_notebook_create_success(user_tracker, notebook_tracker):
    _uid, token = create_emulator_user(email=f"user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(_uid)

    response = client.post(
        "/v1/notebooks",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "  Linear Algebra  "},
    )
    assert response.status_code == 201
    data = response.json()
    notebook_tracker.append(data["id"])

    assert data["name"] == "Linear Algebra"
    assert data["owner_uid"] == _uid
    assert data["is_demo"] is False
    assert data["status"] == "empty"
    assert data["sources_summary"] == []
    assert data["counts"] == {"chunks": 0, "items": 0, "questions_verified": 0}

    # Verify created_at parses as UTC ISO 8601
    dt = datetime.fromisoformat(data["created_at"])
    assert dt.tzinfo in (UTC, None) or dt.utcoffset().total_seconds() == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 101},
        {"name": "Valid Name", "is_demo": True},
        {"name": "Valid Name", "unknown_field": "disallowed"},
    ],
)
def test_notebook_create_validation_errors(payload, user_tracker):
    _uid, token = create_emulator_user(email=f"user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(_uid)

    response = client.post(
        "/v1/notebooks",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == "invalid"


@pytest.mark.parametrize(
    "invalid_id",
    [
        "bad.id",
        "a b",
        "abc\n",
        "x" * 129,
    ],
)
def test_invalid_notebook_id_format_never_touches_firestore(invalid_id, monkeypatch, user_tracker):
    _uid, token = create_emulator_user(email=f"user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(_uid)

    def failing_snapshot(nb: str):
        raise AssertionError(f"Firestore snapshot called for invalid notebook ID: {nb}")

    monkeypatch.setattr("app.api.access.get_notebook_snapshot", failing_snapshot)

    # URL-encode newline if present
    encoded_id = invalid_id.replace("\n", "%0A")
    response = client.get(
        f"/v1/notebooks/{encoded_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "not_found"


def test_notebook_list_ordering_and_isolation(
    user_tracker, notebook_tracker, demo_notebook_context
):
    uid_a, token_a = create_emulator_user(email=f"user_a_{uuid4().hex[:8]}@example.com")
    uid_b, token_b = create_emulator_user(email=f"user_b_{uuid4().hex[:8]}@example.com")
    user_tracker.extend([uid_a, uid_b])

    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User A creates 3 notebooks
    created_a = []
    for name in ["Notebook 1", "Notebook 2", "Notebook 3"]:
        res = client.post("/v1/notebooks", headers=headers_a, json={"name": name})
        assert res.status_code == 201
        created_a.append(res.json()["id"])
        notebook_tracker.append(res.json()["id"])

    # User B creates 1 notebook
    res_b = client.post("/v1/notebooks", headers=headers_b, json={"name": "User B Notebook"})
    assert res_b.status_code == 201
    nb_b_id = res_b.json()["id"]
    notebook_tracker.append(nb_b_id)

    # Seed demo notebook
    seed_demo_notebook(owner_uid="seed-owner")

    # User A lists notebooks
    res_list = client.get("/v1/notebooks", headers=headers_a)
    assert res_list.status_code == 200
    items = res_list.json()["items"]
    item_ids = [item["id"] for item in items]

    # Demo must be first
    assert item_ids[0] == DEMO_NOTEBOOK_ID
    # Other user's notebook must not be present
    assert nb_b_id not in item_ids
    # User A's notebooks must appear in newest-first order (Notebook 3, Notebook 2, Notebook 1)
    user_a_ids_in_list = [id_ for id_ in item_ids if id_ in created_a]
    assert user_a_ids_in_list == [created_a[2], created_a[1], created_a[0]]


def test_notebook_list_demo_not_duplicated_for_owner(
    user_tracker, notebook_tracker, demo_notebook_context
):
    uid, token = create_emulator_user(email=f"owner_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    headers = {"Authorization": f"Bearer {token}"}

    # Seed demo notebook with caller as owner
    seed_demo_notebook(owner_uid=uid)

    # Caller creates another notebook
    res = client.post("/v1/notebooks", headers=headers, json={"name": "Owned Course"})
    assert res.status_code == 201
    nb_id = res.json()["id"]
    notebook_tracker.append(nb_id)

    res_list = client.get("/v1/notebooks", headers=headers)
    assert res_list.status_code == 200
    items = res_list.json()["items"]
    item_ids = [item["id"] for item in items]

    # DEMO_NOTEBOOK_ID must appear exactly once at the beginning
    assert item_ids.count(DEMO_NOTEBOOK_ID) == 1
    assert item_ids[0] == DEMO_NOTEBOOK_ID
    assert nb_id in item_ids


def test_notebook_pagination(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email=f"page_user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    headers = {"Authorization": f"Bearer {token}"}

    created_ids = []
    for i in range(3):
        res = client.post("/v1/notebooks", headers=headers, json={"name": f"Page NB {i}"})
        assert res.status_code == 201
        created_ids.append(res.json()["id"])
        notebook_tracker.append(res.json()["id"])

    # Page 1 with limit=2 (no demo seeded, so only owner's 2 items)
    p1 = client.get("/v1/notebooks?limit=2", headers=headers)
    assert p1.status_code == 200
    p1_data = p1.json()
    assert len(p1_data["items"]) == 2
    assert p1_data["next_cursor"] is not None
    next_cursor = p1_data["next_cursor"]
    assert next_cursor == p1_data["items"][-1]["id"]

    # Page 2 with cursor
    p2 = client.get(f"/v1/notebooks?limit=2&cursor={next_cursor}", headers=headers)
    assert p2.status_code == 200
    p2_data = p2.json()
    assert len(p2_data["items"]) == 1
    assert p2_data["next_cursor"] is None

    # Invalid limits
    assert client.get("/v1/notebooks?limit=0", headers=headers).status_code == 422
    assert client.get("/v1/notebooks?limit=101", headers=headers).status_code == 422

    # Unknown cursor gives 422
    res_unk = client.get("/v1/notebooks?cursor=nonexistent_cursor_id", headers=headers)
    assert res_unk.status_code == 422
    assert res_unk.json()["error"]["code"] == "invalid"

    # Invalid cursor format gives 422
    res_bad_fmt = client.get("/v1/notebooks?cursor=bad.cursor.id", headers=headers)
    assert res_bad_fmt.status_code == 422
    assert res_bad_fmt.json()["error"]["code"] == "invalid"


def test_notebook_cursor_belonging_to_other_user_rejected(user_tracker, notebook_tracker):
    uid_a, token_a = create_emulator_user(email=f"u_a_{uuid4().hex[:8]}@example.com")
    uid_b, token_b = create_emulator_user(email=f"u_b_{uuid4().hex[:8]}@example.com")
    user_tracker.extend([uid_a, uid_b])

    # User A creates notebook
    res_a = client.post(
        "/v1/notebooks",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "A's Notebook"},
    )
    nb_a = res_a.json()["id"]
    notebook_tracker.append(nb_a)

    # User B passes User A's notebook as cursor
    res_b = client.get(
        f"/v1/notebooks?cursor={nb_a}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res_b.status_code == 422
    assert res_b.json()["error"]["code"] == "invalid"


def test_notebook_get_access_control(user_tracker, notebook_tracker, demo_notebook_context):
    uid_a, token_a = create_emulator_user(email=f"owner_{uuid4().hex[:8]}@example.com")
    uid_b, token_b = create_emulator_user(email=f"reader_{uuid4().hex[:8]}@example.com")
    user_tracker.extend([uid_a, uid_b])

    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User A creates a private notebook
    res_a = client.post("/v1/notebooks", headers=headers_a, json={"name": "Private A"})
    assert res_a.status_code == 201
    nb_a = res_a.json()["id"]
    notebook_tracker.append(nb_a)

    # Seed demo notebook
    seed_demo_notebook(owner_uid="seed-owner")

    # User A can get their own notebook
    res_own = client.get(f"/v1/notebooks/{nb_a}", headers=headers_a)
    assert res_own.status_code == 200
    assert res_own.json()["id"] == nb_a

    # User B GET on user A's notebook gives 404 "not_found"
    res_b_on_a = client.get(f"/v1/notebooks/{nb_a}", headers=headers_b)
    assert res_b_on_a.status_code == 404
    assert res_b_on_a.json()["error"]["code"] == "not_found"

    # User B GET on nb_demo_6041 gives 200
    res_demo = client.get(f"/v1/notebooks/{DEMO_NOTEBOOK_ID}", headers=headers_b)
    assert res_demo.status_code == 200
    assert res_demo.json()["id"] == DEMO_NOTEBOOK_ID

    # Missing notebook ID gives 404
    res_missing = client.get("/v1/notebooks/missing_nb_12345", headers=headers_b)
    assert res_missing.status_code == 404
    assert res_missing.json()["error"]["code"] == "not_found"


def test_get_owned_notebook_direct_calls(user_tracker, notebook_tracker, demo_notebook_context):
    uid_a, _ = create_emulator_user(email=f"owner_{uuid4().hex[:8]}@example.com")
    uid_b, _ = create_emulator_user(email=f"other_{uuid4().hex[:8]}@example.com")
    user_tracker.extend([uid_a, uid_b])

    user_a = AuthenticatedUser(uid=uid_a, email="owner@example.com", is_guest=False)
    user_b = AuthenticatedUser(uid=uid_b, email="other@example.com", is_guest=False)

    # Seed User A notebook directly
    db = get_db()
    nb_ref = db.collection("notebooks").document()
    nb_ref.set({
        "name": "User A Direct",
        "owner_uid": uid_a,
        "is_demo": False,
        "status": "empty",
        "sources_summary": [],
        "counts": {"chunks": 0, "items": 0, "questions_verified": 0},
        "created_at": firestore.SERVER_TIMESTAMP,
    })
    nb_a = nb_ref.id
    notebook_tracker.append(nb_a)

    # Seed demo notebook owned by seed-owner
    seed_demo_notebook(owner_uid="seed-owner")

    # Owner A gets their notebook
    nb_obj = get_owned_notebook(nb=nb_a, current_user=user_a)
    assert nb_obj.id == nb_a

    # User B on demo notebook gets 403 "forbidden"
    with pytest.raises(HTTPException) as exc_info:
        get_owned_notebook(nb=DEMO_NOTEBOOK_ID, current_user=user_b)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "forbidden"

    # User B on user A's notebook gets 404 "not_found"
    with pytest.raises(HTTPException) as exc_info:
        get_owned_notebook(nb=nb_a, current_user=user_b)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "not_found"


def test_notebooks_exact_operation_ids():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    assert schema["paths"]["/v1/notebooks"]["post"]["operationId"] == "notebooks_create"
    assert schema["paths"]["/v1/notebooks"]["get"]["operationId"] == "notebooks_list"
    assert schema["paths"]["/v1/notebooks/{nb}"]["get"]["operationId"] == "notebooks_get"
