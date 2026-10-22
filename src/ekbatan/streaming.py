"""One outbox row, as something outside this process reads it.

The framework writes `eventlog.events`; a CDC connector publishes it; some other service consumes
it. That service needs a name for the shape it is reading, and this is it - the same thirteen
fields Java's `ActionEvent` carries, under the same names.

    for message in consumer:
        event = ActionEvent.from_row(message.value)
        if event.event_type == "WalletMoneyDeposited":
            credit(event.model_id, event.payload["amount"])

Nothing here is validated. Four fields are legitimately absent on the sentinel row a unit of work
with no events writes - `model_id`, `model_type`, `event_type` and `payload` are all null there -
so a type that rejected them would reject valid traffic.

**Mapping a raw CDC message is still the consumer's job**, in both ports. A Debezium topic carries
snake_case column names, JSON columns as strings rather than nested objects, and timestamps as
bare epoch microseconds; which of those you get depends on the connector, the converter and the
transform, so a helper here would be right for one pipeline and wrong for the next.
:meth:`from_row` handles the plain case - a mapping whose keys are the column names - and
:meth:`from_message` takes the two conversions that are not guessable as arguments.
"""

from __future__ import annotations

import uuid as _uuid
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from typing import Any, Self

from ._builder import FieldBuilder
from ._json import loads
from .time import Instant

#: The epoch, no zone attached.
_EPOCH = datetime(1970, 1, 1)

__all__ = ["ActionEvent", "ActionEventBuilder"]


@dataclass(frozen=True, kw_only=True, slots=True)
class ActionEvent:
    """A complete outbox row: the action's context, and the event it produced.

    Built with :meth:`builder`, as Java's is, or with keywords. Either way no two fields can be
    transposed - the hazard Java's builder exists for, in a thirteen-argument constructor that
    put `model_type` next to `event_type` and `started_date` next to `completion_date`.
    """

    #: Stable per-event identifier; the outbox row's primary key.
    id: _uuid.UUID
    #: The producing deployment's logical namespace.
    namespace: str
    #: Shared by every event of one invocation, so a consumer can group them.
    action_id: _uuid.UUID
    #: The producing action's bare class name.
    action_name: str
    #: What the action was invoked with, as JSON. Present on every row, sentinel included.
    action_params: Mapping[str, Any]
    #: When `perform` began.
    started_date: Instant
    #: When the persist phase committed.
    completion_date: Instant
    #: The affected model's id, as a string. ``None`` on a sentinel row.
    model_id: str | None = None
    #: The affected model's bare class name. ``None`` on a sentinel row.
    model_type: str | None = None
    #: The event subtype's bare class name - what a consumer routes on. ``None`` on a sentinel.
    event_type: str | None = None
    #: The event body. ``None`` on a sentinel row.
    payload: Mapping[str, Any] | None = None
    #: When the event was produced. Equal to `completion_date` for rows this framework writes.
    event_date: Instant
    #: Whether the in-process fan-out has already turned this row into notifications. A CDC
    #: consumer has no use for it; it is here because it is a column and the row is the contract.
    delivered: bool = False

    @classmethod
    def builder(cls) -> ActionEventBuilder:
        return ActionEventBuilder()

    @property
    def is_sentinel(self) -> bool:
        """Whether this row records a unit of work that emitted nothing.

        One is written per invocation regardless, so every action correlates to a row - without
        it, "nothing happened" and "nothing was recorded" are indistinguishable afterwards.
        """
        return self.event_type is None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Self:
        """Build one from a mapping keyed by column name.

        What a direct read of `eventlog.events` gives you, and what a CDC message becomes once
        the connector's own shapes have been undone.
        """
        known = {f.name for f in fields(cls)}
        missing = sorted(
            n
            for n in known
            if n not in row
            and n not in {"model_id", "model_type", "event_type", "payload", "delivered"}
        )
        if missing:
            raise ValueError(f"not an outbox row: missing {missing}")
        return cls(**{name: row[name] for name in known if name in row})

    @classmethod
    def from_message(
        cls,
        row: Mapping[str, Any],
        *,
        json_columns_are_strings: bool = False,
        timestamps_are_epoch_micros: bool = False,
    ) -> Self:
        """Build one from a CDC message, undoing the two conversions a connector commonly makes.

        Both default to off, because which of them applies is a property of the pipeline rather
        than of the row: a JSON column arrives as a string under some converters and as a nested
        object under others, and a timestamp as epoch microseconds under Debezium's default and
        as an ISO string under others. Passing the wrong one is louder than guessing.
        """
        adjusted = dict(row)
        if json_columns_are_strings:
            for column in ("action_params", "payload"):
                value = adjusted.get(column)
                if isinstance(value, str):
                    adjusted[column] = loads(value)
        if timestamps_are_epoch_micros:
            for column in ("started_date", "completion_date", "event_date"):
                value = adjusted.get(column)
                if isinstance(value, int):
                    # The column's own value, no zone attached: what the zone-less column holds.
                    adjusted[column] = _EPOCH + timedelta(microseconds=value)
        for column in ("id", "action_id"):
            value = adjusted.get(column)
            if isinstance(value, str):
                adjusted[column] = _uuid.UUID(value)
        return cls.from_row(adjusted)


class ActionEventBuilder(FieldBuilder[ActionEvent]):
    """Fluent builder for :class:`ActionEvent`. Obtain via :meth:`ActionEvent.builder`.

    Validates nothing beyond what the dataclass requires, as the class itself validates nothing:
    the four fields a sentinel row leaves null may be left unset.
    """

    __slots__ = ()
    _target = ActionEvent
    _required = (
        "id",
        "namespace",
        "action_id",
        "action_name",
        "action_params",
        "started_date",
        "completion_date",
        "event_date",
    )

    def id(self, id: _uuid.UUID) -> Self:
        return self._set("id", id)

    def namespace(self, namespace: str) -> Self:
        return self._set("namespace", namespace)

    def action_id(self, action_id: _uuid.UUID) -> Self:
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

    def event_type(self, event_type: str | None) -> Self:
        return self._set("event_type", event_type)

    def payload(self, payload: Mapping[str, Any] | None) -> Self:
        return self._set("payload", payload)

    def event_date(self, event_date: Instant) -> Self:
        return self._set("event_date", event_date)

    def delivered(self, delivered: bool) -> Self:
        return self._set("delivered", delivered)
