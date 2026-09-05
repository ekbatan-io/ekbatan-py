"""Work that must run once across a cluster, not once per instance.

Five instances of a service all want to run the nightly stipend; exactly one may. Coordination
therefore lives in the database rather than in each process: every instance polls the same
table, and the one whose conditional update matches the row wins the slot.

**A slot can still run twice.** If an instance stops writing heartbeats - because it died,
because it stalled, because it briefly lost the database - another treats the execution as dead
and runs it again, while the first may still be part way through. The claim protects the row,
not your function: the database stops the stale instance from recording a result, and cannot
undo side effects it has already caused. **Write jobs so a repeat is harmless.** Charge a card,
send an email, or post to an external API only behind a check that makes a second run a no-op.
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import Table, and_, select, update

from .clock import Clock, SystemClock, to_storage
from .errors import ConnectivityError, ValidationError
from .persistence import DatabaseRegistry, insert_ignoring_duplicates

__all__ = [
    "Daily",
    "DistributedJob",
    "Every",
    "FixedDelay",
    "JobContext",
    "JobRegistry",
    "Schedule",
]

LOG = logging.getLogger("ekbatan.jobs")

DEFAULT_HEARTBEAT = timedelta(seconds=5)
DEFAULT_MISSED_HEARTBEATS = 12  # a minute of silence before another instance takes over


# ------------------------------------------------------------------------ schedules


class Schedule(ABC):
    """When a job runs next, given when it last ran."""

    @abstractmethod
    def next_after(self, previous: datetime) -> datetime: ...


@dataclass(frozen=True, slots=True)
class Every(Schedule):
    """Every ``interval``, measured from the previous scheduled time.

    Slots do not drift: a run that takes two minutes does not push the next one two minutes
    later. A run that overshoots several slots skips them rather than trying to catch up,
    because catching up means running the nightly report four times at breakfast.
    """

    interval: timedelta

    def __post_init__(self) -> None:
        if self.interval <= timedelta(0):
            raise ValidationError(f"interval must be positive, got {self.interval}")

    def next_after(self, previous: datetime) -> datetime:
        return previous + self.interval


@dataclass(frozen=True, slots=True)
class FixedDelay(Schedule):
    """``delay`` after the previous run *finished*.

    Right for work whose duration varies and which should not overlap itself.
    """

    delay: timedelta

    def __post_init__(self) -> None:
        if self.delay <= timedelta(0):
            raise ValidationError(f"delay must be positive, got {self.delay}")

    def next_after(self, previous: datetime) -> datetime:
        return previous + self.delay


@dataclass(frozen=True, slots=True)
class Daily(Schedule):
    """At a wall-clock time each day, in UTC."""

    at: time

    def next_after(self, previous: datetime) -> datetime:
        candidate = previous.replace(
            hour=self.at.hour, minute=self.at.minute, second=self.at.second, microsecond=0
        )
        return candidate if candidate > previous else candidate + timedelta(days=1)


# ----------------------------------------------------------------------------- jobs


@dataclass(frozen=True, kw_only=True, slots=True)
class JobContext:
    """What a running job is told about its own standing."""

    name: str
    instance: str
    scheduled_for: datetime
    consecutive_failures: int

    #: 0.0 just after a successful heartbeat, 1.0 when another instance may claim the row.
    #: A job whose work cannot be made idempotent can check this between items and stop before
    #: the takeover rather than racing it.
    fraction_dead: float = 0.0


class DistributedJob(ABC):
    """Something that runs on a schedule, once per slot across the cluster."""

    @property
    @abstractmethod
    def name(self) -> str:
        """A cluster-wide identifier, and the primary key of this job's row.

        Not a label: it is how every instance recognises a job as the same job, which is what
        stops five servers each running the daily report. It must be stable across restarts. A
        name derived from a uuid, from today's date, or from a field the container has not
        filled in yet writes a fresh row every boot, orphans the previous one, and the job
        never actually recurs. Blankness is caught at registration; instability cannot be, so
        that part is on you.
        """

    @property
    @abstractmethod
    def schedule(self) -> Schedule: ...

    @abstractmethod
    def run(self, context: JobContext) -> None:
        """The work. Raising is a failed execution: the failure count goes up and the job is
        rescheduled."""

    @property
    def enabled(self) -> bool:
        """Set False to register the job but never run it - a switch for an environment where
        the work is someone else's, without deleting the code."""
        return True


