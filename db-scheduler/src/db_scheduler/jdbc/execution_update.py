"""`ExecutionUpdate` - one UPDATE, built from whichever columns were actually set.

The reason it exists rather than a fixed statement: a reschedule that was told nothing about
`last_failure` must leave the column alone, while one that was told `None` must clear it. A
dictionary of "columns to write" is the only way to say both, and it is why every setter here
wraps its argument in a `NewValue` rather than storing it directly.

The version guard is the point of the WHERE clause. The new version is written explicitly as
`old + 1` rather than as `version = version + 1`, so the value is known before the statement
runs and the caller can carry it forward.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Self

from sqlalchemy import Table, update

from ..exceptions import ExecutionException
from ..task import Execution, NewValue, TaskInstanceId

if TYPE_CHECKING:
    from sqlalchemy import Connection

__all__ = ["ExecutionUpdate"]

LOG = logging.getLogger("db_scheduler.jdbc")


class ExecutionUpdate:
    """Columns to write on one row, at one version."""

    __slots__ = ("_task_instance", "_updates", "_version", "_version_to_update")

    def __init__(self, task_instance: TaskInstanceId, version_to_update: int) -> None:
        self._task_instance = task_instance
        self._version_to_update = version_to_update
        self._updates: dict[str, Any] = {}
        self._version = version_to_update + 1

    @classmethod
    def for_execution(cls, execution: Execution) -> Self:
        return cls(execution.to_id(), execution.version)

    @property
    def new_version(self) -> int:
        return self._version

    def execution_time(self, execution_time: datetime | None) -> Self:
        self._updates["execution_time"] = NewValue[datetime](execution_time)
        return self

    def task_data(self, task_data: bytes | None) -> Self:
        self._updates["task_data"] = NewValue[bytes](task_data)
        return self

    def picked(self, picked: bool) -> Self:
        if picked is None:
            raise ValueError("Value should never be null")
        self._updates["picked"] = NewValue[bool](picked)
        return self

    def picked_by(self, picked_by: str | None) -> Self:
        self._updates["picked_by"] = NewValue[str](picked_by)
        return self

    def last_success(self, last_success: datetime | None) -> Self:
        self._updates["last_success"] = NewValue[datetime](last_success)
        return self

    def last_failure(self, last_failure: datetime | None) -> Self:
        self._updates["last_failure"] = NewValue[datetime](last_failure)
        return self

    def consecutive_failures(self, consecutive_failures: int | None) -> Self:
        # Zero, not null: the column is read back as a count and `None` would have to be
        # defended against everywhere it is used.
        self._updates["consecutive_failures"] = NewValue[int](consecutive_failures or 0)
        return self

    def last_heartbeat(self, last_heartbeat: datetime | None) -> Self:
        self._updates["last_heartbeat"] = NewValue[datetime](last_heartbeat)
        return self

    def update_single(
        self,
        connection: Connection,
        table: Table,
        bind: Any = lambda moment: moment,
    ) -> None:
        """Run it, and insist that exactly one row moved.

        Zero means somebody else got there first - the row was picked, rescheduled or removed
        between the read and this write - and that is a bug at this layer, not a race to
        tolerate: every caller here has already established the version it holds.
        """
        if not self._updates:
            return

        timestamps = {"execution_time", "last_success", "last_failure", "last_heartbeat"}
        values = {
            column: bind(held.value) if column in timestamps else held.value
            for column, held in self._updates.items()
        }
        values["version"] = self._version

        statement = (
            update(table)
            .where(
                table.c.task_name == self._task_instance.task_name,
                table.c.task_instance == self._task_instance.id,
                table.c.version == self._version_to_update,
            )
            .values(**values)
        )
        LOG.debug("ExecutionUpdate columns: %s", sorted(values))
        updated_rows = connection.execute(statement).rowcount
        if updated_rows != 1:
            raise ExecutionException(
                f"Expected one execution to be updated, but updated {updated_rows}. "
                "Indicates a bug.",
                task_name=self._task_instance.task_name,
                instance_id=self._task_instance.id,
                version=self._version_to_update,
            )

    def __repr__(self) -> str:
        return (
            f"ExecutionUpdate({self._task_instance}, version={self._version_to_update} -> "
            f"{self._version}, columns={sorted(self._updates)})"
        )
