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
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Table

from .._json import decode
from .._metrics import counter, histogram
from ..clock import Clock, SystemClock, to_storage
from ..domain import ModelEvent
from ..persistence import DatabaseRegistry
from ..sharding import ShardIdentifier
from ._across import Concurrently
from .handler import EventEnvelope, EventHandler, EventHandlerRegistry
from .notification import EventNotification, backoff
from .repository import EventNotificationRepository

#: Java's three, name for name. `handled` is tagged with the outcome a row reached; `duration`
#: reports only `succeeded`/`failed`, because at the moment a handler returns the only fact known
#: is whether it threw - whether a failure becomes a retry or an expiry is decided afterwards.
HANDLED = counter(
    "ekbatan.events.handled",
    "Notification rows processed by the event-handling job, tagged by outcome",
    "{notification}",
)
HANDLER_DURATION = histogram(
    "ekbatan.events.handler.duration",
    "Wall-clock duration of a single event-handler invocation",
    "s",
)
#: Recorded on success only, so it measures time-to-delivery rather than time-spent-failing.
#: This is what reveals a growing backlog; a counter cannot, because a shard falling behind
#: still reports a healthy success count.
DELIVERY_LAG = histogram(
    "ekbatan.events.delivery.lag",
    "Age of the source event when its handler succeeded",
    "s",
)

__all__ = ["EventDispatch"]

LOG = logging.getLogger("io.ekbatan.events.localeventhandler.job.EventHandlingJob")

DEFAULT_RETENTION = timedelta(days=7)
DEFAULT_MAX_BACKOFF = timedelta(minutes=5)


#: What a delivery decided, each written by its own repository method.
SUCCEEDED = "succeeded"
FAILED = "failed"
EXPIRED_PREFLIGHT = "expired_preflight"
EXPIRED_POST_FAILURE = "expired_post_failure"
#: No handler is registered under the row's name any more.
DEFERRED = "deferred"

#: The order the outcome buckets are written in - Java's, with this port's own `DEFERRED` last.
_WRITE_ORDER = {
    EXPIRED_PREFLIGHT: 0,
    SUCCEEDED: 1,
    EXPIRED_POST_FAILURE: 2,
    FAILED: 3,
    DEFERRED: 4,
}


@dataclass(frozen=True, slots=True)
class _Outcome:
    """What one notification's delivery decided, in a form several rows can share.

    ``retry_delay`` rather than a resolved timestamp, because the timestamp is what would stop
    two identical outcomes from being one `UPDATE`: computed per row it differs by microseconds
    and every row needs its own statement. Java resolves it once per batch for the same reason.

    A failure carries its row too: whether it is retried or expired is decided after the whole
    batch has run, against one clock reading, as Java's ``postInvokeNow`` is.
    """

    notification_id: Any
    kind: str
    retry_delay: timedelta | None = None
    row: EventNotification | None = None