class JobRegistry:
    """Runs a set of jobs, coordinating with every other instance through one table."""

    __slots__ = (
        "_clock",
        "_heartbeat",
        "_jobs",
        "_missed_limit",
        "_registry",
        "_table",
        "instance",
    )

    def __init__(
        self,
        registry: DatabaseRegistry,
        jobs: Iterable[DistributedJob],
        *,
        table: Table,
        clock: Clock | None = None,
        instance: str | None = None,
        heartbeat: timedelta = DEFAULT_HEARTBEAT,
        missed_heartbeats: int = DEFAULT_MISSED_HEARTBEATS,
    ) -> None:
        self._registry = registry
        self._table = table
        self._clock = clock or SystemClock()
        self._heartbeat = heartbeat
        self._missed_limit = missed_heartbeats
        self.instance = instance or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

        self._jobs: dict[str, DistributedJob] = {}
        for job in jobs:
            name = job.name
            if not name or not name.strip():
                raise ValidationError(
                    f"{type(job).__name__} has a blank name. The name is the primary key of "
                    f"this job's row and how every instance recognises it as the same job."
                )
            if name in self._jobs:
                raise ValidationError(
                    f"two jobs are both called {name!r}; they would share one row and each "
                    f"steal the other's slots"
                )
            self._jobs[name] = job

    # --------------------------------------------------------------------- startup

    def start(self) -> None:
        """Register every job's row, creating it if this is the first instance up.

        Refuses to continue if the table cannot be reached. A job runtime that starts, cannot
        claim its tables, and carries on is the worst outcome available: nothing runs, and
        nothing says so until someone notices the reports stopped.
        """
        now = to_storage(self._clock.now())
        try:
            with self._manager.transaction() as tx:
                # Insert-ignoring-duplicates rather than read-then-insert. Every instance in
                # the cluster runs this at boot, usually within the same second of a rolling
                # deploy: both would read an empty table, both would insert, and one would take
                # a unique violation on a perfectly ordinary startup. The row already being
                # there is the normal case, not an error.
                insert_ignoring_duplicates(
                    tx.connection,
                    self._table,
                    [
                        {
                            "task_name": name,
                            "task_instance": "recurring",
                            "task_data": None,
                            "execution_time": now,
                            "picked": False,
                            "picked_by": None,
                            "last_success": None,
                            "last_failure": None,
                            "consecutive_failures": 0,
                            "last_heartbeat": None,
                            "version": 1,
                        }
                        for name in self._jobs
                    ],
                )
        except Exception as exc:
            raise ConnectivityError(
                f"the scheduler could not register its jobs: {exc}. Nothing would have run, "
                f"and nothing would have said so."
            ) from exc
        LOG.info("registered %d job(s) as instance %s", len(self._jobs), self.instance)

    # --------------------------------------------------------------------- running

    def run_due_once(self) -> int:
        """Claim and run every slot that is due. Returns how many this instance ran."""
        ran = 0
        for name in list(self._jobs):
            if self._claim(name):
                self._run(name)
                ran += 1
        return ran

    def _claim(self, name: str) -> bool:
        """Take the slot, if it is due and nobody else got there first.

        The whole race is one conditional update: the version we read has to still be the
        version in the row. Everyone else's update matches nothing and they move on.
        """
        now = to_storage(self._clock.now())
        dead_before = now - self._heartbeat * self._missed_limit
        table = self._table

        with self._manager.transaction() as tx:
            row = tx.execute(
                select(table).where(table.c.task_name == name, table.c.task_instance == "recurring")
            ).first()
            if row is None or row.execution_time > now:
                return False
            if row.picked:
                if row.last_heartbeat is not None and row.last_heartbeat > dead_before:
                    return False  # someone is on it and still breathing
                LOG.warning(
                    "reclaiming %r from %s: no heartbeat since %s. It may still be running - "
                    "this is why a job has to be safe to run twice.",
                    name,
                    row.picked_by,
                    row.last_heartbeat,
                )

            claimed = tx.execute(
                update(table)
                .where(
                    and_(
                        table.c.task_name == name,
                        table.c.task_instance == "recurring",
                        table.c.version == row.version,
                    )
                )
                .values(
                    picked=True,
                    picked_by=self.instance,
                    last_heartbeat=now,
                    version=row.version + 1,
                )
            ).rowcount
            return bool(claimed)

    def _run(self, name: str) -> None:
        job = self._jobs[name]
        table = self._table

        with self._manager.connection(readonly=True) as conn:
            row = conn.execute(
                select(table).where(table.c.task_name == name, table.c.task_instance == "recurring")
            ).one()

        scheduled_for = row.execution_time
        failures = row.consecutive_failures or 0
        failed: BaseException | None = None

        if job.enabled:
            try:
                job.run(
                    JobContext(
                        name=name,
                        instance=self.instance,
                        scheduled_for=scheduled_for,
                        consecutive_failures=failures,
                    )
                )
            except BaseException as exc:
                failed = exc
                LOG.exception("job %r failed", name)

        now = to_storage(self._clock.now())
        # Next slot from the one that was scheduled, not from now, so a slow run does not push
        # every later slot later. A run that overshot several slots skips them rather than
        # firing four times in a row to catch up.
        following = job.schedule.next_after(scheduled_for)
        while following <= now:
            following = job.schedule.next_after(following)

        with self._manager.transaction() as tx:
            tx.execute(
                update(table)
                .where(table.c.task_name == name, table.c.task_instance == "recurring")
                .values(
                    picked=False,
                    picked_by=None,
                    execution_time=following,
                    last_success=now if failed is None else row.last_success,
                    last_failure=now if failed is not None else row.last_failure,
                    consecutive_failures=0 if failed is None else failures + 1,
                    last_heartbeat=None,
                    version=row.version + 1,
                )
            )

    def heartbeat(self, name: str) -> None:
        """Tell the cluster this instance is still working on the slot.

        Call it from long work. Silence is how another instance decides the execution is dead,
        and it cannot tell a busy process from a stopped one.
        """
        now = to_storage(self._clock.now())
        with self._manager.transaction() as tx:
            tx.execute(
                update(self._table)
                .where(
                    self._table.c.task_name == name,
                    self._table.c.picked_by == self.instance,
                )
                .values(last_heartbeat=now)
            )

    @property
    def _manager(self) -> Any:
        # One shard owns the schedule. Jobs coordinate globally; they are not per-shard work,
        # and running the nightly report once per database is a different thing entirely.
        return self._registry.default
