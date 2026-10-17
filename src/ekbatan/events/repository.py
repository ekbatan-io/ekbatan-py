"""The two tables the local event handler reads and writes, one repository each.

Java's ``EventEntityRepository`` and ``EventNotificationRepository`` in the local event handler
module, method for method. Each takes its table, where Java's names ``eventlog.events`` and
``eventlog.event_notifications`` itself: the schema is not the same on every database here -
SQLite has none - so the application declares each table once and hands the same one to
everything that touches it.

The writes go through the shard's open transaction when there is one, as Java's
``txDbElseDb`` does. The two reads do not, as Java's do not: ``find_undelivered`` and
``find_due`` each take a connection of their own, so inside an open transaction they do not see
its uncommitted rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import Connection, Table, select, update

from ..action.outbox import OUTBOX_COLUMNS, EventEntity
from ..clock import to_storage
from ..errors import ValidationError
from ..fetching import Db
from ..persistence import DatabaseRegistry, insert_ignoring_duplicates
from ..sharding import ShardIdentifier
from .notification import NOTIFICATION_COLUMNS, EventNotification, EventNotificationState

__all__ = ["EventEntityRepository", "EventNotificationRepository"]


def _require_columns(table: Table, columns: tuple[str, ...], what: str) -> Table:
    missing = [c for c in columns if c not in table.c]
    if missing:
        raise ValidationError(f"{table.name} is missing {what} column(s): {missing}")
    return table


class _ShardRepository:
    __slots__ = ("_registry", "_table")

    def __init__(self, database_registry: DatabaseRegistry, table: Table) -> None:
        if database_registry is None:
            raise ValidationError("database_registry cannot be None")
        self._registry = database_registry
        self._table = table

    @contextmanager
    def _tx_db_else_db(self, shard: ShardIdentifier) -> Iterator[Connection]:
        """The transaction open on ``shard``, or a connection that commits each statement."""
        with self._registry.manager(shard).connection() as conn:
            yield conn

    @contextmanager
    def _db(self, shard: ShardIdentifier) -> Iterator[Connection]:
        """Java's ``db(shard)``: the shard's primary, outside any open transaction. Each
        statement commits itself."""
        with Db(self._registry.manager(shard).primary).execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            yield conn

    @contextmanager
    def _readonly_db(self, shard: ShardIdentifier) -> Iterator[Connection]:
        """Java's ``readonlyDb(shard)``: the shard's replica, or its primary when it has none.
        Never the open transaction."""
        with Db(self._registry.manager(shard).replica).execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            yield conn


class EventEntityRepository(_ShardRepository):
    """The fan-out job's view of ``eventlog.events``: what it has not delivered yet, and
    marking it delivered."""

    __slots__ = ()

    def __init__(self, database_registry: DatabaseRegistry, table: Table) -> None:
        super().__init__(database_registry, _require_columns(table, OUTBOX_COLUMNS, "outbox"))

    def find_undelivered(
        self, shard: ShardIdentifier, handled_event_types: Collection[str], limit: int
    ) -> list[EventEntity]:
        """Undelivered events of a type somebody handles, oldest first.

        Only types somebody subscribes to. An event nobody wants is left alone rather than
        marked delivered, so registering a handler later still picks it up - and a sentinel
        row, which has no event type at all, is never selected.

        Read from the shard's replica, or its primary when it has none, as Java's is - so with a
        lagging replica fan-out runs late, never wrong: an event read twice is dropped by the
        unique key on the notification insert. Outside any open transaction, so it does not see
        rows that transaction has written and not yet committed.
        """
        if handled_event_types is None:
            raise ValidationError("handled_event_types cannot be None")
        if not handled_event_types:
            return []
        table = self._table
        with self._readonly_db(shard) as conn:
            rows = conn.execute(
                select(table)
                .where(table.c.delivered.is_(False), table.c.event_type.in_(handled_event_types))
                .order_by(table.c.event_date)
                .limit(limit)
            )
            return [EventEntity.from_row(r) for r in rows]

    def mark_delivered(self, ids: Collection[uuid.UUID], shard: ShardIdentifier) -> None:
        if not ids:
            return
        table = self._table
        with self._tx_db_else_db(shard) as conn:
            conn.execute(update(table).where(table.c.id.in_(ids)).values(delivered=True))


class EventNotificationRepository(_ShardRepository):
    """``eventlog.event_notifications``: written by fan-out, claimed and resolved by dispatch."""

    __slots__ = ()

    def __init__(self, database_registry: DatabaseRegistry, table: Table) -> None:
        super().__init__(
            database_registry, _require_columns(table, NOTIFICATION_COLUMNS, "notification")
        )

    def add_all_no_result(
        self, notifications: Collection[EventNotification], shard: ShardIdentifier
    ) -> None:
        """Insert, letting the unique constraint drop any already there - Java's
        ``onConflictDoNothing``."""
        if not notifications:
            return
        with self._tx_db_else_db(shard) as conn:
            insert_ignoring_duplicates(conn, self._table, [n.to_row() for n in notifications])

    def find_due(
        self, shard: ShardIdentifier, limit: int, now: datetime
    ) -> list[EventNotification]:
        """Pending or failed notifications whose retry time has come, soonest first.

        Read from the primary, outside any open transaction, as Java's is. Never the replica: a
        replica that has not yet seen a row marked ``SUCCEEDED`` would hand it to its handler
        again.
        """
        table = self._table
        with self._db(shard) as conn:
            rows = conn.execute(
                select(table)
                .where(
                    table.c.state.in_(
                        [EventNotificationState.PENDING.value, EventNotificationState.FAILED.value]
                    ),
                    table.c.next_retry_at <= to_storage(now),
                )
                .order_by(table.c.next_retry_at)
                .limit(limit)
            )
            return [EventNotification.from_row(r) for r in rows]

    def mark_succeeded_all(
        self, ids: Collection[uuid.UUID], now: datetime, shard: ShardIdentifier
    ) -> None:
        self._mark(ids, shard, state=EventNotificationState.SUCCEEDED, attempt=True, now=now)

    def mark_failed_bucket(
        self,
        ids: Collection[uuid.UUID],
        next_retry_at: datetime,
        now: datetime,
        shard: ShardIdentifier,
    ) -> None:
        self._mark(
            ids,
            shard,
            state=EventNotificationState.FAILED,
            attempt=True,
            now=now,
            next_retry_at=next_retry_at,
        )

    def mark_expired_all_preflight(
        self, ids: Collection[uuid.UUID], now: datetime, shard: ShardIdentifier
    ) -> None:
        """Expired before the handler was called, so the attempt count stays as it was."""
        self._mark(ids, shard, state=EventNotificationState.EXPIRED, attempt=False, now=now)

    def mark_expired_all_post_failure(
        self, ids: Collection[uuid.UUID], now: datetime, shard: ShardIdentifier
    ) -> None:
        """Expired after a failed call, which counts as an attempt."""
        self._mark(ids, shard, state=EventNotificationState.EXPIRED, attempt=True, now=now)

    def defer_pending_bucket(
        self,
        ids: Collection[uuid.UUID],
        next_retry_at: datetime,
        now: datetime,
        shard: ShardIdentifier,
    ) -> None:
        """Leave pending, not attempted, until ``next_retry_at``.

        Not Java's: this is how this port treats a notification whose handler is no longer
        registered, where Java counts the missing handler as a failed attempt.
        """
        self._mark(
            ids,
            shard,
            state=EventNotificationState.PENDING,
            attempt=False,
            now=now,
            next_retry_at=next_retry_at,
        )

    def _mark(
        self,
        ids: Collection[uuid.UUID],
        shard: ShardIdentifier,
        *,
        state: EventNotificationState,
        attempt: bool,
        now: datetime,
        next_retry_at: datetime | None = None,
    ) -> None:
        if not ids:
            return
        table = self._table
        # Bound as the columns hold them - UTC, no zone - whatever the column type.
        values: dict[str, object] = {"state": state.value, "updated_date": to_storage(now)}
        if attempt:
            values["attempts"] = table.c.attempts + 1
        if next_retry_at is not None:
            values["next_retry_at"] = to_storage(next_retry_at)
        with self._tx_db_else_db(shard) as conn:
            conn.execute(update(table).where(table.c.id.in_(ids)).values(**values))
