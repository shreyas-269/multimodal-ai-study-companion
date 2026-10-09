"""Unit and integration tests for Study Coach core (Task SC1)."""

import ast
import random
import sys
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import IntEnum
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

import pytest

from app.coach import (
    CoachEvent,
    CoachInputError,
    EventKind,
    InMemoryCoachStorage,
    MasteryRecord,
    NeedsWorkItem,
    QuestionType,
    RecordResult,
    TopicInfo,
    bkt,
    get_progress,
    get_topics_needing_work,
    record_chat_signal,
    record_checkbox,
    record_quiz_answer,
)

T0 = datetime(2026, 10, 7, 0, 0, 0, tzinfo=UTC)


class StrSubclass(str):
    pass


class FloatSubclass(float):
    pass


class DummyEnum(IntEnum):
    ONE = 1
    TWO = 2


class SpyStorage:
    """Wrapper around InMemoryCoachStorage to monitor protocol calls."""

    def __init__(self, inner: InMemoryCoachStorage) -> None:
        self.inner = inner
        self.calls: dict[str, int] = defaultdict(int)

    def get_topics(self, nb: str) -> list[TopicInfo]:
        self.calls["get_topics"] += 1
        return self.inner.get_topics(nb)

    def get_checked(self, nb: str, uid: str) -> dict[str, bool]:
        self.calls["get_checked"] += 1
        return self.inner.get_checked(nb, uid)

    def get_all_mastery(self, nb: str, uid: str) -> dict[str, MasteryRecord]:
        self.calls["get_all_mastery"] += 1
        return self.inner.get_all_mastery(nb, uid)

    def list_events(
        self, nb: str, uid: str, *, kind: EventKind, since: datetime
    ) -> list[CoachEvent]:
        self.calls["list_events"] += 1
        return self.inner.list_events(nb, uid, kind=kind, since=since)

    def add_event(
        self, nb: str, uid: str, event: CoachEvent, *, event_id: str | None
    ) -> Literal["applied", "duplicate"]:
        self.calls["add_event"] += 1
        return self.inner.add_event(nb, uid, event, event_id=event_id)

    def update_mastery(
        self,
        nb: str,
        uid: str,
        topic_id: str,
        fn: Callable[[MasteryRecord | None], MasteryRecord],
        *,
        event: CoachEvent,
        event_id: str | None,
    ) -> Literal["applied", "duplicate"]:
        self.calls["update_mastery"] += 1
        return self.inner.update_mastery(nb, uid, topic_id, fn, event=event, event_id=event_id)

    def set_topics(self, nb: str, topics: list[TopicInfo]) -> None:
        self.inner.set_topics(nb, topics)

    def set_checked(self, nb: str, uid: str, checked: dict[str, bool]) -> None:
        self.inner.set_checked(nb, uid, checked)

    def set_mastery(
        self, nb: str, uid: str, topic_id: str, record: MasteryRecord
    ) -> None:
        self.inner.set_mastery(nb, uid, topic_id, record)

    def snapshot(self) -> dict[str, Any]:
        return self.inner.snapshot()


def _make_demo_topics() -> list[TopicInfo]:
    return [
        TopicInfo(id="t1", name="Topic 1", order=1, prerequisite_ids=(), is_other=False),
        TopicInfo(id="t2", name="Topic 2", order=2, prerequisite_ids=("t1",), is_other=False),
        TopicInfo(id="t3", name="Topic 3", order=3, prerequisite_ids=("t2",), is_other=False),
        TopicInfo(id="t4", name="Topic 4", order=4, prerequisite_ids=("t1",), is_other=False),
        TopicInfo(id="t5", name="Topic 5", order=5, prerequisite_ids=("t1", "t4"), is_other=False),
        TopicInfo(id="t6", name="Topic 6", order=6, prerequisite_ids=("t2", "t5"), is_other=False),
        TopicInfo(id="other", name="Other material", order=7, prerequisite_ids=(), is_other=True),
    ]


# ==================== DONE-WHEN 1 ====================
def test_done_when_1_bkt_updates_from_prior() -> None:
    cases = [
        ("mcq", 1.0, 0.6657303371),
        ("mcq", 0.0, 0.1959459459),
        ("short", 1.0, 0.825),
        ("short", 0.0, 0.1886363636),
        ("numerical", 1.0, 0.9024590164),
        ("numerical", 0.0, 0.1866906475),
        ("short", 0.5, 0.825),
        ("short", 0.4999, 0.1886363636),
    ]

    for idx, (q_type, score, expected) in enumerate(cases):
        storage = InMemoryCoachStorage()
        storage.set_topics("nb1", _make_demo_topics())
        res = record_quiz_answer(
            storage,
            "nb1",
            "u1",
            topic_id="t1",
            attempt_id=f"att_{idx}",
            question_type=q_type,  # type: ignore[arg-type]
            score=score,
            coach_on=True,
            now=T0,
        )
        assert res.outcome == "applied"
        stored_record = storage.get_all_mastery("nb1", "u1")["t1"]
        assert stored_record.p_known == pytest.approx(expected, abs=1e-9)
        assert res.p_known == round(stored_record.p_known, 4)


# ==================== DONE-WHEN 2 & CHANGE 6 ====================
def test_done_when_2_forgetting_and_floor() -> None:
    storage = InMemoryCoachStorage()
    topics = _make_demo_topics()
    storage.set_topics("nb1", topics)

    storage.set_mastery("nb1", "u1", "t1", MasteryRecord(p_known=0.9, n_obs=1, last_updated=T0))

    # Read at 0 days -> 0.9
    p0 = get_progress(storage, "nb1", "u1", coach_on=True, now=T0)
    assert storage.get_all_mastery("nb1", "u1")["t1"].p_known == pytest.approx(0.9, abs=1e-12)
    t1_p0 = next(t for t in p0.topics if t.topic_id == "t1")
    assert t1_p0.p_known == pytest.approx(0.9, abs=1e-12)

    # Read at 14 days -> 0.6
    p14 = get_progress(storage, "nb1", "u1", coach_on=True, now=T0 + timedelta(days=14))
    t1_p14 = next(t for t in p14.topics if t.topic_id == "t1")
    assert t1_p14.p_known == pytest.approx(0.6, abs=1e-12)

    # Read at 28 days -> 0.45
    p28 = get_progress(storage, "nb1", "u1", coach_on=True, now=T0 + timedelta(days=28))
    t1_p28 = next(t for t in p28.topics if t.topic_id == "t1")
    assert t1_p28.p_known == pytest.approx(0.45, abs=1e-12)

    # Read when now is before last_updated -> 0.9
    p_past = get_progress(storage, "nb1", "u1", coach_on=True, now=T0 - timedelta(days=5))
    t1_p_past = next(t for t in p_past.topics if t.topic_id == "t1")
    assert t1_p_past.p_known == pytest.approx(0.9, abs=1e-12)

    # Stored 0.2 read at any age -> 0.2
    storage.set_mastery("nb1", "u1", "t2", MasteryRecord(p_known=0.2, n_obs=1, last_updated=T0))
    p_low = get_progress(storage, "nb1", "u1", coach_on=True, now=T0 + timedelta(days=14))
    t2_p_low = next(t for t in p_low.topics if t.topic_id == "t2")
    assert t2_p_low.p_known == pytest.approx(0.2, abs=1e-12)

    # L12: also at 0 days and at 1e6 days
    p_low_0 = get_progress(storage, "nb1", "u1", coach_on=True, now=T0)
    t2_p_0 = next(t for t in p_low_0.topics if t.topic_id == "t2")
    assert t2_p_0.p_known == pytest.approx(0.2, abs=1e-12)

    p_low_1e6 = get_progress(
        storage, "nb1", "u1", coach_on=True, now=T0 + timedelta(days=1_000_000)
    )
    t2_p_1e6 = next(t for t in p_low_1e6.topics if t.topic_id == "t2")
    assert t2_p_1e6.p_known == pytest.approx(0.2, abs=1e-12)

    # bkt.decay(bkt.decay(0.9, 5), 9) equals bkt.decay(0.9, 14)
    step2 = bkt.decay(bkt.decay(0.9, 5.0), 9.0)
    step1 = bkt.decay(0.9, 14.0)
    assert step2 == pytest.approx(step1, abs=1e-12)

    # Correct mcq 14 days after stored 0.9 stores 0.8671875
    storage_mcq = InMemoryCoachStorage()
    storage_mcq.set_topics("nb1", topics)
    storage_mcq.set_mastery(
        "nb1", "u1", "t1", MasteryRecord(p_known=0.9, n_obs=1, last_updated=T0)
    )
    res_mcq = record_quiz_answer(
        storage_mcq,
        "nb1",
        "u1",
        topic_id="t1",
        attempt_id="att_14d",
        question_type="mcq",
        score=1.0,
        coach_on=True,
        now=T0 + timedelta(days=14),
    )
    assert res_mcq.outcome == "applied"
    stored_mcq = storage_mcq.get_all_mastery("nb1", "u1")["t1"]
    assert stored_mcq.p_known == pytest.approx(0.8671875, abs=1e-12)

    # Checkbox tick on a new topic stores 0.8 with n_obs 0
    storage_tick = InMemoryCoachStorage()
    storage_tick.set_topics("nb1", topics)
    res_tick_new = record_checkbox(
        storage_tick, "nb1", "u1", topic_id="t1", checked=True, coach_on=True, now=T0
    )
    assert res_tick_new.outcome == "applied"
    rec_tick_new = storage_tick.get_all_mastery("nb1", "u1")["t1"]
    assert rec_tick_new.p_known == pytest.approx(0.8, abs=1e-12)
    assert rec_tick_new.n_obs == 0

    # Tick on a stored 0.9 keeps 0.9
    storage_tick.set_mastery(
        "nb1", "u1", "t2", MasteryRecord(p_known=0.9, n_obs=3, last_updated=T0)
    )
    res_tick_high = record_checkbox(
        storage_tick, "nb1", "u1", topic_id="t2", checked=True, coach_on=True, now=T0
    )
    assert res_tick_high.outcome == "applied"
    rec_tick_high = storage_tick.get_all_mastery("nb1", "u1")["t2"]
    assert rec_tick_high.p_known == pytest.approx(0.9, abs=1e-12)
    assert rec_tick_high.n_obs == 3

    # Tick 28 days after stored 0.9 stores 0.8 with last_updated = tick time
    tick_time = T0 + timedelta(days=28)
    res_tick_28d = record_checkbox(
        storage_tick, "nb1", "u1", topic_id="t2", checked=True, coach_on=True, now=tick_time
    )
    assert res_tick_28d.outcome == "applied"
    rec_tick_28d = storage_tick.get_all_mastery("nb1", "u1")["t2"]
    assert rec_tick_28d.p_known == pytest.approx(0.8, abs=1e-12)
    assert rec_tick_28d.last_updated == tick_time

    # Untick changes no mastery and writes one checkbox event with value 0.0
    storage_untick = InMemoryCoachStorage()
    storage_untick.set_topics("nb1", topics)
    storage_untick.set_mastery(
        "nb1", "u1", "t1", MasteryRecord(p_known=0.85, n_obs=2, last_updated=T0)
    )
    res_untick = record_checkbox(
        storage_untick,
        "nb1",
        "u1",
        topic_id="t1",
        checked=False,
        coach_on=True,
        now=T0 + timedelta(days=1),
    )
    assert res_untick.outcome == "applied"
    assert res_untick.p_known is None
    rec_after_untick = storage_untick.get_all_mastery("nb1", "u1")["t1"]
    assert rec_after_untick.p_known == pytest.approx(0.85, abs=1e-12)
    assert rec_after_untick.n_obs == 2
    assert rec_after_untick.last_updated == T0
    evs = storage_untick.list_events("nb1", "u1", kind="checkbox", since=T0)
    assert len(evs) == 1
    assert evs[0].value == 0.0
    assert evs[0].kind == "checkbox"


