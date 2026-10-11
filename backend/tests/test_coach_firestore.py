"""Unit and integration tests for Firestore Study Coach storage."""

import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from google.cloud import firestore

from app.coach import (
    CoachEvent,
    InMemoryCoachStorage,
    MasteryRecord,
    TopicInfo,
    get_progress,
    get_topics_needing_work,
    record_chat_signal,
    record_checkbox,
    record_quiz_answer,
)
from app.db.client import get_db
from app.db.coach import coach_storage, get_study_coach_enabled
from app.db.paths import (
    coach_event_path,
    coach_events_collection_path,
    mastery_path,
    member_path,
    topic_path,
    user_path,
)
from tests.conftest import create_emulator_user


def test_t1_new_marker_writes_mastery_and_event(user_tracker, notebook_tracker):
    """T1: update_mastery with new marker writes mastery/{t} and coach_events/{event_id}."""
    uid, _ = create_emulator_user()
    user_tracker.append(uid)
    nb = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb)

    storage = coach_storage(nb, uid)
    now = datetime(2026, 10, 11, 10, 0, 0, tzinfo=UTC)
    event = CoachEvent(
        kind="quiz_answer",
        topic_id="t1",
        value=1.0,
        created_at=now,
    )

    outcome = storage.update_mastery(
        nb,
        uid,
        "t1",
        lambda curr: MasteryRecord(p_known=0.8, n_obs=1, last_updated=now),
        event=event,
        event_id="qa_marker_t1",
    )
    assert outcome == "applied"

    db = get_db()
    m_snap = db.document(mastery_path(nb, uid, "t1")).get()
    assert m_snap.exists
    m_data = m_snap.to_dict() or {}
    assert m_data["p_known"] == 0.8
    assert m_data["n_obs"] == 1

    ev_snap = db.document(coach_event_path(nb, uid, "qa_marker_t1")).get()
    assert ev_snap.exists
    ev_data = ev_snap.to_dict() or {}
    assert ev_data["kind"] == "quiz_answer"
    assert ev_data["topic_id"] == "t1"
    assert ev_data["value"] == 1.0


def test_t2_same_marker_idempotent_no_fn_call(user_tracker, notebook_tracker):
    """T2: Second call with same marker returns 'duplicate' without calling fn."""
    uid, _ = create_emulator_user()
    user_tracker.append(uid)
    nb = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb)

    storage = coach_storage(nb, uid)
    now = datetime(2026, 10, 11, 10, 0, 0, tzinfo=UTC)
    event = CoachEvent(
        kind="quiz_answer",
        topic_id="t1",
        value=1.0,
        created_at=now,
    )

    out1 = storage.update_mastery(
        nb,
        uid,
        "t1",
        lambda curr: MasteryRecord(p_known=0.8, n_obs=1, last_updated=now),
        event=event,
        event_id="qa_marker_t2",
    )
    assert out1 == "applied"

    fn_call_count = [0]

    def should_not_run(curr: MasteryRecord | None) -> MasteryRecord:
        fn_call_count[0] += 1
        return MasteryRecord(p_known=0.9, n_obs=2, last_updated=now)

    out2 = storage.update_mastery(
        nb,
        uid,
        "t1",
        should_not_run,
        event=event,
        event_id="qa_marker_t2",
    )
    assert out2 == "duplicate"
    assert fn_call_count[0] == 0

    db = get_db()
    m_snap = db.document(mastery_path(nb, uid, "t1")).get()
    m_data = m_snap.to_dict() or {}
    assert m_data["n_obs"] == 1
    assert m_data["p_known"] == 0.8


