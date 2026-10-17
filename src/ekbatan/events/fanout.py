"""Turning committed events into work.

One pass per shard: read the events nothing has fanned out yet whose type somebody subscribes
to, write one notification per subscriber, and mark those events delivered. The writes are one
transaction, so a crash half way leaves the events undelivered rather than the work lost. The
read is not part of it, as in Java: it takes its own connection. A row read twice - by a lagging
replica, say - is harmless, because the unique key on (event, handler) drops the second insert.

Fan-out and dispatch are separate stages because they are separate pieces of state.
``delivered`` says the event has been turned into work; a notification's state says whether the
work succeeded. Collapsing them means a handler failure either loses the event or replays it
forever.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import Table

from .._metrics import counter
from ..clock import Clock, SystemClock, to_storage
from ..persistence import DatabaseRegistry
from ..sharding import ShardIdentifier
from ._across import Concurrently
from .handler import EventHandlerRegistry
from .notification import EventNotification, EventNotificationState
from .repository import EventEntityRepository, EventNotificationRepository

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

LOG = logging.getLogger("io.ekbatan.events.localeventhandler.job.EventFanoutJob")


class EventFanout:
    """Materialises notifications from the outbox."""

    __slots__ = (
        "_batch",
        "_clock",
        "_event_repository",
        "_handlers",
        "_notification_repository",
        "_registry",
        "_shards",
    )

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
        self._event_repository = EventEntityRepository(registry, events)
        self._notification_repository = EventNotificationRepository(registry, notifications)
        self._clock = clock or SystemClock()
        self._batch = batch_size
        self._shards = Concurrently(len(registry.shards), name="ekbatan-fanout-shard", log=LOG)

    def drain_once(self) -> int:
        """One batch on every shard, the shards concurrently.

        Java forks a virtual thread per shard here and joins them. Returns how many events
        were fanned out. A single shard runs inline, with no pool.
        """
        return sum(self._shards.run(self._registry.shards, self._drain_shard))

    def close(self) -> None:
        """Let the shard pool's threads go."""
        self._shards.close()

    def drain_until_idle(self, *, limit: int = 1000) -> int:
        total = 0
        for _ in range(limit):
            done = self.drain_once()
            total += done
            if done == 0:
                return total
        LOG.warning("fan-out still had work after %d rounds; stopping to avoid spinning", limit)
        return total

    def _drain_shard(self, shard: ShardIdentifier) -> int:
        subscribed = self._handlers.event_types
        if not subscribed:
            return 0

        with self._registry.manager(shard).transaction():
            events = self._event_repository.find_undelivered(shard, subscribed, self._batch)
            if not events:
                return 0

            now = to_storage(self._clock.now())
            notifications = [
                EventNotification.builder()
                .id(uuid.uuid4())
                .event_id(event.id)
                .handler_name(handler.name)
                # The event's context, copied across, so dispatch never reads the events
                # table again and a handler's envelope is one row.
                .namespace(event.namespace)
                .action_id(event.action_id)
                .action_name(event.action_name)
                .action_params(event.action_params)
                .started_date(event.started_date)
                .completion_date(event.completion_date)
                .model_id(event.model_id)
                .model_type(event.model_type)
                .event_type(event.event_type)  # type: ignore[arg-type]  # never a sentinel
                .payload(event.payload)
                .event_date(event.event_date)
                .state(EventNotificationState.PENDING)
                .attempts(0)
                .next_retry_at(now)
                .created_date(now)
                .updated_date(now)
                .build()
                for event in events
                for handler in self._handlers.subscribers(event.event_type)  # type: ignore[arg-type]
            ]

            self._notification_repository.add_all_no_result(notifications, shard)
            self._event_repository.mark_delivered([e.id for e in events], shard)

        # After the transaction commits, not inside it. A count recorded for work that then
        # rolled back is a dashboard disagreeing with the database.
        EVENTS_FANNED_OUT.add(len(events))
        NOTIFICATIONS_CREATED.add(len(notifications))
        return len(events)


DEFAULT_POLL = timedelta(seconds=1)
