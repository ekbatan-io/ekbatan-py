"""The saga, its compensation, and its replay.

    make

Java's `WalletSagaIntegrationTest` covers the same three: the happy path, the compensation
paths, and that a replayed delivery is a no-op.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text

from ekbatan.clock import SystemClock
from ekbatan.config import split_login
from ekbatan.ids import ShardedId
from ekbatan.migrations import validate_scripts
from ekbatan.sharding import DEFAULT_SHARD

from .actions import (
    CompleteTransferAction,
    CompleteTransferParams,
    InitiateTransferAction,
    OpenWalletAction,
    OpenWalletParams,
    TransferParams,
    TransferResult,
)
from .app import MIGRATIONS, SagaService, build
from .model import Currency, StepName, Wallet
from .schema import EVENT_NOTIFICATIONS, EVENTS, TRANSFER_STEPS

#: The database's password: a marker, so a test can look for it where it must not be.
SECRET = "Mk-7f3a-SECRET"


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    from testcontainers.community.postgres import PostgresContainer

    # PostgreSQL, because the directory says so. Java's counterpart is `-pg` too, and the
    # reason is the same: a saga is about steps that commit independently, and a database that
    # serialises every writer makes that harder to see rather than easier.
    with PostgresContainer("postgres:17-alpine", driver="psycopg", password=SECRET) as container:
        yield container.get_connection_url()


def use(patch: pytest.MonkeyPatch, database: str) -> None:
    """Fill `ekbatan.yaml`'s placeholders the way a deployment does, in the process environment:
    the container's url, its login kept apart. `patch` restores what was there afterwards."""
    url, username, password = split_login(database)
    patch.setenv("WALLET_DATABASE_URL", url)
    patch.setenv("WALLET_DATABASE_USERNAME", username or "")
    patch.setenv("WALLET_DATABASE_PASSWORD", password or "")


@pytest.fixture(scope="module")
def service(database: str) -> Iterator[SagaService]:
    # One running service for every test, as Java's test shares one application context. A
    # service per test would also be correct, and slow: each new scheduler waits for its first
    # poll - ten seconds - before it takes over the event jobs the last one left behind.
    # A *running* clock, not a frozen one. The event trail below is read in `event_date` order,
    # and the outbox has no ordinal column - so under a frozen clock every step ties and the
    # order falls back to a random uuid. Each saga step is a separate unit of work at a
    # different instant, which is exactly what makes the trail readable; freezing time would
    # be testing an order neither port promises.
    with pytest.MonkeyPatch.context() as patch:
        use(patch, database)
        built = build(clock=SystemClock())
    built.start()
    try:
        yield built
    finally:
        built.close()


#: The event types the saga's handlers subscribe to. Fan-out reads only these; the rest - a
#: wallet opened, a transfer completed - have no subscriber and stay undelivered, correctly.
HANDLED = ("TransferInitiatedEvent", "TransferFailedEvent")


def settled(service: SagaService, within: float = 15.0) -> None:
    """Wait until the saga has run as far as it will go - Java's test waits the same way, up to
    15 seconds. The fan-out and handling jobs run in the background, so a step lands whenever
    they reach it: done means every handled event fanned out and no delivery waiting or due a
    retry."""
    deadline = time.monotonic() + within
    while True:
        with service.registry.default.connection(readonly=True) as conn:
            undelivered = conn.execute(
                select(func.count())
                .select_from(EVENTS)
                .where(EVENTS.c.delivered.is_(False))
                .where(EVENTS.c.event_type.in_(HANDLED))
            ).scalar_one()
            waiting = conn.execute(
                select(func.count())
                .select_from(EVENT_NOTIFICATIONS)
                .where(EVENT_NOTIFICATIONS.c.state.in_(("PENDING", "FAILED")))
            ).scalar_one()
        if undelivered == 0 and waiting == 0:
            return
        if time.monotonic() > deadline:
            raise AssertionError(
                f"after {within}s: {undelivered} events not fanned out, "
                f"{waiting} deliveries waiting"
            )
        time.sleep(0.1)