def test_t3_fn_wrong_type_raises_typeerror_and_no_writes(user_tracker, notebook_tracker):
    """T3: fn returning non-MasteryRecord raises TypeError and aborts without writes."""
    uid, _ = create_emulator_user()
    user_tracker.append(uid)
    nb = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb)

    storage = coach_storage(nb, uid)
    now = datetime(2026, 10, 11, 10, 0, 0, tzinfo=UTC)
    event = CoachEvent(
        kind="quiz_answer",
        topic_id="t1",
        value=1.0,
        created_at=now,
    )

    with pytest.raises(TypeError, match="fn must return MasteryRecord"):
        storage.update_mastery(
            nb,
            uid,
            "t1",
            lambda curr: "invalid_return_value",  # type: ignore[return-value]
            event=event,
            event_id="qa_marker_t3",
        )

    db = get_db()
    assert not db.document(mastery_path(nb, uid, "t1")).get().exists
    assert not db.document(coach_event_path(nb, uid, "qa_marker_t3")).get().exists


def test_t4_concurrent_threads_same_marker_race(user_tracker, notebook_tracker):
    """T4: 3 concurrent threads race update_mastery on the same marker; exactly 1 applies."""
    uid, _ = create_emulator_user()
    user_tracker.append(uid)
    nb = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb)

    storage = coach_storage(nb, uid)
    now = datetime(2026, 10, 11, 10, 0, 0, tzinfo=UTC)
    event = CoachEvent(
        kind="quiz_answer",
        topic_id="t1",
        value=1.0,
        created_at=now,
    )

    barrier = threading.Barrier(3)
    results = [None, None, None]
    exceptions = [None, None, None]

    def worker(idx: int) -> None:
        try:
            barrier.wait()
            res = storage.update_mastery(
                nb,
                uid,
                "t1",
                lambda curr: MasteryRecord(
                    p_known=0.8,
                    n_obs=(curr.n_obs + 1) if curr else 1,
                    last_updated=now,
                ),
                event=event,
                event_id="qa_race_marker",
            )
            results[idx] = res
        except Exception as exc:
            exceptions[idx] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for exc in exceptions:
        assert exc is None, f"Worker raised exception: {exc}"

    assert results.count("applied") == 1
    assert results.count("duplicate") == 2

    db = get_db()
    m_snap = db.document(mastery_path(nb, uid, "t1")).get()
    assert m_snap.exists
    m_data = m_snap.to_dict() or {}
    assert m_data["n_obs"] == 1

    ev_docs = list(db.collection(coach_events_collection_path(nb, uid)).stream())
    assert len(ev_docs) == 1


def test_t5_datetime_utc_microsecond_roundtrip(user_tracker, notebook_tracker):
    """T5: Timestamps round-trip as exact UTC datetime with preserved microseconds."""
    uid, _ = create_emulator_user()
    user_tracker.append(uid)
    nb = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb)

    storage = coach_storage(nb, uid)
    dt = datetime(2026, 10, 11, 14, 25, 36, 654321, tzinfo=UTC)
    event = CoachEvent(
        kind="quiz_answer",
        topic_id="t1",
        value=1.0,
        created_at=dt,
    )

    storage.update_mastery(
        nb,
        uid,
        "t1",
        lambda curr: MasteryRecord(p_known=0.75, n_obs=1, last_updated=dt),
        event=event,
        event_id="qa_t5",
    )

    mastery_map = storage.get_all_mastery(nb, uid)
    rec = mastery_map["t1"]
    assert type(rec.last_updated) is datetime
    assert rec.last_updated.tzinfo is not None
    assert rec.last_updated == dt
    assert rec.last_updated.microsecond == 654321

    events = storage.list_events(nb, uid, kind="quiz_answer", since=dt - timedelta(days=1))
    assert len(events) == 1
    ev = events[0]
    assert type(ev.created_at) is datetime
    assert ev.created_at.tzinfo is not None
    assert ev.created_at == dt
    assert ev.created_at.microsecond == 654321


