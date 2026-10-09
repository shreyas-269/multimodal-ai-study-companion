"""Study Coach core procedures and validation."""

import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from . import bkt
from .storage import CoachStorage
from .types import (
    CoachEvent,
    CoachInputError,
    MasteryRecord,
    NeedsWork,
    NeedsWorkItem,
    Progress,
    QuestionType,
    RecordResult,
    TopicInfo,
    TopicProgress,
)

CHAT_SIGNAL_WINDOW_DAYS: int = 14
CHAT_SIGNAL_WEIGHT: float = 0.05
CHAT_SIGNAL_CAP: int = 3
DEFAULT_LIMIT: int = 3

_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}", re.ASCII)


def _validate_id(val: Any, name: str) -> str:
    if type(val) is not str or not _ID_RE.fullmatch(val):
        raise CoachInputError(f"invalid id: {name} must be 1-128 ASCII chars [A-Za-z0-9_-]")
    return val


def _validate_time(now: Any) -> datetime:
    if not isinstance(now, datetime):
        raise CoachInputError("invalid time: now must be a datetime instance")
    if now.tzinfo is None or now.utcoffset() is None:
        raise CoachInputError("invalid time: now must be timezone-aware")
    try:
        now_utc = now.astimezone(UTC)
        _ = now_utc - timedelta(days=CHAT_SIGNAL_WINDOW_DAYS)
        return now_utc
    except (OverflowError, ValueError) as err:
        raise CoachInputError("invalid time: datetime conversion or window overflow") from err


def _validate_flag(flag: Any, name: str) -> bool:
    if type(flag) is not bool:
        raise CoachInputError(f"invalid flag: {name} must be exactly a bool")
    return flag


def _validate_score(score: Any) -> float:
    if isinstance(score, bool):
        raise CoachInputError("invalid score: boolean is not a valid score")
    if isinstance(score, int):
        if 0 <= score <= 1:
            return float(score)
        raise CoachInputError("invalid score: integer score must be 0 or 1")
    if isinstance(score, float):
        if math.isfinite(score) and 0.0 <= score <= 1.0:
            return float(score)
        raise CoachInputError("invalid score: float score must be finite and between 0.0 and 1.0")
    raise CoachInputError("invalid score: score must be an int or float")


def _validate_question_type(question_type: Any) -> str:
    if type(question_type) is not str or question_type not in ("mcq", "short", "numerical"):
        raise CoachInputError(
            f"invalid question type: must be 'mcq', 'short', or 'numerical', got {question_type!r}"
        )
    return question_type


def _validate_limit(limit: Any) -> int:
    if type(limit) is not int or limit < 1 or limit > 20:
        raise CoachInputError("invalid limit: limit must be an int between 1 and 20")
    return limit


def _find_topic(topics: list[TopicInfo], topic_id: str, nb: str) -> TopicInfo:
    for t in topics:
        if t.id == topic_id:
            return t
    raise CoachInputError(f"unknown topic: {topic_id} not found in notebook {nb}")


def record_quiz_answer(
    storage: CoachStorage,
    nb: str,
    uid: str,
    *,
    topic_id: str,
    attempt_id: str,
    question_type: QuestionType,
    score: float,
    coach_on: bool,
    now: datetime,
) -> RecordResult:
    _validate_id(nb, "nb")
    _validate_id(uid, "uid")
    _validate_id(topic_id, "topic_id")
    _validate_id(attempt_id, "attempt_id")
    _validate_question_type(question_type)
    score_f = _validate_score(score)
    _validate_flag(coach_on, "coach_on")
    now_utc = _validate_time(now)

    topics = storage.get_topics(nb)
    topic = _find_topic(topics, topic_id, nb)

    if not coach_on:
        return RecordResult(outcome="coach_off", topic_id=topic_id, p_known=None)

    if topic.is_other:
        return RecordResult(outcome="ignored_other", topic_id=topic_id, p_known=None)

    event = CoachEvent(
        kind="quiz_answer",
        topic_id=topic_id,
        value=score_f,
        created_at=now_utc,
    )
    event_id = f"qa_{attempt_id}"
    computed_new_p: list[float] = []

    def _update_mastery_record(record: MasteryRecord | None) -> MasteryRecord:
        if record is None:
            base = bkt.PRIOR
            old_n_obs = 0
            old_last_updated = now_utc
        else:
            elapsed_days = max(0.0, (now_utc - record.last_updated).total_seconds() / 86400.0)
            base = bkt.decay(record.p_known, elapsed_days)
            old_n_obs = record.n_obs
            old_last_updated = record.last_updated

        new_p = bkt.update(base, bkt.is_correct(score_f), question_type)
        new_n_obs = old_n_obs + 1
        new_last_updated = max(old_last_updated, now_utc)
        computed_new_p.append(new_p)
        return MasteryRecord(
            p_known=new_p,
            n_obs=new_n_obs,
            last_updated=new_last_updated,
        )

    outcome = storage.update_mastery(
        nb, uid, topic_id, _update_mastery_record, event=event, event_id=event_id
    )

    if outcome == "applied":
        return RecordResult(
            outcome="applied",
            topic_id=topic_id,
            p_known=round(computed_new_p[-1], 4),
        )
    return RecordResult(outcome="duplicate", topic_id=topic_id, p_known=None)


