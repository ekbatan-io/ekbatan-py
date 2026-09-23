"""Work that must run once across a cluster, not once per instance.

A port of `io.ekbatan.distributedjobs`. Five instances of a service all want to run the nightly
stipend; exactly one may. Coordination lives in the database: db-scheduler's `scheduled_tasks`
table, polled by every instance, where the one whose conditional update matches the row wins the
slot.

**A slot can still run twice.** If an instance stops writing heartbeats - because it died,
because it stalled, because it briefly lost the database - another treats the execution as dead
and runs it again, while the first may still be part way through. The claim protects the row,
not your function. **Write jobs so a repeat is harmless.**
"""

from __future__ import annotations

import atexit
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Sequence
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Self

from db_scheduler.builder import DEFAULT_POLLING_INTERVAL, SchedulerBuilder, create
from db_scheduler.clock import SettableClock
from db_scheduler.scheduled_execution import ScheduledExecutionsFilter
from db_scheduler.scheduler import Scheduler
from db_scheduler.task import ExecutionContext
from db_scheduler.task.helper import Tasks
from db_scheduler.task.schedule import Schedule
from sqlalchemy import Engine

from ..errors import ValidationError
from ._shutdown import ShutdownHook
from .config import JobsConfig, JobsConfigBuilder

if TYPE_CHECKING:
    from db_scheduler.testhelper import ManualScheduler

__all__ = [
    "EKBATAN_HEARTBEAT_INTERVAL",
    "DistributedJob",
    "ExecutionContext",
    "JobRegistry",
    "JobRegistryBuilder",
    "JobsConfig",
    "JobsConfigBuilder",
    "ManualJobRegistry",
    "Schedule",
    "job_registry",
]

LOG = logging.getLogger("ekbatan.distributedjobs")

#: The framework's heartbeat interval - deliberately ours, not db-scheduler's five minutes.
#:
#: Named rather than inlined because the line beside it reads db-scheduler's
#: `DEFAULT_POLLING_INTERVAL`, and a bare `timedelta(seconds=10)` next to it looked like the same
#: thing. It is not: both happen to be ten seconds, which is coincidence.
EKBATAN_HEARTBEAT_INTERVAL = timedelta(seconds=10)


class DistributedJob(ABC):
    """Something that runs on a schedule, once per slot across the cluster.

    You create the instance - by hand, or through a container - and hand it to
    :meth:`JobRegistryBuilder.with_job`. The builder registers instances; it never constructs
    them, so anything a job needs must be given to it before it is registered.
    """

    @abstractmethod
    def name(self) -> str:
        """A cluster-wide identifier for this job.

        Not a label. It is the primary key of the job's row in `scheduled_tasks`, and it is how
        every instance recognises a job as the same job - which is what stops five servers each
        running the daily report.

        It must therefore be non-blank and *stable*: the same value on every call, on every
        instance, and across restarts. A name built from a uuid, from today's date, or from a
        field the container has not filled in yet breaks that - each boot writes a fresh row,
        the previous one is orphaned, and the job never actually recurs. The registry rejects a
        blank name at build time; it cannot check stability, so that part is on you.
        """

    @abstractmethod
    def schedule(self) -> Schedule:
        """When the job should run next, computed by db-scheduler from the previous execution.

        Return a `DisabledSchedule` to register the job but never run it - a switch for an
        environment where the work is someone else's, without deleting the code.
        """

    @abstractmethod
    def execute(self, ctx: ExecutionContext) -> None:
        """The work; runs on a worker thread of the local :class:`JobRegistry`.

        Raising is a failed execution: `consecutive_failures` is incremented on the row and the
        next run is rescheduled per the :meth:`schedule`.

        **The same slot can run twice.** After the dead-execution window another instance
        reclaims the row and runs the job again, and the instance being replaced is neither told
        nor interrupted. Heartbeats are written by a housekeeping thread, so work that does not
        touch the scheduler's database keeps going without noticing - the usual cause is not a
        crash but a brief inability to reach it. Write this so a repeat is harmless.

        Where that is impractical, `ctx` carries this instance's own standing and the job can
        stop before the takeover::

            health = ctx.currently_executing.heartbeat_state
            for item in items:
                # 0.0 just after a successful heartbeat, 1.0 when another instance may claim it.
                if health.fraction_dead > 0.5:
                    return
                process(item)

        Returning early counts as a normal completion, so the row reschedules per its schedule.
        The check is cooperative and only as good as where it is placed: it fits a job that
        loops over work items, cannot help one inside a single long call, and narrows the
        overlap window rather than closing it.
        """