def a_wallet(service: SagaService, balance: str = "100.00", currency: str = "EUR") -> Wallet:
    return service.executor.execute(
        OpenWalletAction,
        OpenWalletParams(owner_id=uuid4(), currency=currency, opening_balance=Decimal(balance)),
    )


def transfer(
    service: SagaService, source: Wallet, target: ShardedId[Wallet], amount: str
) -> TransferResult:
    result: TransferResult = service.executor.execute(
        InitiateTransferAction,
        TransferParams(from_wallet_id=source.id, to_wallet_id=target, amount=Decimal(amount)),
    )
    return result


def events_on(service: SagaService, wallet_id: ShardedId[Wallet]) -> list[str]:
    """The event trail of one aggregate, in order - what an audit query would read."""
    with service.registry.default.connection(readonly=True) as conn:
        rows = conn.execute(
            select(EVENTS.c.event_type)
            .where(EVENTS.c.model_id == str(wallet_id.value))
            .where(EVENTS.c.event_type.is_not(None))
            .order_by(EVENTS.c.event_date, EVENTS.c.id)
        ).scalars()
        return [row for row in rows]


def test_a_transfer_moves_the_money_in_two_commits(service: SagaService) -> None:
    source, destination = a_wallet(service, "100.00"), a_wallet(service, "0.00")

    result = transfer(service, source, destination.id, "30.00")

    # Step one has committed, on its own, and returned: the money has left the source. Where it
    # is at this instant depends on the background jobs, so only step one's own result is read.
    assert result.source.balance == Decimal("70.0000")

    settled(service)

    assert service.wallets.get(source.id).balance == Decimal("70.0000")
    assert service.wallets.get(destination.id).balance == Decimal("30.0000")
    assert service.steps.steps_of(result.transfer_id) == [StepName.CREDIT]


def test_the_source_carries_the_whole_story(service: SagaService) -> None:
    """Choreography leaves its state in the event log rather than in a coordinator."""
    source, destination = a_wallet(service, "100.00"), a_wallet(service, "0.00")

    transfer(service, source, destination.id, "30.00")
    settled(service)

    assert events_on(service, source.id) == ["WalletOpenedEvent", "TransferInitiatedEvent"]
    assert events_on(service, destination.id) == ["WalletOpenedEvent", "TransferCompletedEvent"]


def test_a_closed_destination_is_compensated_not_lost(service: SagaService) -> None:
    """The headline case. The debit already committed, so the money is put back by a *new*
    action with its own commit - forward-only compensation."""
    source, destination = a_wallet(service, "100.00"), a_wallet(service, "0.00")
    service.wallets.update(service.wallets.get(destination.id).close())

    result = transfer(service, source, destination.id, "30.00")
    assert service.wallets.get(source.id).balance == Decimal("70.0000"), "debited"

    settled(service)

    assert service.wallets.get(source.id).balance == Decimal("100.0000"), "and made whole"
    assert service.wallets.get(destination.id).balance == Decimal("0.0000"), "untouched"
    assert service.steps.steps_of(result.transfer_id) == [StepName.CREDIT, StepName.REFUND], (
        "both steps ran - the credit step *attempted* and failed, and the refund compensated it"
    )


def test_the_failure_and_the_refund_land_on_the_source_s_trail(service: SagaService) -> None:
    """`TransferFailedEvent` is emitted on the source, not the destination - the destination may be
    closed, or may not exist at all. Reading one aggregate tells the whole story."""
    source, destination = a_wallet(service, "100.00"), a_wallet(service, "0.00")
    service.wallets.update(service.wallets.get(destination.id).close())

    transfer(service, source, destination.id, "30.00")
    settled(service)

    assert events_on(service, source.id) == [
        "WalletOpenedEvent",
        "TransferInitiatedEvent",
        "TransferFailedEvent",
        "TransferRefundedEvent",
    ]


