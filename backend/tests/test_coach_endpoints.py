import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.coach import (
    get_progress,
    record_quiz_answer,
)
from app.db import get_db, user_path
from app.db.coach import coach_storage
from app.db.paths import coach_events_collection_path, mastery_path, member_path
from app.db.topics import write_topics_batch
from app.main import app
from tests.conftest import create_emulator_user


def setup_topics(nb_id: str) -> None:
    topics = [
        {
            "id": "t1",
            "name": "Topic 1",
            "order": 1,
            "prerequisite_ids": [],
            "is_other": False,
            "summary": "Summary 1",
            "locations": [],
            "location_count": 0,
        },
        {
            "id": "t2",
            "name": "Topic 2",
            "order": 2,
            "prerequisite_ids": ["t1"],
            "is_other": False,
            "summary": "Summary 2",
            "locations": [],
            "location_count": 0,
        },
        {
            "id": "t3",
            "name": "Topic 3",
            "order": 3,
            "prerequisite_ids": ["t2"],
            "is_other": False,
            "summary": "Summary 3",
            "locations": [],
            "location_count": 0,
        },
    ]
    write_topics_batch(nb_id, topics)


def set_user_study_coach(uid: str, enabled: bool) -> None:
    db = get_db()
    db.document(user_path(uid)).set({"study_coach": enabled}, merge=True)


def test_coach_operation_ids_pinned():
    client = TestClient(app)
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    paths = schema.get("paths", {})

    assert paths["/v1/notebooks/{nb}/progress"]["get"]["operationId"] == "progress_get"
    assert (
        paths["/v1/notebooks/{nb}/needs-work"]["get"]["operationId"] == "needs_work_get"
    )
    assert (
        paths["/v1/notebooks/{nb}/topics/{t}/checked"]["put"]["operationId"]
        == "topics_put_checked"
    )


def test_e1_no_token_401(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email="e1_owner@test.com")
    user_tracker.append(uid)
    client_auth = TestClient(app, headers={"Authorization": f"Bearer {token}"})

    res_nb = client_auth.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_nb.status_code == 201
    nb_id = res_nb.json()["id"]
    notebook_tracker.append(nb_id)
    setup_topics(nb_id)

    client_anon = TestClient(app)
    res_prog = client_anon.get(f"/v1/notebooks/{nb_id}/progress")
    assert res_prog.status_code == 401

    res_nw = client_anon.get(f"/v1/notebooks/{nb_id}/needs-work")
    assert res_nw.status_code == 401

    res_chk = client_anon.put(
        f"/v1/notebooks/{nb_id}/topics/t1/checked", json={"checked": True}
    )
    assert res_chk.status_code == 401


def test_e2_another_user_notebook_404(user_tracker, notebook_tracker):
    u1, token1 = create_emulator_user(email="e2_u1@test.com")
    u2, token2 = create_emulator_user(email="e2_u2@test.com")
    user_tracker.extend([u1, u2])

    client1 = TestClient(app, headers={"Authorization": f"Bearer {token1}"})
    client2 = TestClient(app, headers={"Authorization": f"Bearer {token2}"})

    res_nb = client1.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_nb.status_code == 201
    nb_id = res_nb.json()["id"]
    notebook_tracker.append(nb_id)
    setup_topics(nb_id)

    res_prog = client2.get(f"/v1/notebooks/{nb_id}/progress")
    assert res_prog.status_code == 404

    res_nw = client2.get(f"/v1/notebooks/{nb_id}/needs-work")
    assert res_nw.status_code == 404

    res_chk = client2.put(
        f"/v1/notebooks/{nb_id}/topics/t1/checked", json={"checked": True}
    )
    assert res_chk.status_code == 404


