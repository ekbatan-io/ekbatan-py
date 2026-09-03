"""Running the work fan-out created.

One pass per shard: take the notifications that are due, hand each to its handler, and record
what happened. Failures are retried on a capped backoff until the retention window closes, at
which point the row expires and the handler is never called for it again - because a queue that
never drains is an outage that never ends.

The batch is the subtle part. Each notification is isolated from its siblings, including
against the failure class an ordinary ``except Exception`` misses. Letting one escape past the
batch's state writes means a sibling that already succeeded never has its success recorded, so
it is invoked again on every poll: at-least-once quietly becomes once-per-poll-forever, and
nothing in the logs says so.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Table, select, update

from .._json import decode
from ..clock import Clock, SystemClock, to_storage
from ..domain import ModelEvent
from ..persistence import DatabaseRegistry
from ..sharding import ShardIdentifier
from .handler import EventEnvelope, EventHandler, EventHandlerRegistry
from .notification import NotificationState, backoff

__all__ = ["EventDispatch"]

LOG = logging.getLogger("ekbatan.events")

DEFAULT_RETENTION = timedelta(days=7)
DEFAULT_MAX_BACKOFF = timedelta(minutes=5)


@dataclass(slots=True)
class _Outcome:
    notification_id: Any
    state: NotificationState
    attempts: int
    next_retry_at: datetime


class EventDispatch:
    """Delivers notifications to handlers, at least once."""

    __slots__ = (
        "_batch",
        "_clock",
        "_handlers",
        "_max_backoff",
        "_notifications",
        "_registry",
        "_retention",
    )

    def __init__(
        self,
        registry: DatabaseRegistry,
        handlers: EventHandlerRegistry,
        *,
        notifications: Table,
        clock: Clock | None = None,
        batch_size: int = 100,
        retention: timedelta = DEFAULT_RETENTION,
        max_backoff: timedelta = DEFAULT_MAX_BACKOFF,
    ) -> None:
        self._registry = registry
        self._handlers = handlers
        self._notifications = notifications
        self._clock = clock or SystemClock()
        self._batch = batch_size
        self._retention = retention
        self._max_backoff = max_backoff

    def drain_once(self) -> int:
        """One batch on every shard. Returns how many notifications reached a new state."""
        return sum(self._drain_shard(shard) for shard in self._registry.shards)

    def drain_until_idle(self, *, limit: int = 1000) -> int:
        total = 0
        for _ in range(limit):
            done = self.drain_once()
            total += done
            if done == 0:
                return total
        LOG.warning("dispatch still had work after %d rounds; stopping to avoid spinning", limit)
        return total

    def _drain_shard(self, shard: ShardIdentifier) -> int:
        table = self._notifications
        manager = self._registry.manager(shard)
        now = to_storage(self._clock.now())

        with manager.connection(readonly=True) as conn:
            due = list(
                conn.execute(
                    select(table)
                    .where(
                        table.c.state.in_(
                            [NotificationState.PENDING.value, NotificationState.FAILED.value]
                        ),
                        table.c.next_retry_at <= now,
                    )
                    .order_by(table.c.next_retry_at)
                    .limit(self._batch)
                )
            )
        if not due:
            return 0

        outcomes = [self._deliver(row) for row in due]

        # Written after the whole batch has been attempted, in one transaction. Every outcome
        # is recorded or none is - and no single delivery can take the others' results with it.
        with manager.transaction() as conn:
            for outcome in outcomes:
                conn.execute(
                    update(table)
                    .where(table.c.id == outcome.notification_id)
                    .values(
                        state=outcome.state.value,
                        attempts=outcome.attempts,
                        next_retry_at=outcome.next_retry_at,
                        updated_date=to_storage(self._clock.now()),
                    )
                )
        return len(outcomes)

    def _deliver(self, row: Any) -> _Outcome:
        now = to_storage(self._clock.now())
        attempts = row.attempts + 1

        if self._expired(row.event_date, now):
            # Checked before invoking, not after. Handing a handler an event already known to
            # be too old risks a side effect nobody wanted - a stale email, a reversed charge -
            # for work that will be discarded either way.
            LOG.info(
                "notification %s expired before delivery: %s is past its retention window",
                row.id,
                row.event_type,
            )
            return _Outcome(row.id, NotificationState.EXPIRED, row.attempts, now)

        handler = self._handlers.by_name(row.handler_name)
        if handler is None:
            # Nobody is listening under that name any more. Left pending rather than expired:
            # a handler removed by a bad deploy comes back, and its queue should still be there.
            LOG.warning(
                "no handler named %r is registered; leaving its notifications alone",
                row.handler_name,
            )
            return _Outcome(
                row.id,
                NotificationState.PENDING,
                row.attempts,
                now + backoff(attempts, self._max_backoff),
            )

        try:
            handler.handle(self._envelope(handler, row, attempts))
        except BaseException as error:
            # BaseException on purpose. The failure class an ordinary `except Exception`
            # misses is exactly the one that would otherwise escape and discard this batch's
            # siblings, whose successes would then never be recorded.
            after = to_storage(self._clock.now())
            if self._expired(row.event_date, after):
                LOG.warning(
                    "notification %s failed and is now past its retention window: %s: %s",
                    row.id,
                    type(error).__name__,
                    error,
                )
                return _Outcome(row.id, NotificationState.EXPIRED, attempts, after)
            delay = backoff(attempts, self._max_backoff)
            LOG.warning(
                "handler %r failed on attempt %d (%s: %s); retrying in %s",
                row.handler_name,
                attempts,
                type(error).__name__,
                error,
                delay,
            )
            return _Outcome(row.id, NotificationState.FAILED, attempts, after + delay)

        return _Outcome(row.id, NotificationState.SUCCEEDED, attempts, now)

    def _expired(self, event_date: datetime, now: datetime) -> bool:
        return event_date + self._retention <= now

    def _envelope(
        self, handler: EventHandler[Any], row: Any, attempt: int
    ) -> EventEnvelope[ModelEvent]:
        event_type = handler.event_type
        payload = row.payload or {}
        return EventEnvelope(
            event=decode(event_type, payload),
            event_id=row.event_id,
            namespace=row.namespace,
            action_id=row.action_id,
            action_name=row.action_name,
            action_params=row.action_params or {},
            started_date=row.started_date,
            completion_date=row.completion_date,
            event_date=row.event_date,
            model_id=row.model_id,
            model_type=row.model_type,
            attempt=attempt,
        )