def test_a_mismatched_currency_is_refused_the_same_way(service: SagaService) -> None:
    source = a_wallet(service, "100.00", "EUR")
    destination = a_wallet(service, "0.00", "USD")

    transfer(service, source, destination.id, "30.00")
    settled(service)

    assert service.wallets.get(source.id).balance == Decimal("100.0000")
    assert "TransferRefundedEvent" in events_on(service, source.id)


def test_a_destination_that_does_not_exist_is_refused_the_same_way(service: SagaService) -> None:
    """There is no aggregate to fail on at the far end, which is exactly why the failure event
    goes on the source."""
    source = a_wallet(service, "100.00")

    transfer(service, source, ShardedId[Wallet](uuid4()), "30.00")
    settled(service)

    assert service.wallets.get(source.id).balance == Decimal("100.0000")
    assert "TransferRefundedEvent" in events_on(service, source.id)


def test_a_replayed_delivery_does_not_credit_twice(service: SagaService) -> None:
    """Local delivery is at-least-once *by design*, so the saga has to survive a repeat.

    The second `CompleteTransferAction` stages the same `(transfer_id, CREDIT)` marker, and
    the unique
    constraint refuses the insert, and the credit beside it is rolled back with it. Invoked
    directly rather than by duplicating a notification row, because what is being tested is the
    action's idempotency and not the dispatcher's bookkeeping.
    """
    # The driver's error, not one of the framework's. Ekbatan classifies what *it* decides -
    # a lost version check is a `StaleRecordError`, a bad argument a `ValidationError` - and
    # deliberately does not translate a constraint the application itself declared. So an
    # application using a unique index for idempotency catches the database's error, which is
    # also the one carrying which constraint fired.
    from sqlalchemy.exc import IntegrityError

    source, destination = a_wallet(service, "100.00"), a_wallet(service, "0.00")
    result = transfer(service, source, destination.id, "30.00")
    settled(service)

    assert service.wallets.get(destination.id).balance == Decimal("30.0000")

    replay = CompleteTransferParams(
        transfer_id=result.transfer_id,
        from_wallet_id=source.id,
        to_wallet_id=destination.id,
        amount=Decimal("30.00"),
        currency="EUR",
    )
    with pytest.raises(IntegrityError):
        service.executor.execute(CompleteTransferAction, replay)

    assert service.wallets.get(destination.id).balance == Decimal("30.0000"), (
        "the credit was rolled back with the marker that refused"
    )
    assert service.steps.steps_of(result.transfer_id) == [StepName.CREDIT], "still one"


def test_the_step_is_a_step_name_in_python_and_text_in_the_database(
    service: SagaService,
) -> None:
    """The forced type that picks `transfer_steps.step` by name: a `StepName` both ways - in a
    row read back and in a query's condition - and the plain name in the table."""
    source, destination = a_wallet(service, "100.00"), a_wallet(service, "0.00")
    result = transfer(service, source, destination.id, "30.00")
    settled(service)

    [marker] = service.steps.find_where(TRANSFER_STEPS.c.transfer_id == result.transfer_id)
    assert marker.step is StepName.CREDIT
    refunds = service.steps.find_where(
        TRANSFER_STEPS.c.transfer_id == result.transfer_id,
        TRANSFER_STEPS.c.step == StepName.REFUND,
    )
    assert refunds == [], "the condition's StepName went through the converter too"
    with service.registry.default.connection(readonly=True) as conn:
        stored = conn.execute(
            text("SELECT step FROM transfer_steps WHERE transfer_id = :id"),
            {"id": result.transfer_id},
        ).scalar_one()
    assert stored == "CREDIT"