def test_e3_study_coach_off(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email="e3_off@test.com")
    user_tracker.append(uid)
    set_user_study_coach(uid, False)

    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    res_nb = client.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_nb.status_code == 201
    nb_id = res_nb.json()["id"]
    notebook_tracker.append(nb_id)
    setup_topics(nb_id)

    # Tick t2
    res_chk = client.put(
        f"/v1/notebooks/{nb_id}/topics/t2/checked", json={"checked": True}
    )
    assert res_chk.status_code == 200
    assert res_chk.json() == {"topic_id": "t2", "checked": True}

    # Progress lists t1-t3 with real checked values and null mastery fields
    res_prog = client.get(f"/v1/notebooks/{nb_id}/progress")
    assert res_prog.status_code == 200
    p_data = res_prog.json()
    assert p_data["study_coach"] is False
    assert p_data["next_cursor"] is None
    items = p_data["items"]
    assert len(items) == 3

    assert items[0]["topic_id"] == "t1"
    assert items[0]["checked"] is False
    assert items[0]["p_known"] is None
    assert items[0]["mastered"] is None
    assert items[0]["n_obs"] is None
    assert items[0]["last_updated"] is None

    assert items[1]["topic_id"] == "t2"
    assert items[1]["checked"] is True
    assert items[1]["p_known"] is None
    assert items[1]["mastered"] is None
    assert items[1]["n_obs"] is None
    assert items[1]["last_updated"] is None

    assert items[2]["topic_id"] == "t3"
    assert items[2]["checked"] is False
    assert items[2]["p_known"] is None
    assert items[2]["mastered"] is None
    assert items[2]["n_obs"] is None
    assert items[2]["last_updated"] is None

    # Needs-work lists unchecked topics in syllabus order (t1, then t3)
    res_nw = client.get(f"/v1/notebooks/{nb_id}/needs-work")
    assert res_nw.status_code == 200
    nw_data = res_nw.json()
    assert nw_data["study_coach"] is False
    assert nw_data["next_cursor"] is None
    assert [it["topic_id"] for it in nw_data["items"]] == ["t1", "t3"]
    for it in nw_data["items"]:
        assert it["p_known"] is None
        assert it["chat_signals"] is None


def test_e4_study_coach_on_progress_and_needs_work(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email="e4_on@test.com")
    user_tracker.append(uid)
    set_user_study_coach(uid, True)

    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    res_nb = client.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_nb.status_code == 201
    nb_id = res_nb.json()["id"]
    notebook_tracker.append(nb_id)
    setup_topics(nb_id)

    # Record quiz answers to create mastery
    storage = coach_storage(nb_id, uid)
    now = datetime.now(UTC)

    # t1: wrong answer -> low mastery
    record_quiz_answer(
        storage,
        nb_id,
        uid,
        topic_id="t1",
        attempt_id=uuid.uuid4().hex[:8],
        question_type="mcq",
        score=0.0,
        coach_on=True,
        now=now,
    )
    # t2: also wrong answer -> low mastery (t2 builds on t1)
    record_quiz_answer(
        storage,
        nb_id,
        uid,
        topic_id="t2",
        attempt_id=uuid.uuid4().hex[:8],
        question_type="mcq",
        score=0.0,
        coach_on=True,
        now=now,
    )

    # Check progress agrees with core
    res_prog = client.get(f"/v1/notebooks/{nb_id}/progress")
    assert res_prog.status_code == 200
    prog_data = res_prog.json()
    assert prog_data["study_coach"] is True

    core_prog = get_progress(storage, nb_id, uid, coach_on=True, now=now)
    assert len(prog_data["items"]) == len(core_prog.topics)
    for api_item, core_item in zip(prog_data["items"], core_prog.topics, strict=True):
        assert api_item["topic_id"] == core_item.topic_id
        assert api_item["checked"] == core_item.checked
        assert api_item["p_known"] == core_item.p_known
        if core_item.label == "mastered":
            assert api_item["mastered"] is True
        else:
            assert api_item["mastered"] is False
        assert api_item["n_obs"] == core_item.n_obs
        if core_item.last_updated is not None:
            assert api_item["last_updated"] is not None
        else:
            assert api_item["last_updated"] is None

    # Needs-work puts weak prerequisite t1 before topic t2 that builds on it
    res_nw = client.get(f"/v1/notebooks/{nb_id}/needs-work")
    assert res_nw.status_code == 200
    nw_data = res_nw.json()
    assert nw_data["study_coach"] is True
    topic_ids = [it["topic_id"] for it in nw_data["items"]]
    assert "t1" in topic_ids and "t2" in topic_ids
    assert topic_ids.index("t1") < topic_ids.index("t2")


