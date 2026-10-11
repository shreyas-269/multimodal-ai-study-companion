"""Firestore storage implementation and helpers for Study Coach."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.coach import CoachEvent, EventKind, MasteryRecord, TopicInfo
from app.db.client import get_db
from app.db.paths import (
    coach_event_path,
    coach_events_collection_path,
    mastery_collection_path,
    mastery_path,
    member_path,
    topics_collection_path,
    user_path,
)


def _to_utc_datetime(dt: Any) -> datetime:
    """Normalize datetime to plain timezone-aware UTC datetime."""
    if dt.tzinfo is None:
        d = dt.replace(tzinfo=UTC)
    else:
        d = dt.astimezone(UTC)
    return datetime(
        d.year, d.month, d.day, d.hour, d.minute, d.second, d.microsecond, tzinfo=UTC
    )


class FirestoreCoachStorage:
    """Firestore implementation of the CoachStorage protocol."""

    def __init__(self, client: firestore.Client, notebook_id: str, uid: str) -> None:
        self.client = client
        self.notebook_id = notebook_id
        self.uid = uid

    def _check_bound(self, nb: str, uid: str | None = None) -> None:
        if nb != self.notebook_id:
            raise ValueError(
                f"mismatched notebook_id: expected {self.notebook_id}, got {nb}"
            )
        if uid is not None and uid != self.uid:
            raise ValueError(f"mismatched uid: expected {self.uid}, got {uid}")

    def get_topics(self, nb: str) -> list[TopicInfo]:
        self._check_bound(nb)
        coll_ref = self.client.collection(topics_collection_path(nb))
        stream = (
            coll_ref.select(["name", "order", "prerequisite_ids", "is_other"])
            .order_by("order")
            .limit(100)
            .stream()
        )
        topics: list[TopicInfo] = []
        for doc in stream:
            d = doc.to_dict() or {}
            topics.append(
                TopicInfo(
                    id=doc.id,
                    name=d.get("name", ""),
                    order=int(d.get("order", 0)),
                    prerequisite_ids=tuple(d.get("prerequisite_ids") or ()),
                    is_other=bool(d.get("is_other", False)),
                )
            )
        return topics

    def get_checked(self, nb: str, uid: str) -> dict[str, bool]:
        self._check_bound(nb, uid)
        snap = self.client.document(member_path(nb, uid)).get(field_paths=["checked"])
        if not snap.exists:
            return {}
        raw = (snap.to_dict() or {}).get("checked")
        if isinstance(raw, dict):
            return {k: v is True for k, v in raw.items()}
        return {}

    def get_all_mastery(self, nb: str, uid: str) -> dict[str, MasteryRecord]:
        self._check_bound(nb, uid)
        stream = (
            self.client.collection(mastery_collection_path(nb, uid)).limit(100).stream()
        )
        mastery: dict[str, MasteryRecord] = {}
        for doc in stream:
            d = doc.to_dict() or {}
            mastery[doc.id] = MasteryRecord(
                p_known=float(d["p_known"]),
                n_obs=int(d["n_obs"]),
                last_updated=_to_utc_datetime(d["last_updated"]),
            )
        return mastery

    def list_events(
        self, nb: str, uid: str, *, kind: EventKind, since: datetime
    ) -> list[CoachEvent]:
        self._check_bound(nb, uid)
        since_utc = _to_utc_datetime(since)
        stream = (
            self.client.collection(coach_events_collection_path(nb, uid))
            .where(filter=FieldFilter("created_at", ">=", since_utc))
            .order_by("created_at")
            .limit(1000)
            .stream()
        )
        events: list[CoachEvent] = []
        for doc in stream:
            d = doc.to_dict() or {}
            created_at = _to_utc_datetime(d["created_at"])
            if d.get("kind") == kind and created_at >= since_utc:
                events.append(
                    CoachEvent(
                        kind=d["kind"],
                        topic_id=d["topic_id"],
                        value=float(d["value"]),
                        created_at=created_at,
                    )
                )
        return events

    def add_event(
        self, nb: str, uid: str, event: CoachEvent, *, event_id: str | None
    ) -> Literal["applied", "duplicate"]:
        self._check_bound(nb, uid)
        data = {
            "kind": event.kind,
            "topic_id": event.topic_id,
            "value": event.value,
            "created_at": _to_utc_datetime(event.created_at),
        }
        if event_id is not None:
            doc_ref = self.client.document(coach_event_path(nb, uid, event_id))
            try:
                doc_ref.create(data)
                return "applied"
            except AlreadyExists:
                return "duplicate"
        else:
            coll_ref = self.client.collection(coach_events_collection_path(nb, uid))
            coll_ref.document().set(data)
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
        self._check_bound(nb, uid)
        mastery_ref = self.client.document(mastery_path(nb, uid, topic_id))
        event_ref = (
            self.client.document(coach_event_path(nb, uid, event_id))
            if event_id is not None
            else self.client.collection(coach_events_collection_path(nb, uid)).document()
        )

        @firestore.transactional
        def _tx(tx: firestore.Transaction) -> Literal["applied", "duplicate"]:
            # 1. READS BEFORE WRITES
            if event_id is not None:
                if event_ref.get(transaction=tx).exists:
                    return "duplicate"
            mastery_snap = mastery_ref.get(transaction=tx)

            # 2. CURRENT RECORD
            if mastery_snap.exists:
                d = mastery_snap.to_dict() or {}
                curr = MasteryRecord(
                    p_known=float(d["p_known"]),
                    n_obs=int(d["n_obs"]),
                    last_updated=_to_utc_datetime(d["last_updated"]),
                )
            else:
                curr = None

            # 3. CALL fn & TYPE CHECK (BEFORE ANY SET)
            new_rec = fn(curr)
            if not isinstance(new_rec, MasteryRecord):
                raise TypeError(
                    f"fn must return MasteryRecord, got {type(new_rec).__name__}"
                )

            # 4. WRITES (SETS)
            tx.set(
                mastery_ref,
                {
                    "p_known": new_rec.p_known,
                    "n_obs": new_rec.n_obs,
                    "last_updated": _to_utc_datetime(new_rec.last_updated),
                },
            )
            tx.set(
                event_ref,
                {
                    "kind": event.kind,
                    "topic_id": event.topic_id,
                    "value": event.value,
                    "created_at": _to_utc_datetime(event.created_at),
                },
            )
            return "applied"

        return _tx(self.client.transaction(max_attempts=10))


def coach_storage(notebook_id: str, uid: str) -> FirestoreCoachStorage:
    """Return FirestoreCoachStorage bound to client, notebook_id, and uid."""
    return FirestoreCoachStorage(get_db(), notebook_id, uid)


def get_study_coach_enabled(uid: str) -> bool:
    """Return whether Study Coach is enabled for user uid."""
    db = get_db()
    snap = db.document(user_path(uid)).get(field_paths=["study_coach"])
    if not snap.exists:
        return False
    data = snap.to_dict() or {}
    return data.get("study_coach") is True