def test_the_currency_is_a_currency_in_python_and_text_in_the_database(
    service: SagaService,
) -> None:
    """The forced type that picks every `CHAR(3)` column by its type. The wallet holds a
    `Currency`; the column and the transfer's event hold the plain code."""
    source, destination = a_wallet(service, "100.00"), a_wallet(service, "0.00")
    transfer(service, source, destination.id, "30.00")
    settled(service)  # so the steps it starts do not land in the next test

    assert service.wallets.get(source.id).currency == Currency("EUR")
    with service.registry.default.connection(readonly=True) as conn:
        stored = conn.execute(
            text("SELECT currency FROM wallets WHERE id = :id"), {"id": source.id.value}
        ).scalar_one()
        initiated = conn.execute(
            select(EVENTS.c.payload).where(
                EVENTS.c.model_id == str(source.id.value),
                EVENTS.c.event_type == "TransferInitiatedEvent",
            )
        ).scalar_one()
        opened = conn.execute(
            select(EVENTS.c.payload).where(
                EVENTS.c.model_id == str(source.id.value),
                EVENTS.c.event_type == "WalletOpenedEvent",
            )
        ).scalar_one()
    assert stored == "EUR"
    assert opened["openedAt"].endswith("Z"), "the event's Instant: UTC digits with Java's Z"
    assert initiated["currency"] == "EUR"


def test_a_transfer_larger_than_the_balance_never_starts(service: SagaService) -> None:
    """Refused by the domain, in step one, before any event exists - so there is nothing to
    compensate and no saga to run."""
    from ekbatan.errors import ValidationError

    source, destination = a_wallet(service, "10.00"), a_wallet(service, "0.00")

    with pytest.raises(ValidationError, match="cannot transfer"):
        transfer(service, source, destination.id, "30.00")

    assert service.wallets.get(source.id).balance == Decimal("10.0000")
    assert events_on(service, source.id) == ["WalletOpenedEvent"]


def test_every_step_is_its_own_unit_of_work(service: SagaService) -> None:
    """Three commits in the failing case, two in the happy one - each independently durable,
    which is the property that lets the steps live on different databases later."""
    source, destination = a_wallet(service, "100.00"), a_wallet(service, "0.00")
    service.wallets.update(service.wallets.get(destination.id).close())

    before = _action_ids(service)
    transfer(service, source, destination.id, "30.00")
    settled(service)

    started = _action_ids(service) - before
    assert len(started) == 3, "initiate, complete-but-failed, refund - three transactions"


def _action_ids(service: SagaService) -> set[UUID]:
    with service.registry.default.connection(readonly=True) as conn:
        return set(conn.execute(select(EVENTS.c.action_id)).scalars())


# -- the same saga, through HTTP ---------------------------------------------------------------
#
# One endpoint changes shape when the work behind it becomes a saga, and that is the thing worth
# showing a reader: `POST /transfers` answers **202**, not 200, because only step one has
# committed when it returns.