def test_e5_limit_validation(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email="e5_limit@test.com")
    user_tracker.append(uid)

    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    res_nb = client.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_nb.status_code == 201
    nb_id = res_nb.json()["id"]
    notebook_tracker.append(nb_id)
    setup_topics(nb_id)

    res_l0 = client.get(f"/v1/notebooks/{nb_id}/needs-work?limit=0")
    assert res_l0.status_code == 422
    assert res_l0.json()["error"]["code"] == "invalid"

    res_l7 = client.get(f"/v1/notebooks/{nb_id}/needs-work?limit=7")
    assert res_l7.status_code == 422
    assert res_l7.json()["error"]["code"] == "invalid"

    res_l1 = client.get(f"/v1/notebooks/{nb_id}/needs-work?limit=1")
    assert res_l1.status_code == 200
    assert len(res_l1.json()["items"]) <= 1


def test_e6_tick_coach_on(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email="e6_tick@test.com")
    user_tracker.append(uid)
    set_user_study_coach(uid, True)

    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    res_nb = client.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_nb.status_code == 201
    nb_id = res_nb.json()["id"]
    notebook_tracker.append(nb_id)
    setup_topics(nb_id)

    db = get_db()
    res_tick = client.put(
        f"/v1/notebooks/{nb_id}/topics/t1/checked", json={"checked": True}
    )
    assert res_tick.status_code == 200
    assert res_tick.json() == {"topic_id": "t1", "checked": True}

    mem_snap = db.document(member_path(nb_id, uid)).get()
    assert mem_snap.exists
    checked_map = (mem_snap.to_dict() or {}).get("checked", {})
    assert checked_map.get("t1") is True

    mastery_snap = db.document(mastery_path(nb_id, uid, "t1")).get()
    assert mastery_snap.exists
    m_data = mastery_snap.to_dict() or {}
    assert m_data.get("p_known", 0.0) >= 0.35

    events_ref = db.collection(coach_events_collection_path(nb_id, uid))
    cb_events = [
        d.to_dict()
        for d in events_ref.stream()
        if (d.to_dict() or {}).get("kind") == "checkbox"
        and (d.to_dict() or {}).get("topic_id") == "t1"
    ]
    assert len(cb_events) == 1
    assert cb_events[0]["value"] == 1.0


def test_e7_same_tick_again(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email="e7_repeat@test.com")
    user_tracker.append(uid)
    set_user_study_coach(uid, True)

    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    res_nb = client.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_nb.status_code == 201
    nb_id = res_nb.json()["id"]
    notebook_tracker.append(nb_id)
    setup_topics(nb_id)

    db = get_db()
    res_tick1 = client.put(
        f"/v1/notebooks/{nb_id}/topics/t1/checked", json={"checked": True}
    )
    assert res_tick1.status_code == 200

    events_ref = db.collection(coach_events_collection_path(nb_id, uid))
    events_before = [
        d.to_dict()
        for d in events_ref.stream()
        if (d.to_dict() or {}).get("kind") == "checkbox"
        and (d.to_dict() or {}).get("topic_id") == "t1"
    ]
    assert len(events_before) == 1

    res_tick2 = client.put(
        f"/v1/notebooks/{nb_id}/topics/t1/checked", json={"checked": True}
    )
    assert res_tick2.status_code == 200
    events_after = [
        d.to_dict()
        for d in events_ref.stream()
        if (d.to_dict() or {}).get("kind") == "checkbox"
        and (d.to_dict() or {}).get("topic_id") == "t1"
    ]
    assert len(events_after) == 1


