"""The local event handler's two repositories, against a real database.

Java's `EventEntityRepository` and `EventNotificationRepository` have no tests of their own - the
fan-out and handling job tests cover them from outside, and so do this port's. These pin down
each method on its own: what it selects, what it changes, and what it leaves alone.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from ekbatan.action.outbox import EventEntity
from ekbatan.action.outbox import EventEntityRepository as OutboxRepository
from ekbatan.events.notification import EventNotification, EventNotificationState
from ekbatan.events.repository import EventEntityRepository, EventNotificationRepository
from ekbatan.sharding import DEFAULT_SHARD
from fixtures.schema import EVENT_NOTIFICATIONS

NOW = datetime(2026, 3, 1, 12, 0)


def an_event(event_type: str | None = "WalletCreatedEvent", minutes: int = 0) -> EventEntity:
    at = NOW + timedelta(minutes=minutes)
    return EventEntity.create_event_entity(
        id=uuid.uuid4(),
        namespace="example.wallet",
        action_id=uuid.uuid4(),
        action_name="CreateWalletAction",
        action_params={"owner": "ada"},
        started_date=at,
        completion_date=at,
        model_id=None if event_type is None else "w-1",
        model_type=None if event_type is None else "Wallet",
        event_type=event_type,
        payload=None if event_type is None else {"currency": "EUR"},
        event_date=at,
    ).build()


def a_notification(
    state: EventNotificationState = EventNotificationState.PENDING,
    *,
    attempts: int = 0,
    retry_in: timedelta = timedelta(0),
) -> EventNotification:
    return (
        EventNotification.builder()
        .id(uuid.uuid4())
        .event_id(uuid.uuid4())
        .handler_name("audit")
        .state(state)
        .attempts(attempts)
        .next_retry_at(NOW + retry_in)
        .created_date(NOW)
        .updated_date(NOW)
        .namespace("example.wallet")
        .action_id(uuid.uuid4())
        .action_name("CreateWalletAction")
        .action_params({})
        .started_date(NOW)
        .completion_date(NOW)
        .event_type("WalletCreatedEvent")
        .event_date(NOW)
        .build()
    )


@pytest.fixture
def events(registry):
    return EventEntityRepository(registry)


@pytest.fixture
def notifications(registry):
    return EventNotificationRepository(registry)


def stored(registry, notification: EventNotification):
    with registry.default.connection() as conn:
        row = conn.execute(
            select(EVENT_NOTIFICATIONS).where(EVENT_NOTIFICATIONS.c.id == notification.id)
        ).one()
    return EventNotification.from_row(row)


# -- events -------------------------------------------------------------------------------------


def test_find_undelivered_takes_handled_types_oldest_first_and_skips_the_rest(registry, events):
    later = an_event(minutes=2)
    earlier = an_event(minutes=1)
    unhandled = an_event("NobodyListens")
    sentinel = an_event(None)
    OutboxRepository(registry).add_all_no_result(
        [later, earlier, unhandled, sentinel], DEFAULT_SHARD
    )

    found = events.find_undelivered(DEFAULT_SHARD, ["WalletCreatedEvent"], 10)

    assert [e.id for e in found] == [earlier.id, later.id]


def test_find_undelivered_honours_the_limit(registry, events):
    OutboxRepository(registry).add_all_no_result(
        [an_event(minutes=m) for m in range(3)], DEFAULT_SHARD
    )
    assert len(events.find_undelivered(DEFAULT_SHARD, ["WalletCreatedEvent"], 2)) == 2


def test_find_undelivered_with_no_handled_types_reads_nothing(registry, events):
    OutboxRepository(registry).add_all_no_result([an_event()], DEFAULT_SHARD)
    assert events.find_undelivered(DEFAULT_SHARD, [], 10) == []


def test_mark_delivered_takes_exactly_those_rows_out(registry, events):
    first, second = an_event(minutes=1), an_event(minutes=2)
    OutboxRepository(registry).add_all_no_result([first, second], DEFAULT_SHARD)

    events.mark_delivered([first.id], DEFAULT_SHARD)

    assert [e.id for e in events.find_undelivered(DEFAULT_SHARD, ["WalletCreatedEvent"], 10)] == [
        second.id
    ]


# -- notifications ------------------------------------------------------------------------------


def test_a_notification_already_there_is_not_inserted_twice(registry, notifications):
    """Java's `onConflictDoNothing`: fan-out running twice over the same events is harmless."""
    notification = a_notification()
    notifications.add_all_no_result([notification], DEFAULT_SHARD)
    notifications.add_all_no_result([notification], DEFAULT_SHARD)

    assert len(notifications.find_due(DEFAULT_SHARD, 10, NOW)) == 1


def test_find_due_takes_pending_and_failed_whose_time_has_come_soonest_first(notifications):
    failed = a_notification(
        EventNotificationState.FAILED, attempts=1, retry_in=-timedelta(minutes=1)
    )
    pending = a_notification()
    not_yet = a_notification(retry_in=timedelta(minutes=5))
    done = a_notification(EventNotificationState.SUCCEEDED)
    notifications.add_all_no_result([pending, failed, not_yet, done], DEFAULT_SHARD)

    due = notifications.find_due(DEFAULT_SHARD, 10, NOW)

    assert [n.id for n in due] == [failed.id, pending.id]
    assert due[0].state is EventNotificationState.FAILED