def record_chat_signal(
    storage: CoachStorage,
    nb: str,
    uid: str,
    *,
    topic_id: str,
    message_id: str,
    coach_on: bool,
    now: datetime,
) -> RecordResult:
    _validate_id(nb, "nb")
    _validate_id(uid, "uid")
    _validate_id(topic_id, "topic_id")
    _validate_id(message_id, "message_id")
    _validate_flag(coach_on, "coach_on")
    now_utc = _validate_time(now)

    topics = storage.get_topics(nb)
    topic = _find_topic(topics, topic_id, nb)

    if not coach_on:
        return RecordResult(outcome="coach_off", topic_id=topic_id, p_known=None)

    if topic.is_other:
        return RecordResult(outcome="ignored_other", topic_id=topic_id, p_known=None)

    event = CoachEvent(
        kind="chat_signal",
        topic_id=topic_id,
        value=1.0,
        created_at=now_utc,
    )
    event_id = f"cs_{message_id}"
    outcome = storage.add_event(nb, uid, event, event_id=event_id)
    return RecordResult(outcome=outcome, topic_id=topic_id, p_known=None)


def record_checkbox(
    storage: CoachStorage,
    nb: str,
    uid: str,
    *,
    topic_id: str,
    checked: bool,
    coach_on: bool,
    now: datetime,
) -> RecordResult:
    _validate_id(nb, "nb")
    _validate_id(uid, "uid")
    _validate_id(topic_id, "topic_id")
    _validate_flag(checked, "checked")
    _validate_flag(coach_on, "coach_on")
    now_utc = _validate_time(now)

    topics = storage.get_topics(nb)
    topic = _find_topic(topics, topic_id, nb)

    if not coach_on:
        return RecordResult(outcome="coach_off", topic_id=topic_id, p_known=None)

    if topic.is_other:
        return RecordResult(outcome="ignored_other", topic_id=topic_id, p_known=None)

    if checked:
        event = CoachEvent(
            kind="checkbox",
            topic_id=topic_id,
            value=1.0,
            created_at=now_utc,
        )
        computed_new_p: list[float] = []

        def _tick_record(record: MasteryRecord | None) -> MasteryRecord:
            if record is None:
                base = bkt.PRIOR
                old_n_obs = 0
                old_last_updated = now_utc
            else:
                elapsed_days = max(0.0, (now_utc - record.last_updated).total_seconds() / 86400.0)
                base = bkt.decay(record.p_known, elapsed_days)
                old_n_obs = record.n_obs
                old_last_updated = record.last_updated

            new_p = max(base, bkt.CHECKBOX_FLOOR)
            new_last_updated = max(old_last_updated, now_utc)
            computed_new_p.append(new_p)
            return MasteryRecord(
                p_known=new_p,
                n_obs=old_n_obs,
                last_updated=new_last_updated,
            )

        outcome = storage.update_mastery(
            nb, uid, topic_id, _tick_record, event=event, event_id=None
        )
        return RecordResult(
            outcome=outcome,
            topic_id=topic_id,
            p_known=round(computed_new_p[-1], 4),
        )
    else:
        event = CoachEvent(
            kind="checkbox",
            topic_id=topic_id,
            value=0.0,
            created_at=now_utc,
        )
        outcome = storage.add_event(nb, uid, event, event_id=None)
        return RecordResult(outcome=outcome, topic_id=topic_id, p_known=None)