@pytest.fixture
def client(database: str, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from .api import create_app

    use(monkeypatch, database)
    with TestClient(create_app()) as running:
        with running.app.state.service.registry.default.connection() as conn:
            for table in ("event_notifications", "events", "transfer_steps", "wallets"):
                conn.execute(text(f"TRUNCATE TABLE {table}"))
        yield running


def open_via_http(client, balance: str = "100.00", currency: str = "EUR") -> dict:
    created = client.post(
        "/wallets",
        json={"owner_id": str(uuid4()), "currency": currency, "opening_balance": balance},
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_a_transfer_is_accepted_not_completed(client) -> None:
    """202 rather than 200. The money has left the source and has not arrived anywhere - a 200
    would be a lie about something still in flight."""
    source, destination = open_via_http(client), open_via_http(client, "0.00")

    accepted = client.post(
        f"/wallets/{source['id']}/transfers",
        json={"to_wallet_id": destination["id"], "amount": "30.00"},
    )

    assert accepted.status_code == 202
    body = accepted.json()
    assert body["source"]["balance"] == "70.00", "step one committed"
    assert body["status_url"] == f"/transfers/{body['transfer_id']}"


def test_the_status_endpoint_follows_the_saga_forward(client) -> None:
    source, destination = open_via_http(client), open_via_http(client, "0.00")
    accepted = client.post(
        f"/wallets/{source['id']}/transfers",
        json={"to_wallet_id": destination["id"], "amount": "30.00"},
    ).json()

    settled(client.app.state.service)
    after = client.get(accepted["status_url"]).json()

    assert after["state"] == "COMPLETED" and after["steps"] == ["CREDIT"]
    assert client.get(f"/wallets/{destination['id']}").json()["balance"] == "30.00"


def test_a_failed_transfer_reports_itself_refunded(client) -> None:
    """The compensation is visible to the client as a state, not as an error - because nothing
    went wrong: the transfer could not be completed and the money came back."""
    source = open_via_http(client)
    closed = open_via_http(client, "0.00", "USD")  # wrong currency

    accepted = client.post(
        f"/wallets/{source['id']}/transfers",
        json={"to_wallet_id": closed["id"], "amount": "30.00"},
    ).json()
    settled(client.app.state.service)

    status = client.get(accepted["status_url"]).json()
    assert status["state"] == "REFUNDED"
    assert status["steps"] == ["CREDIT", "REFUND"]
    assert client.get(f"/wallets/{source['id']}").json()["balance"] == "100.00", "made whole"


def test_a_currency_that_is_not_three_capitals_is_a_400(client) -> None:
    refused = client.post(
        "/wallets", json={"owner_id": str(uuid4()), "currency": "usd", "opening_balance": "1.00"}
    )
    assert refused.status_code == 400, refused.text
    assert open_via_http(client)["currency"] == "EUR"


def test_the_service_runs_on_the_settings_in_ekbatan_yaml(service: SagaService) -> None:
    """`ekbatan.yaml` is the service's Ekbatan config, as `application.yml` is in Java: the
    namespace and the driver setting reaching the server come from it."""
    assert service.executor.namespace == "example.fastapi_wallet_saga_pg"
    with service.registry.manager(DEFAULT_SHARD).primary.connect() as conn:
        reported = conn.execute(text("SELECT current_setting('application_name')"))
        assert reported.scalar() == "wallet-service"
        # `options: -c TimeZone=UTC`: every session on UTC, whatever the server's default.
        assert conn.execute(text("SELECT current_setting('TimeZone')")).scalar() == "UTC"


def test_a_request_that_waits_out_the_pool_is_a_503(client) -> None:
    """The request pool is the cap on requests waiting on the database: past `poolSize` they
    queue for a connection, and one that waits longer than `poolTimeout` (five seconds in
    `ekbatan.yaml`) is a 503 with `Retry-After`, not a 500. Every connection is held first."""
    primary = client.app.state.service.registry.default.primary
    held = [primary.connect() for _ in range(primary.pool.size())]
    try:
        refused = client.get(f"/wallets/{uuid4()}")
    finally:
        for connection in held:
            connection.close()

    assert refused.status_code == 503, refused.text
    assert refused.headers["Retry-After"] == "1"
    assert client.get(f"/wallets/{uuid4()}").status_code == 404, "served again once one is free"


def test_the_migration_files_pass_the_checks_a_deploy_runs():
    """No database needed, so CI catches it: two files with one version, a file whose name would
    be skipped without a word, a rollback file. Each is refused here rather than at startup."""
    assert [script.script for script in validate_scripts(MIGRATIONS)] == ["V0001__schema.sql"]


def test_no_service_object_shows_the_password(service: SagaService) -> None:
    """The database's real password is a marker, set in the environment as a deployment sets it.
    Nothing the service builds shows it in its repr or str."""
    with service.registry.default.connection() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1, "it really logged in with it"

    shown = [service]
    for field in dataclasses.fields(service):
        value = getattr(service, field.name)
        shown += list(value) if isinstance(value, tuple) else [value]
    for registry in [o for o in shown if hasattr(o, "manager") and hasattr(o, "shards")]:
        for shard in registry.shards:
            manager = registry.manager(shard)
            shown += [manager, manager.primary, manager.replica]
    shown += [o.pool for o in shown if hasattr(o, "pool")]
    for thing in shown:
        assert SECRET not in repr(thing), type(thing).__name__
        assert SECRET not in str(thing), type(thing).__name__