def test_t6_event_listing_bounds_and_filtering(user_tracker, notebook_tracker):
    """T6: list_events boundary conditions and kind filtering match InMemoryCoachStorage."""
    uid, _ = create_emulator_user()
    user_tracker.append(uid)
    nb = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb)

    fs_storage = coach_storage(nb, uid)
    mem_storage = InMemoryCoachStorage()

    base = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)

    ev_exact = CoachEvent(
        kind="quiz_answer", topic_id="t1", value=1.0, created_at=base
    )
    ev_past = CoachEvent(
        kind="quiz_answer",
        topic_id="t1",
        value=0.5,
        created_at=base - timedelta(seconds=1),
    )
    ev_future = CoachEvent(
        kind="quiz_answer",
        topic_id="t1",
        value=0.8,
        created_at=base + timedelta(seconds=1),
    )
    ev_other_kind = CoachEvent(
        kind="chat_signal", topic_id="t1", value=1.0, created_at=base
    )

    for i, ev in enumerate([ev_exact, ev_past, ev_future, ev_other_kind]):
        fs_storage.add_event(nb, uid, ev, event_id=f"ev_{i}")
        mem_storage.add_event(nb, uid, ev, event_id=f"ev_{i}")

    fs_results = fs_storage.list_events(nb, uid, kind="quiz_answer", since=base)
    mem_results = mem_storage.list_events(nb, uid, kind="quiz_answer", since=base)

    assert len(fs_results) == 2
    assert [e.created_at for e in fs_results] == [base, base + timedelta(seconds=1)]
    assert fs_results == mem_results


def test_t7_chat_signal_duplicate_and_checkbox_auto_ids(user_tracker, notebook_tracker):
    """T7: cs_ duplicate prevention, checkbox auto-IDs without '_', and bound checks."""
    uid, _ = create_emulator_user()
    user_tracker.append(uid)
    nb = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb)

    storage = coach_storage(nb, uid)
    now = datetime(2026, 10, 11, 10, 0, 0, tzinfo=UTC)
    chat_ev = CoachEvent(kind="chat_signal", topic_id="t1", value=1.0, created_at=now)

    out1 = storage.add_event(nb, uid, chat_ev, event_id="cs_msg_123")
    assert out1 == "applied"
    out2 = storage.add_event(nb, uid, chat_ev, event_id="cs_msg_123")
    assert out2 == "duplicate"

    cb_ev1 = CoachEvent(kind="checkbox", topic_id="t1", value=0.0, created_at=now)
    cb_ev2 = CoachEvent(kind="checkbox", topic_id="t1", value=0.0, created_at=now)
    out_cb1 = storage.add_event(nb, uid, cb_ev1, event_id=None)
    out_cb2 = storage.add_event(nb, uid, cb_ev2, event_id=None)
    assert out_cb1 == "applied"
    assert out_cb2 == "applied"

    db = get_db()
    all_events = list(db.collection(coach_events_collection_path(nb, uid)).stream())
    event_doc_ids = {doc.id for doc in all_events}
    assert "cs_msg_123" in event_doc_ids

    auto_ids = [doc_id for doc_id in event_doc_ids if doc_id != "cs_msg_123"]
    assert len(auto_ids) == 2
    for aid in auto_ids:
        assert "_" not in aid

    with pytest.raises(ValueError, match="mismatched notebook_id"):
        storage.get_topics("other_nb")
    with pytest.raises(ValueError, match="mismatched"):
        storage.get_checked(nb, "other_uid")
    with pytest.raises(ValueError, match="mismatched"):
        storage.get_all_mastery(nb, "other_uid")
    with pytest.raises(ValueError, match="mismatched"):
        storage.list_events("other_nb", uid, kind="quiz_answer", since=now)
    with pytest.raises(ValueError, match="mismatched"):
        storage.add_event(nb, "other_uid", chat_ev, event_id=None)
    with pytest.raises(ValueError, match="mismatched"):
        storage.update_mastery(
            "other_nb",
            uid,
            "t1",
            lambda c: MasteryRecord(p_known=0.5, n_obs=1, last_updated=now),
            event=chat_ev,
            event_id=None,
        )


