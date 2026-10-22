"""The queue row, its lifecycle, and how long a retry waits."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Any, Self

from .._builder import FieldBuilder
from ..errors import ValidationError
from ..time import Instant

__all__ = [
    "EventNotification",
    "EventNotificationBuilder",
    "EventNotificationState",
    "backoff",
]


class EventNotificationState(Enum):
    """Where one (event, handler) pair has got to.

    ``SUCCEEDED`` and ``EXPIRED`` are terminal: those rows leave the dispatch index and are
    never picked up again. The two exist separately because "it worked" and "we gave up" are
    different facts and only one of them is worth paging about.
    """

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


#: The columns a notifications table must have - one per :class:`EventNotification` field.
NOTIFICATION_COLUMNS = (
    "id",
    "event_id",
    "handler_name",
    "state",
    "attempts",
    "next_retry_at",
    "created_date",
    "updated_date",
    "namespace",
    "action_id",
    "action_name",
    "action_params",
    "started_date",
    "completion_date",
    "model_id",
    "model_type",
    "event_type",
    "payload",
    "event_date",
)


@dataclass(frozen=True, kw_only=True, slots=True, eq=False)
class EventNotification:
    """One row of ``eventlog.event_notifications`` - Java's ``EventNotification``.

    One per (event, subscribed handler): the notification's own retry state, then the event and
    its action's context, copied from ``eventlog.events`` at fan-out time so a handler's
    envelope is built from this row alone.
    """

    # notification identity + retry/lifecycle state
    id: uuid.UUID
    event_id: uuid.UUID
    handler_name: str
    state: EventNotificationState
    attempts: int
    next_retry_at: Instant
    created_date: Instant
    updated_date: Instant
    # the event and its action's context, copied at fan-out time
    namespace: str
    action_id: uuid.UUID
    action_name: str
    action_params: Mapping[str, Any]
    started_date: Instant
    completion_date: Instant
    model_id: str | None = None
    model_type: str | None = None
    event_type: str
    payload: Mapping[str, Any] | None = None
    event_date: Instant

    def __post_init__(self) -> None:
        for name in (
            "id",
            "event_id",
            "state",
            "next_retry_at",
            "created_date",
            "updated_date",
            "namespace",
            "action_id",
            "action_name",
            "action_params",
            "started_date",
            "completion_date",
            "event_date",
        ):
            if getattr(self, name) is None:
                raise ValidationError(f"{name} cannot be None")
        for name in ("handler_name", "event_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"{name} cannot be blank")
        # Not a state the retry arithmetic can produce, so it can only come from a row built by
        # hand.
        if self.attempts < 0:
            raise ValidationError("attempts cannot be negative")

    @classmethod
    def builder(cls) -> EventNotificationBuilder:
        return EventNotificationBuilder()

    @classmethod
    def from_row(cls, row: Any) -> EventNotification:
        """One read back from the table."""
        values = {name: getattr(row, name) for name in NOTIFICATION_COLUMNS}
        values["state"] = EventNotificationState(values["state"])
        return cls(**values)

    def to_row(self) -> dict[str, Any]:
        """The values to insert, keyed by column; the state by name, as Java stores it."""
        values = {name: getattr(self, name) for name in NOTIFICATION_COLUMNS}
        values["state"] = self.state.value
        return values


class EventNotificationBuilder(FieldBuilder[EventNotification]):
    """Fluent builder for :class:`EventNotification`. Obtain via
    :meth:`EventNotification.builder`."""

    __slots__ = ()
    _target = EventNotification
    _required = (
        "id",
        "event_id",
        "handler_name",
        "state",
        "attempts",
        "next_retry_at",
        "created_date",
        "updated_date",
        "namespace",
        "action_id",
        "action_name",
        "action_params",
        "started_date",
        "completion_date",
        "event_type",
        "event_date",
    )

    def id(self, id: uuid.UUID) -> Self:
        return self._set("id", id)

    def event_id(self, event_id: uuid.UUID) -> Self:
        return self._set("event_id", event_id)

    def handler_name(self, handler_name: str) -> Self:
        return self._set("handler_name", handler_name)

    def state(self, state: EventNotificationState) -> Self:
        return self._set("state", state)

    def attempts(self, attempts: int) -> Self:
        return self._set("attempts", attempts)

    def next_retry_at(self, next_retry_at: Instant) -> Self:
        return self._set("next_retry_at", next_retry_at)

    def created_date(self, created_date: Instant) -> Self:
        return self._set("created_date", created_date)

    def updated_date(self, updated_date: Instant) -> Self:
        return self._set("updated_date", updated_date)

    def namespace(self, namespace: str) -> Self:
        return self._set("namespace", namespace)

    def action_id(self, action_id: uuid.UUID) -> Self:
        return self._set("action_id", action_id)

    def action_name(self, action_name: str) -> Self:
        return self._set("action_name", action_name)

    def action_params(self, action_params: Mapping[str, Any]) -> Self:
        return self._set("action_params", action_params)

    def started_date(self, started_date: Instant) -> Self:
        return self._set("started_date", started_date)

    def completion_date(self, completion_date: Instant) -> Self:
        return self._set("completion_date", completion_date)

    def model_id(self, model_id: str | None) -> Self:
        return self._set("model_id", model_id)

    def model_type(self, model_type: str | None) -> Self:
        return self._set("model_type", model_type)

    def event_type(self, event_type: str) -> Self:
        return self._set("event_type", event_type)

    def payload(self, payload: Mapping[str, Any] | None) -> Self:
        return self._set("payload", payload)

    def event_date(self, event_date: Instant) -> Self:
        return self._set("event_date", event_date)


BASE = timedelta(seconds=30)
_MAX_DOUBLINGS = 30


def backoff(attempts: int, cap: timedelta) -> timedelta:
    """How long before the next attempt: doubling from 30 seconds, flattening at ``cap``.

    ``attempts`` counts what has happened so far, so the first failure passes 1 and waits 30
    seconds. The cap matters more than the curve: without one, a handler that fails all
    afternoon schedules its next attempt some time next year.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")
    if cap <= timedelta(0):
        raise ValueError(f"cap must be positive, got {cap}")
    grown: timedelta = BASE * (2 ** min(attempts - 1, _MAX_DOUBLINGS))
    return min(grown, cap)
