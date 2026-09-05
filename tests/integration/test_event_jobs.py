"""The event pipeline as two distributed jobs.

Fan-out and handling are work a cluster does once per slot, not once per instance. Ten
instances each draining the same notifications would deliver every event ten times, so they
are claimed through `scheduled_tasks` like any other job.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from ekbatan import HandlingConfig, LocalEventHandlerConfig
from ekbatan.errors import ValidationError
from ekbatan.events.jobs import (
    DEFAULT_FANOUT_BATCH_SIZE,
    DEFAULT_POLL_DELAY,
    FANOUT_JOB_NAME,
    HANDLING_JOB_NAME,
    EventFanoutJob,
    EventHandlingJob,
)
from ekbatan.jobs import FixedDelay, JobRegistry
from fixtures.actions import CreateWallet, CreateWalletParams
from fixtures.handlers import RecordingHandler
from fixtures.schema import EVENT_NOTIFICATIONS, EVENTS, SCHEDULED_TASKS


@pytest.fixture
def recorder():
    from fixtures.domain import WalletCreated

    return RecordingHandler("recorder", WalletCreated)


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
    assert a_fanout(registry, handlers, clock).name == "ekbatan-event-fanout"
    assert a_handling(registry, handlers, clock).name == "ekbatan-event-handling"


def test_they_are_scheduled_at_a_fixed_delay(registry, handlers, clock):
    assert a_fanout(registry, handlers, clock).schedule == FixedDelay(DEFAULT_POLL_DELAY)
    assert a_handling(registry, handlers, clock).schedule == FixedDelay(DEFAULT_POLL_DELAY)


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

    assert fanout.schedule == FixedDelay(timedelta(seconds=5))
    assert fanout.fanout._batch == 17
    assert handling.schedule == FixedDelay(timedelta(seconds=9))
    assert handling.dispatch._batch == 23
    assert handling.dispatch._max_backoff == timedelta(minutes=2)
    assert handling.dispatch._retention == timedelta(days=3)


def test_an_unset_knob_falls_through_to_the_framework_default(registry, handlers, clock):
    partial = LocalEventHandlerConfig(fanout_poll_delay=timedelta(seconds=30))
    fanout = a_fanout(registry, handlers, clock, config=partial)

    assert fanout.schedule == FixedDelay(timedelta(seconds=30))
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

    assert built.name == FANOUT_JOB_NAME
    assert built.fanout._batch == 7


def test_the_builder_says_what_it_is_missing(registry):
    with pytest.raises(ValidationError, match=r"needs \['events', 'handlers', 'notifications'\]"):
        EventFanoutJob.builder().registry(registry).build()


# ------------------------------------------------------------------ as jobs


def test_the_pipeline_runs_from_the_job_table(registry, executor, handlers, recorder, clock):
    """End to end: an action commits an event, and the two jobs - claimed from the table -
    turn it into a delivered notification."""
    tuning = LocalEventHandlerConfig(handling=HandlingConfig(enabled=True))
    jobs = JobRegistry(
        registry,
        [
            a_fanout(registry, handlers, clock, config=tuning),
            a_handling(registry, handlers, clock, config=tuning),
        ],
        table=SCHEDULED_TASKS,
        clock=clock,
    )
    jobs.start()

    with registry.default.connection(readonly=True) as conn:
        rows = {r[0] for r in conn.execute(select(SCHEDULED_TASKS.c.task_name))}
    assert rows == {FANOUT_JOB_NAME, HANDLING_JOB_NAME}, "both hold a row of their own"

    executor.execute(
        CreateWallet(),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1.00")),
    )

    assert jobs.run_due_once() == 2
    assert len(recorder.received) == 1, "fan-out materialised it and handling delivered it"


def test_a_disabled_handling_job_holds_its_row_and_does_nothing(
    registry, executor, handlers, recorder, clock
):
    jobs = JobRegistry(
        registry,
        [a_fanout(registry, handlers, clock), a_handling(registry, handlers, clock)],
        table=SCHEDULED_TASKS,
        clock=clock,
    )
    jobs.start()

    executor.execute(
        CreateWallet(),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1.00")),
    )
    jobs.run_due_once()

    assert recorder.received == [], "the notification is waiting; nothing in process took it"
    with registry.default.connection(readonly=True) as conn:
        pending = conn.execute(select(EVENT_NOTIFICATIONS.c.state)).scalars().all()
    assert pending == ["PENDING"], "fan-out still ran; only delivery is switched off"