def test_e8_untick(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email="e8_untick@test.com")
    user_tracker.append(uid)
    set_user_study_coach(uid, True)

    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    res_nb = client.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_nb.status_code == 201
    nb_id = res_nb.json()["id"]
    notebook_tracker.append(nb_id)
    setup_topics(nb_id)

    db = get_db()
    res_tick = client.put(
        f"/v1/notebooks/{nb_id}/topics/t1/checked", json={"checked": True}
    )
    assert res_tick.status_code == 200

    p_before_untick = (
        db.document(mastery_path(nb_id, uid, "t1")).get().to_dict() or {}
    )["p_known"]

    res_untick = client.put(
        f"/v1/notebooks/{nb_id}/topics/t1/checked", json={"checked": False}
    )
    assert res_untick.status_code == 200
    assert res_untick.json() == {"topic_id": "t1", "checked": False}

    mem_snap_unticked = db.document(member_path(nb_id, uid)).get()
    checked_map_unticked = (mem_snap_unticked.to_dict() or {}).get("checked", {})
    assert "t1" not in checked_map_unticked

    p_after_untick = (
        db.document(mastery_path(nb_id, uid, "t1")).get().to_dict() or {}
    )["p_known"]
    assert p_after_untick == p_before_untick

    events_ref = db.collection(coach_events_collection_path(nb_id, uid))
    cb_events_untick = [
        d.to_dict()
        for d in events_ref.stream()
        if (d.to_dict() or {}).get("kind") == "checkbox"
        and (d.to_dict() or {}).get("topic_id") == "t1"
    ]
    assert len(cb_events_untick) == 2
    zero_events = [ev for ev in cb_events_untick if ev["value"] == 0.0]
    assert len(zero_events) == 1



def test_e9_tick_with_study_coach_off(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email="e9_off_tick@test.com")
    user_tracker.append(uid)
    set_user_study_coach(uid, False)

    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    res_nb = client.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_nb.status_code == 201
    nb_id = res_nb.json()["id"]
    notebook_tracker.append(nb_id)
    setup_topics(nb_id)

    res_tick = client.put(
        f"/v1/notebooks/{nb_id}/topics/t1/checked", json={"checked": True}
    )
    assert res_tick.status_code == 200

    db = get_db()
    mem_snap = db.document(member_path(nb_id, uid)).get()
    checked_map = (mem_snap.to_dict() or {}).get("checked", {})
    assert checked_map.get("t1") is True

    # No coach_events or mastery documents exist
    events = list(db.collection(coach_events_collection_path(nb_id, uid)).stream())
    assert len(events) == 0

    mastery_docs = list(
        db.collection(f"notebooks/{nb_id}/members/{uid}/mastery").stream()
    )
    assert len(mastery_docs) == 0


def test_e10_unknown_malformed_empty_notebook_404(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email="e10_err@test.com")
    user_tracker.append(uid)

    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    res_nb = client.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_nb.status_code == 201
    nb_id = res_nb.json()["id"]
    notebook_tracker.append(nb_id)
    setup_topics(nb_id)

    # Unknown topic ID
    assert (
        client.put(
            f"/v1/notebooks/{nb_id}/topics/t5/checked", json={"checked": True}
        ).status_code
        == 404
    )

    # Malformed topic ID
    assert (
        client.put(
            f"/v1/notebooks/{nb_id}/topics/t0/checked", json={"checked": True}
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/v1/notebooks/{nb_id}/topics/t7/checked", json={"checked": True}
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/v1/notebooks/{nb_id}/topics/bad/checked", json={"checked": True}
        ).status_code
        == 404
    )

    # Notebook without topics
    res_empty_nb = client.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_empty_nb.status_code == 201
    empty_nb_id = res_empty_nb.json()["id"]
    notebook_tracker.append(empty_nb_id)

    assert (
        client.put(
            f"/v1/notebooks/{empty_nb_id}/topics/t1/checked", json={"checked": True}
        ).status_code
        == 404
    )