class EventDispatch:
    """Delivers notifications to handlers, at least once."""

    __slots__ = (
        "_batch",
        "_clock",
        "_delivery",
        "_handlers",
        "_max_backoff",
        "_notification_repository",
        "_registry",
        "_retention",
        "_shards",
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
        self._notification_repository = EventNotificationRepository(registry, notifications)
        self._clock = clock or SystemClock()
        self._batch = batch_size
        self._retention = retention
        self._max_backoff = max_backoff
        self._shards = Concurrently(len(registry.shards), name="ekbatan-dispatch-shard", log=LOG)
        # One pool per shard, each the width of a batch - which is the shape Java has, where
        # the executor is created inside each shard's batch rather than around the round.
        self._delivery = {
            shard: Concurrently(batch_size, name=f"ekbatan-handler-{shard}", log=LOG)
            for shard in registry.shards
        }

    def drain_once(self) -> int:
        """One batch on every shard, the shards concurrently.

        Java forks a virtual thread per shard here too. Returns how many notifications
        reached a new state. Within one shard a batch is still delivered in order - that
        half is its own divergence, under "Dispatch delivers one notification at a time".
        """
        return sum(self._shards.run(self._registry.shards, self._drain_shard))

    def close(self) -> None:
        """Let the shard and handler pools' threads go."""
        self._shards.close()
        for pool in self._delivery.values():
            pool.close()

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
        repository = self._notification_repository
        due = repository.find_due(shard, self._batch, to_storage(self._clock.now()))
        if not due:
            return 0

        # Java forks a virtual thread per notification here and joins them. A handler is
        # application code that is almost always waiting on something, so this is time a
        # batch spends blocked rather than computing - and the GIL is released for it.
        outcomes = self._delivery[shard].run(due, self._deliver)

        # Written after the whole batch has been attempted, each bucket its own statement that
        # commits itself, in Java's order: pre-flight expiries, successes, post-failure expiries,
        # then the retries. No transaction around them, as in Java: if one write fails, the
        # ones before it stay written, so a handler that already succeeded is not called again.
        # No single delivery can take the others' results with it.
        #
        # One statement per *bucket* rather than per row, which is what Java does: rows that
        # decided the same thing are one `UPDATE ... WHERE id IN (...)`. A batch of a hundred
        # first attempts that all succeed is one statement, not a hundred. Retries group by
        # their delay, which is Java's `failedRetryByAttempts` - the delay follows from the
        # attempt count.
        #
        # One `now` for the whole batch, again as Java's `postInvokeNow` is. Resolved per row it
        # would differ by microseconds and no two rows could ever share a statement.
        now = to_storage(self._clock.now())
        outcomes = [self._retry_or_expire(outcome, now) for outcome in outcomes]
        buckets: dict[tuple[str, timedelta | None], list[Any]] = defaultdict(list)
        for outcome in outcomes:
            buckets[(outcome.kind, outcome.retry_delay)].append(outcome.notification_id)

        for (kind, delay), ids in sorted(buckets.items(), key=lambda b: _WRITE_ORDER[b[0][0]]):
            if kind == SUCCEEDED:
                repository.mark_succeeded_all(ids, now, shard)
            elif kind == EXPIRED_PREFLIGHT:
                repository.mark_expired_all_preflight(ids, now, shard)
            elif kind == EXPIRED_POST_FAILURE:
                repository.mark_expired_all_post_failure(ids, now, shard)
            elif kind == FAILED:
                repository.mark_failed_bucket(ids, now + delay, now, shard)  # type: ignore[operator]
            else:
                repository.defer_pending_bucket(ids, now + delay, now, shard)  # type: ignore[operator]
        return len(outcomes)

    def _deliver(self, row: EventNotification) -> _Outcome:
        now = to_storage(self._clock.now())
        attempts = row.attempts + 1

        if self._expired(row.event_date, now):
            # Checked before invoking, not after. Handing a handler an event already known to
            # be too old risks a side effect nobody wanted - a stale email, a reversed charge -
            # for work that will be discarded either way.
            # ERROR, not INFO. Expiry is the only outcome that discards an event, and a
            # handler that could never succeed produces a wall of warnings while retrying and
            # then nothing at all at the moment its event is thrown away.
            LOG.error(
                "notification %s for handler %r was discarded: %s was already past its "
                "retention window when it was claimed",
                row.id,
                row.handler_name,
                row.event_type,
            )
            HANDLED.add(1, {"outcome": "expired_preflight", "handler": row.handler_name})
            return _Outcome(row.id, EXPIRED_PREFLIGHT)

        handler = self._handlers.by_name(row.handler_name)
        if handler is None:
            # Nobody is listening under that name any more. Left pending rather than expired:
            # a handler removed by a bad deploy comes back, and its queue should still be there.
            LOG.warning(
                "no handler named %r is registered; leaving its notifications alone",
                row.handler_name,
            )
            return _Outcome(row.id, DEFERRED, backoff(attempts, self._max_backoff))

        started = time.monotonic()
        try:
            handler.handle(self._envelope(handler, row, attempts))
        except BaseException as error:
            HANDLER_DURATION.record(
                time.monotonic() - started,
                {"handler": row.handler_name, "outcome": "failed"},
            )
            # BaseException on purpose. The failure class an ordinary `except Exception`
            # misses is exactly the one that would otherwise escape and discard this batch's
            # siblings, whose successes would then never be recorded.
            LOG.warning(
                "handler %r failed on attempt %d (%s: %s)",
                row.handler_name,
                attempts,
                type(error).__name__,
                error,
            )
            # Retry or expire is decided once the whole batch has run - `_retry_or_expire`.
            return _Outcome(row.id, FAILED, backoff(attempts, self._max_backoff), row)

        HANDLER_DURATION.record(
            time.monotonic() - started, {"handler": row.handler_name, "outcome": "succeeded"}
        )
        HANDLED.add(1, {"outcome": "succeeded", "handler": row.handler_name})
        DELIVERY_LAG.record(
            (to_storage(self._clock.now()) - row.event_date).total_seconds(),
            {"handler": row.handler_name},
        )
        return _Outcome(row.id, SUCCEEDED)

    def _retry_or_expire(self, outcome: _Outcome, now: datetime) -> _Outcome:
        """A failure is retried unless its next retry would land past the retention deadline,
        in which case it expires now - Java's rule, against the batch's one clock reading.

        Waiting for the deadline to pass instead would leave the row ``FAILED`` with a retry
        nobody will ever make, and expire it later without the handler being called.
        """
        row = outcome.row
        if outcome.kind != FAILED or row is None or outcome.retry_delay is None:
            return outcome
        if now + outcome.retry_delay > row.event_date + self._retention:
            LOG.error(
                "notification %s for handler %r was discarded: it exhausted the retention "
                "window after repeated handler failures",
                row.id,
                row.handler_name,
            )
            HANDLED.add(1, {"outcome": "expired_postfailure", "handler": row.handler_name})
            return _Outcome(row.id, EXPIRED_POST_FAILURE)
        LOG.warning(
            "notification %s for handler %r will be retried in %s",
            row.id,
            row.handler_name,
            outcome.retry_delay,
        )
        HANDLED.add(1, {"outcome": "failed_retry", "handler": row.handler_name})
        return outcome

    def _expired(self, event_date: datetime, now: datetime) -> bool:
        return event_date + self._retention <= now

    def _envelope(
        self, handler: EventHandler[Any], row: EventNotification, attempt: int
    ) -> EventEnvelope[ModelEvent]:
        event_type = handler.event_type
        payload = row.payload or {}
        return EventEnvelope(
            event=decode(event_type, payload),
            event_id=row.event_id,
            namespace=row.namespace,
            action_id=row.action_id,
            action_name=row.action_name,
            action_params=dict(row.action_params or {}),
            started_date=row.started_date,
            completion_date=row.completion_date,
            event_date=row.event_date,
            model_id=row.model_id,
            model_type=row.model_type,
            attempt=attempt,
        )