class JobRegistry:
    """A single db-scheduler :class:`Scheduler`, configured with a set of jobs.

    Each registered job becomes a recurring task, started on :meth:`start`.

    The engine should typically be a *dedicated* one, separate from the application's, since
    db-scheduler polls continuously - and because a task body holding the last connection
    would starve the heartbeat and get its own execution declared dead.
    """

    __slots__ = (
        "_hook",
        "_job_names",
        "_names_requiring_registration",
        "_scheduler",
        "_stop_on_exit",
    )

    def __init__(
        self,
        scheduler: Scheduler,
        job_names: Sequence[str],
        names_requiring_registration: Sequence[str],
        shutdown_hook: bool = False,
        stop_on_exit: bool = True,
    ) -> None:
        self._scheduler = scheduler
        self._stop_on_exit = stop_on_exit
        #: Built here and installed in `start()`, because a registry that was built and never
        #: started has no business holding the process's signals.
        self._hook = ShutdownHook(self.stop) if shutdown_hook else None
        self._job_names = list(job_names)
        #: The subset of `job_names` that must have a row in `scheduled_tasks` once started.
        #: Jobs whose schedule is disabled are excluded: db-scheduler deliberately writes no
        #: execution for those, so their absence is correct rather than a failure.
        self._names_requiring_registration = list(names_requiring_registration)

    @property
    def scheduler(self) -> Scheduler:
        """The underlying scheduler, for anything this facade does not expose."""
        return self._scheduler

    @property
    def job_names(self) -> tuple[str, ...]:
        return tuple(self._job_names)

    def start(self) -> None:
        """Begin polling and executing registered jobs."""
        LOG.info("Starting JobRegistry with %d job(s): %s", len(self._job_names), self._job_names)
        self._scheduler.start()
        self._verify_every_job_registered()
        # Last, and only once the jobs are known to be registered: a hook that drained a
        # scheduler which had failed to start would be answering for nothing.
        if self._stop_on_exit:
            # Unregistered first, so starting twice leaves one entry rather than two.
            atexit.unregister(self._stop_at_exit)
            atexit.register(self._stop_at_exit)
        if self._hook is not None:
            self._hook.install()

    def _verify_every_job_registered(self) -> None:
        """Confirm that starting actually registered the jobs, because nothing else will.

        Registering a job means writing a row to `scheduled_tasks`; the scheduler finds work only
        by reading that table. db-scheduler performs those writes inside `start()`, which catches
        whatever each one throws, logs it and continues - so `start()` returns normally whether
        every row was written or none were. A missing table, a database that is down, or an
        engine without write permission all end the same way: the application boots, health
        checks pass, the scheduler polls an empty table, and no job ever runs. Not late - never.
        """
        try:
            # `ScheduledExecutionsFilter.all()`, not the default listing: that one excludes
            # picked rows and so hides an execution that is currently running. A job that fired
            # in the moment between start() and this check would otherwise look unregistered and
            # take the application's startup down with it.
            missing = [
                name
                for name in self._names_requiring_registration
                if not self._scheduler.get_scheduled_executions_for_task(
                    name, filter=ScheduledExecutionsFilter.all()
                )
            ]
        except Exception as exc:
            raise ValidationError(
                "JobRegistry.start() could not verify that its jobs were registered. The "
                f"scheduler is running but may have no work: {self._names_requiring_registration}"
            ) from exc
        if missing:
            raise ValidationError(
                f"JobRegistry.start() completed but {len(missing)} of "
                f"{len(self._names_requiring_registration)} job(s) were not registered: "
                f"{missing}. db-scheduler logs the cause at ERROR during startup - usually a "
                f"missing scheduled_tasks table, or a database the jobs engine cannot write to. "
                f"Those jobs would never run."
            )

    def stop(self) -> None:
        """Stop the scheduler, letting in-flight executions finish first.

        Bounded by `shutdown_max_wait`. An execution still running when that expires is
        abandoned, leaving its row picked with a frozen heartbeat - so it is recovered later by
        the same dead-execution path as a crash.
        """
        if self._stop_on_exit:
            # A stopped registry has nothing left to drain, and the entry would otherwise keep
            # it - and its engine - alive until the interpreter exits.
            atexit.unregister(self._stop_at_exit)
        self._drain()

    def _drain(self) -> None:
        LOG.info("Stopping JobRegistry")
        if self._hook is not None:
            # Before draining, and unconditionally: a second signal while this one is winding
            # down has to reach whatever was there before us, which for an impatient operator
            # pressing Ctrl-C twice is the default handler and an immediate exit.
            self._hook.remove()
        self._scheduler.stop()
        LOG.info("JobRegistry stopped")

    def _stop_at_exit(self) -> None:
        """What `atexit` runs: :meth:`stop`, without touching the `atexit` list mid-run.

        Never raises. An exception here is printed by the interpreter and the exit carries on,
        but a drain that failed is worth a line in the application's own log, and there it goes.
        """
        try:
            self._drain()
        except BaseException:
            LOG.exception("draining the JobRegistry at exit failed")

    def __enter__(self) -> JobRegistry:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def __repr__(self) -> str:
        return f"JobRegistry({len(self._job_names)} jobs: {self._job_names})"