def get_topics_needing_work(
    storage: CoachStorage,
    nb: str,
    uid: str,
    *,
    coach_on: bool,
    now: datetime,
    limit: int = DEFAULT_LIMIT,
) -> NeedsWork:
    _validate_id(nb, "nb")
    _validate_id(uid, "uid")
    _validate_flag(coach_on, "coach_on")
    now_utc = _validate_time(now)
    _validate_limit(limit)

    topics = storage.get_topics(nb)
    non_other_topics = [t for t in topics if not t.is_other]

    if not coach_on:
        checked_map = storage.get_checked(nb, uid)
        unchecked = [t for t in non_other_topics if not checked_map.get(t.id, False)]
        unchecked.sort(key=lambda t: (t.order, t.id))
        items = [
            NeedsWorkItem(
                topic_id=t.id,
                name=t.name,
                reason="unchecked",
                p_known=None,
                chat_signals=None,
            )
            for t in unchecked[:limit]
        ]
        return NeedsWork(coach_on=False, items=items)

    mastery_map = storage.get_all_mastery(nb, uid)
    window_start = now_utc - timedelta(days=CHAT_SIGNAL_WINDOW_DAYS)
    chat_events = storage.list_events(nb, uid, kind="chat_signal", since=window_start)

    chat_counts: dict[str, int] = {}
    for ev in chat_events:
        if window_start <= ev.created_at <= now_utc:
            chat_counts[ev.topic_id] = chat_counts.get(ev.topic_id, 0) + 1

    candidates: dict[str, dict[str, Any]] = {}
    for t in non_other_topics:
        rec = mastery_map.get(t.id)
        if rec is None:
            eff_p = bkt.PRIOR
            reason = "not_started"
        else:
            elapsed_days = max(0.0, (now_utc - rec.last_updated).total_seconds() / 86400.0)
            eff_p = bkt.decay(rec.p_known, elapsed_days)
            reason = "low_mastery"

        if eff_p < bkt.MASTERED_THRESHOLD:
            signals = chat_counts.get(t.id, 0)
            capped = min(signals, CHAT_SIGNAL_CAP)
            rank_score = eff_p - (CHAT_SIGNAL_WEIGHT * capped)
            candidates[t.id] = {
                "topic": t,
                "eff_p": eff_p,
                "signals": signals,
                "rank_score": rank_score,
                "reason": reason,
            }

    topic_by_id = {t.id: t for t in topics}

    def _get_transitive_prereqs(tid: str) -> set[str]:
        result: set[str] = set()
        visited: set[str] = set()
        curr_topic = topic_by_id.get(tid)
        stack = list(curr_topic.prerequisite_ids) if curr_topic else []
        while stack:
            curr = stack.pop()
            if curr not in topic_by_id or curr in visited:
                continue
            visited.add(curr)
            result.add(curr)
            stack.extend(topic_by_id[curr].prerequisite_ids)
        result.discard(tid)
        return result

    def _is_on_cycle(tid: str) -> bool:
        visited: set[str] = set()
        curr_topic = topic_by_id.get(tid)
        stack = list(curr_topic.prerequisite_ids) if curr_topic else []
        while stack:
            curr = stack.pop()
            if curr == tid:
                return True
            if curr not in topic_by_id or curr in visited:
                continue
            visited.add(curr)
            for p in topic_by_id[curr].prerequisite_ids:
                if p == tid:
                    return True
                if p not in visited and p in topic_by_id:
                    stack.append(p)
        return False

    transitive_prereqs = {cid: _get_transitive_prereqs(cid) for cid in candidates}
    cycle_map = {cid: _is_on_cycle(cid) for cid in candidates}

    def _sort_key(cid: str) -> tuple[float, int, str]:
        c = candidates[cid]
        return (c["rank_score"], c["topic"].order, cid)

    unplaced = set(candidates.keys())
    placed: list[str] = []

    while unplaced:
        unblocked = [c for c in unplaced if not (transitive_prereqs[c] & unplaced)]
        if unblocked:
            chosen = min(unblocked, key=_sort_key)
        else:
            on_cycle = [c for c in unplaced if cycle_map[c]]
            if on_cycle:
                chosen = min(on_cycle, key=_sort_key)
            else:
                chosen = min(unplaced, key=_sort_key)
        unplaced.remove(chosen)
        placed.append(chosen)

    items = [
        NeedsWorkItem(
            topic_id=cid,
            name=candidates[cid]["topic"].name,
            reason=candidates[cid]["reason"],
            p_known=round(candidates[cid]["eff_p"], 4),
            chat_signals=candidates[cid]["signals"],
        )
        for cid in placed[:limit]
    ]
    return NeedsWork(coach_on=True, items=items)


def get_progress(
    storage: CoachStorage,
    nb: str,
    uid: str,
    *,
    coach_on: bool,
    now: datetime,
) -> Progress:
    _validate_id(nb, "nb")
    _validate_id(uid, "uid")
    _validate_flag(coach_on, "coach_on")
    now_utc = _validate_time(now)

    topics = storage.get_topics(nb)
    non_other_topics = [t for t in topics if not t.is_other]
    non_other_topics.sort(key=lambda t: (t.order, t.id))

    checked_map = storage.get_checked(nb, uid)

    if not coach_on:
        topic_progresses = [
            TopicProgress(
                topic_id=t.id,
                name=t.name,
                order=t.order,
                checked=checked_map.get(t.id, False),
                p_known=None,
                n_obs=None,
                last_updated=None,
                label=None,
            )
            for t in non_other_topics
        ]
        return Progress(coach_on=False, topics=topic_progresses)

    mastery_map = storage.get_all_mastery(nb, uid)
    topic_progresses = []
    for t in non_other_topics:
        rec = mastery_map.get(t.id)
        if rec is None:
            p_known = round(bkt.PRIOR, 4)
            n_obs = 0
            last_updated = None
            lbl = "not_started"
        else:
            elapsed_days = max(0.0, (now_utc - rec.last_updated).total_seconds() / 86400.0)
            eff_p = bkt.decay(rec.p_known, elapsed_days)
            p_known = round(eff_p, 4)
            n_obs = rec.n_obs
            last_updated = rec.last_updated
            lbl = bkt.label(eff_p, has_record=True)

        topic_progresses.append(
            TopicProgress(
                topic_id=t.id,
                name=t.name,
                order=t.order,
                checked=checked_map.get(t.id, False),
                p_known=p_known,
                n_obs=n_obs,
                last_updated=last_updated,
                label=lbl,
            )
        )

    return Progress(coach_on=True, topics=topic_progresses)