def test_done_when_2_tick_values_at_4_4_and_4_6_days() -> None:
    # Change 6: 4.4-day and 4.6-day tick values through get_progress and bkt.decay
    storage = InMemoryCoachStorage()
    topics = _make_demo_topics()
    storage.set_topics("nb1", topics)
    storage.set_mastery(
        "nb1", "u1", "t1", MasteryRecord(p_known=0.8, n_obs=0, last_updated=T0)
    )

    # 4.4 days
    expected_4_4 = 0.3 + 0.5 * 0.5 ** (4.4 / 14.0)
    decay_4_4 = bkt.decay(0.8, 4.4)
    assert decay_4_4 == pytest.approx(expected_4_4, abs=1e-12)
    assert decay_4_4 == pytest.approx(0.7021245400, abs=1e-9)

    read_4_4 = get_progress(storage, "nb1", "u1", coach_on=True, now=T0 + timedelta(days=4.4))
    tp_4_4 = next(t for t in read_4_4.topics if t.topic_id == "t1")
    assert tp_4_4.label == "mastered"
    assert tp_4_4.p_known == round(expected_4_4, 4)

    # 4.6 days
    expected_4_6 = 0.3 + 0.5 * 0.5 ** (4.6 / 14.0)
    decay_4_6 = bkt.decay(0.8, 4.6)
    assert decay_4_6 == pytest.approx(expected_4_6, abs=1e-12)
    assert decay_4_6 == pytest.approx(0.6981623111, abs=1e-9)

    read_4_6 = get_progress(storage, "nb1", "u1", coach_on=True, now=T0 + timedelta(days=4.6))
    tp_4_6 = next(t for t in read_4_6.topics if t.topic_id == "t1")
    assert tp_4_6.label == "learning"
    assert tp_4_6.p_known == round(expected_4_6, 4)


# ==================== DONE-WHEN 3 ====================
def test_done_when_3_study_coach_off() -> None:
    inner = InMemoryCoachStorage()
    topics = _make_demo_topics()
    inner.set_topics("nb1", topics)
    inner.set_checked("nb1", "u1", {"t1": True, "t2": False})
    spy = SpyStorage(inner)

    snap_before = spy.snapshot()

    r1 = record_quiz_answer(
        spy,
        "nb1",
        "u1",
        topic_id="t1",
        attempt_id="a1",
        question_type="mcq",
        score=1.0,
        coach_on=False,
        now=T0,
    )
    assert r1.outcome == "coach_off"
    assert r1.p_known is None
    assert spy.snapshot() == snap_before

    r2 = record_chat_signal(
        spy, "nb1", "u1", topic_id="t1", message_id="m1", coach_on=False, now=T0
    )
    assert r2.outcome == "coach_off"
    assert r2.p_known is None
    assert spy.snapshot() == snap_before

    r3 = record_checkbox(
        spy, "nb1", "u1", topic_id="t1", checked=True, coach_on=False, now=T0
    )
    assert r3.outcome == "coach_off"
    assert r3.p_known is None
    assert spy.snapshot() == snap_before

    r4 = record_checkbox(
        spy, "nb1", "u1", topic_id="t1", checked=False, coach_on=False, now=T0
    )
    assert r4.outcome == "coach_off"
    assert r4.p_known is None
    assert spy.snapshot() == snap_before

    nw = get_topics_needing_work(spy, "nb1", "u1", coach_on=False, now=T0, limit=5)
    assert nw.coach_on is False
    expected_ids = ["t2", "t3", "t4", "t5", "t6"]
    assert [item.topic_id for item in nw.items] == expected_ids
    for item in nw.items:
        assert item.reason == "unchecked"
        assert item.p_known is None
        assert item.chat_signals is None

    prog = get_progress(spy, "nb1", "u1", coach_on=False, now=T0)
    assert prog.coach_on is False
    assert len(prog.topics) == 6
    assert prog.topics[0].topic_id == "t1"
    assert prog.topics[0].checked is True
    assert prog.topics[1].topic_id == "t2"
    assert prog.topics[1].checked is False
    for tp in prog.topics:
        assert tp.p_known is None
        assert tp.n_obs is None
        assert tp.last_updated is None
        assert tp.label is None

    assert spy.calls["get_all_mastery"] == 0
    assert spy.calls["list_events"] == 0
    assert spy.calls["add_event"] == 0
    assert spy.calls["update_mastery"] == 0


# ==================== DONE-WHEN 4 ====================
def test_done_when_4_dedupe_sequential_and_concurrent() -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())

    # Sequential dedupe on quiz answer
    r1 = record_quiz_answer(
        storage,
        "nb1",
        "u1",
        topic_id="t1",
        attempt_id="dup_1",
        question_type="mcq",
        score=1.0,
        coach_on=True,
        now=T0,
    )
    assert r1.outcome == "applied"
    snap_after_r1 = storage.snapshot()

    r2 = record_quiz_answer(
        storage,
        "nb1",
        "u1",
        topic_id="t1",
        attempt_id="dup_1",
        question_type="mcq",
        score=1.0,
        coach_on=True,
        now=T0,
    )
    assert r2.outcome == "duplicate"
    assert r2.p_known is None
    assert storage.snapshot() == snap_after_r1

    # Sequential dedupe on chat signal
    c1 = record_chat_signal(
        storage, "nb1", "u1", topic_id="t1", message_id="msg_dup", coach_on=True, now=T0
    )
    assert c1.outcome == "applied"
    snap_after_c1 = storage.snapshot()

    c2 = record_chat_signal(
        storage, "nb1", "u1", topic_id="t1", message_id="msg_dup", coach_on=True, now=T0
    )
    assert c2.outcome == "duplicate"
    assert storage.snapshot() == snap_after_c1

    # Concurrency with 16 threads attempting same attempt ID
    old_switch = sys.getswitchinterval()
    try:
        sys.setswitchinterval(1e-6)
        barrier = threading.Barrier(16, timeout=10.0)
        results: list[RecordResult] = []
        errors: list[Exception] = []
        threads: list[threading.Thread] = []

        def worker() -> None:
            try:
                barrier.wait()
                res = record_quiz_answer(
                    storage,
                    "nb1",
                    "u1",
                    topic_id="t2",
                    attempt_id="concurrent_att",
                    question_type="mcq",
                    score=1.0,
                    coach_on=True,
                    now=T0,
                )
                results.append(res)
            except Exception as e:
                errors.append(e)

        for _ in range(16):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10.0)
            assert not t.is_alive()

        assert len(errors) == 0
        assert len(results) == 16
        applied_count = sum(1 for r in results if r.outcome == "applied")
        duplicate_count = sum(1 for r in results if r.outcome == "duplicate")
        assert applied_count == 1
        assert duplicate_count == 15
        assert storage.get_all_mastery("nb1", "u1")["t2"].n_obs == 1

        # 16 threads attempting same message ID on chat signal
        snap_before_c = storage.snapshot()
        initial_next_event = snap_before_c["next_event_number"]
        c_barrier = threading.Barrier(16, timeout=10.0)
        c_results: list[RecordResult] = []
        c_errors: list[Exception] = []
        c_threads: list[threading.Thread] = []

        def c_worker() -> None:
            try:
                c_barrier.wait()
                res = record_chat_signal(
                    storage,
                    "nb1",
                    "u1",
                    topic_id="t2",
                    message_id="concurrent_msg",
                    coach_on=True,
                    now=T0,
                )
                c_results.append(res)
            except Exception as e:
                c_errors.append(e)

        for _ in range(16):
            t = threading.Thread(target=c_worker)
            c_threads.append(t)
            t.start()

        for t in c_threads:
            t.join(timeout=10.0)
            assert not t.is_alive()

        assert len(c_errors) == 0
        assert len(c_results) == 16
        c_applied = sum(1 for r in c_results if r.outcome == "applied")
        c_duplicate = sum(1 for r in c_results if r.outcome == "duplicate")
        assert c_applied == 1
        assert c_duplicate == 15
        # L13: exactly one event with ID cs_<message_id> and unchanged next_event_number
        snap_after_c = storage.snapshot()
        user_events = snap_after_c["events"].get(("nb1", "u1"), {})
        cs_events = [k for k in user_events if k == "cs_concurrent_msg"]
        assert len(cs_events) == 1
        assert snap_after_c["next_event_number"] == initial_next_event

    finally:
        sys.setswitchinterval(old_switch)


