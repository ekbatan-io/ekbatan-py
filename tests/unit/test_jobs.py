"""Building a job registry, and the tuning it refuses.

Java's `JobRegistryBuilderTest`, `JobRegistryDialectPinningTest` and
`JobsConfigDurationValidationTest`. Nothing here needs a server: an `Engine` resolves its dialect
from the url when it is created and contacts nothing, which is the same property Java gets from
Hikari's `initializationFailTimeout = -1` - so every host below deliberately does not resolve.

The refusals that also have an integration-level case live in `tests/integration/test_jobs.py`,
against a real database. What is here is what needs no database at all: the argument checks, the
once-only name read, the dialect wiring, and the duration validation.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from db_scheduler.jdbc.customization import (
    DefaultJdbcCustomization,
    MariaDBJdbcCustomization,
    MySQL8JdbcCustomization,
    PostgreSqlJdbcCustomization,
)
from db_scheduler.task.schedule import FixedDelay
from sqlalchemy import create_engine, select

from ekbatan.distributedjobs import DistributedJob, JobsConfig, job_registry
from ekbatan.errors import ValidationError

PG = "postgresql+psycopg://u:p@nowhere-9e3f:5432/db"
MYSQL = "mysql+pymysql://u:p@nowhere-9e3f:3306/db"
MARIADB = "mariadb+pymysql://u:p@nowhere-9e3f:3306/db"


class NoOpJob(DistributedJob):
    """Exists only so `build()` has something to register."""

    def __init__(self, name: str = "no-op") -> None:
        self._name = name

    def name(self) -> str:
        return self._name

    def schedule(self) -> FixedDelay:
        return FixedDelay(timedelta(seconds=60))

    def execute(self, ctx: object) -> None: ...


def an_engine(url: str = PG):
    return create_engine(url)


# -- the builder's own arguments ---------------------------------------------------------------


class TestJobRegistryBuilder:
    def test_with_jobs_rejects_a_none_collection(self) -> None:
        """The collection itself, not one of its elements - which is the case beside it in
        `tests/integration/test_jobs.py`."""
        with pytest.raises(ValidationError, match="jobs cannot be None"):
            job_registry().with_jobs(None)  # type: ignore[arg-type]

    def test_build_succeeds_when_the_schedule_is_present(self) -> None:
        """The null-schedule check must not reject a legitimate one."""
        registry = job_registry().engine(an_engine()).with_job(NoOpJob("daily-report")).build()

        assert registry is not None
        assert registry.job_names == ("daily-report",)

    def test_the_name_is_read_only_once_so_an_unstable_name_cannot_disagree_with_itself(
        self,
    ) -> None:
        """`name()` is an ordinary method a user writes, and nothing obliges it to answer the
        same way twice - a name built from a date, a uuid, or a field the container has not
        injected yet will not.

        It used to be re-read at each of seven sites, so a row could be written under one value
        while the uniqueness check examined another and the logs reported a third, leaving
        nothing that mentions the job by the name it actually has in the database.
        """
        calls = 0

        class Unstable(DistributedJob):
            def name(self) -> str:
                nonlocal calls
                calls += 1
                return f"unstable-{calls}"

            def schedule(self) -> FixedDelay:
                return FixedDelay(timedelta(milliseconds=100))

            def execute(self, ctx: object) -> None: ...

        registry = job_registry().engine(an_engine()).with_job(Unstable()).build()

        assert calls == 1, "name() must be consulted exactly once per job during build()"
        assert registry.job_names == ("unstable-1",)


# -- the dialect, decided without a connection -------------------------------------------------


class TestJobRegistryDialectPinning:
    """Java has to pin the dialect by hand and read it back reflectively.

    There, db-scheduler detects it by opening a connection and reading `DatabaseMetaData`, and
    Ekbatan's pools let an application start before its database is reachable - so detection can
    lose that race and leave a scheduler emitting generic SQL for its whole lifetime. Here the
    dialect comes off the `Engine`, which resolved it from the url with no I/O, so there is no
    race to lose. These tests still exist because "cannot lose the race" is a claim, and the
    wiring that makes it true is what could break.
    """

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (PG, PostgreSqlJdbcCustomization),
            (MARIADB, MariaDBJdbcCustomization),
            (MYSQL, MySQL8JdbcCustomization),
        ],
    )
    def test_the_customization_is_set_before_the_scheduler_is_created(
        self, url: str, expected: type
    ) -> None:
        registry = job_registry().engine(an_engine(url)).with_job(NoOpJob()).build()

        customization = registry.scheduler.scheduler_task_repository.customization
        assert isinstance(customization._delegate, expected)  # type: ignore[attr-defined]

    @pytest.mark.parametrize("url", [MYSQL, MARIADB])
    def test_the_poll_query_uses_limit_on_mysql_and_mariadb(self, url: str) -> None:
        """Why any of this matters. Every dialect override emits `LIMIT n`; the generic ANSI
        form is `OFFSET ... FETCH FIRST`, which MySQL and MariaDB cannot parse - so a scheduler
        that fell back would issue an invalid poll query forever while the application reported
        healthy.

        The port reaches the same guarantee one layer lower: SQLAlchemy compiles the limit for
        the dialect the engine already knows, so this asserts the emitted SQL rather than a
        string on the customization.
        """
        from db_scheduler.jdbc.schema import scheduled_tasks_table
        from sqlalchemy import MetaData

        table = scheduled_tasks_table(MetaData())
        compiled = str(select(table).limit(10).compile(dialect=an_engine(url).dialect))

        assert "LIMIT" in compiled
        assert "FETCH FIRST" not in compiled

    def test_an_unknown_database_falls_back_rather_than_failing(self) -> None:
        """Fetch-then-pick works everywhere, so a database this port has not been tested
        against still coordinates correctly - it just does not get the round-trip saving."""
        from db_scheduler.jdbc.customization import autodetect

        assert isinstance(autodetect("nosuchdb")._delegate, DefaultJdbcCustomization)  # type: ignore[attr-defined]


# -- durations that would turn the scheduler loops into spins ----------------------------------


class TestJobsConfigDurationValidation:
    """Nothing validated these before, so a mistyped zero travelled from configuration into
    db-scheduler untouched. It does not fail there either: the poll loop skips its wait entirely
    when the duration is not positive, and queries the database as fast as one core can go -
    silently, with the application reporting healthy."""

    @pytest.mark.parametrize("value", [timedelta(0), timedelta(seconds=-1), timedelta(minutes=-10)])
    def test_a_non_positive_polling_interval_is_rejected(self, value: timedelta) -> None:
        with pytest.raises(ValidationError) as raised:
            JobsConfig(polling_interval=value)

        assert "ekbatan.jobs.polling-interval" in str(raised.value)
        assert "tight loop" in str(raised.value)

    @pytest.mark.parametrize("value", [timedelta(0), timedelta(seconds=-1)])
    def test_a_non_positive_heartbeat_interval_is_rejected(self, value: timedelta) -> None:
        """The heartbeat is not only a heartbeat: the dead-execution window is built from it, so
        a tiny value also makes live executions look dead."""
        with pytest.raises(ValidationError, match=re.escape("ekbatan.jobs.heartbeat-interval")):
            JobsConfig(heartbeat_interval=value)

    def test_a_zero_shutdown_wait_is_allowed_but_a_negative_one_is_not(self) -> None:
        """Zero here is a real choice - "do not wait for in-flight jobs" - so only negatives are
        wrong."""
        assert JobsConfig(shutdown_max_wait=timedelta(0)).shutdown_max_wait == timedelta(0)

        with pytest.raises(ValidationError, match=re.escape("ekbatan.jobs.shutdown-max-wait")):
            JobsConfig(shutdown_max_wait=timedelta(seconds=-1))

    def test_the_error_names_the_property_and_the_offending_value(self) -> None:
        with pytest.raises(ValidationError) as raised:
            JobsConfig(polling_interval=timedelta(0))

        assert "ekbatan.jobs.polling-interval" in str(raised.value)
        assert "0:00:00" in str(raised.value)

    def test_ordinary_values_are_accepted(self) -> None:
        config = JobsConfig(
            polling_interval=timedelta(seconds=10),
            heartbeat_interval=timedelta(minutes=5),
            shutdown_max_wait=timedelta(seconds=30),
        )

        assert config.polling_interval == timedelta(seconds=10)
        assert config.heartbeat_interval == timedelta(minutes=5)
        assert config.shutdown_max_wait == timedelta(seconds=30)

    def test_unset_knobs_remain_empty(self) -> None:
        """Validation must not turn "unset" into an error."""
        config = JobsConfig.defaults()

        assert config.polling_interval is None
        assert config.heartbeat_interval is None
        assert config.shutdown_max_wait is None

    def test_a_sub_millisecond_interval_is_accepted(self) -> None:
        """Positive and legal; the waiter rounds it, it does not spin."""
        assert JobsConfig(polling_interval=timedelta(microseconds=500)).polling_interval

    def test_the_builder_validates_what_the_dataclass_validates(self) -> None:
        """Java's knobs are only reachable through a builder, so its test drives that. Here the
        dataclass is the public shape and the builder is a convenience - both must refuse."""
        with pytest.raises(ValidationError, match=re.escape("ekbatan.jobs.polling-interval")):
            JobsConfig.builder().polling_interval(timedelta(0)).build()


class TestSizingTheWorkerPool:
    """Java hands db-scheduler `Executors.newVirtualThreadPerTaskExecutor()`, so its worker
    count is effectively unbounded and `threads(jobs.size())` only sizes the polling batch.
    CPython has no virtual threads, so the workers here are a fixed pool of real OS threads and
    the number has to be chosen rather than assumed."""

    def test_the_default_is_one_worker_per_registered_job(self) -> None:
        """A recurring task is a single row, so a job has at most one execution in flight."""
        built = (
            job_registry()
            .engine(an_engine())
            .with_jobs([NoOpJob("a"), NoOpJob("b"), NoOpJob("c")])
            .build()
        )

        assert built.scheduler.threadpool_size == 3

    def test_it_can_be_raised_for_jobs_that_overlap_themselves(self) -> None:
        built = job_registry().engine(an_engine()).with_job(NoOpJob()).workers(12).build()

        assert built.scheduler.threadpool_size == 12

    def test_and_lowered_below_the_job_count(self) -> None:
        """Legal, and worth being able to do: a deployment that would rather queue its jobs than
        hold a connection open per job."""
        built = (
            job_registry()
            .engine(an_engine())
            .with_jobs([NoOpJob("a"), NoOpJob("b"), NoOpJob("c")])
            .workers(1)
            .build()
        )

        assert built.scheduler.threadpool_size == 1

    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_pool_that_cannot_run_anything_is_refused(self, bad: int) -> None:
        with pytest.raises(ValidationError, match="workers must be at least 1"):
            job_registry().engine(an_engine()).with_job(NoOpJob()).workers(bad)

    def test_the_manual_builder_sizes_the_same_way(self) -> None:
        """So a test and production do not differ on the one number this controls."""
        from db_scheduler.clock import SettableClock

        built = (
            job_registry()
            .engine(an_engine())
            .with_job(NoOpJob())
            .workers(4)
            .build_manual(SettableClock())
        )

        assert built.scheduler.threadpool_size == 4