def test_t8_get_topics_select_excludes_locations(
    user_tracker, notebook_tracker, monkeypatch
):
    """T8: get_topics projects TopicInfo fields and excludes large locations array."""
    uid, _ = create_emulator_user()
    user_tracker.append(uid)
    nb = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb)

    db = get_db()
    db.document(topic_path(nb, "t1")).set({
        "name": "Probability Theory",
        "order": 1,
        "prerequisite_ids": ["t0"],
        "is_other": False,
        "locations": [{"page": i, "text": f"Long text snippet {i}"} for i in range(50)],
    })

    captured_select_args = []
    orig_select = firestore.CollectionReference.select

    def spy_select(self, *args, **kwargs):
        if args:
            captured_select_args.append(args[0])
        elif "field_paths" in kwargs:
            captured_select_args.append(kwargs["field_paths"])
        return orig_select(self, *args, **kwargs)

    monkeypatch.setattr(firestore.CollectionReference, "select", spy_select)

    storage = coach_storage(nb, uid)
    topics = storage.get_topics(nb)

    assert len(captured_select_args) == 1
    selected = captured_select_args[0]
    expected_fields = {"name", "order", "prerequisite_ids", "is_other"}
    assert set(selected) == expected_fields
    assert "locations" not in selected

    assert len(topics) == 1
    t = topics[0]
    assert t.id == "t1"
    assert t.name == "Probability Theory"
    assert t.order == 1
    assert t.prerequisite_ids == ("t0",)
    assert t.is_other is False


