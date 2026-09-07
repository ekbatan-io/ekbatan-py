"""Turning committed events into work.

One pass per shard: read the events nothing has fanned out yet whose type somebody subscribes
to, write one notification per subscriber, and mark those events delivered. All in one
transaction, so a crash half way leaves the events undelivered rather than the work lost.

Fan-out and dispatch are separate stages because they are separate pieces of state.
``delivered`` says the event has been turned into work; a notification's state says whether the
work succeeded. Collapsing them means a handler failure either loses the event or replays it
forever.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import Table, select, update

from .._metrics import counter
from ..clock import Clock, SystemClock, to_storage
from ..persistence import DatabaseRegistry, insert_ignoring_duplicates
from .handler import EventHandlerRegistry
from .notification import EventNotificationState

#: Java's two, name for name.
EVENTS_FANNED_OUT = counter(
    "ekbatan.events.fanned_out",
    "Source events transitioned from undelivered to delivered by the fan-out job",
    "{event}",
)
NOTIFICATIONS_CREATED = counter(
    "ekbatan.events.notifications_created",
    "Notification rows materialized by the fan-out job (one per event x subscribed handler)",
    "{notification}",
)

__all__ = ["EventFanout"]

LOG = logging.getLogger("ekbatan.events")


class EventFanout:
    """Materialises notifications from the outbox."""

    __slots__ = ("_batch", "_clock", "_events", "_handlers", "_notifications", "_registry")

    def __init__(
        self,
        registry: DatabaseRegistry,
        handlers: EventHandlerRegistry,
        *,
        events: Table,
        notifications: Table,
        clock: Clock | None = None,
        batch_size: int = 200,
    ) -> None:
        self._registry = registry
        self._handlers = handlers
        self._events = events
        self._notifications = notifications
        self._clock = clock or SystemClock()
        self._batch = batch_size

    def drain_once(self) -> int:
        """One batch on every shard. Returns how many events were fanned out."""
        return sum(self._drain_shard(shard) for shard in self._registry.shards)

    def drain_until_idle(self, *, limit: int = 1000) -> int:
        total = 0
        for _ in range(limit):
            done = self.drain_once()
            total += done
            if done == 0:
                return total
        LOG.warning("fan-out still had work after %d rounds; stopping to avoid spinning", limit)
        return total

    def _drain_shard(self, shard: object) -> int:
        subscribed = self._handlers.event_types
        if not subscribed:
            return 0

        manager = self._registry.manager(shard)  # type: ignore[arg-type]
        with manager.transaction() as tx:
            # Only types somebody subscribes to. An event nobody wants is left alone rather
            # than marked delivered, so registering a handler later still picks it up - and
            # so a sentinel row, which has no event type at all, is never selected.
            rows = list(
                tx.execute(
                    select(self._events)
                    .where(
                        self._events.c.delivered.is_(False),
                        self._events.c.event_type.in_(subscribed),
                    )
                    .order_by(self._events.c.event_date)
                    .limit(self._batch)
                )
            )
            if not rows:
                return 0

            now = to_storage(self._clock.now())
            pending = []
            for row in rows:
                for handler in self._handlers.subscribers(row.event_type):
                    pending.append(
                        {
                            "id": uuid.uuid4(),
                            "event_id": row.id,
                            "handler_name": handler.name,
                            # The event's context, copied across, so dispatch never reads the
                            # events table again and a handler's envelope is one row.
                            "namespace": row.namespace,
                            "action_id": row.action_id,
                            "action_name": row.action_name,
                            "action_params": row.action_params,
                            "started_date": row.started_date,
                            "completion_date": row.completion_date,
                            "model_id": row.model_id,
                            "model_type": row.model_type,
                            "event_type": row.event_type,
                            "payload": row.payload,
                            "event_date": row.event_date,
                            "state": EventNotificationState.PENDING.value,
                            "attempts": 0,
                            "next_retry_at": now,
                            "created_date": now,
                            "updated_date": now,
                        }
                    )

            if pending:
                insert_ignoring_duplicates(tx.connection, self._notifications, pending)

            tx.execute(
                update(self._events)
                .where(self._events.c.id.in_([r.id for r in rows]))
                .values(delivered=True)
            )

        # After the transaction commits, not inside it. A count recorded for work that then
        # rolled back is a dashboard disagreeing with the database.
        EVENTS_FANNED_OUT.add(len(rows))
        NOTIFICATIONS_CREATED.add(len(pending))
        return len(rows)


DEFAULT_POLL = timedelta(seconds=1)