# ==================== DONE-WHEN 5 & CHANGE 5 ====================
def test_done_when_5_atomicity_and_failure_rollback() -> None:
    topics = _make_demo_topics()
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", topics)

    old_switch = sys.getswitchinterval()
    try:
        sys.setswitchinterval(1e-6)
        barrier = threading.Barrier(16, timeout=10.0)
        errors: list[Exception] = []
        threads: list[threading.Thread] = []

        def worker(idx: int) -> None:
            try:
                barrier.wait()
                record_quiz_answer(
                    storage,
                    "nb1",
                    "u1",
                    topic_id="t1",
                    attempt_id=f"distinct_{idx:02d}",
                    question_type="mcq",
                    score=1.0,
                    coach_on=True,
                    now=T0,
                )
            except Exception as e:
                errors.append(e)

        for i in range(16):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10.0)
            assert not t.is_alive()

        assert len(errors) == 0
        stored = storage.get_all_mastery("nb1", "u1")["t1"]
        assert stored.n_obs == 16

        seq_p = bkt.PRIOR
        for _ in range(16):
            seq_p = bkt.update(seq_p, True, "mcq")
        assert stored.p_known == pytest.approx(seq_p, abs=1e-12)

    finally:
        sys.setswitchinterval(old_switch)

    # Storage lock held during fn in update_mastery
    lock_checked: list[bool] = []

    def spy_fn(rec: MasteryRecord | None) -> MasteryRecord:
        lock_checked.append(storage._lock.locked())
        return MasteryRecord(p_known=0.8, n_obs=1, last_updated=T0)

    event = CoachEvent(kind="quiz_answer", topic_id="t3", value=1.0, created_at=T0)
    out = storage.update_mastery("nb1", "u1", "t3", spy_fn, event=event, event_id="lock_test")
    assert out == "applied"
    assert lock_checked == [True]

    # Raising fn leaves snapshot() exactly unchanged on fresh and populated store
    fresh_storage = InMemoryCoachStorage()
    fresh_snap_before = fresh_storage.snapshot()

    def raising_fn(rec: MasteryRecord | None) -> MasteryRecord:
        raise RuntimeError("simulated error inside fn")

    with pytest.raises(RuntimeError):
        fresh_storage.update_mastery(
            "nb_fresh", "u_fresh", "t1", raising_fn, event=event, event_id="qa_raise"
        )
    assert fresh_storage.snapshot() == fresh_snap_before

    def normal_fn(rec: MasteryRecord | None) -> MasteryRecord:
        return MasteryRecord(p_known=0.5, n_obs=1, last_updated=T0)

    out_norm = fresh_storage.update_mastery(
        "nb_fresh", "u_fresh", "t1", normal_fn, event=event, event_id="qa_raise"
    )
    assert out_norm == "applied"

    pop_snap_before = storage.snapshot()
    with pytest.raises(RuntimeError):
        storage.update_mastery("nb1", "u1", "t1", raising_fn, event=event, event_id="qa_pop_raise")
    assert storage.snapshot() == pop_snap_before

    out_pop = storage.update_mastery(
        "nb1", "u1", "t1", normal_fn, event=event, event_id="qa_pop_raise"
    )
    assert out_pop == "applied"

    # Change 5: fn returning non-MasteryRecord raises TypeError and leaves snapshot()
    # unchanged on a fresh store as well as on a populated store
    def bad_return_fn(rec: MasteryRecord | None) -> Any:
        return "not a mastery record"

    # Fresh store
    fresh_storage2 = InMemoryCoachStorage()
    fresh_snap2_before = fresh_storage2.snapshot()
    with pytest.raises(TypeError):
        fresh_storage2.update_mastery(
            "nb_fresh2", "u_fresh2", "t1", bad_return_fn, event=event, event_id="qa_bad_fresh"
        )
    assert fresh_storage2.snapshot() == fresh_snap2_before

    out_fresh_norm = fresh_storage2.update_mastery(
        "nb_fresh2", "u_fresh2", "t1", normal_fn, event=event, event_id="qa_bad_fresh"
    )
    assert out_fresh_norm == "applied"

    # Populated store
    pop_snap_before_bad = storage.snapshot()
    with pytest.raises(TypeError):
        storage.update_mastery(
            "nb1", "u1", "t1", bad_return_fn, event=event, event_id="qa_bad_return"
        )
    assert storage.snapshot() == pop_snap_before_bad

    out_after_bad = storage.update_mastery(
        "nb1", "u1", "t1", normal_fn, event=event, event_id="qa_bad_return"
    )
    assert out_after_bad == "applied"


# ==================== DONE-WHEN 6 ====================
def test_done_when_6_chat_signal_does_not_modify_mastery() -> None:
    storage = InMemoryCoachStorage()
    topics = _make_demo_topics()
    storage.set_topics("nb1", topics)
    initial_rec = MasteryRecord(p_known=0.6, n_obs=3, last_updated=T0)
    storage.set_mastery("nb1", "u1", "t1", initial_rec)

    res = record_chat_signal(
        storage,
        "nb1",
        "u1",
        topic_id="t1",
        message_id="msg_signal",
        coach_on=True,
        now=T0 + timedelta(days=2),
    )
    assert res.outcome == "applied"
    assert res.p_known is None

    stored_rec = storage.get_all_mastery("nb1", "u1")["t1"]
    assert stored_rec.p_known == pytest.approx(0.6, abs=1e-12)
    assert stored_rec.n_obs == 3
    assert stored_rec.last_updated == T0


# ==================== DONE-WHEN 7 & CHANGE 2 ====================
def test_done_when_7_needs_work_scenarios() -> None:
    topics = _make_demo_topics()

    # (a) t1 0.5, t2 0.2, others 0.9 -> [t1, t2] (prerequisite t1 comes first)
    s_a = InMemoryCoachStorage()
    s_a.set_topics("nb1", topics)
    s_a.set_mastery("nb1", "u1", "t1", MasteryRecord(p_known=0.5, n_obs=1, last_updated=T0))
    s_a.set_mastery("nb1", "u1", "t2", MasteryRecord(p_known=0.2, n_obs=1, last_updated=T0))
    for t_id in ["t3", "t4", "t5", "t6"]:
        s_a.set_mastery("nb1", "u1", t_id, MasteryRecord(p_known=0.9, n_obs=1, last_updated=T0))

    nw_a = get_topics_needing_work(s_a, "nb1", "u1", coach_on=True, now=T0, limit=5)
    assert [item.topic_id for item in nw_a.items] == ["t1", "t2"]

    # (b) t3 0.55, t4 0.62, others 0.9
    # Case 1: 3 chat signals on t4 inside window -> t4 before t3
    s_b1 = InMemoryCoachStorage()
    s_b1.set_topics("nb1", topics)
    s_b1.set_mastery("nb1", "u1", "t3", MasteryRecord(p_known=0.55, n_obs=1, last_updated=T0))
    s_b1.set_mastery("nb1", "u1", "t4", MasteryRecord(p_known=0.62, n_obs=1, last_updated=T0))
    for t_id in ["t1", "t2", "t5", "t6"]:
        s_b1.set_mastery("nb1", "u1", t_id, MasteryRecord(p_known=0.9, n_obs=1, last_updated=T0))

    for i in range(3):
        s_b1.add_event(
            "nb1",
            "u1",
            CoachEvent(kind="chat_signal", topic_id="t4", value=1.0, created_at=T0),
            event_id=f"cs_{i}",
        )
    nw_b1 = get_topics_needing_work(s_b1, "nb1", "u1", coach_on=True, now=T0, limit=5)
    assert [item.topic_id for item in nw_b1.items] == ["t4", "t3"]

    # Case 2: 1 inside window, 2 older than 14 days -> t3 before t4
    s_b2 = InMemoryCoachStorage()
    s_b2.set_topics("nb1", topics)
    s_b2.set_mastery("nb1", "u1", "t3", MasteryRecord(p_known=0.55, n_obs=1, last_updated=T0))
    s_b2.set_mastery("nb1", "u1", "t4", MasteryRecord(p_known=0.62, n_obs=1, last_updated=T0))
    for t_id in ["t1", "t2", "t5", "t6"]:
        s_b2.set_mastery("nb1", "u1", t_id, MasteryRecord(p_known=0.9, n_obs=1, last_updated=T0))

    s_b2.add_event(
        "nb1",
        "u1",
        CoachEvent(
            kind="chat_signal", topic_id="t4", value=1.0, created_at=T0 - timedelta(days=5)
        ),
        event_id="cs_in",
    )
    s_b2.add_event(
        "nb1",
        "u1",
        CoachEvent(
            kind="chat_signal", topic_id="t4", value=1.0, created_at=T0 - timedelta(days=15)
        ),
        event_id="cs_old1",
    )
    s_b2.add_event(
        "nb1",
        "u1",
        CoachEvent(
            kind="chat_signal", topic_id="t4", value=1.0, created_at=T0 - timedelta(days=20)
        ),
        event_id="cs_old2",
    )
    nw_b2 = get_topics_needing_work(s_b2, "nb1", "u1", coach_on=True, now=T0, limit=5)
    assert [item.topic_id for item in nw_b2.items] == ["t3", "t4"]

    # (c) new user -> [t1, t2, t3], all with reason "not_started"
    s_c = InMemoryCoachStorage()
    s_c.set_topics("nb1", topics)
    nw_c = get_topics_needing_work(s_c, "nb1", "u1", coach_on=True, now=T0, limit=3)
    assert [item.topic_id for item in nw_c.items] == ["t1", "t2", "t3"]
    for item in nw_c.items:
        assert item.reason == "not_started"

    # (d) "other" never appears, even with low mastery stored for it
    s_d = InMemoryCoachStorage()
    s_d.set_topics("nb1", topics)
    s_d.set_mastery("nb1", "u1", "other", MasteryRecord(p_known=0.1, n_obs=1, last_updated=T0))
    for t_id in ["t1", "t2", "t3", "t4", "t5", "t6"]:
        s_d.set_mastery("nb1", "u1", t_id, MasteryRecord(p_known=0.9, n_obs=1, last_updated=T0))
    nw_d = get_topics_needing_work(s_d, "nb1", "u1", coach_on=True, now=T0, limit=5)
    assert len(nw_d.items) == 0

    # (e) Cycle test: topics A and B are prerequisites of each other, X has prerequisite A
    cycle_topics = [
        TopicInfo(id="A", name="Topic A", order=1, prerequisite_ids=("B",), is_other=False),
        TopicInfo(id="B", name="Topic B", order=2, prerequisite_ids=("A",), is_other=False),
        TopicInfo(id="X", name="Topic X", order=3, prerequisite_ids=("A",), is_other=False),
    ]
    s_e = InMemoryCoachStorage()
    s_e.set_topics("nb_cycle", cycle_topics)
    s_e.set_mastery("nb_cycle", "u1", "X", MasteryRecord(p_known=0.1, n_obs=1, last_updated=T0))
    s_e.set_mastery("nb_cycle", "u1", "A", MasteryRecord(p_known=0.3, n_obs=1, last_updated=T0))
    s_e.set_mastery("nb_cycle", "u1", "B", MasteryRecord(p_known=0.4, n_obs=1, last_updated=T0))

    nw_e = get_topics_needing_work(s_e, "nb_cycle", "u1", coach_on=True, now=T0, limit=20)
    items_e = [item.topic_id for item in nw_e.items]
    assert len(items_e) == 3
    assert set(items_e) == {"A", "B", "X"}
    assert items_e.index("X") > items_e.index("A")
    assert items_e.index("X") > items_e.index("B")

    # Every topic mastered returns empty list
    s_all_mastered = InMemoryCoachStorage()
    s_all_mastered.set_topics("nb1", topics)
    for t in topics:
        s_all_mastered.set_mastery(
            "nb1", "u1", t.id, MasteryRecord(p_known=0.9, n_obs=1, last_updated=T0)
        )
    nw_empty = get_topics_needing_work(
        s_all_mastered, "nb1", "u1", coach_on=True, now=T0, limit=5
    )
    assert len(nw_empty.items) == 0

    # SpyStorage asserts exactly one list_events call in coach-on needs-work
    spy_nw = SpyStorage(s_a)
    get_topics_needing_work(spy_nw, "nb1", "u1", coach_on=True, now=T0, limit=5)
    assert spy_nw.calls["list_events"] == 1