def test_e11_seen_question_ids_preserved(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email="e11_seen@test.com")
    user_tracker.append(uid)

    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    res_nb = client.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_nb.status_code == 201
    nb_id = res_nb.json()["id"]
    notebook_tracker.append(nb_id)
    setup_topics(nb_id)

    # Pre-populate member document with seen_question_ids
    db = get_db()
    mem_ref = db.document(member_path(nb_id, uid))
    mem_ref.set(
        {
            "checked": {},
            "seen_question_ids": ["q_001", "q_002"],
            "diagnostic": {"status": "not_started", "quiz_id": None},
        }
    )

    # Tick
    res_tick = client.put(
        f"/v1/notebooks/{nb_id}/topics/t1/checked", json={"checked": True}
    )
    assert res_tick.status_code == 200
    snap1 = mem_ref.get()
    assert (snap1.to_dict() or {}).get("seen_question_ids") == ["q_001", "q_002"]

    # Untick
    res_untick = client.put(
        f"/v1/notebooks/{nb_id}/topics/t1/checked", json={"checked": False}
    )
    assert res_untick.status_code == 200
    snap2 = mem_ref.get()
    assert (snap2.to_dict() or {}).get("seen_question_ids") == ["q_001", "q_002"]


def test_e12_checked_in_strict_validation(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email="e12_strict@test.com")
    user_tracker.append(uid)

    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    res_nb = client.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_nb.status_code == 201
    nb_id = res_nb.json()["id"]
    notebook_tracker.append(nb_id)
    setup_topics(nb_id)

    res_str = client.put(
        f"/v1/notebooks/{nb_id}/topics/t1/checked", json={"checked": "true"}
    )
    assert res_str.status_code == 422
    assert res_str.json()["error"]["code"] == "invalid"

    res_extra = client.put(
        f"/v1/notebooks/{nb_id}/topics/t1/checked",
        json={"checked": True, "extra_field": "val"},
    )
    assert res_extra.status_code == 422
    assert res_extra.json()["error"]["code"] == "invalid"


def test_e13_coach_call_raising_stays_200(user_tracker, notebook_tracker, monkeypatch):
    uid, token = create_emulator_user(email="e13_raise@test.com")
    user_tracker.append(uid)
    set_user_study_coach(uid, True)

    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    res_nb = client.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_nb.status_code == 201
    nb_id = res_nb.json()["id"]
    notebook_tracker.append(nb_id)
    setup_topics(nb_id)

    # Monkeypatch record_checkbox where the route looks it up
    monkeypatch.setattr(
        "app.api.coach.record_checkbox",
        MagicMock(side_effect=RuntimeError("Coach exception")),
    )

    res_tick = client.put(
        f"/v1/notebooks/{nb_id}/topics/t1/checked", json={"checked": True}
    )
    assert res_tick.status_code == 200
    assert res_tick.json() == {"topic_id": "t1", "checked": True}

    db = get_db()
    mem_snap = db.document(member_path(nb_id, uid)).get()
    checked_map = (mem_snap.to_dict() or {}).get("checked", {})
    assert checked_map.get("t1") is True


def test_e14_empty_notebook_progress_and_needs_work(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email="e14_empty@test.com")
    user_tracker.append(uid)

    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    res_nb = client.post(
        "/v1/notebooks", json={"name": f"nb_test_sc3_{uuid.uuid4().hex[:12]}"}
    )
    assert res_nb.status_code == 201
    nb_id = res_nb.json()["id"]
    notebook_tracker.append(nb_id)

    res_prog = client.get(f"/v1/notebooks/{nb_id}/progress")
    assert res_prog.status_code == 200
    assert res_prog.json()["items"] == []

    res_nw = client.get(f"/v1/notebooks/{nb_id}/needs-work")
    assert res_nw.status_code == 200
    assert res_nw.json()["items"] == []
