"""The event pipeline as two distributed jobs.

Fan-out and handling are work a cluster does once per slot, not once per instance. Ten
instances each draining the same notifications would deliver every event ten times, so they
are claimed through `scheduled_tasks` like any other job.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from db_scheduler.clock import SettableClock
from db_scheduler.task.schedule import DisabledSchedule, FixedDelay
from sqlalchemy import select

from ekbatan import HandlingConfig, LocalEventHandlerConfig
from ekbatan.distributedjobs import job_registry
from ekbatan.errors import ValidationError
from ekbatan.events.jobs import (
    DEFAULT_FANOUT_BATCH_SIZE,
    DEFAULT_POLL_DELAY,
    FANOUT_JOB_NAME,
    HANDLING_JOB_NAME,
    EventFanoutJob,
    EventHandlingJob,
)
from fixtures.actions import CreateWalletAction, CreateWalletParams
from fixtures.executing import run
from fixtures.handlers import RecordingHandler
from fixtures.schema import EVENT_NOTIFICATIONS, EVENTS, SCHEDULED_TASKS


@pytest.fixture
def recorder():
    from fixtures.domain import WalletCreatedEvent

    return RecordingHandler("recorder", WalletCreatedEvent)


@pytest.fixture
def handlers(recorder):
    from ekbatan.events import EventHandlerRegistry

    return EventHandlerRegistry([recorder])


def a_fanout(registry, handlers, clock, **kwargs):
    return EventFanoutJob(
        registry,
        handlers,
        events=EVENTS,
        notifications=EVENT_NOTIFICATIONS,
        clock=clock,
        **kwargs,
    )


def a_handling(registry, handlers, clock, **kwargs):
    return EventHandlingJob(
        registry, handlers, notifications=EVENT_NOTIFICATIONS, clock=clock, **kwargs
    )


# ------------------------------------------------------------------ what they are


def test_they_carry_the_names_the_java_implementation_uses(registry, handlers, clock):
    """The name is a primary key in `scheduled_tasks`. A mixed deployment coordinates on one
    row per job, so the two ports have to agree on what it is called."""
    assert a_fanout(registry, handlers, clock).name() == "ekbatan-event-fanout"
    assert a_handling(registry, handlers, clock).name() == "ekbatan-event-handling"


def test_they_are_scheduled_at_a_fixed_delay(registry, handlers, clock):
    assert a_fanout(registry, handlers, clock).schedule() == FixedDelay(DEFAULT_POLL_DELAY)

    # Handling is opt-in, and a job that is switched off says so through its schedule rather
    # than through a separate flag - which is how Java says it, and what db-scheduler reads to
    # decide whether to write a row at all.
    assert isinstance(a_handling(registry, handlers, clock).schedule(), DisabledSchedule)
    switched_on = a_handling(
        registry,
        handlers,
        clock,
        config=LocalEventHandlerConfig(handling=HandlingConfig(enabled=True)),
    )
    assert switched_on.schedule() == FixedDelay(DEFAULT_POLL_DELAY)


def test_handling_is_off_unless_it_is_switched_on(registry, handlers, clock):
    """A deployment draining through an external pipeline registers its handlers - they decode
    the event - and must not also run a consumer in process."""
    assert a_fanout(registry, handlers, clock).enabled is True
    assert a_handling(registry, handlers, clock).enabled is False

    on = LocalEventHandlerConfig(handling=HandlingConfig(enabled=True))
    assert a_handling(registry, handlers, clock, config=on).enabled is True


def test_a_blank_name_is_refused(registry, handlers, clock):
    with pytest.raises(ValidationError, match="blank name"):
        a_fanout(registry, handlers, clock, name="  ")


# ------------------------------------------------------------------ configuration


def test_the_config_reaches_the_drainers(registry, handlers, clock):
    tuning = LocalEventHandlerConfig(
        fanout_poll_delay=timedelta(seconds=5),
        fanout_batch_size=17,
        handling_poll_delay=timedelta(seconds=9),
        handling_batch_size=23,
        handling_max_backoff_cap=timedelta(minutes=2),
        handling_retention_window=timedelta(days=3),
        handling=HandlingConfig(enabled=True),
    )

    fanout = a_fanout(registry, handlers, clock, config=tuning)
    handling = a_handling(registry, handlers, clock, config=tuning)

    assert fanout.schedule() == FixedDelay(timedelta(seconds=5))
    assert fanout.fanout._batch == 17
    assert handling.schedule() == FixedDelay(timedelta(seconds=9))
    assert handling.dispatch._batch == 23
    assert handling.dispatch._max_backoff == timedelta(minutes=2)
    assert handling.dispatch._retention == timedelta(days=3)


def test_an_unset_knob_falls_through_to_the_framework_default(registry, handlers, clock):
    partial = LocalEventHandlerConfig(fanout_poll_delay=timedelta(seconds=30))
    fanout = a_fanout(registry, handlers, clock, config=partial)

    assert fanout.schedule() == FixedDelay(timedelta(seconds=30))
    assert fanout.fanout._batch == DEFAULT_FANOUT_BATCH_SIZE, "the one that was not set"


def test_a_nonsense_knob_is_refused_at_configuration_time():
    with pytest.raises(ValidationError, match="fanout_poll_delay must be positive"):
        LocalEventHandlerConfig(fanout_poll_delay=timedelta(0))
    with pytest.raises(ValidationError, match="handling_batch_size must be at least 1"):
        LocalEventHandlerConfig(handling_batch_size=0)


def test_the_builder_reaches_the_same_place(registry, handlers, clock):
    built = (
        EventFanoutJob.builder()
        .registry(registry)
        .handlers(handlers)
        .events(EVENTS)
        .notifications(EVENT_NOTIFICATIONS)
        .config(LocalEventHandlerConfig(fanout_batch_size=7))
        .clock(clock)
        .build()
    )

    assert built.name() == FANOUT_JOB_NAME
    assert built.fanout._batch == 7


def test_the_builder_says_what_it_is_missing(registry):
    with pytest.raises(ValidationError, match=r"needs \['events', 'handlers', 'notifications'\]"):
        EventFanoutJob.builder().registry(registry).build()


# ------------------------------------------------------------------ as jobs


def a_job_registry(registry, jobs):
    """The two event jobs on a manual scheduler, so a test says when a round happens."""
    return (
        job_registry()
        .engine(registry.default.primary)
        .with_jobs(jobs)
        .build_manual(SettableClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC)))
    )


def test_the_pipeline_runs_from_the_job_table(registry, executor, handlers, recorder, clock):
    """End to end: an action commits an event, and the two jobs - claimed from the table -
    turn it into a delivered notification."""
    tuning = LocalEventHandlerConfig(handling=HandlingConfig(enabled=True))
    fanout_job = a_fanout(registry, handlers, clock, config=tuning)
    handling_job = a_handling(registry, handlers, clock, config=tuning)
    jobs = a_job_registry(registry, [fanout_job, handling_job])
    jobs.start()

    with registry.default.connection(readonly=True) as conn:
        rows = {r[0] for r in conn.execute(select(SCHEDULED_TASKS.c.task_name))}
    assert rows == {FANOUT_JOB_NAME, HANDLING_JOB_NAME}, "both hold a row of their own"

    run(
        executor,
        CreateWalletAction(executor.clock),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1.00")),
    )

    # `drain_once()` rather than driving the scheduler, which is how Java's own integration
    # tests exercise these two - `fanoutJob.drainOneRound()`. `execute()` never returns: it is
    # one execution for the life of the process, so a test that ran it to completion would not
    # finish either.
    fanout_job.fanout.drain_once()
    handling_job.dispatch.drain_once()

    assert len(recorder.received) == 1, "fan-out materialised it and handling delivered it"


def test_a_disabled_handling_job_holds_its_row_and_does_nothing(
    registry, executor, handlers, recorder, clock
):
    fanout_job = a_fanout(registry, handlers, clock)
    jobs = a_job_registry(registry, [fanout_job, a_handling(registry, handlers, clock)])
    jobs.start()

    run(
        executor,
        CreateWalletAction(executor.clock),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1.00")),
    )
    fanout_job.fanout.drain_once()  # handling is switched off, so only this one runs

    assert recorder.received == [], "the notification is waiting; nothing in process took it"
    with registry.default.connection(readonly=True) as conn:
        pending = conn.execute(select(EVENT_NOTIFICATIONS.c.state)).scalars().all()
    assert pending == ["PENDING"], "fan-out still ran; only delivery is switched off"


def test_the_metrics_are_the_ones_the_java_implementation_emits(
    registry, executor, handlers, recorder, clock
):
    """The instrument names are a cross-language contract, exactly as the span names are.

    A dashboard reading `ekbatan.events.handled{outcome="expired_postfailure"}` is reading
    across both ports, and a name invented here would quietly show only half the fleet.
    """
    sdk = pytest.importorskip("opentelemetry.sdk.metrics")
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    run(
        executor,
        CreateWalletAction(executor.clock),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1.00")),
    )

    # `metrics._METER_PROVIDER` does not exist - the global lives in `_internal`, unlike
    # tracing's. `set_meter_provider` would work once and then refuse, which is no good in a
    # suite, so the module attribute is set directly and restored afterwards.
    import opentelemetry.metrics._internal as metrics_internal

    reader = InMemoryMetricReader()
    previous = metrics_internal._METER_PROVIDER
    metrics_internal._METER_PROVIDER = sdk.MeterProvider(metric_readers=[reader])
    try:
        import importlib

        import ekbatan._metrics
        import ekbatan.events.dispatch
        import ekbatan.events.fanout

        # The instruments are module-level, so they were built against whatever meter existed
        # at import. Reloading rebuilds them against this test's reader.
        importlib.reload(ekbatan._metrics)
        fanout_module = importlib.reload(ekbatan.events.fanout)
        dispatch_module = importlib.reload(ekbatan.events.dispatch)

        fanned = fanout_module.EventFanout(
            registry,
            handlers,
            events=EVENTS,
            notifications=EVENT_NOTIFICATIONS,
            clock=clock,
        ).drain_once()
        handled = dispatch_module.EventDispatch(
            registry, handlers, notifications=EVENT_NOTIFICATIONS, clock=clock
        ).drain_once()

        # Collected before the provider is swapped back; a reader detached from its provider
        # has nothing to report.
        collected = reader.get_metrics_data()
    finally:
        metrics_internal._METER_PROVIDER = previous
        for module in ("ekbatan._metrics", "ekbatan.events.fanout", "ekbatan.events.dispatch"):
            importlib.reload(importlib.import_module(module))

    emitted = {
        metric.name
        for resource in (collected.resource_metrics if collected else [])
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }

    assert fanned == 1 and handled == 1, "one event, fanned out and delivered"
    assert recorder.calls == 1, "and the handler actually ran"
    assert {
        "ekbatan.events.fanned_out",
        "ekbatan.events.notifications_created",
        "ekbatan.events.handled",
        "ekbatan.events.handler.duration",
        "ekbatan.events.delivery.lag",
    } <= emitted, f"missing instruments; got {sorted(emitted)}"