def test_done_when_7_change_2_graph_scenarios() -> None:
    # Change 2.1: self-prerequisite: topics a (prereq ["a"]) and b (prereq ["a"]),
    # both candidates -> [a, b]
    s_self = InMemoryCoachStorage()
    s_self.set_topics(
        "nb_self",
        [
            TopicInfo(id="a", name="Topic A", order=1, prerequisite_ids=("a",), is_other=False),
            TopicInfo(id="b", name="Topic B", order=2, prerequisite_ids=("a",), is_other=False),
            TopicInfo(id="c", name="Topic C", order=3, prerequisite_ids=(), is_other=False),
        ],
    )
    s_self.set_mastery("nb_self", "u1", "a", MasteryRecord(p_known=0.4, n_obs=1, last_updated=T0))
    s_self.set_mastery("nb_self", "u1", "b", MasteryRecord(p_known=0.3, n_obs=1, last_updated=T0))
    s_self.set_mastery("nb_self", "u1", "c", MasteryRecord(p_known=0.9, n_obs=1, last_updated=T0))

    nw_self = get_topics_needing_work(s_self, "nb_self", "u1", coach_on=True, now=T0, limit=5)
    assert [item.topic_id for item in nw_self.items] == ["a", "b"]

    # Change 2.2: unknown prerequisite: topic a (prereq ["ghost"]) and topic b with
    # prereq "a" -> [a, b]
    s_ghost = InMemoryCoachStorage()
    s_ghost.set_topics(
        "nb_ghost",
        [
            TopicInfo(id="a", name="Topic A", order=1, prerequisite_ids=("ghost",), is_other=False),
            TopicInfo(id="b", name="Topic B", order=2, prerequisite_ids=("a",), is_other=False),
            TopicInfo(id="c", name="Topic C", order=3, prerequisite_ids=(), is_other=False),
        ],
    )
    s_ghost.set_mastery("nb_ghost", "u1", "a", MasteryRecord(p_known=0.4, n_obs=1, last_updated=T0))
    s_ghost.set_mastery("nb_ghost", "u1", "b", MasteryRecord(p_known=0.2, n_obs=1, last_updated=T0))
    s_ghost.set_mastery("nb_ghost", "u1", "c", MasteryRecord(p_known=0.9, n_obs=1, last_updated=T0))

    nw_ghost = get_topics_needing_work(s_ghost, "nb_ghost", "u1", coach_on=True, now=T0, limit=5)
    assert [item.topic_id for item in nw_ghost.items] == ["a", "b"]

    # Change 2.3: limit 1 with Done-when 7(a) data -> exactly one item and it is t1
    topics = _make_demo_topics()
    s_lim1 = InMemoryCoachStorage()
    s_lim1.set_topics("nb1", topics)
    s_lim1.set_mastery("nb1", "u1", "t1", MasteryRecord(p_known=0.5, n_obs=1, last_updated=T0))
    s_lim1.set_mastery("nb1", "u1", "t2", MasteryRecord(p_known=0.2, n_obs=1, last_updated=T0))
    for t_id in ["t3", "t4", "t5", "t6"]:
        s_lim1.set_mastery("nb1", "u1", t_id, MasteryRecord(p_known=0.9, n_obs=1, last_updated=T0))

    nw_lim1 = get_topics_needing_work(s_lim1, "nb1", "u1", coach_on=True, now=T0, limit=1)
    assert len(nw_lim1.items) == 1
    assert nw_lim1.items[0].topic_id == "t1"

    # Change 2.4 / M2: chat signals never make a mastered topic a candidate:
    # t1 stored 0.72 with 3 chat signals inside window, other topics mastered -> t1 not in result
    s_sig = InMemoryCoachStorage()
    s_sig.set_topics("nb1", topics)
    s_sig.set_mastery("nb1", "u1", "t1", MasteryRecord(p_known=0.72, n_obs=1, last_updated=T0))
    for t_id in ["t2", "t3", "t4", "t5", "t6"]:
        s_sig.set_mastery("nb1", "u1", t_id, MasteryRecord(p_known=0.9, n_obs=1, last_updated=T0))
    for i in range(3):
        s_sig.add_event(
            "nb1",
            "u1",
            CoachEvent(kind="chat_signal", topic_id="t1", value=1.0, created_at=T0),
            event_id=f"cs_sig_{i}",
        )
    nw_sig = get_topics_needing_work(s_sig, "nb1", "u1", coach_on=True, now=T0, limit=5)
    assert "t1" not in [item.topic_id for item in nw_sig.items]
    assert len(nw_sig.items) == 0


# ==================== DONE-WHEN 8 & CHANGE 1 ====================
INVALID_IDS = [
    "a1\n", "a1 ", " a1", "", "a" * 129, "\u00e91", "\u0661\u0662", "\uff211", "a1\u00a0",
    "a/b", "a.b", StrSubclass("a1"), None, 12,
]
VALID_IDS = ["a" * 128, "Ab_9-z"]

INVALID_SCORES = [
    True, False, "0.5", None, Decimal("0.5"), Fraction(1, 2), 10**400, -1, 2,
    float("nan"), float("inf"), float("-inf"), 1.0000001, -1e-9, DummyEnum.TWO, FloatSubclass(2.0),
]
VALID_SCORES = [0, 1, 0.0, 1.0, 0.5, -0.0, DummyEnum.ONE, FloatSubclass(0.5)]

INVALID_TIMES = [datetime(2026, 10, 7), "2026-10-07", None]
INVALID_FLAGS = [1, 0, None, "true"]
INVALID_QUESTION_TYPES = ["MCQ", "", None, "essay", StrSubclass("mcq")]
INVALID_LIMITS = [0, 21, True, 3.0, None]
VALID_LIMITS = [1, 20]


