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
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, Table, and_, case, delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from ..clock import Clock, SystemClock
from ..event import SchedulerListeners
from ..exceptions import DbSchedulerException, ExecutionException, TaskInstanceException
from ..resolver import Resolvable, TaskResolver
from ..scheduled_execution import ScheduledExecutionsFilter, TaskSummary
from ..serializer import Serializer
from ..task import (
    Execution,
    Priority,
    RescheduleUpdate,
    ScheduledTaskInstance,
    TaskInstance,
)
from .customization import JdbcCustomization, autodetect
from .execution_update import ExecutionUpdate
from .schema import scheduled_tasks_table

__all__ = ["JdbcTaskRepository"]

LOG = logging.getLogger("db_scheduler.jdbc")

#: Distinguishes "leave the data alone" from "set the data to null", which are different
#: instructions and both legitimate.
_UNCHANGED: Any = object()

#: db-scheduler truncates the scheduler name it writes into `picked_by` to fifty characters.
#: The column is text, but a hostname plus a uuid is not worth more than that and the two ports
#: should write the same thing.
PICKED_BY_MAX = 50


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
        task_resolver: TaskResolver | None = None,
    ) -> None:
        from sqlalchemy import MetaData

        self.engine = engine
        self.persist_timestamp_in_utc = persist_timestamp_in_utc
        self.scheduler_name = scheduler_name[:PICKED_BY_MAX]
        self.clock = clock or SystemClock()
        self.order_by_priority = order_by_priority
        self.customization = customization or autodetect(
            engine.dialect.name,
            getattr(engine.dialect, "server_version_info", None),
            persist_timestamp_in_utc,
        )

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
        # A repository with no resolver is the standalone-client case db-scheduler supports:
        # nothing is known about any task, every name is unresolved, and data comes back as the
        # raw bytes rather than as a guess at a type.
        self.task_resolver = task_resolver or TaskResolver(SchedulerListeners(), self.clock)

    def _bind(self, moment: datetime | None) -> datetime | None:
        """A time to write: exactly as given. No zone is added, removed or moved - what the
        clock or the caller gave is what the column gets, and what it reads back."""
        return moment

    def _excluding_unresolved(self, statement: Any) -> Any:
        """`task_name not in (...)`, for the names this instance has no task for.

        Without it the poll fetches the same unrunnable rows on every cycle, finds them
        unresolvable, and does it again - which on a table with one abandoned task name is a
        scheduler that never gets to the work it *can* do.
        """
        unresolved = [one.task_name for one in self.task_resolver.unresolved]
        if not unresolved:
            return statement
        return statement.where(self.table.c.task_name.not_in(unresolved))

    # ------------------------------------------------------------------ creating

    def _fix_execution_time(self, instance: Any) -> ScheduledTaskInstance:
        """Accept either shape: a time already decided, or a function of now to decide it."""
        if isinstance(instance, ScheduledTaskInstance):
            return instance
        return ScheduledTaskInstance.fix_execution_time(instance, self.clock)

    def create_if_not_exists(self, instance: Any) -> bool:
        """Insert, unless the row is already there.

        Declined rather than raised: every instance in the cluster registers its recurring tasks
        at boot, usually within the same second of a rolling deploy, so the row already existing
        is the normal case.

        A failed insert is checked rather than assumed. If the row *is* there, another thread
        won the race and this returns False; if it is not, the insert failed for some other
        reason and that is an error, not a quiet no-op.
        """
        scheduled = self._fix_execution_time(instance)
        task_instance = scheduled.task_instance
        existing = self.get_execution(task_instance.task_name, task_instance.id)
        if existing is not None:
            LOG.debug(
                "Execution not created, it already exists. Due: %s", existing.execution_time
            )
            return False
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    insert(self.table),
                    [self._row_for(task_instance, scheduled.execution_time)],
                )
        except IntegrityError as exc:
            LOG.debug(
                "Exception when inserting execution. Assuming it to be a constraint violation.",
                exc_info=True,
            )
            if self.get_execution(task_instance.task_name, task_instance.id) is None:
                raise TaskInstanceException(
                    "Failed to add new execution.",
                    task_instance.task_name,
                    task_instance.id,
                ) from exc
            LOG.debug("Execution not created, another thread created it.")
            return False
        return True

    def create_batch(self, instances: Sequence[Any]) -> None:
        """Insert several. One statement, and all or nothing.

        db-scheduler raises `FailedToScheduleBatchException` if any of them collides, rather
        than silently writing a subset - a caller scheduling a batch means the batch.
        """
        if not instances:
            return
        scheduled = [self._fix_execution_time(one) for one in instances]
        rows = [self._row_for(one.task_instance, one.execution_time) for one in scheduled]
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
        statement = self._excluding_unresolved(
            select(table)
            .where(table.c.picked == False, table.c.execution_time <= self._bind(now))  # noqa: E712
        ).order_by(*order).limit(limit)
        with self.engine.connect() as conn:
            return self._to_executions(conn.execute(statement))

    def get_execution(self, task_name: str, task_instance: str) -> Execution | None:
        statement = select(self.table).where(
            self.table.c.task_name == task_name, self.table.c.task_instance == task_instance
        )
        with self.engine.connect() as conn:
            rows = list(conn.execute(statement))
        if len(rows) > 1:
            raise TaskInstanceException(
                "Found more than one matching execution for task name/id combination.",
                task_name,
                task_instance,
            )
        if not rows:
            return None
        # A lookup by name is asked for one specific row, so it comes back whether or not this
        # instance could run it, and without teaching the poll's exclusion filter anything.
        return self._to_execution(
            rows[0], include_unresolved=True, add_unresolved_to_exclusion_filter=False
        )

    def get_scheduled_executions(
        self,
        filter: ScheduledExecutionsFilter | None = None,
        task_name: str | None = None,
        *,
        picked: bool | None = None,
        limit: int | None = None,
    ) -> list[Execution]:
        """List scheduled executions, filtered and paged.

        The cursor is a *pair* - execution time and instance id - because execution times are
        not unique. Paging on time alone across two rows due at the same instant either skips
        one or returns it twice.

        `picked` and `limit` are accepted directly as a convenience; a `ScheduledExecutionsFilter`
        says the same thing and more.
        """
        if filter is None:
            filter = ScheduledExecutionsFilter.all()
            if picked is not None:
                filter = filter.with_picked(picked)
            if limit is not None:
                filter = filter.limit(limit)

        table = self.table
        statement = select(table)
        if filter.picked_value is not None:
            statement = statement.where(table.c.picked == filter.picked_value)

        after = filter.after_execution
        if after is not None:
            bound = self._bind(after.execution_time)
            statement = statement.where(
                (table.c.execution_time >= bound)
                & (
                    or_(
                        table.c.execution_time > bound,
                        table.c.task_instance > after.task_instance_id,
                    )
                )
            )
        before = filter.before_execution
        if before is not None:
            bound = self._bind(before.execution_time)
            statement = statement.where(
                (table.c.execution_time <= bound)
                & (
                    or_(
                        table.c.execution_time < bound,
                        table.c.task_instance < before.task_instance_id,
                    )
                )
            )
        if task_name is not None:
            statement = statement.where(table.c.task_name == task_name)
        if not filter.include_unresolved:
            statement = self._excluding_unresolved(statement)

        # Backwards only when paging backwards *and* not forwards: a range query reads forwards
        # like any other, and flipping it would hand the caller the page in reverse.
        backwards = before is not None and after is None
        statement = (
            statement.order_by(table.c.execution_time.desc(), table.c.task_instance.desc())
            if backwards
            else statement.order_by(table.c.execution_time.asc(), table.c.task_instance.asc())
        )
        if filter.limit_value is not None:
            statement = statement.limit(filter.limit_value)

        with self.engine.connect() as conn:
            return self._to_executions(
                conn.execute(statement),
                include_unresolved=filter.include_unresolved,
                add_unresolved_to_exclusion_filter=False,
            )

    def get_scheduled_executions_summary_by_task(self) -> list[TaskSummary]:
        """One row per task name: how many, how they are doing, and since when.

        `running` is picked; `failing` and `scheduled` are both *not* picked, split on whether
        they have failures behind them. A picked row is neither - it is running right now, and
        counting it as scheduled would double-count it.
        """
        table = self.table
        failures = func.coalesce(table.c.consecutive_failures, 0)
        statement = (
            select(
                table.c.task_name,
                func.count().label("instance_count"),
                func.sum(case((table.c.picked == True, 1), else_=0)).label(  # noqa: E712
                    "running_count"
                ),
                func.sum(
                    case(((table.c.picked == False) & (failures > 0), 1), else_=0)  # noqa: E712
                ).label("failing_count"),
                func.sum(
                    case(((table.c.picked == False) & (failures == 0), 1), else_=0)  # noqa: E712
                ).label("scheduled_count"),
                func.min(table.c.execution_time).label("earliest_execution_time"),
                func.max(table.c.last_success).label("latest_last_success"),
                func.max(table.c.last_failure).label("latest_last_failure"),
                func.max(table.c.consecutive_failures).label("max_consecutive_failures"),
            )
            .group_by(table.c.task_name)
            .order_by(table.c.task_name)
        )
        with self.engine.connect() as conn:
            return [
                TaskSummary(
                    task_name=row.task_name,
                    instance_count=int(row.instance_count),
                    running_count=int(row.running_count or 0),
                    failing_count=int(row.failing_count or 0),
                    scheduled_count=int(row.scheduled_count or 0),
                    earliest_execution_time=row.earliest_execution_time,
                    latest_last_success=row.latest_last_success,
                    latest_last_failure=row.latest_last_failure,
                    max_consecutive_failures=int(row.max_consecutive_failures or 0),
                )
                for row in conn.execute(statement)
            ]

    def get_dead_executions(self, older_than: datetime) -> list[Execution]:
        """Picked, but the instance holding it stopped saying so.

        Only picked rows: an unpicked row nobody is holding is not dead, it is simply waiting.
        """
        table = self.table
        statement = self._excluding_unresolved(
            select(table)
            .where(table.c.picked == True, table.c.last_heartbeat <= self._bind(older_than))  # noqa: E712
        ).order_by(table.c.last_heartbeat.asc())
        with self.engine.connect() as conn:
            return self._to_executions(conn.execute(statement))

    def get_executions_failing_longer_than(
        self, interval: timedelta, *, now: datetime | None = None
    ) -> list[Execution]:
        table = self.table
        boundary = self._bind((now or self.clock.now()) - interval)
        statement = self._excluding_unresolved(
            select(table).where(
                table.c.consecutive_failures > 0,
                (table.c.last_success.is_(None)) | (table.c.last_success < boundary),
            )
        )
        with self.engine.connect() as conn:
            # A monitoring query: it neither returns unrunnable rows nor teaches the poll's
            # exclusion filter about them.
            return self._to_executions(
                conn.execute(statement), add_unresolved_to_exclusion_filter=False
            )

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
        """Postgres: pick the ids with `FOR UPDATE SKIP LOCKED`, update them, return the rows.

        The statement cannot exclude unresolved names - it is one statement, and the exclusion
        list is a Python-side set - so anything unrunnable is picked first and *unpicked*
        immediately. Leaving it picked would strand the row until the dead-execution detector
        got to it.
        """
        table = self.table
        order = (
            (table.c.priority.desc(), table.c.execution_time.asc())
            if self.order_by_priority
            else (table.c.execution_time.asc(),)
        )
        candidates = (
            self._excluding_unresolved(
                select(table.c.task_name, table.c.task_instance).where(
                    table.c.picked == False,  # noqa: E712
                    table.c.execution_time <= self._bind(now),
                )
            )
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
            # Unresolved rows are included here on purpose. The exclusion above only knows the
            # names seen *before*, so a name unresolvable for the first time is picked by this
            # statement - and leaving it picked would strand the row until the dead-execution
            # detector got to it.
            all_candidates = self._to_executions(
                conn.execute(statement), include_unresolved=True
            )

        unresolved = [
            one for one in all_candidates if self.task_resolver.is_unresolved(one.task_name)
        ]
        if unresolved:
            self.unpick_picked_batch(unresolved)
        return [
            one for one in all_candidates if not self.task_resolver.is_unresolved(one.task_name)
        ]

    def _lock_and_fetch_generic(self, now: datetime, limit: int) -> list[Execution]:
        """MySQL 8: lock the rows in a transaction, update them, read them back in the same one.

        No `version` in the update's WHERE clause, deliberately: the rows are locked for the
        length of the transaction, so nothing else can have moved them, and the version guard
        would only add a way for the statement to miss.
        """
        table = self.table
        order = (
            (table.c.priority.desc(), table.c.execution_time.asc())
            if self.order_by_priority
            else (table.c.execution_time.asc(),)
        )
        with self.engine.begin() as conn:
            statement = self._excluding_unresolved(
                select(table)
                .where(table.c.picked == False, table.c.execution_time <= self._bind(now))  # noqa: E712
            ).order_by(*order).limit(limit).with_for_update(skip_locked=True)
            candidates = self._to_executions(conn.execute(statement))
            if not candidates:
                return []

            picked_by = self.scheduler_name
            last_heartbeat = self.clock.now()
            locked: list[Execution] = []
            no_lock: list[Execution] = []
            for candidate in candidates:
                updated = conn.execute(
                    update(table)
                    .where(
                        table.c.task_name == candidate.task_name,
                        table.c.task_instance == candidate.id,
                    )
                    .values(
                        picked=True,
                        picked_by=picked_by,
                        last_heartbeat=self._bind(last_heartbeat),
                        version=table.c.version + 1,
                    )
                ).rowcount
                (locked if updated > 0 else no_lock).append(candidate)

            if no_lock:
                LOG.error(
                    "Did not update same amount of executions that were locked in the "
                    "transaction. This might mean some assumption is wrong here, or that "
                    "transaction is not working. Needs to be investigated. Updated: %d, "
                    "expected: %d",
                    len(locked),
                    len(candidates),
                )
                LOG.warning(
                    "Returning picked executions for processing. Did not manage to pick "
                    "executions: %s",
                    ",".join(str(one.task_instance) for one in no_lock),
                )
            return [one.update_to_picked(picked_by, last_heartbeat) for one in locked]

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
        next_execution_time: datetime | RescheduleUpdate,
        *,
        last_success: datetime | None = None,
        last_failure: datetime | None = None,
        consecutive_failures: int | None = None,
        new_data: Any = _UNCHANGED,
    ) -> bool:
        """Move an execution to its next time and release the claim.

        Takes either a `RescheduleUpdate` - which can say "clear this column" as distinct from
        "leave it alone" - or the individual values, which is what the completion handlers do.
        """
        if isinstance(next_execution_time, RescheduleUpdate):
            reschedule_update = next_execution_time
        else:
            builder = RescheduleUpdate.to_execution_time(next_execution_time)
            if last_success is not None:
                builder = builder.last_success(last_success)
            if last_failure is not None:
                builder = builder.last_failure(last_failure)
            if consecutive_failures is not None:
                builder = builder.consecutive_failures(consecutive_failures)
            if new_data is not _UNCHANGED:
                builder = builder.data(new_data)
            reschedule_update = builder.build()

        update_statement = ExecutionUpdate.for_execution(execution)
        update_statement.picked(False)
        update_statement.picked_by(None)
        update_statement.last_heartbeat(None)
        update_statement.execution_time(reschedule_update.execution_time)

        if reschedule_update.last_success is not None:
            update_statement.last_success(reschedule_update.last_success.value)
        if reschedule_update.last_failure is not None:
            update_statement.last_failure(reschedule_update.last_failure.value)
        if reschedule_update.consecutive_failures is not None:
            update_statement.consecutive_failures(
                reschedule_update.consecutive_failures.value
            )
        if reschedule_update.data is not None:
            update_statement.task_data(
                self.serializer.serialize(reschedule_update.data.value)
            )

        with self.engine.begin() as conn:
            update_statement.update_single(conn, self.table, self._bind)
        return True

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
            raise ExecutionException(
                f"Expected one execution to be removed, but removed {removed}. Indicates a bug.",
                execution,
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
        """Remove one execution and schedule another, in one transaction.

        Takes either shape, as Java's two overloads do: a `ScheduledTaskInstance` whose time is
        already decided - which is what a completion handler hands over - or a
        `SchedulableInstance` that still has to be asked.
        """
        table = self.table
        scheduled = self._fix_execution_time(new_instance)
        new_instance = scheduled.task_instance
        execution_time: datetime = scheduled.execution_time
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
            conn.execute(insert(table), [self._row_for(new_instance, execution_time)])
        return execution_time

    # ------------------------------------------------------------------ mapping

    def _to_execution(
        self,
        row: Any,
        *,
        include_unresolved: bool = False,
        add_unresolved_to_exclusion_filter: bool = True,
    ) -> Execution | None:
        """One row, as an `Execution` - or nothing, when its task is unknown.

        Resolving the name is what decides how `task_data` is read. A known task names a data
        class, so the bytes are deserialized into it; an unknown one - a standalone client, or a
        task this instance does not deploy - gets the raw bytes back rather than a guess.
        """
        execution_time = row.execution_time
        assert execution_time is not None, "execution_time is NOT NULL in the schema"
        task = self.task_resolver.resolve(
            Resolvable.of(row.task_name, execution_time), add_unresolved_to_exclusion_filter
        )
        if task is None and not include_unresolved:
            if add_unresolved_to_exclusion_filter:
                LOG.warning(
                    "Failed to find implementation for task with name '%s'. Execution will be "
                    "excluded from due. The scheduler normally delete unresolved tasks after "
                    "14d. To handle manually, either delete the execution from the database, "
                    "or add an implementation for it. ",
                    row.task_name,
                )
            return None

        raw = row.task_data
        # Deserialized once, on demand: a poll that fetches a hundred due rows and runs three of
        # them should pay for three, not a hundred - and a handler that reads `data` twice
        # should still pay once. Java wraps the same supplier in a memoizer.
        unread: list[Any] = []

        def data() -> Any:
            if not unread:
                unread.append(
                    raw if task is None else self.serializer.deserialize(task.data_class, raw)
                )
            return unread[0]

        instance = TaskInstance[Any](
            row.task_name,
            row.task_instance,
            priority=row.priority if row.priority is not None else Priority.MEDIUM,
            data_supplier=data,
        )
        return Execution(
            execution_time=execution_time,
            task_instance=instance,
            picked=bool(row.picked),
            picked_by=row.picked_by,
            last_success=row.last_success,
            last_failure=row.last_failure,
            consecutive_failures=row.consecutive_failures or 0,
            last_heartbeat=row.last_heartbeat,
            version=row.version,
        )

    def _to_executions(
        self,
        rows: Any,
        *,
        include_unresolved: bool = False,
        add_unresolved_to_exclusion_filter: bool = True,
    ) -> list[Execution]:
        """Map a result set, dropping the rows whose task could not be resolved."""
        mapped = (
            self._to_execution(
                row,
                include_unresolved=include_unresolved,
                add_unresolved_to_exclusion_filter=add_unresolved_to_exclusion_filter,
            )
            for row in rows
        )
        return [execution for execution in mapped if execution is not None]


def _tuple_in(table: Table, candidates: Any) -> Any:
    """`(task_name, task_instance) IN (subquery)`, which the primary key makes exact."""
    from sqlalchemy import tuple_

    return tuple_(table.c.task_name, table.c.task_instance).in_(
        select(candidates.c.task_name, candidates.c.task_instance)
    )
