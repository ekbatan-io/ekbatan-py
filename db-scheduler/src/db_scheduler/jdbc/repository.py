"""Reading and writing `scheduled_tasks`.

A port of `JdbcTaskRepository`. Every method here is one statement against the table, and the
important ones all carry `WHERE version = ?` - the optimistic lock that turns "several instances
polling the same table" into "exactly one of them runs this slot".

There is no ORM. The Java implementation writes SQL by hand for the same reason: what these
statements say, and in particular what their WHERE clauses say, *is* the coordination.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, Table, and_, delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from ..clock import Clock, SystemClock
from ..exceptions import DbSchedulerException, TaskInstanceException
from ..serializer import Serializer
from ..task import Execution, Priority, TaskInstance
from .customization import JdbcCustomization, autodetect
from .schema import scheduled_tasks_table

__all__ = ["JdbcTaskRepository", "TaskSummary"]

LOG = logging.getLogger("db_scheduler.jdbc")

#: Distinguishes "leave the data alone" from "set the data to null", which are different
#: instructions and both legitimate.
_UNCHANGED: Any = object()

#: db-scheduler truncates the scheduler name it writes into `picked_by` to fifty characters.
#: The column is text, but a hostname plus a uuid is not worth more than that and the two ports
#: should write the same thing.
PICKED_BY_MAX = 50


@dataclass(frozen=True, slots=True)
class TaskSummary:
    """How many executions each task has, for an overview screen."""

    task_name: str
    count: int


class JdbcTaskRepository:
    """The table, as operations."""

    def __init__(
        self,
        engine: Engine,
        *,
        scheduler_name: str,
        serializer: Serializer | None = None,
        table: Table | None = None,
        table_name: str | None = None,
        clock: Clock | None = None,
        customization: JdbcCustomization | None = None,
        order_by_priority: bool = False,
        persist_timestamp_in_utc: bool = True,
    ) -> None:
        from sqlalchemy import MetaData

        self.engine = engine
        self.persist_timestamp_in_utc = persist_timestamp_in_utc
        self.scheduler_name = scheduler_name[:PICKED_BY_MAX]
        self.clock = clock or SystemClock()
        self.order_by_priority = order_by_priority
        self.customization = customization or autodetect(engine.dialect.name)

        if table is not None:
            self.table = table
        elif table_name is not None:
            self.table = scheduled_tasks_table(
                MetaData(), table_name, persist_timestamp_in_utc=persist_timestamp_in_utc
            )
        else:
            self.table = scheduled_tasks_table(
                MetaData(), persist_timestamp_in_utc=persist_timestamp_in_utc
            )

        from ..serializer import JsonSerializer

        self.serializer = serializer or JsonSerializer()

    def _bind(self, moment: datetime | None) -> datetime | None:
        """One place that knows which timestamp shape this table uses."""
        return _to_column(moment, self.persist_timestamp_in_utc)

    # ------------------------------------------------------------------ creating

    def create_if_not_exists(self, schedulable: Any) -> bool:
        """Insert, unless the row is already there.

        Declined rather than raised: every instance in the cluster registers its recurring
        tasks at boot, usually within the same second of a rolling deploy, so the row already
        existing is the normal case.
        """
        instance = schedulable.task_instance
        execution_time = schedulable.next_execution_time(self.clock.now())
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(self.table), [self._row_for(instance, execution_time)])
        except IntegrityError:
            LOG.debug(
                "Execution not created, it already exists. task=%s id=%s",
                instance.task_name,
                instance.id,
            )
            return False
        return True

    def create_batch(self, schedulables: Sequence[Any]) -> None:
        """Insert several. One statement, and all or nothing.

        db-scheduler raises `FailedToScheduleBatchException` if any of them collides, rather
        than silently writing a subset - a caller scheduling a batch means the batch.
        """
        if not schedulables:
            return
        now = self.clock.now()
        rows = [
            self._row_for(s.task_instance, s.next_execution_time(now)) for s in schedulables
        ]
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(self.table), rows)
        except IntegrityError as exc:
            raise DbSchedulerException(
                f"failed to schedule a batch of {len(rows)} execution(s); none were written. "
                f"At least one of them already exists."
            ) from exc

    def _row_for(self, instance: TaskInstance[Any], execution_time: datetime) -> dict[str, Any]:
        return {
            "task_name": instance.task_name,
            "task_instance": instance.id,
            "task_data": self.serializer.serialize(instance.data),
            "execution_time": self._bind(execution_time),
            "picked": False,
            "picked_by": None,
            "last_success": None,
            "last_failure": None,
            "consecutive_failures": 0,
            "last_heartbeat": None,
            "version": 1,
            "priority": instance.priority,
        }

    # ------------------------------------------------------------------ reading

    def get_due(self, now: datetime, limit: int) -> list[Execution]:
        """Executions that are due and nobody has claimed."""
        table = self.table
        order = (
            (table.c.priority.desc(), table.c.execution_time.asc())
            if self.order_by_priority
            else (table.c.execution_time.asc(),)
        )
        statement = (
            select(table)
            .where(table.c.picked == False, table.c.execution_time <= self._bind(now))  # noqa: E712
            .order_by(*order)
            .limit(limit)
        )
        with self.engine.connect() as conn:
            return [self._to_execution(row) for row in conn.execute(statement)]

    def get_execution(self, task_name: str, task_instance: str) -> Execution | None:
        statement = select(self.table).where(
            self.table.c.task_name == task_name, self.table.c.task_instance == task_instance
        )
        with self.engine.connect() as conn:
            row = conn.execute(statement).first()
        return self._to_execution(row) if row is not None else None

    def get_scheduled_executions(
        self,
        *,
        task_name: str | None = None,
        picked: bool | None = None,
        limit: int | None = None,
    ) -> list[Execution]:
        table = self.table
        statement = select(table).order_by(table.c.execution_time.asc())
        if task_name is not None:
            statement = statement.where(table.c.task_name == task_name)
        if picked is not None:
            statement = statement.where(table.c.picked == picked)
        if limit is not None:
            statement = statement.limit(limit)
        with self.engine.connect() as conn:
            return [self._to_execution(row) for row in conn.execute(statement)]

    def get_scheduled_executions_summary(self) -> list[TaskSummary]:
        table = self.table
        statement = (
            select(table.c.task_name, func.count().label("n"))
            .group_by(table.c.task_name)
            .order_by(table.c.task_name)
        )
        with self.engine.connect() as conn:
            return [TaskSummary(row.task_name, row.n) for row in conn.execute(statement)]

    def get_dead_executions(self, older_than: datetime) -> list[Execution]:
        """Picked, but the instance holding it stopped saying so.

        Only picked rows: an unpicked row nobody is holding is not dead, it is simply waiting.
        """
        table = self.table
        statement = (
            select(table)
            .where(table.c.picked == True, table.c.last_heartbeat <= self._bind(older_than))  # noqa: E712
            .order_by(table.c.last_heartbeat.asc())
        )
        with self.engine.connect() as conn:
            return [self._to_execution(row) for row in conn.execute(statement)]

    def get_executions_failing_longer_than(
        self, interval: timedelta, *, now: datetime | None = None
    ) -> list[Execution]:
        table = self.table
        boundary = self._bind((now or self.clock.now()) - interval)
        statement = select(table).where(
            table.c.consecutive_failures > 0,
            (table.c.last_success.is_(None)) | (table.c.last_success < boundary),
        )
        with self.engine.connect() as conn:
            return [self._to_execution(row) for row in conn.execute(statement)]

    # ------------------------------------------------------------------ claiming

    def pick(self, execution: Execution, time_picked: datetime) -> Execution | None:
        """Claim one execution, or find that somebody else already has.

        The four conditions together are the whole coordination: the right row, still unpicked,
        still at the version we read. A loser's UPDATE matches nothing and returns 0.
        """
        table = self.table
        statement = (
            update(table)
            .where(
                and_(
                    table.c.task_name == execution.task_name,
                    table.c.task_instance == execution.id,
                    table.c.picked == False,  # noqa: E712
                    table.c.version == execution.version,
                )
            )
            .values(
                picked=True,
                picked_by=self.scheduler_name,
                last_heartbeat=self._bind(time_picked),
                version=table.c.version + 1,
            )
        )
        with self.engine.begin() as conn:
            updated = conn.execute(statement).rowcount

        if updated == 0:
            LOG.debug(
                "Failed to pick execution. It must have been picked by another scheduler. %s",
                execution,
            )
            return None

        picked = self.get_execution(execution.task_name, execution.id)
        if picked is None:
            raise DbSchedulerException(
                "Unable to find the execution just picked. It must have been deleted by another "
                "thread. Indicates a bug."
            )
        if not picked.picked:
            raise DbSchedulerException(
                f"Picked execution does not have the expected state in the database: {picked}"
            )
        return picked

    def unpick_picked_batch(self, executions: Sequence[Execution]) -> None:
        """Put claimed rows back, for a task nobody in this process knows how to run."""
        if not executions:
            return
        table = self.table
        with self.engine.begin() as conn:
            for execution in executions:
                conn.execute(
                    update(table)
                    .where(
                        table.c.task_name == execution.task_name,
                        table.c.task_instance == execution.id,
                        table.c.picked == True,  # noqa: E712
                        table.c.version == execution.version,
                    )
                    .values(picked=False, picked_by=None, version=table.c.version + 1)
                )

    def lock_and_get_due(self, now: datetime, limit: int) -> list[Execution]:
        """Claim and return in one go, where the dialect can."""
        self.verify_supports_lock_and_fetch()
        if self.customization.supports_single_statement_lock_and_fetch:
            return self._lock_and_fetch_single_statement(now, limit)
        return self._lock_and_fetch_generic(now, limit)

    def _lock_and_fetch_single_statement(self, now: datetime, limit: int) -> list[Execution]:
        """Postgres: pick the ids with `FOR UPDATE SKIP LOCKED`, update them, return the rows."""
        table = self.table
        order = (
            (table.c.priority.desc(), table.c.execution_time.asc())
            if self.order_by_priority
            else (table.c.execution_time.asc(),)
        )
        candidates = (
            select(table.c.task_name, table.c.task_instance)
            .where(table.c.picked == False, table.c.execution_time <= self._bind(now))  # noqa: E712
            .order_by(*order)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .subquery()
        )
        statement = (
            update(table)
            .where(
                _tuple_in(table, candidates),
            )
            .values(
                picked=True,
                picked_by=self.scheduler_name,
                last_heartbeat=self._bind(now),
                version=table.c.version + 1,
            )
            .returning(table)
        )
        with self.engine.begin() as conn:
            return [self._to_execution(row) for row in conn.execute(statement)]

    def _lock_and_fetch_generic(self, now: datetime, limit: int) -> list[Execution]:
        """MySQL 8: lock the rows in a transaction, update them, read them back in the same one."""
        table = self.table
        order = (
            (table.c.priority.desc(), table.c.execution_time.asc())
            if self.order_by_priority
            else (table.c.execution_time.asc(),)
        )
        with self.engine.begin() as conn:
            locked = list(
                conn.execute(
                    select(table)
                    .where(table.c.picked == False, table.c.execution_time <= self._bind(now))  # noqa: E712
                    .order_by(*order)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            picked: list[Execution] = []
            for row in locked:
                conn.execute(
                    update(table)
                    .where(
                        table.c.task_name == row.task_name,
                        table.c.task_instance == row.task_instance,
                        table.c.version == row.version,
                    )
                    .values(
                        picked=True,
                        picked_by=self.scheduler_name,
                        last_heartbeat=self._bind(now),
                        version=table.c.version + 1,
                    )
                )
                execution = self._to_execution(row)
                picked.append(execution.update_to_picked(self.scheduler_name, now))
            return picked

    @property
    def supports_lock_and_fetch(self) -> bool:
        return self.customization.supports_lock_and_fetch

    def verify_supports_lock_and_fetch(self) -> None:
        if not self.customization.supports_lock_and_fetch:
            raise DbSchedulerException(
                f"the {self.customization.name} customization does not support lock-and-fetch, "
                f"i.e. SELECT FOR UPDATE ... SKIP LOCKED. Use the fetch polling strategy, which "
                f"is correct on every dialect - the claim is the version guard, not the row lock."
            )

    # ------------------------------------------------------------------ updating

    def reschedule(
        self,
        execution: Execution,
        next_execution_time: datetime,
        *,
        last_success: datetime | None = None,
        last_failure: datetime | None = None,
        consecutive_failures: int | None = None,
        new_data: Any = _UNCHANGED,
    ) -> bool:
        """Move an execution to its next time and release the claim."""
        table = self.table
        values: dict[str, Any] = {
            "picked": False,
            "picked_by": None,
            "last_heartbeat": None,
            "execution_time": self._bind(next_execution_time),
            "version": table.c.version + 1,
        }
        if last_success is not None:
            values["last_success"] = self._bind(last_success)
        if last_failure is not None:
            values["last_failure"] = self._bind(last_failure)
        if consecutive_failures is not None:
            values["consecutive_failures"] = consecutive_failures
        if new_data is not _UNCHANGED:
            values["task_data"] = self.serializer.serialize(new_data)

        statement = (
            update(table)
            .where(
                table.c.task_name == execution.task_name,
                table.c.task_instance == execution.id,
                table.c.version == execution.version,
            )
            .values(**values)
        )
        with self.engine.begin() as conn:
            return conn.execute(statement).rowcount == 1

    def update_heartbeat(self, execution: Execution, heartbeat: datetime) -> bool:
        """Say this instance is still working on it.

        A miss is not an error. The completion handler may have finished and moved the row a
        moment before the heartbeat thread got there - a real race, rare, and harmless.
        """
        table = self.table
        statement = (
            update(table)
            .where(
                table.c.task_name == execution.task_name,
                table.c.task_instance == execution.id,
                table.c.version == execution.version,
            )
            .values(last_heartbeat=self._bind(heartbeat))
        )
        with self.engine.begin() as conn:
            updated = conn.execute(statement).rowcount

        if updated == 0:
            LOG.debug(
                "Did not update heartbeat. The execution must have been removed or rescheduled. "
                "task-instance=%s",
                execution.task_instance,
            )
            return False
        return True

    def update_heartbeat_with_retry(
        self, execution: Execution, heartbeat: datetime, tries: int
    ) -> bool:
        """Retry a heartbeat that failed for an infrastructure reason.

        Silence is how the cluster decides an instance is dead, so one dropped connection
        should not start a takeover of work that is going fine.
        """
        while True:
            try:
                return self.update_heartbeat(execution, heartbeat)
            except Exception as exc:
                tries -= 1
                if tries <= 0:
                    LOG.warning("Failed to update heartbeat. No more retries. %s", exc)
                    return False
                LOG.info("Failed to update heartbeat. Remaining retries=%d. %s", tries, exc)

    # ------------------------------------------------------------------ removing

    def remove(self, execution: Execution) -> None:
        table = self.table
        statement = delete(table).where(
            table.c.task_name == execution.task_name,
            table.c.task_instance == execution.id,
            table.c.version == execution.version,
        )
        with self.engine.begin() as conn:
            removed = conn.execute(statement).rowcount
        if removed != 1:
            raise TaskInstanceException(
                "expected exactly one execution to be removed, but the row had moved - it was "
                "rescheduled or removed by somebody else",
                execution.task_name,
                execution.id,
            )

    def remove_executions(self, task_name: str) -> int:
        """Every execution of one task. For a task nobody implements any more."""
        with self.engine.begin() as conn:
            return int(
                conn.execute(
                    delete(self.table).where(self.table.c.task_name == task_name)
                ).rowcount
            )

    def replace(self, to_be_replaced: Execution, new_instance: Any) -> datetime:
        """Remove one execution and schedule another, in one transaction."""
        table = self.table
        execution_time: datetime = new_instance.next_execution_time(self.clock.now())
        with self.engine.begin() as conn:
            removed = conn.execute(
                delete(table).where(
                    table.c.task_name == to_be_replaced.task_name,
                    table.c.task_instance == to_be_replaced.id,
                    table.c.version == to_be_replaced.version,
                )
            ).rowcount
            if removed != 1:
                raise TaskInstanceException(
                    "could not replace the execution: the row had already moved",
                    to_be_replaced.task_name,
                    to_be_replaced.id,
                )
            conn.execute(
                insert(table), [self._row_for(new_instance.task_instance, execution_time)]
            )
        return execution_time

    # ------------------------------------------------------------------ mapping

    def _to_execution(self, row: Any) -> Execution:
        raw = row.task_data
        # Deserialize on demand: a poll that fetches a hundred due rows and runs three of them
        # should pay for three, not a hundred. Java holds the same thing behind a Supplier.
        def data() -> Any:
            return self.serializer.deserialize(raw)

        instance = TaskInstance[Any](
            row.task_name,
            row.task_instance,
            priority=row.priority if row.priority is not None else Priority.MEDIUM,
            data_supplier=data,
        )
        execution_time = _aware(row.execution_time)
        assert execution_time is not None, "execution_time is NOT NULL in the schema"
        return Execution(
            execution_time=execution_time,
            task_instance=instance,
            picked=bool(row.picked),
            picked_by=row.picked_by,
            last_success=_aware(row.last_success),
            last_failure=_aware(row.last_failure),
            consecutive_failures=row.consecutive_failures or 0,
            last_heartbeat=_aware(row.last_heartbeat),
            version=row.version,
        )


def _tuple_in(table: Table, candidates: Any) -> Any:
    """`(task_name, task_instance) IN (subquery)`, which the primary key makes exact."""
    from sqlalchemy import tuple_

    return tuple_(table.c.task_name, table.c.task_instance).in_(
        select(candidates.c.task_name, candidates.c.task_instance)
    )


def _to_column(moment: datetime | None, persist_in_utc: bool) -> datetime | None:
    """The value to bind, in the shape the column expects.

    Zone-less UTC when the column is zone-less, an aware UTC value when it is not. Getting this
    the wrong way round is the whole hazard: a naive value bound to a `timestamptz` is read as
    the session's local time, which is right only where that is UTC.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        raise ValueError(
            f"{moment!r} has no timezone. Instants written here are compared across machines, "
            f"so a naive datetime is ambiguous."
        )
    as_utc = moment.astimezone(UTC)
    return as_utc.replace(tzinfo=None) if persist_in_utc else as_utc


def _aware(moment: datetime | None) -> datetime | None:
    """The other direction. A zone-less column holds UTC, so say so; a zoned one already knows."""
    if moment is None:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