class ManualJobRegistry(JobRegistry):
    """A registry whose scheduler runs on the calling thread, with a clock a test can set.

    Only the type differs from :class:`JobRegistry`; it exists so that
    ``registry.scheduler.run_any_due_executions()`` type-checks. Java has no counterpart -
    its tests reach for db-scheduler's `TestHelper` directly.
    """

    __slots__ = ()

    @property
    def scheduler(self) -> ManualScheduler:
        from db_scheduler.testhelper import ManualScheduler as _ManualScheduler

        scheduler = super().scheduler
        assert isinstance(scheduler, _ManualScheduler)
        return scheduler


def job_registry() -> JobRegistryBuilder:
    """A fresh builder for :class:`JobRegistry`."""
    return JobRegistryBuilder()


class JobRegistryBuilder:
    """Fluent builder for :class:`JobRegistry`. Obtain via :func:`job_registry`."""

    __slots__ = (
        "_engine",
        "_heartbeat_interval",
        "_jobs",
        "_poll_interval",
        "_scheduler_customizer",
        "_shutdown_hook",
        "_shutdown_max_wait",
        "_stop_on_exit",
        "_workers",
    )

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._jobs: list[DistributedJob] = []
        self._poll_interval: timedelta = DEFAULT_POLLING_INTERVAL
        self._heartbeat_interval: timedelta = EKBATAN_HEARTBEAT_INTERVAL
        self._shutdown_max_wait: timedelta | None = None
        self._scheduler_customizer: Callable[[SchedulerBuilder], None] | None = None
        self._workers: int | None = None
        self._shutdown_hook = False
        self._stop_on_exit = True

    def engine(self, engine: Engine) -> Self:
        """The database db-scheduler will poll. Required.

        Where Java takes a `ConnectionProvider` wrapping a DataSource, this takes the
        SQLAlchemy equivalent. Should typically be a *dedicated* engine, isolated from
        application traffic. Caller retains ownership.
        """
        self._engine = engine
        return self

    def with_job(self, job: DistributedJob) -> Self:
        """Add one job."""
        if job is None:
            # Rejected here rather than at build(), where it surfaced as a bare AttributeError
            # from job.name() with no indication of which call put the None in the list.
            raise ValidationError("job cannot be None")
        self._jobs.append(job)
        return self

    def with_jobs(self, jobs: Iterable[DistributedJob]) -> Self:
        """Add several. Names must be unique within the resulting registry."""
        if jobs is None:
            raise ValidationError("jobs cannot be None")
        for job in jobs:
            # The collection was checked but not its contents, so a None element still reached
            # build() and failed there unattributed - the same defect as with_job(None), one
            # level down. A container producing a null bean is the realistic way in.
            if job is None:
                raise ValidationError("jobs cannot contain a None job")
            self._jobs.append(job)
        return self

    def poll_interval(self, duration: timedelta) -> Self:
        """How often the scheduler polls for due jobs."""
        self._poll_interval = duration
        return self

    def heartbeat_interval(self, duration: timedelta) -> Self:
        """How often a running execution stamps `last_heartbeat`, which is how other instances
        tell it apart from a crashed one.

        Defaults to :data:`EKBATAN_HEARTBEAT_INTERVAL` (10s) - *not* db-scheduler's five
        minutes, which is the one knob here that does not fall through to the library. It is
        also the unit the dead-execution threshold is built from: db-scheduler declares an
        execution dead after ``heartbeat_interval * missed_heartbeats_limit``, and that limit
        defaults to 6. So the effective window is **60 seconds** where db-scheduler's own
        defaults would give 30 minutes.

        The short window is deliberate for this framework. The event fanout and handling jobs
        are themselves :class:`DistributedJob`s, so the scheduler carries the in-process event
        pipeline - and a recurring task is a single row, so a crashed instance freezes that
        entire schedule until the row is revived. Half an hour of stalled fanout is a worse
        outcome than an occasional early revival. Lengthen it if your jobs run long and a
        duplicate is costlier than a delay; the tolerated-miss count is reachable through
        :meth:`customize_scheduler` as ``missed_heartbeats_limit`` (minimum 4).

        Note the heartbeat is written by a housekeeping thread, not by the thread running your
        job, so a long-running ``execute()`` is not itself at risk of being declared dead.
        """
        self._heartbeat_interval = duration
        return self

    def shutdown_max_wait(self, duration: timedelta) -> Self:
        """How long :meth:`JobRegistry.stop` waits for in-flight executions before giving up."""
        self._shutdown_max_wait = duration
        return self

    def workers(self, count: int) -> Self:
        """The **ceiling** on how many threads run job bodies. Defaults to one per registered job.

        This knob exists because CPython has no virtual threads. Java hands db-scheduler an
        ``Executors.newVirtualThreadPerTaskExecutor()``, so its worker count is effectively
        unbounded and ``threads(jobs.size())`` only sizes the polling batch. Here the workers are
        real OS threads, so a ceiling has to be chosen rather than assumed.

        A ceiling, not a reservation: threads are started as work arrives and only when no
        worker is already waiting, so a registry whose jobs are rarely due costs nothing for
        the ones it never runs. Twenty registered jobs with nothing due hold **no** worker
        threads. They are not retired once started, though - CPython has no counterpart to
        `newCachedThreadPool()`'s idle timeout, and neither does this.

        One per job is the right default: a recurring task is a single row, so a job has at most
        one execution in flight at a time and N jobs need N workers. Raise it if you schedule the
        same job to overlap itself, or use ``enable_immediate_execution`` through
        :meth:`customize_scheduler`; below the job count, a slow job delays the others rather
        than only itself.

        **On connections.** A running job holds none by itself - measured: db-scheduler takes a
        connection to claim the row, releases it, runs the body with none open, then takes
        another to write the result. What holds one is the body's *own* database work, for as
        long as that work takes. So a job that waits on an HTTP call needs no connection while
        it waits, and workers may outnumber connections safely; a registry whose jobs all run
        actions at once wants a connection each at that moment, and db-scheduler warns when the
        two are mismatched - its own wording says "if task bodies hold connections", and that
        condition is the whole of it.
        """
        if count < 1:
            raise ValidationError(f"workers must be at least 1, got {count}")
        self._workers = count
        return self

    def stop_on_exit(self, enabled: bool = True) -> Self:
        """Drain when the interpreter exits. **On by default.**

        Java's ``registerShutdownHook`` defaults to on because a JVM shutdown hook only adds to
        a list. So does ``atexit``, which is what this uses: a registry that was started and
        never stopped is stopped as the process exits, alongside whatever else registered, and
        displacing nothing. OpenTelemetry's ``TracerProvider(shutdown_on_exit=True)`` and
        Sentry's flush work the same way, and for the same reason.

        It covers a normal exit, an unhandled exception and **Ctrl-C**. It does **not** cover
        SIGTERM on its own, measured: with SIGTERM's default disposition the process is killed
        and no `atexit` hook runs - and under uvicorn neither, because uvicorn re-raises the
        signal after its own shutdown. That is the JVM's other half, turning SIGTERM into an
        orderly exit, and CPython does not do it. Under a server the lifespan's shutdown covers
        SIGTERM; in a bare process :meth:`register_shutdown_hook` does.

        :meth:`JobRegistry.stop` takes the entry off again, so a registry that was stopped is
        not drained twice and is not kept alive by the list.
        """
        self._stop_on_exit = enabled
        return self

    def register_shutdown_hook(self, enabled: bool = True) -> Self:
        """Drain on SIGTERM and SIGINT, instead of being cut off mid-execution. **Off by default.**

        The additive half of Java's hook is :meth:`stop_on_exit`, and it is on. This is the other
        half - catching SIGTERM so the drain runs on a container stop - and it is off, because
        CPython has **one** handler slot per signal and installing one takes the application's
        SIGTERM away from it. Every library read while deciding this leaves signals to whatever
        owns the process - OpenTelemetry, Sentry and APScheduler never touch them; gunicorn,
        uvicorn and the Celery, RQ, Dramatiq and Huey workers take them because they *are* the
        process. That is a thing to be asked for, not assumed.

        Asked for, it behaves: the handler that was there is kept and called afterwards, and
        where there was none the default is restored and the signal re-raised, so the process
        still dies of it. A hook that drained cleanly and then left the process alive would be
        worse than the gap it closes. :meth:`stop` removes it again, so a second Ctrl-C from an
        impatient operator reaches the default handler and exits at once.

        Without it, a container stop ends the process with executions still `picked` and their
        heartbeats frozen; another instance can only take them after the dead-execution window,
        which is 60 seconds on the framework's defaults. With it, they finish - bounded by
        ``shutdown_max_wait``.

        **It needs the main thread**, because CPython only allows a signal handler to be
        installed there, and it raises rather than silently doing nothing if it has not got one.
        A service that already has somewhere to call :meth:`JobRegistry.stop` - a FastAPI
        lifespan, a Django shutdown - wants none of this and should keep it off.
        """
        self._shutdown_hook = enabled
        return self

    def customize_scheduler(self, customizer: Callable[[SchedulerBuilder], None]) -> Self:
        """Escape hatch for db-scheduler settings this builder does not expose - for example
        ``missed_heartbeats_limit``, ``delete_unresolved_after``, or the polling strategy.

        Applied *last* in :meth:`build`, so anything set here wins over the framework's
        defaults. Use sparingly: overriding ``executor_service`` or ``threads`` defeats the
        framework's threading model.
        """
        self._scheduler_customizer = customizer
        return self

    def with_config(self, config: JobsConfig) -> Self:
        """Apply a :class:`JobsConfig`. An unset knob leaves this builder's default alone."""
        if config.polling_interval is not None:
            self.poll_interval(config.polling_interval)
        if config.heartbeat_interval is not None:
            self.heartbeat_interval(config.heartbeat_interval)
        if config.shutdown_max_wait is not None:
            self.shutdown_max_wait(config.shutdown_max_wait)
        return self

    def build_manual(self, clock: SettableClock | None = None) -> ManualJobRegistry:
        """A registry with the threads and the clock taken out. **For tests.**

        Java has no counterpart - its tests reach for db-scheduler's own `TestHelper`, and this
        is that, wrapped so a test gets the same :class:`JobRegistry` it would in production.
        Nothing runs until asked::

            registry = job_registry().engine(engine).with_job(job).build_manual(clock)
            registry.start()
            registry.scheduler.run_any_due_executions()   # runs on this thread, and has finished
            registry.scheduler.tick(timedelta(days=1))    # a day of waiting, in microseconds
        """
        from db_scheduler.clock import SettableClock as _SettableClock
        from db_scheduler.testhelper import create_manual_scheduler

        names, tasks, requiring = self._prepare()
        assert self._engine is not None  # _prepare refuses otherwise
        builder = (
            create_manual_scheduler(self._engine)
            .clock(clock or _SettableClock())
            .add_start_tasks(tasks)
            .threads(self._workers or len(self._jobs))
            .heartbeat_interval(self._heartbeat_interval)
            .polling_interval(self._poll_interval)
        )
        if self._scheduler_customizer is not None:
            self._scheduler_customizer(builder)
        # Never registered with `atexit`: a test that starts a manual registry and forgets it
        # should not have it drained, against a database the suite has already torn down, as
        # the interpreter exits.
        return ManualJobRegistry(builder.build(), names, requiring, stop_on_exit=False)

    def _prepare(self) -> tuple[list[str], list[Any], list[str]]:
        """Validate the jobs and turn them into recurring tasks. Shared by both build paths."""
        if self._engine is None:
            raise ValidationError("engine is required")
        if not self._jobs:
            raise ValidationError("at least one DistributedJob must be registered")

        # Each job is asked for its name exactly once, and that captured value is used for
        # everything afterwards: the uniqueness check, the task registration, the check in
        # start(), and every log line. `name()` is an ordinary method a user writes, and nothing
        # obliges it to answer the same way twice - a name built from a date, a uuid, or a field
        # the container has not injected yet will not. Asking repeatedly meant the row could be
        # written under one name while the uniqueness check examined another and the logs
        # reported a third, leaving nothing that mentions the job by the name it has in the
        # database.
        names: list[str] = []
        for job in self._jobs:
            name = job.name()
            if not name or not name.strip():
                # The name is what would normally identify the offending job, and here it is the
                # broken thing - so the class is what the message can point at.
                raise ValidationError(
                    f"DistributedJob {type(job).__name__} returned a blank name(); the name is "
                    f"the row's primary key in scheduled_tasks"
                )
            names.append(name)
        if len(names) != len(set(names)):
            raise ValidationError(
                f"DistributedJob names must be unique within a JobRegistry; got: {names}"
            )

        tasks = []
        # A disabled schedule is deliberately never written to scheduled_tasks, so its absence
        # after start() is correct - only the rest are expected to appear.
        names_requiring_registration: list[str] = []
        for job, name in zip(self._jobs, names, strict=True):
            # Asked once and checked here, because nothing downstream will. db-scheduler stores
            # the schedule without looking at it, then dereferences it during start() - and that
            # catches whatever it throws, logs one line and continues. The scheduler comes up,
            # every other job registers, and this one is simply never written: nothing becomes
            # due for it, so it never runs at all, while start() reports success and names it.
            schedule = job.schedule()
            if schedule is None:
                raise ValidationError(f"DistributedJob '{name}' returned a null schedule()")
            if not schedule.is_disabled:
                names_requiring_registration.append(name)
            tasks.append(Tasks.recurring(name, schedule).execute(_run_job(job, name)))

        # Checked here as well as in JobsConfig, because a hand-wired registry never passes
        # through the config.
        if self._poll_interval is None or self._poll_interval.total_seconds() <= 0:
            raise ValidationError(f"poll_interval must be positive, but was {self._poll_interval}")
        if self._heartbeat_interval is None or self._heartbeat_interval.total_seconds() <= 0:
            raise ValidationError(
                f"heartbeat_interval must be positive, but was {self._heartbeat_interval}. "
                f"db-scheduler also derives its dead-execution window from it."
            )
        if self._shutdown_max_wait is not None and self._shutdown_max_wait.total_seconds() < 0:
            raise ValidationError(
                f"shutdown_max_wait cannot be negative, but was {self._shutdown_max_wait}"
            )
        return names, tasks, names_requiring_registration

    def build(self) -> JobRegistry:
        """A configured registry; raises if required fields are unset or job names collide."""
        names, tasks, names_requiring_registration = self._prepare()
        assert self._engine is not None  # _prepare refuses otherwise

        # No `jdbcCustomizationFor(url)` counterpart. Java has to pin the SQL dialect by hand,
        # because db-scheduler detects it by opening a connection and reading DatabaseMetaData -
        # and Ekbatan's pools let an application start before its database is reachable, so that
        # detection can lose the race and leave a scheduler emitting generic SQL that MySQL and
        # MariaDB cannot parse. SQLAlchemy resolves the dialect from the URL when the Engine is
        # created, with no I/O, so there is no race to lose and nothing to pin.
        builder = (
            create(self._engine)
            .add_start_tasks(tasks)
            .threads(self._workers or len(self._jobs))
            .heartbeat_interval(self._heartbeat_interval)
            .polling_interval(self._poll_interval)
        )
        if self._shutdown_max_wait is not None:
            builder.shutdown_max_wait(self._shutdown_max_wait)
        if self._scheduler_customizer is not None:
            self._scheduler_customizer(builder)

        return JobRegistry(
            builder.build(),
            names,
            names_requiring_registration,
            self._shutdown_hook,
            self._stop_on_exit,
        )


def _run_job(job: DistributedJob, name: str) -> Callable[[Any, ExecutionContext], None]:
    """Wrap a job so its start, finish and failure are logged under Ekbatan's own logger."""

    def run(instance: Any, ctx: ExecutionContext) -> None:
        LOG.info("Job '%s' execution started", name)
        try:
            job.execute(ctx)
        except BaseException as exc:
            # BaseException, not Exception. db-scheduler does log what escapes, but under its
            # own logger: anyone filtering on `ekbatan.*` saw a job log "execution started" and
            # then nothing at all, with no terminal line either way. Re-raised unchanged, so
            # db-scheduler still treats it as a failure; this only makes our own log tell the
            # whole story.
            LOG.error(
                "Job '%s' execution failed: %s: %s",
                name,
                type(exc).__name__,
                exc,
                exc_info=exc,
            )
            raise
        LOG.info("Job '%s' execution finished", name)

    return run