# Change 1: Parametrized invalid IDs on all calls taking them
@pytest.mark.parametrize("inv_id", INVALID_IDS)
@pytest.mark.parametrize("arg_name", ["nb", "uid", "topic_id"])
def test_validation_invalid_ids_record_calls(inv_id: Any, arg_name: str) -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())
    spy = SpyStorage(storage)
    snap_before = spy.snapshot()

    kwargs: dict[str, Any] = {
        "nb": "nb1",
        "uid": "u1",
        "topic_id": "t1",
    }
    kwargs[arg_name] = inv_id

    # 1. record_quiz_answer
    with pytest.raises(CoachInputError) as exc_info:
        record_quiz_answer(
            spy,
            kwargs["nb"],
            kwargs["uid"],
            topic_id=kwargs["topic_id"],
            attempt_id="a1",
            question_type="mcq",
            score=1.0,
            coach_on=True,
            now=T0,
        )
    assert str(exc_info.value).startswith("invalid id:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0

    # 2. record_chat_signal
    with pytest.raises(CoachInputError) as exc_info:
        record_chat_signal(
            spy,
            kwargs["nb"],
            kwargs["uid"],
            topic_id=kwargs["topic_id"],
            message_id="m1",
            coach_on=True,
            now=T0,
        )
    assert str(exc_info.value).startswith("invalid id:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0

    # 3. record_checkbox
    with pytest.raises(CoachInputError) as exc_info:
        record_checkbox(
            spy,
            kwargs["nb"],
            kwargs["uid"],
            topic_id=kwargs["topic_id"],
            checked=True,
            coach_on=True,
            now=T0,
        )
    assert str(exc_info.value).startswith("invalid id:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0


@pytest.mark.parametrize("inv_id", INVALID_IDS)
@pytest.mark.parametrize("arg_name", ["nb", "uid"])
def test_validation_invalid_ids_read_calls(inv_id: Any, arg_name: str) -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())
    spy = SpyStorage(storage)
    snap_before = spy.snapshot()

    kwargs: dict[str, Any] = {"nb": "nb1", "uid": "u1"}
    kwargs[arg_name] = inv_id

    # 4. get_topics_needing_work
    with pytest.raises(CoachInputError) as exc_info:
        get_topics_needing_work(
            spy, kwargs["nb"], kwargs["uid"], coach_on=True, now=T0
        )
    assert str(exc_info.value).startswith("invalid id:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0

    # 5. get_progress
    with pytest.raises(CoachInputError) as exc_info:
        get_progress(spy, kwargs["nb"], kwargs["uid"], coach_on=True, now=T0)
    assert str(exc_info.value).startswith("invalid id:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0


@pytest.mark.parametrize("inv_id", INVALID_IDS)
def test_validation_invalid_ids_call_specific(inv_id: Any) -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())
    spy = SpyStorage(storage)
    snap_before = spy.snapshot()

    # attempt_id on record_quiz_answer
    with pytest.raises(CoachInputError) as exc_info:
        record_quiz_answer(
            spy,
            "nb1",
            "u1",
            topic_id="t1",
            attempt_id=inv_id,
            question_type="mcq",
            score=1.0,
            coach_on=True,
            now=T0,
        )
    assert str(exc_info.value).startswith("invalid id:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0

    # message_id on record_chat_signal
    with pytest.raises(CoachInputError) as exc_info:
        record_chat_signal(
            spy, "nb1", "u1", topic_id="t1", message_id=inv_id, coach_on=True, now=T0
        )
    assert str(exc_info.value).startswith("invalid id:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0


@pytest.mark.parametrize("valid_id", VALID_IDS)
def test_validation_valid_ids_on_all_five_calls(valid_id: str) -> None:
    # Positive tests verifying VALID_IDS work on all 5 calls
    storage = InMemoryCoachStorage()
    storage.set_topics(valid_id, [
        TopicInfo(id=valid_id, name="Valid Topic", order=1, prerequisite_ids=(), is_other=False)
    ])

    # 1. record_quiz_answer
    r1 = record_quiz_answer(
        storage,
        valid_id,
        valid_id,
        topic_id=valid_id,
        attempt_id=valid_id,
        question_type="mcq",
        score=1.0,
        coach_on=True,
        now=T0,
    )
    assert r1.outcome == "applied"

    # 2. record_chat_signal
    r2 = record_chat_signal(
        storage,
        valid_id,
        valid_id,
        topic_id=valid_id,
        message_id=valid_id,
        coach_on=True,
        now=T0,
    )
    assert r2.outcome == "applied"

    # 3. record_checkbox
    r3 = record_checkbox(
        storage,
        valid_id,
        valid_id,
        topic_id=valid_id,
        checked=True,
        coach_on=True,
        now=T0,
    )
    assert r3.outcome == "applied"

    # 4. get_topics_needing_work
    nw = get_topics_needing_work(storage, valid_id, valid_id, coach_on=True, now=T0)
    assert nw.coach_on is True

    # 5. get_progress
    prog = get_progress(storage, valid_id, valid_id, coach_on=True, now=T0)
    assert prog.coach_on is True


# Change 1: now on all five calls
@pytest.mark.parametrize("inv_time", INVALID_TIMES)
def test_validation_invalid_now_on_all_five_calls(inv_time: Any) -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())
    spy = SpyStorage(storage)
    snap_before = spy.snapshot()

    # 1. record_quiz_answer
    with pytest.raises(CoachInputError) as exc_info:
        record_quiz_answer(
            spy,
            "nb1",
            "u1",
            topic_id="t1",
            attempt_id="a1",
            question_type="mcq",
            score=1.0,
            coach_on=True,
            now=inv_time,
        )
    assert str(exc_info.value).startswith("invalid time:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0

    # 2. record_chat_signal
    with pytest.raises(CoachInputError) as exc_info:
        record_chat_signal(
            spy, "nb1", "u1", topic_id="t1", message_id="m1", coach_on=True, now=inv_time
        )
    assert str(exc_info.value).startswith("invalid time:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0

    # 3. record_checkbox
    with pytest.raises(CoachInputError) as exc_info:
        record_checkbox(
            spy, "nb1", "u1", topic_id="t1", checked=True, coach_on=True, now=inv_time
        )
    assert str(exc_info.value).startswith("invalid time:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0

    # 4. get_topics_needing_work
    with pytest.raises(CoachInputError) as exc_info:
        get_topics_needing_work(spy, "nb1", "u1", coach_on=True, now=inv_time)
    assert str(exc_info.value).startswith("invalid time:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0

    # 5. get_progress
    with pytest.raises(CoachInputError) as exc_info:
        get_progress(spy, "nb1", "u1", coach_on=True, now=inv_time)
    assert str(exc_info.value).startswith("invalid time:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0


# Change 1: coach_on on all five calls
@pytest.mark.parametrize("inv_flag", INVALID_FLAGS)
def test_validation_invalid_coach_on_on_all_five_calls(inv_flag: Any) -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())
    spy = SpyStorage(storage)
    snap_before = spy.snapshot()

    # 1. record_quiz_answer
    with pytest.raises(CoachInputError) as exc_info:
        record_quiz_answer(
            spy,
            "nb1",
            "u1",
            topic_id="t1",
            attempt_id="a1",
            question_type="mcq",
            score=1.0,
            coach_on=inv_flag,
            now=T0,
        )
    assert str(exc_info.value).startswith("invalid flag:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0

    # 2. record_chat_signal
    with pytest.raises(CoachInputError) as exc_info:
        record_chat_signal(
            spy, "nb1", "u1", topic_id="t1", message_id="m1", coach_on=inv_flag, now=T0
        )
    assert str(exc_info.value).startswith("invalid flag:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0

    # 3. record_checkbox
    with pytest.raises(CoachInputError) as exc_info:
        record_checkbox(
            spy, "nb1", "u1", topic_id="t1", checked=True, coach_on=inv_flag, now=T0
        )
    assert str(exc_info.value).startswith("invalid flag:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0

    # 4. get_topics_needing_work
    with pytest.raises(CoachInputError) as exc_info:
        get_topics_needing_work(spy, "nb1", "u1", coach_on=inv_flag, now=T0)
    assert str(exc_info.value).startswith("invalid flag:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0

    # 5. get_progress
    with pytest.raises(CoachInputError) as exc_info:
        get_progress(spy, "nb1", "u1", coach_on=inv_flag, now=T0)
    assert str(exc_info.value).startswith("invalid flag:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0


# Change 1: checked on record_checkbox
@pytest.mark.parametrize("inv_flag", INVALID_FLAGS)
def test_validation_invalid_checked_on_record_checkbox(inv_flag: Any) -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())
    spy = SpyStorage(storage)
    snap_before = spy.snapshot()

    with pytest.raises(CoachInputError) as exc_info:
        record_checkbox(
            spy, "nb1", "u1", topic_id="t1", checked=inv_flag, coach_on=True, now=T0
        )
    assert str(exc_info.value).startswith("invalid flag:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0


# Change 1: score on record_quiz_answer
@pytest.mark.parametrize("inv_score", INVALID_SCORES)
def test_validation_invalid_scores_on_record_quiz_answer(inv_score: Any) -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())
    spy = SpyStorage(storage)
    snap_before = spy.snapshot()

    with pytest.raises(CoachInputError) as exc_info:
        record_quiz_answer(
            spy,
            "nb1",
            "u1",
            topic_id="t1",
            attempt_id="a1",
            question_type="mcq",
            score=inv_score,
            coach_on=True,
            now=T0,
        )
    assert str(exc_info.value).startswith("invalid score:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0


@pytest.mark.parametrize("v_score", VALID_SCORES)
def test_validation_valid_scores(v_score: Any) -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())
    res = record_quiz_answer(
        storage,
        "nb1",
        "u1",
        topic_id="t1",
        attempt_id="v_att_valid",
        question_type="mcq",
        score=v_score,
        coach_on=True,
        now=T0,
    )
    assert res.outcome == "applied"
    # L14: assert stored quiz_answer event has type(value) is float and value == float(score)
    qa_event = storage.snapshot()["events"][("nb1", "u1")]["qa_v_att_valid"]
    assert type(qa_event.value) is float
    assert qa_event.value == float(v_score)


# Change 1: question_type on record_quiz_answer
@pytest.mark.parametrize("inv_qt", INVALID_QUESTION_TYPES)
def test_validation_invalid_question_types_on_record_quiz_answer(inv_qt: Any) -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())
    spy = SpyStorage(storage)
    snap_before = spy.snapshot()

    with pytest.raises(CoachInputError) as exc_info:
        record_quiz_answer(
            spy,
            "nb1",
            "u1",
            topic_id="t1",
            attempt_id="a1",
            question_type=inv_qt,
            score=1.0,
            coach_on=True,
            now=T0,
        )
    assert str(exc_info.value).startswith("invalid question type:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0


# Change 1: limit on get_topics_needing_work
@pytest.mark.parametrize("inv_lim", INVALID_LIMITS)
def test_validation_invalid_limits_on_get_topics_needing_work(inv_lim: Any) -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())
    spy = SpyStorage(storage)
    snap_before = spy.snapshot()

    with pytest.raises(CoachInputError) as exc_info:
        get_topics_needing_work(spy, "nb1", "u1", coach_on=True, now=T0, limit=inv_lim)
    assert str(exc_info.value).startswith("invalid limit:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0


@pytest.mark.parametrize("v_lim", VALID_LIMITS)
def test_validation_valid_limits(v_lim: int) -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())
    nw = get_topics_needing_work(storage, "nb1", "u1", coach_on=True, now=T0, limit=v_lim)
    assert nw.coach_on is True


# Change 1 / M8: unknown topic on the three record calls parametrized over coach_on
@pytest.mark.parametrize("coach_on", [True, False])
def test_validation_unknown_topic_on_all_three_record_calls(coach_on: bool) -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())

    # 1. record_quiz_answer
    spy1 = SpyStorage(storage)
    snap1 = spy1.snapshot()
    with pytest.raises(CoachInputError) as exc_info:
        record_quiz_answer(
            spy1,
            "nb1",
            "u1",
            topic_id="unknown_t",
            attempt_id="a1",
            question_type="mcq",
            score=1.0,
            coach_on=coach_on,
            now=T0,
        )
    assert str(exc_info.value).startswith("unknown topic:")
    assert spy1.snapshot() == snap1
    assert spy1.calls == {"get_topics": 1}

    # 2. record_chat_signal
    spy2 = SpyStorage(storage)
    snap2 = spy2.snapshot()
    with pytest.raises(CoachInputError) as exc_info:
        record_chat_signal(
            spy2, "nb1", "u1", topic_id="unknown_t", message_id="m1", coach_on=coach_on, now=T0
        )
    assert str(exc_info.value).startswith("unknown topic:")
    assert spy2.snapshot() == snap2
    assert spy2.calls == {"get_topics": 1}

    # 3. record_checkbox
    spy3 = SpyStorage(storage)
    snap3 = spy3.snapshot()
    with pytest.raises(CoachInputError) as exc_info:
        record_checkbox(
            spy3, "nb1", "u1", topic_id="unknown_t", checked=True, coach_on=coach_on, now=T0
        )
    assert str(exc_info.value).startswith("unknown topic:")
    assert spy3.snapshot() == snap3
    assert spy3.calls == {"get_topics": 1}


# Change 4: Time conversion with UTC+05:30
def test_change_4_time_conversion_offset() -> None:
    t0_offset = T0.astimezone(timezone(timedelta(hours=5, minutes=30)))

    # Storage for offset calls
    s_offset = InMemoryCoachStorage()
    s_offset.set_topics("nb1", _make_demo_topics())

    # Storage for UTC calls
    s_utc = InMemoryCoachStorage()
    s_utc.set_topics("nb1", _make_demo_topics())

    # Quiz answer
    r_qa_off = record_quiz_answer(
        s_offset,
        "nb1",
        "u1",
        topic_id="t1",
        attempt_id="qa_1",
        question_type="mcq",
        score=1.0,
        coach_on=True,
        now=t0_offset,
    )
    r_qa_utc = record_quiz_answer(
        s_utc,
        "nb1",
        "u1",
        topic_id="t1",
        attempt_id="qa_1",
        question_type="mcq",
        score=1.0,
        coach_on=True,
        now=T0,
    )
    rec_qa = s_offset.get_all_mastery("nb1", "u1")["t1"]
    ev_qa = s_offset.list_events("nb1", "u1", kind="quiz_answer", since=T0 - timedelta(days=1))[0]
    assert rec_qa.last_updated.utcoffset() == timedelta(0)
    assert ev_qa.created_at.utcoffset() == timedelta(0)
    assert r_qa_off.p_known == r_qa_utc.p_known
    assert rec_qa.p_known == s_utc.get_all_mastery("nb1", "u1")["t1"].p_known

    # Checkbox tick
    r_cb_off = record_checkbox(
        s_offset, "nb1", "u1", topic_id="t2", checked=True, coach_on=True, now=t0_offset
    )
    r_cb_utc = record_checkbox(
        s_utc, "nb1", "u1", topic_id="t2", checked=True, coach_on=True, now=T0
    )
    rec_cb = s_offset.get_all_mastery("nb1", "u1")["t2"]
    ev_cb = s_offset.list_events("nb1", "u1", kind="checkbox", since=T0 - timedelta(days=1))[0]
    assert rec_cb.last_updated.utcoffset() == timedelta(0)
    assert ev_cb.created_at.utcoffset() == timedelta(0)
    assert r_cb_off.p_known == r_cb_utc.p_known
    assert rec_cb.p_known == s_utc.get_all_mastery("nb1", "u1")["t2"].p_known

    # Chat signal
    r_cs_off = record_chat_signal(
        s_offset, "nb1", "u1", topic_id="t1", message_id="cs_1", coach_on=True, now=t0_offset
    )
    r_cs_utc = record_chat_signal(
        s_utc, "nb1", "u1", topic_id="t1", message_id="cs_1", coach_on=True, now=T0
    )
    ev_cs = s_offset.list_events("nb1", "u1", kind="chat_signal", since=T0 - timedelta(days=1))[0]
    assert ev_cs.created_at.utcoffset() == timedelta(0)
    assert r_cs_off.outcome == r_cs_utc.outcome


# Change 7: OverflowError with extreme datetimes on all five calls
@pytest.mark.parametrize("extreme_now", [
    datetime.min.replace(tzinfo=UTC),
    datetime.max.replace(tzinfo=UTC),
])
@pytest.mark.parametrize("call_name", ["quiz", "chat", "checkbox", "needs_work", "progress"])
def test_change_7_time_extremes_overflow(extreme_now: datetime, call_name: str) -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())

    try:
        if call_name == "quiz":
            record_quiz_answer(
                storage,
                "nb1",
                "u1",
                topic_id="t1",
                attempt_id="a_ext",
                question_type="mcq",
                score=1.0,
                coach_on=True,
                now=extreme_now,
            )
        elif call_name == "chat":
            record_chat_signal(
                storage,
                "nb1",
                "u1",
                topic_id="t1",
                message_id="m_ext",
                coach_on=True,
                now=extreme_now,
            )
        elif call_name == "checkbox":
            record_checkbox(
                storage, "nb1", "u1", topic_id="t1", checked=True, coach_on=True, now=extreme_now
            )
        elif call_name == "needs_work":
            get_topics_needing_work(storage, "nb1", "u1", coach_on=True, now=extreme_now)
        elif call_name == "progress":
            get_progress(storage, "nb1", "u1", coach_on=True, now=extreme_now)
    except CoachInputError as exc:
        assert str(exc).startswith("invalid time:")
    except Exception as exc:
        pytest.fail(f"{call_name} raised unexpected exception {type(exc).__name__}: {exc}")


def test_ignored_other_writes_nothing() -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())
    snap_before = storage.snapshot()

    r1 = record_quiz_answer(
        storage,
        "nb1",
        "u1",
        topic_id="other",
        attempt_id="a1",
        question_type="mcq",
        score=1.0,
        coach_on=True,
        now=T0,
    )
    assert r1.outcome == "ignored_other"
    assert r1.p_known is None
    assert storage.snapshot() == snap_before

    r2 = record_chat_signal(
        storage, "nb1", "u1", topic_id="other", message_id="m1", coach_on=True, now=T0
    )
    assert r2.outcome == "ignored_other"
    assert r2.p_known is None
    assert storage.snapshot() == snap_before

    r3 = record_checkbox(
        storage, "nb1", "u1", topic_id="other", checked=True, coach_on=True, now=T0
    )
    assert r3.outcome == "ignored_other"
    assert r3.p_known is None
    assert storage.snapshot() == snap_before


# ==================== DONE-WHEN 9 ====================
def _run_fuzz_simulation(seed: int) -> tuple[dict[str, Any], list[RecordResult]]:
    rng = random.Random(seed)
    topics = _make_demo_topics()
    topic_ids = [t.id for t in topics]
    q_types: list[QuestionType] = ["mcq", "short", "numerical"]

    storage = InMemoryCoachStorage()
    storage.set_topics("nb_fuzz", topics)

    results: list[RecordResult] = []
    current_time = T0
    applied_quiz_answers = 0

    for i in range(2000):
        time_delta_days = rng.uniform(-2.0, 5.0)
        current_time = current_time + timedelta(days=time_delta_days)
        tid = rng.choice(topic_ids)
        op = rng.choice(["quiz", "chat", "checkbox"])
        coach_on = rng.choice([True, True, True, False])

        stored_map = storage.get_all_mastery("nb_fuzz", "u_fuzz")
        old_rec = stored_map.get(tid)
        if old_rec is None:
            base_p = bkt.PRIOR
        else:
            elapsed = max(0.0, (current_time - old_rec.last_updated).total_seconds() / 86400.0)
            base_p = bkt.decay(old_rec.p_known, elapsed)

        if op == "quiz":
            qt = rng.choice(q_types)
            score = rng.choice([0.0, 0.25, 0.5, 0.75, 1.0])
            att_id = (
                f"fuzz_att_{rng.randint(0, i):04d}"
                if i > 0 and rng.random() < 0.2
                else f"fuzz_att_{i:04d}"
            )
            res = record_quiz_answer(
                storage,
                "nb_fuzz",
                "u_fuzz",
                topic_id=tid,
                attempt_id=att_id,
                question_type=qt,
                score=score,
                coach_on=coach_on,
                now=current_time,
            )
            results.append(res)

            if res.outcome == "applied":
                applied_quiz_answers += 1
                new_rec = storage.get_all_mastery("nb_fuzz", "u_fuzz")[tid]
                expected_lu = (
                    current_time if old_rec is None else max(old_rec.last_updated, current_time)
                )
                assert new_rec.last_updated == expected_lu
                expected_p = bkt.update(base_p, bkt.is_correct(score), qt)
                assert new_rec.p_known == pytest.approx(expected_p, abs=1e-12)
                if bkt.is_correct(score):
                    assert new_rec.p_known >= base_p - 1e-12
                assert bkt.update(base_p, True, qt) >= bkt.update(base_p, False, qt)

        elif op == "chat":
            msg_id = (
                f"fuzz_msg_{rng.randint(0, i):04d}"
                if i > 0 and rng.random() < 0.2
                else f"fuzz_msg_{i:04d}"
            )
            res = record_chat_signal(
                storage,
                "nb_fuzz",
                "u_fuzz",
                topic_id=tid,
                message_id=msg_id,
                coach_on=coach_on,
                now=current_time,
            )
            results.append(res)

        else:
            checked = rng.choice([True, False])
            res = record_checkbox(
                storage,
                "nb_fuzz",
                "u_fuzz",
                topic_id=tid,
                checked=checked,
                coach_on=coach_on,
                now=current_time,
            )
            results.append(res)
            if res.outcome == "applied" and checked:
                new_rec = storage.get_all_mastery("nb_fuzz", "u_fuzz")[tid]
                expected_lu = (
                    current_time if old_rec is None else max(old_rec.last_updated, current_time)
                )
                assert new_rec.last_updated == expected_lu
                assert new_rec.p_known == pytest.approx(max(base_p, 0.8), abs=1e-12)

    final_mastery = storage.get_all_mastery("nb_fuzz", "u_fuzz")
    for _tid, rec in final_mastery.items():
        assert 0.0 <= rec.p_known <= 1.0

    total_n_obs = sum(rec.n_obs for rec in final_mastery.values())
    assert total_n_obs == applied_quiz_answers

    return storage.snapshot(), results


def test_done_when_9_fuzz_2000_operations() -> None:
    snap1, res1 = _run_fuzz_simulation(20261007)
    snap2, res2 = _run_fuzz_simulation(20261007)

    assert snap1 == snap2
    assert len(res1) == len(res2)
    for r1, r2 in zip(res1, res2, strict=True):
        assert r1 == r2


# ==================== DONE-WHEN 10 & CHANGE 8 ====================
def test_done_when_10_seal_all_exports() -> None:
    import app.coach

    expected_all = [
        "record_quiz_answer",
        "record_chat_signal",
        "record_checkbox",
        "get_topics_needing_work",
        "get_progress",
        "CoachStorage",
        "InMemoryCoachStorage",
        "CoachInputError",
        "TopicInfo",
        "MasteryRecord",
        "CoachEvent",
        "RecordResult",
        "TopicProgress",
        "Progress",
        "NeedsWorkItem",
        "NeedsWork",
        "QuestionType",
        "EventKind",
        "Outcome",
        "ProgressLabel",
        "NeedsWorkReason",
    ]
    assert list(app.coach.__all__) == expected_all


def _resolve_import_from(
    module: str, is_package: bool, level: int, import_module: str | None
) -> str:
    parts = module.split(".")
    package_parts = parts if is_package else parts[:-1]
    drop_count = level - 1
    if drop_count > len(package_parts):
        base_parts: list[str] = []
    elif drop_count > 0:
        base_parts = package_parts[:-drop_count]
    else:
        base_parts = list(package_parts)
    if import_module:
        base_parts.extend(import_module.split("."))
    return ".".join(base_parts)


def scan_source(source: str, module: str, is_package: bool = False) -> list[str]:
    violations: list[str] = []
    tree = ast.parse(source)
    is_inside_coach = module == "app.coach" or module.startswith("app.coach.")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                if not is_inside_coach:
                    if target.startswith("app.coach."):
                        violations.append(f"Import {target} outside coach")
                else:
                    if target == "time" or target.startswith("time."):
                        violations.append(f"Import {target} inside coach")
                    elif any(
                        target == p or target.startswith(p + ".")
                        for p in (
                            "firebase_admin", "google", "fastapi", "app.db", "app.llm", "app.api"
                        )
                    ):
                        violations.append(f"Import {target} inside coach")

        elif isinstance(node, ast.ImportFrom):
            level = node.level
            if level == 0:
                target = node.module or ""
            else:
                target = _resolve_import_from(module, is_package, level, node.module)

            if not is_inside_coach:
                if target.startswith("app.coach."):
                    violations.append(f"ImportFrom {target} outside coach")
                elif target == "app.coach":
                    for alias in node.names:
                        if alias.name in ("bkt", "types", "storage", "core"):
                            violations.append(f"ImportFrom app.coach import {alias.name}")
            else:
                if target == "time" or target.startswith("time."):
                    violations.append(f"ImportFrom {target} inside coach")
                elif target == "app":
                    for alias in node.names:
                        full = f"app.{alias.name}"
                        if any(
                            full == p or full.startswith(p + ".")
                            for p in (
                                "firebase_admin",
                                "google",
                                "fastapi",
                                "app.db",
                                "app.llm",
                                "app.api",
                            )
                        ):
                            violations.append(f"ImportFrom {full} inside coach")
                elif any(
                    target == p or target.startswith(p + ".")
                    for p in (
                        "firebase_admin", "google", "fastapi", "app.db", "app.llm", "app.api"
                    )
                ):
                    violations.append(f"ImportFrom {target} inside coach")

        elif isinstance(node, ast.Call):
            if is_inside_coach:
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in ("now", "utcnow", "today"):
                    violations.append(f"Call to {func.attr} inside coach")

    return violations


def test_done_when_10_scan_source_self_test() -> None:
    positives = [
        ("from app.coach.bkt import x", "app.main", False),
        ("import app.coach.bkt", "app.main", False),
        ("from app.coach import bkt", "app.main", False),
        ("from .coach import bkt", "app.main", False),
        ("from ..coach.core import x", "app.api.topics", False),
        ("import firebase_admin", "app.coach.core", False),
        ("import google.cloud", "app.coach.core", False),
        ("from fastapi import FastAPI", "app.coach.core", False),
        ("from app.llm import client", "app.coach.core", False),
        ("from app.api import notebooks", "app.coach.core", False),
        ("from app.db import topics", "app.coach.core", False),
        ("from ..db import topics", "app.coach.core", False),
        ("import time", "app.coach.core", False),
        ("from time import monotonic", "app.coach.core", False),
        ("from datetime import datetime\ndatetime.now()", "app.coach.core", False),
        ("import datetime\ndatetime.datetime.utcnow()", "app.coach.core", False),
        ("from datetime import date\ndate.today()", "app.coach.core", False),
        # Change 8 additions
        ("from app import db", "app.coach.core", False),
        ("from app import llm", "app.coach.core", False),
        ("from app import api", "app.coach.core", False),
    ]
    for src, mod, is_pkg in positives:
        v = scan_source(src, mod, is_pkg)
        assert len(v) == 1, f"Expected 1 violation for {src!r} in {mod}, got {v}"

    negatives = [
        ("from app.coach import record_quiz_answer", "app.main", False),
        ("from .coach import get_progress", "app.main", False),
        ("from .coach import bkt", "app.api.topics", False),
        ("from datetime import UTC, datetime", "app.coach.core", False),
        ("from .types import MasteryRecord", "app.coach.core", False),
        ("from . import bkt", "app.coach.core", False),
        # Change 8 addition: outside coach package gives 0 violations
        ("from app import db", "app.main", False),
    ]
    for src, mod, is_pkg in negatives:
        v = scan_source(src, mod, is_pkg)
        assert len(v) == 0, f"Expected 0 violations for {src!r} in {mod}, got {v}"


def test_done_when_10_real_backend_app_scan() -> None:
    app_dir = Path(__file__).parent.parent / "app"
    all_violations: dict[str, list[str]] = {}

    for path in app_dir.rglob("*.py"):
        rel = path.relative_to(app_dir.parent)
        parts = list(rel.parts)
        is_pkg = parts[-1] == "__init__.py"
        if is_pkg:
            mod_parts = parts[:-1]
        else:
            mod_parts = parts[:-1] + [parts[-1][:-3]]
        mod_name = ".".join(mod_parts)

        raw = path.read_bytes()
        try:
            source = raw.decode("utf-8")
        except Exception:
            source = raw.decode("latin-1")

        try:
            v = scan_source(source, mod_name, is_package=is_pkg)
            if v:
                all_violations[str(path)] = v
        except Exception as exc:
            pytest.fail(f"Failed to parse {path}: {exc}")

    assert all_violations == {}


# ==================== DONE-WHEN 11 & CHANGE 3 ====================
def test_done_when_11_get_progress_coach_on() -> None:
    storage = InMemoryCoachStorage()
    topics = _make_demo_topics()
    storage.set_topics("nb1", topics)
    storage.set_checked("nb1", "u1", {"t1": True, "t2": False})

    # New user: every non-other topic has p_known 0.3, n_obs 0, last_updated None,
    # label "not_started"
    p_new = get_progress(storage, "nb1", "u1", coach_on=True, now=T0)
    assert p_new.coach_on is True
    assert len(p_new.topics) == 6
    assert [tp.topic_id for tp in p_new.topics] == ["t1", "t2", "t3", "t4", "t5", "t6"]
    assert p_new.topics[0].checked is True
    assert p_new.topics[1].checked is False

    for tp in p_new.topics:
        assert tp.p_known == pytest.approx(0.3, abs=1e-12)
        assert tp.n_obs == 0
        assert tp.last_updated is None
        assert tp.label == "not_started"

    # Stored 0.9 read 14 days later -> p_known 0.6, label "learning"
    storage.set_mastery("nb1", "u1", "t1", MasteryRecord(p_known=0.9, n_obs=2, last_updated=T0))
    p_14d = get_progress(storage, "nb1", "u1", coach_on=True, now=T0 + timedelta(days=14))
    tp1 = next(t for t in p_14d.topics if t.topic_id == "t1")
    assert tp1.p_known == pytest.approx(0.6, abs=1e-12)
    assert tp1.label == "learning"
    assert tp1.n_obs == 2
    assert tp1.last_updated == T0

    # Stored 0.7 at 0 days ("mastered") vs stored 0.6999 ("learning")
    storage.set_mastery("nb1", "u1", "t2", MasteryRecord(p_known=0.7, n_obs=1, last_updated=T0))
    storage.set_mastery("nb1", "u1", "t3", MasteryRecord(p_known=0.6999, n_obs=1, last_updated=T0))

    p_thresh = get_progress(storage, "nb1", "u1", coach_on=True, now=T0)
    tp2 = next(t for t in p_thresh.topics if t.topic_id == "t2")
    tp3 = next(t for t in p_thresh.topics if t.topic_id == "t3")
    assert tp2.p_known == pytest.approx(0.7, abs=1e-12)
    assert tp2.label == "mastered"
    assert tp3.p_known == pytest.approx(0.6999, abs=1e-12)
    assert tp3.label == "learning"


def test_change_3_get_progress_ordering_and_rounding() -> None:
    # Change 3: Shuffled insertion order, tie-breaking by topic_id, >4 decimals rounding
    storage = InMemoryCoachStorage()
    # Shuffled order with orders:
    # t_c: order 2
    # t_b: order 1
    # t_a: order 1 (t_a should tie-break before t_b)
    # t_d: order 3
    # other: order 0, is_other=True (excluded)
    shuffled_topics = [
        TopicInfo(id="t_c", name="Topic C", order=2, prerequisite_ids=(), is_other=False),
        TopicInfo(id="other", name="Other", order=0, prerequisite_ids=(), is_other=True),
        TopicInfo(id="t_b", name="Topic B", order=1, prerequisite_ids=(), is_other=False),
        TopicInfo(id="t_d", name="Topic D", order=3, prerequisite_ids=(), is_other=False),
        TopicInfo(id="t_a", name="Topic A", order=1, prerequisite_ids=(), is_other=False),
    ]
    storage.set_topics("nb_round", shuffled_topics)

    # Set stored values with more than 4 decimals
    storage.set_mastery(
        "nb_round", "u1", "t_a", MasteryRecord(p_known=0.912345678, n_obs=2, last_updated=T0)
    )
    storage.set_mastery(
        "nb_round", "u1", "t_b", MasteryRecord(p_known=0.654321987, n_obs=3, last_updated=T0)
    )
    storage.set_mastery(
        "nb_round", "u1", "t_c", MasteryRecord(p_known=0.555555555, n_obs=1, last_updated=T0)
    )
    # t_d has no mastery record -> defaults to PRIOR

    read_time = T0 + timedelta(days=5.5)
    prog = get_progress(storage, "nb_round", "u1", coach_on=True, now=read_time)

    # Assert returned order: non-other topics ordered by (order, topic_id)
    # order 1: t_a, t_b; order 2: t_c; order 3: t_d
    assert [tp.topic_id for tp in prog.topics] == ["t_a", "t_b", "t_c", "t_d"]

    # Assert each p_known == round(expected, 4)
    expected_a = bkt.decay(0.912345678, 5.5)
    expected_b = bkt.decay(0.654321987, 5.5)
    expected_c = bkt.decay(0.555555555, 5.5)
    expected_d = bkt.PRIOR

    assert prog.topics[0].p_known == round(expected_a, 4)
    assert prog.topics[1].p_known == round(expected_b, 4)
    assert prog.topics[2].p_known == round(expected_c, 4)
    assert prog.topics[3].p_known == round(expected_d, 4)


# ==================== DONE-WHEN 12 / 13 & T10 ====================
def test_task_files_are_ascii_without_bom() -> None:
    test_dir = Path(__file__).parent
    backend_dir = test_dir.parent

    target_files = [
        backend_dir / "app" / "coach" / "__init__.py",
        backend_dir / "app" / "coach" / "bkt.py",
        backend_dir / "app" / "coach" / "types.py",
        backend_dir / "app" / "coach" / "storage.py",
        backend_dir / "app" / "coach" / "core.py",
        test_dir / "test_coach_core.py",
    ]

    for fpath in target_files:
        assert fpath.exists(), f"File {fpath} does not exist"
        data = fpath.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf"), f"File {fpath} has UTF-8 BOM"
        for byte_idx, b in enumerate(data):
            assert b < 128, f"File {fpath} contains non-ASCII byte 0x{b:02x} at position {byte_idx}"


# ==================== VERIFY ITEMS M1 - M8, L10 ====================
def test_m1_self_prerequisite() -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics(
        "nb_m1",
        [
            TopicInfo(id="a", name="Topic A", order=1, prerequisite_ids=("a",), is_other=False),
            TopicInfo(id="c", name="Topic C", order=2, prerequisite_ids=(), is_other=False),
        ],
    )
    storage.set_mastery("nb_m1", "u1", "a", MasteryRecord(p_known=0.1, n_obs=1, last_updated=T0))
    storage.set_mastery("nb_m1", "u1", "c", MasteryRecord(p_known=0.4, n_obs=1, last_updated=T0))
    res = get_topics_needing_work(storage, "nb_m1", "u1", coach_on=True, now=T0, limit=5)
    assert [item.topic_id for item in res.items] == ["a", "c"]


def test_m3_window_edges() -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics(
        "nb_m3",
        [
            TopicInfo(id="p", name="Topic P", order=1, prerequisite_ids=(), is_other=False),
            TopicInfo(id="q", name="Topic Q", order=2, prerequisite_ids=(), is_other=False),
        ],
    )
    storage.set_mastery("nb_m3", "u1", "p", MasteryRecord(p_known=0.5, n_obs=1, last_updated=T0))
    storage.set_mastery("nb_m3", "u1", "q", MasteryRecord(p_known=0.5, n_obs=1, last_updated=T0))

    # exactly now - 14 days (counts)
    storage.add_event(
        "nb_m3",
        "u1",
        CoachEvent(kind="chat_signal", topic_id="p", value=1.0, created_at=T0 - timedelta(days=14)),
        event_id="cs_edge_1",
    )
    # at now - 14 days - 1 microsecond (does not count)
    storage.add_event(
        "nb_m3",
        "u1",
        CoachEvent(
            kind="chat_signal",
            topic_id="p",
            value=1.0,
            created_at=T0 - timedelta(days=14, microseconds=1),
        ),
        event_id="cs_edge_2",
    )
    # at now + 1 day (does not count)
    storage.add_event(
        "nb_m3",
        "u1",
        CoachEvent(
            kind="chat_signal",
            topic_id="p",
            value=1.0,
            created_at=T0 + timedelta(days=1),
        ),
        event_id="cs_edge_3",
    )

    res = get_topics_needing_work(storage, "nb_m3", "u1", coach_on=True, now=T0, limit=5)
    assert [item.topic_id for item in res.items] == ["p", "q"]
    p_item = next(item for item in res.items if item.topic_id == "p")
    q_item = next(item for item in res.items if item.topic_id == "q")
    assert p_item.chat_signals == 1
    assert q_item.chat_signals == 0


def test_m4_cap_and_item_fields() -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics(
        "nb_m4",
        [
            TopicInfo(id="t3", name="Topic 3", order=1, prerequisite_ids=(), is_other=False),
            TopicInfo(id="t4", name="Topic 4", order=2, prerequisite_ids=(), is_other=False),
        ],
    )
    storage.set_mastery("nb_m4", "u1", "t3", MasteryRecord(p_known=0.5, n_obs=1, last_updated=T0))
    storage.set_mastery("nb_m4", "u1", "t4", MasteryRecord(p_known=0.34, n_obs=1, last_updated=T0))

    # 7 chat signals for t3 inside window
    for i in range(7):
        storage.add_event(
            "nb_m4",
            "u1",
            CoachEvent(kind="chat_signal", topic_id="t3", value=1.0, created_at=T0),
            event_id=f"cs_m4_{i}",
        )

    res = get_topics_needing_work(storage, "nb_m4", "u1", coach_on=True, now=T0, limit=5)
    # (a) Assert order is [t4, t3]
    assert [item.topic_id for item in res.items] == ["t4", "t3"]

    # (b) Assert t3 item equals real count 7
    t3_item = next(item for item in res.items if item.topic_id == "t3")
    assert t3_item == NeedsWorkItem(
        topic_id="t3",
        name="Topic 3",
        reason="low_mastery",
        p_known=round(0.5, 4),
        chat_signals=7,
    )

    # (c) own topic stored 0.123456789: assert p_known == round(0.123456789, 4)
    storage_c = InMemoryCoachStorage()
    storage_c.set_topics(
        "nb_m4c",
        [
            TopicInfo(id="tc", name="Topic C", order=1, prerequisite_ids=(), is_other=False),
        ],
    )
    storage_c.set_mastery(
        "nb_m4c", "u1", "tc", MasteryRecord(p_known=0.123456789, n_obs=1, last_updated=T0)
    )
    res_c = get_topics_needing_work(storage_c, "nb_m4c", "u1", coach_on=True, now=T0, limit=5)
    assert len(res_c.items) == 1
    assert res_c.items[0].p_known == round(0.123456789, 4)


def test_m5_tie_breakers() -> None:
    # Case 1: [z (order 1), a (order 2)] give [z, a]
    s1 = InMemoryCoachStorage()
    s1.set_topics(
        "nb_m5_1",
        [
            TopicInfo(id="z", name="Topic Z", order=1, prerequisite_ids=(), is_other=False),
            TopicInfo(id="a", name="Topic A", order=2, prerequisite_ids=(), is_other=False),
        ],
    )
    res1 = get_topics_needing_work(s1, "nb_m5_1", "u1", coach_on=True, now=T0, limit=5)
    assert [item.topic_id for item in res1.items] == ["z", "a"]

    # Case 2: [b (order 1), a (order 1)] give [a, b]
    s2 = InMemoryCoachStorage()
    s2.set_topics(
        "nb_m5_2",
        [
            TopicInfo(id="b", name="Topic B", order=1, prerequisite_ids=(), is_other=False),
            TopicInfo(id="a", name="Topic A", order=1, prerequisite_ids=(), is_other=False),
        ],
    )
    res2 = get_topics_needing_work(s2, "nb_m5_2", "u1", coach_on=True, now=T0, limit=5)
    assert [item.topic_id for item in res2.items] == ["a", "b"]


def test_m6_coach_off_sort_and_limit() -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics(
        "nb_m6",
        [
            TopicInfo(id="b", name="Topic B", order=2, prerequisite_ids=(), is_other=False),
            TopicInfo(id="c", name="Topic C", order=1, prerequisite_ids=(), is_other=False),
            TopicInfo(id="a", name="Topic A", order=1, prerequisite_ids=(), is_other=False),
            TopicInfo(id="other", name="Other", order=0, prerequisite_ids=(), is_other=True),
        ],
    )
    res = get_topics_needing_work(storage, "nb_m6", "u1", coach_on=False, now=T0, limit=2)
    assert [item.topic_id for item in res.items] == ["a", "c"]
    for item in res.items:
        assert item.reason == "unchecked"


def test_m7_last_updated_max_old_now() -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())
    storage.set_mastery("nb1", "u1", "t1", MasteryRecord(p_known=0.5, n_obs=1, last_updated=T0))

    now_past = T0 - timedelta(days=3)
    res_qa = record_quiz_answer(
        storage,
        "nb1",
        "u1",
        topic_id="t1",
        attempt_id="att_past",
        question_type="mcq",
        score=1.0,
        coach_on=True,
        now=now_past,
    )
    assert res_qa.outcome == "applied"
    rec_qa = storage.get_all_mastery("nb1", "u1")["t1"]
    assert rec_qa.last_updated == T0
    assert rec_qa.p_known == pytest.approx(bkt.update(0.5, True, "mcq"), abs=1e-12)

    # Tick: p_known == 0.8, last_updated == T0
    storage.set_mastery("nb1", "u1", "t2", MasteryRecord(p_known=0.5, n_obs=1, last_updated=T0))
    res_cb = record_checkbox(
        storage,
        "nb1",
        "u1",
        topic_id="t2",
        checked=True,
        coach_on=True,
        now=now_past,
    )
    assert res_cb.outcome == "applied"
    rec_cb = storage.get_all_mastery("nb1", "u1")["t2"]
    assert rec_cb.last_updated == T0
    assert rec_cb.p_known == pytest.approx(0.8, abs=1e-12)


def test_m8_record_calls_other_topic_coach_off() -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())

    # quiz
    spy1 = SpyStorage(storage)
    snap1 = spy1.snapshot()
    r_qa = record_quiz_answer(
        spy1,
        "nb1",
        "u1",
        topic_id="other",
        attempt_id="att_other",
        question_type="mcq",
        score=1.0,
        coach_on=False,
        now=T0,
    )
    assert r_qa.outcome == "coach_off"
    assert r_qa.topic_id == "other"
    assert r_qa.p_known is None
    assert spy1.snapshot() == snap1

    # chat
    spy2 = SpyStorage(storage)
    snap2 = spy2.snapshot()
    r_chat = record_chat_signal(
        spy2,
        "nb1",
        "u1",
        topic_id="other",
        message_id="msg_other",
        coach_on=False,
        now=T0,
    )
    assert r_chat.outcome == "coach_off"
    assert r_chat.topic_id == "other"
    assert r_chat.p_known is None
    assert spy2.snapshot() == snap2

    # checkbox
    spy3 = SpyStorage(storage)
    snap3 = spy3.snapshot()
    r_cb = record_checkbox(
        spy3,
        "nb1",
        "u1",
        topic_id="other",
        checked=True,
        coach_on=False,
        now=T0,
    )
    assert r_cb.outcome == "coach_off"
    assert r_cb.topic_id == "other"
    assert r_cb.p_known is None
    assert spy3.snapshot() == snap3


def test_l10_str_subclass_question_type() -> None:
    storage = InMemoryCoachStorage()
    storage.set_topics("nb1", _make_demo_topics())
    spy = SpyStorage(storage)
    snap_before = spy.snapshot()
    with pytest.raises(CoachInputError) as exc_info:
        record_quiz_answer(
            spy,
            "nb1",
            "u1",
            topic_id="t1",
            attempt_id="a1",
            question_type=StrSubclass("mcq"),  # type: ignore[arg-type]
            score=1.0,
            coach_on=True,
            now=T0,
        )
    assert str(exc_info.value).startswith("invalid question type:")
    assert spy.snapshot() == snap_before
    assert len(spy.calls) == 0
