"""Storage protocol and thread-safe in-memory storage implementation."""

import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal, Protocol

from .types import CoachEvent, EventKind, MasteryRecord, TopicInfo


class CoachStorage(Protocol):
    def get_topics(self, nb: str) -> list[TopicInfo]: ...
    def get_checked(self, nb: str, uid: str) -> dict[str, bool]: ...
    def get_all_mastery(self, nb: str, uid: str) -> dict[str, MasteryRecord]: ...
    def list_events(
        self, nb: str, uid: str, *, kind: EventKind, since: datetime
    ) -> list[CoachEvent]:
        """The Firestore implementation queries a created_at range only and filters kind in
        Python: an equality filter plus a range filter needs a composite index, which
        the emulator does not enforce."""
        ...
    def add_event(
        self, nb: str, uid: str, event: CoachEvent, *, event_id: str | None
    ) -> Literal["applied", "duplicate"]: ...
    def update_mastery(
        self,
        nb: str,
        uid: str,
        topic_id: str,
        fn: Callable[[MasteryRecord | None], MasteryRecord],
        *,
        event: CoachEvent,
        event_id: str | None,
    ) -> Literal["applied", "duplicate"]: ...


class InMemoryCoachStorage:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._topics: dict[str, list[TopicInfo]] = {}
        self._checked: dict[tuple[str, str], dict[str, bool]] = {}
        self._mastery: dict[tuple[str, str], dict[str, MasteryRecord]] = {}
        self._events: dict[tuple[str, str], dict[str, CoachEvent]] = {}
        self._next_event_number: int = 1

    def get_topics(self, nb: str) -> list[TopicInfo]:
        with self._lock:
            return list(self._topics.get(nb, []))

    def get_checked(self, nb: str, uid: str) -> dict[str, bool]:
        with self._lock:
            return dict(self._checked.get((nb, uid), {}))

    def get_all_mastery(self, nb: str, uid: str) -> dict[str, MasteryRecord]:
        with self._lock:
            return dict(self._mastery.get((nb, uid), {}))

    def list_events(
        self, nb: str, uid: str, *, kind: EventKind, since: datetime
    ) -> list[CoachEvent]:
        ("""The Firestore implementation queries a created_at range only and filters kind in"""
         """ Python: an equality filter plus a range filter needs a composite index, which"""
         """ the emulator does not enforce.""")
        with self._lock:
            user_events = self._events.get((nb, uid), {})
            return [
                ev for ev in user_events.values()
                if ev.kind == kind and ev.created_at >= since
            ]

    def add_event(
        self, nb: str, uid: str, event: CoachEvent, *, event_id: str | None
    ) -> Literal["applied", "duplicate"]:
        with self._lock:
            user_events = self._events.get((nb, uid), {})
            if event_id is not None and event_id in user_events:
                return "duplicate"

            if event_id is not None:
                target_id = event_id
            else:
                target_id = f"ev_{self._next_event_number:06d}"
                self._next_event_number += 1

            if (nb, uid) not in self._events:
                self._events[(nb, uid)] = {}
            self._events[(nb, uid)][target_id] = event
            return "applied"

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
        with self._lock:
            user_events = self._events.get((nb, uid), {})
            if event_id is not None and event_id in user_events:
                return "duplicate"

            user_mastery = self._mastery.get((nb, uid), {})
            current_record = user_mastery.get(topic_id)

            new_record = fn(current_record)
            if not isinstance(new_record, MasteryRecord):
                raise TypeError(f"fn must return MasteryRecord, got {type(new_record).__name__}")

            if event_id is not None:
                target_id = event_id
            else:
                target_id = f"ev_{self._next_event_number:06d}"
                self._next_event_number += 1

            if (nb, uid) not in self._mastery:
                self._mastery[(nb, uid)] = {}
            self._mastery[(nb, uid)][topic_id] = new_record

            if (nb, uid) not in self._events:
                self._events[(nb, uid)] = {}
            self._events[(nb, uid)][target_id] = event
            return "applied"

    def set_topics(self, nb: str, topics: list[TopicInfo]) -> None:
        with self._lock:
            self._topics[nb] = list(topics)

    def set_checked(self, nb: str, uid: str, checked: dict[str, bool]) -> None:
        with self._lock:
            self._checked[(nb, uid)] = dict(checked)

    def set_mastery(
        self, nb: str, uid: str, topic_id: str, record: MasteryRecord
    ) -> None:
        with self._lock:
            if (nb, uid) not in self._mastery:
                self._mastery[(nb, uid)] = {}
            self._mastery[(nb, uid)][topic_id] = record

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "topics": {nb: tuple(t_list) for nb, t_list in self._topics.items()},
                "checked": {k: dict(v) for k, v in self._checked.items()},
                "mastery": {k: dict(v) for k, v in self._mastery.items()},
                "events": {k: dict(v) for k, v in self._events.items()},
                "next_event_number": self._next_event_number,
            }