def test_t9_parity_in_memory_and_firestore_scripted_sequence(
    user_tracker, notebook_tracker
):
    """T9: Scripted sequence through core procedures matches between InMemory and Firestore."""
    uid, _ = create_emulator_user()
    user_tracker.append(uid)
    nb = f"nb_test_sc2_{uuid4().hex[:12]}"
    notebook_tracker.append(nb)

    db = get_db()
    topics_seed = [
        TopicInfo(id="t1", name="Topic 1", order=1, prerequisite_ids=(), is_other=False),
        TopicInfo(id="t2", name="Topic 2", order=2, prerequisite_ids=("t1",), is_other=False),
        TopicInfo(id="t_other", name="Other", order=3, prerequisite_ids=(), is_other=True),
    ]

    for t in topics_seed:
        db.document(topic_path(nb, t.id)).set({
            "name": t.name,
            "order": t.order,
            "prerequisite_ids": list(t.prerequisite_ids),
            "is_other": t.is_other,
            "locations": [],
        })

    mem_storage = InMemoryCoachStorage()
    mem_storage.set_topics(nb, topics_seed)

    fs_storage = coach_storage(nb, uid)

    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)

    # Day 1: MCQ answer on t1
    d1 = base + timedelta(days=1)
    record_quiz_answer(
        mem_storage, nb, uid, topic_id="t1", attempt_id="a1",
        question_type="mcq", score=1.0, coach_on=True, now=d1,
    )
    record_quiz_answer(
        fs_storage, nb, uid, topic_id="t1", attempt_id="a1",
        question_type="mcq", score=1.0, coach_on=True, now=d1,
    )

    # Day 2: MCQ answer on t2 (wrong)
    d2 = base + timedelta(days=2)
    record_quiz_answer(
        mem_storage, nb, uid, topic_id="t2", attempt_id="a2",
        question_type="mcq", score=0.0, coach_on=True, now=d2,
    )
    record_quiz_answer(
        fs_storage, nb, uid, topic_id="t2", attempt_id="a2",
        question_type="mcq", score=0.0, coach_on=True, now=d2,
    )

    # Day 3: Chat signal on t1
    d3 = base + timedelta(days=3)
    record_chat_signal(
        mem_storage, nb, uid, topic_id="t1", message_id="m1", coach_on=True, now=d3
    )
    record_chat_signal(
        fs_storage, nb, uid, topic_id="t1", message_id="m1", coach_on=True, now=d3
    )

    # Day 4: Numerical answer on t1
    d4 = base + timedelta(days=4)
    record_quiz_answer(
        mem_storage, nb, uid, topic_id="t1", attempt_id="a3",
        question_type="numerical", score=1.0, coach_on=True, now=d4,
    )
    record_quiz_answer(
        fs_storage, nb, uid, topic_id="t1", attempt_id="a3",
        question_type="numerical", score=1.0, coach_on=True, now=d4,
    )

    # Day 5: Checkbox tick on t1
    d5 = base + timedelta(days=5)
    record_checkbox(
        mem_storage, nb, uid, topic_id="t1", checked=True, coach_on=True, now=d5
    )
    record_checkbox(
        fs_storage, nb, uid, topic_id="t1", checked=True, coach_on=True, now=d5
    )

    # Day 7: Short answer on t2
    d7 = base + timedelta(days=7)
    record_quiz_answer(
        mem_storage, nb, uid, topic_id="t2", attempt_id="a4",
        question_type="short", score=1.0, coach_on=True, now=d7,
    )
    record_quiz_answer(
        fs_storage, nb, uid, topic_id="t2", attempt_id="a4",
        question_type="short", score=1.0, coach_on=True, now=d7,
    )

    # Day 10: Chat signal on t2
    d10 = base + timedelta(days=10)
    record_chat_signal(
        mem_storage, nb, uid, topic_id="t2", message_id="m2", coach_on=True, now=d10
    )
    record_chat_signal(
        fs_storage, nb, uid, topic_id="t2", message_id="m2", coach_on=True, now=d10
    )

    # Day 12: Checkbox untick on t1
    d12 = base + timedelta(days=12)
    record_checkbox(
        mem_storage, nb, uid, topic_id="t1", checked=False, coach_on=True, now=d12
    )
    record_checkbox(
        fs_storage, nb, uid, topic_id="t1", checked=False, coach_on=True, now=d12
    )

    # Day 15: Answer on t1
    d15 = base + timedelta(days=15)
    record_quiz_answer(
        mem_storage, nb, uid, topic_id="t1", attempt_id="a5",
        question_type="mcq", score=0.0, coach_on=True, now=d15,
    )
    record_quiz_answer(
        fs_storage, nb, uid, topic_id="t1", attempt_id="a5",
        question_type="mcq", score=0.0, coach_on=True, now=d15,
    )

    # Day 18: Answer on t_other (ignored)
    d18 = base + timedelta(days=18)
    record_quiz_answer(
        mem_storage, nb, uid, topic_id="t_other", attempt_id="a6",
        question_type="mcq", score=1.0, coach_on=True, now=d18,
    )
    record_quiz_answer(
        fs_storage, nb, uid, topic_id="t_other", attempt_id="a6",
        question_type="mcq", score=1.0, coach_on=True, now=d18,
    )

    # Seed members checked directly
    checked_map = {"t1": True, "t2": False}
    mem_storage.set_checked(nb, uid, checked_map)
    db.document(member_path(nb, uid)).set({"checked": checked_map})

    d20 = base + timedelta(days=20)
    prog_mem = get_progress(mem_storage, nb, uid, coach_on=True, now=d20)
    prog_fs = get_progress(fs_storage, nb, uid, coach_on=True, now=d20)
    assert prog_fs == prog_mem

    nw_mem = get_topics_needing_work(mem_storage, nb, uid, coach_on=True, now=d20, limit=3)
    nw_fs = get_topics_needing_work(fs_storage, nb, uid, coach_on=True, now=d20, limit=3)
    assert nw_fs == nw_mem


def test_t10_get_study_coach_enabled_states(user_tracker):
    """T10: get_study_coach_enabled returns True only for stored True."""
    uid_true, _ = create_emulator_user()
    uid_false, _ = create_emulator_user()
    uid_none, _ = create_emulator_user()
    uid_missing_field, _ = create_emulator_user()
    user_tracker.extend([uid_true, uid_false, uid_none, uid_missing_field])

    db = get_db()
    db.document(user_path(uid_true)).set({"study_coach": True})
    db.document(user_path(uid_false)).set({"study_coach": False})
    db.document(user_path(uid_none)).set({"study_coach": None})
    db.document(user_path(uid_missing_field)).set({"other_field": 123})

    assert get_study_coach_enabled(uid_true) is True
    assert get_study_coach_enabled(uid_false) is False
    assert get_study_coach_enabled(uid_none) is False
    assert get_study_coach_enabled(uid_missing_field) is False
    assert get_study_coach_enabled(f"non_existent_{uuid4().hex[:8]}") is False