def test_mark_succeeded_counts_the_attempt(registry, notifications):
    notification = a_notification(attempts=2)
    notifications.add_all_no_result([notification], DEFAULT_SHARD)
    later = NOW + timedelta(seconds=5)

    notifications.mark_succeeded_all([notification.id], later, DEFAULT_SHARD)

    after = stored(registry, notification)
    assert after.state is EventNotificationState.SUCCEEDED
    assert after.attempts == 3
    assert after.updated_date == later


def test_mark_failed_counts_the_attempt_and_moves_the_retry(registry, notifications):
    notification = a_notification()
    notifications.add_all_no_result([notification], DEFAULT_SHARD)
    retry = NOW + timedelta(seconds=30)

    notifications.mark_failed_bucket([notification.id], retry, NOW, DEFAULT_SHARD)

    after = stored(registry, notification)
    assert (after.state, after.attempts, after.next_retry_at) == (
        EventNotificationState.FAILED,
        1,
        retry,
    )


def test_expiry_before_the_call_is_not_an_attempt_and_after_a_failure_is(registry, notifications):
    preflight, post_failure = a_notification(attempts=4), a_notification(attempts=4)
    notifications.add_all_no_result([preflight, post_failure], DEFAULT_SHARD)

    notifications.mark_expired_all_preflight([preflight.id], NOW, DEFAULT_SHARD)
    notifications.mark_expired_all_post_failure([post_failure.id], NOW, DEFAULT_SHARD)

    assert stored(registry, preflight).attempts == 4
    assert stored(registry, post_failure).attempts == 5
    assert stored(registry, preflight).state is EventNotificationState.EXPIRED
    assert stored(registry, post_failure).state is EventNotificationState.EXPIRED


def test_deferring_leaves_it_pending_and_unattempted(registry, notifications):
    notification = a_notification(attempts=1)
    notifications.add_all_no_result([notification], DEFAULT_SHARD)
    retry = NOW + timedelta(minutes=1)

    notifications.defer_pending_bucket([notification.id], retry, NOW, DEFAULT_SHARD)

    after = stored(registry, notification)
    assert (after.state, after.attempts, after.next_retry_at) == (
        EventNotificationState.PENDING,
        1,
        retry,
    )


def test_marking_nothing_touches_nothing(notifications):
    notifications.mark_succeeded_all([], NOW, DEFAULT_SHARD)
    notifications.mark_failed_bucket([], NOW, NOW, DEFAULT_SHARD)


def test_the_timestamps_come_back_as_they_were_written(notifications):
    """Equal to what was written, no zone added - so comparable with the default clock."""
    notification = a_notification()
    notifications.add_all_no_result([notification], DEFAULT_SHARD)
    (back,) = notifications.find_due(DEFAULT_SHARD, 10, NOW)
    assert back.created_date == NOW
    assert back.created_date.tzinfo is None
    assert back.event_date.tzinfo is None


# -- which connection a read takes ---------------------------------------------------------------


def test_find_undelivered_does_not_join_an_open_transaction(registry, events):
    """Java's `findUndelivered` reads on a connection of its own, so inside an open transaction
    it does not see that transaction's uncommitted rows."""
    with registry.default.transaction():
        OutboxRepository(registry).add_all_no_result([an_event()], DEFAULT_SHARD)
        inside = events.find_undelivered(DEFAULT_SHARD, ["WalletCreatedEvent"], 10)
    assert inside == [], "find_undelivered joined the open transaction; Java's does not"
    assert len(events.find_undelivered(DEFAULT_SHARD, ["WalletCreatedEvent"], 10)) == 1


def test_find_due_does_not_join_an_open_transaction(registry, notifications):
    """Java's `findDue` reads on `db()`, a plain primary connection, never the transaction."""
    with registry.default.transaction():
        notifications.add_all_no_result([a_notification()], DEFAULT_SHARD)
        inside = notifications.find_due(DEFAULT_SHARD, 10, NOW)
    assert inside == [], "find_due joined the open transaction; Java's does not"
    assert len(notifications.find_due(DEFAULT_SHARD, 10, NOW)) == 1


@pytest.fixture
def lagging_replica(backend):
    """A shard whose replica has seen nothing: the primary and the replica are two databases."""
    from ekbatan.persistence import DatabaseRegistry, TransactionManager
    from fixtures.schema import metadata

    written, lagging = "ekbatan_written", "ekbatan_lagging"
    for name in (written, lagging):
        backend.prepare(name, metadata)
        backend.truncate(name, metadata)
    manager = TransactionManager(
        DEFAULT_SHARD, backend.data_source(written), backend.data_source(lagging)
    )
    with DatabaseRegistry({DEFAULT_SHARD: manager}, DEFAULT_SHARD) as reg:
        yield reg


def test_fan_out_reads_the_replica_and_handling_reads_the_primary(lagging_replica):
    """Java's `findUndelivered` reads `readonlyDb()`, its `findDue` reads `db()`. With a
    lagging replica fan-out runs late; handling never sees a stale row."""
    OutboxRepository(lagging_replica).add_all_no_result([an_event()], DEFAULT_SHARD)
    EventNotificationRepository(lagging_replica).add_all_no_result(
        [a_notification()], DEFAULT_SHARD
    )

    undelivered = EventEntityRepository(lagging_replica).find_undelivered(
        DEFAULT_SHARD, ["WalletCreatedEvent"], 10
    )
    due = EventNotificationRepository(lagging_replica).find_due(DEFAULT_SHARD, 10, NOW)

    assert undelivered == [], "find_undelivered read the primary; Java's reads the replica"
    assert len(due) == 1, "find_due read the replica; Java's reads the primary"
