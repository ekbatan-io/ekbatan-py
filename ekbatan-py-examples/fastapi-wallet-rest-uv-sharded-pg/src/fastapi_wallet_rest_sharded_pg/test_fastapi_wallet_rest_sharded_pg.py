"""The example's own test, against two real Postgres servers - the global shard and the Mexico
shard, as the service runs in production.

    make

It asks what the Java example's test asks: each wallet lands on the shard its country names, a
deposit's event reaches the handler and leaves a notification, closing a wallet closes it, and
the driver settings in `ekbatan.yaml` reach the database. Two checks are its own: no object of the
service shows the database password, and the migration files pass the checks a deploy runs.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

from ekbatan.config import split_login
from ekbatan.migrations import validate_scripts

from .actions import WalletCreateAction
from .app import MIGRATIONS, Settings, WalletService
from .model import NotificationKind

GLOBAL_SHARD = WalletCreateAction.GLOBAL_SHARD
MEXICO_SHARD = WalletCreateAction.MEXICO_SHARD
#: The containers' password: a marker no object of the service may show.
SECRET = "Mk-7f3a-SECRET"


@pytest.fixture(scope="module")
def databases() -> Iterator[dict[str, str]]:
    """Two servers, one per shard, and what fills `ekbatan.yaml`'s placeholders from them."""
    with (
        PostgresContainer(
            "postgres:17-alpine", driver="psycopg", password=SECRET, dbname="wallet_global"
        ) as global_server,
        PostgresContainer(
            "postgres:17-alpine", driver="psycopg", password=SECRET, dbname="wallet_mexico"
        ) as mexico_server,
    ):
        # testcontainers renders a `postgresql+psycopg://` url only if asked for the driver;
        # the framework reads the dialect off the scheme, so it has to be the real one.
        global_url, username, password = split_login(global_server.get_connection_url())
        mexico_url, _, _ = split_login(mexico_server.get_connection_url())
        yield {
            "WALLET_GLOBAL_URL": global_url,
            "WALLET_MEXICO_URL": mexico_url,
            "WALLET_DATABASE_USERNAME": username or "",
            "WALLET_DATABASE_PASSWORD": password or "",
        }


def use(monkeypatch: pytest.MonkeyPatch, databases: dict[str, str]) -> None:
    """Put the placeholders in the process environment, as a deployment does; `monkeypatch`
    puts each variable back when the test is done."""
    for name, value in databases.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def client(databases: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from .api import create_app

    use(monkeypatch, databases)
    # `with TestClient(...)` is what runs the lifespan - the migrations, the pools and the
    # scheduler. Without the `with`, none of that happens.
    with TestClient(create_app(Settings(job_workers=3))) as running:
        yield running


def service_of(client: TestClient) -> WalletService:
    service: WalletService = cast(Any, client.app).state.service
    return service


def create_wallet(
    client: TestClient, country_code: str, currency: str, initial_balance: str
) -> dict[str, Any]:
    response = client.post(
        "/wallets",
        json={
            "countryCode": country_code,
            "ownerId": str(uuid4()),
            "currency": currency,
            "initialBalance": initial_balance,
        },
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def deposit(client: TestClient, wallet_id: UUID, amount: str, recipient: str) -> Any:
    return client.post(
        f"/wallets/{wallet_id}/deposits", json={"amount": amount, "recipient": recipient}
    )


def eventually[T](read: Callable[[], T], within: float = 30.0) -> T:
    """Polled rather than asserted at once: fan-out and dispatch are real background jobs on a
    real schedule, and a background job finishes when it finishes."""
    deadline = time.monotonic() + within
    while True:
        found = read()
        if found or time.monotonic() > deadline:
            return found
        time.sleep(0.2)


def test_separate_wallets_route_to_their_own_shards(client: TestClient) -> None:
    global_wallet = create_wallet(client, "US", "USD", "10.00")
    mexico_wallet = create_wallet(client, "MX", "MXN", "20.00")
    global_id = UUID(global_wallet["id"])
    mexico_id = UUID(mexico_wallet["id"])

    assert (global_wallet["shardGroup"], global_wallet["shardMember"]) == (0, 0)
    assert (mexico_wallet["shardGroup"], mexico_wallet["shardMember"]) == (1, 0)

    global_deposit = deposit(client, global_id, "5.00", "global@example.com").json()
    mexico_deposit = deposit(client, mexico_id, "7.00", "mexico@example.com").json()
    assert Decimal(global_deposit["balance"]) == Decimal("15.00")
    assert Decimal(mexico_deposit["balance"]) == Decimal("27.00")

    wallet_repository = service_of(client).wallet_repository
    assert wallet_repository.exists_on_shard(GLOBAL_SHARD, global_id)
    assert not wallet_repository.exists_on_shard(MEXICO_SHARD, global_id)
    assert wallet_repository.exists_on_shard(MEXICO_SHARD, mexico_id)
    assert not wallet_repository.exists_on_shard(GLOBAL_SHARD, mexico_id)


def test_deposit_emits_event_and_handler_creates_notification(client: TestClient) -> None:
    """A deposit on the Mexico shard: its notification is written on the default shard, where
    every notification lives."""
    mexico_id = UUID(create_wallet(client, "MX", "MXN", "20.00")["id"])

    response = deposit(client, mexico_id, "7.00", "mexico@example.com")
    assert response.status_code == 200

    notification_repository = service_of(client).notification_repository
    notifications = eventually(lambda: notification_repository.find_all_by_wallet_id(mexico_id))
    assert len(notifications) == 1, "the handler never ran; fan-out or dispatch is not running"
    assert notifications[0].kind is NotificationKind.MONEY_DEPOSITED
    assert notifications[0].recipient == "mexico@example.com"
    assert "Deposit of" in notifications[0].message


def test_close_transitions_wallet_to_closed_state(client: TestClient) -> None:
    wallet_id = create_wallet(client, "US", "EUR", "0.00")["id"]

    response = client.post(f"/wallets/{wallet_id}/close")

    assert response.status_code == 200
    assert client.get(f"/wallets/{wallet_id}").json()["state"] == "CLOSED"


def test_driver_settings_from_the_configuration_reach_the_database(client: TestClient) -> None:
    """`ekbatan.yaml` gives every primaryConfig a dataSourceProperties entry; each shard's
    primary pool must have handed it to the driver."""
    database_registry = service_of(client).database_registry
    for shard in (GLOBAL_SHARD, MEXICO_SHARD):
        with database_registry.manager(shard).connection() as connection:
            name = connection.execute(text("SELECT current_setting('application_name')"))
            assert name.scalar() == "wallet-service", shard


def test_no_service_object_shows_the_password(
    databases: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The containers' password is the marker `SECRET`. The service is built from the
    environment and really connects with it - and not one object it holds shows it: not the
    settings, the service, its registry, its executor, nor any data source, engine or pool."""
    from .app import build

    use(monkeypatch, databases)
    settings = Settings()
    service = build(settings)
    try:
        with service.database_registry.default.connection() as connection:
            assert connection.execute(text("SELECT 1")).scalar() == 1, "connected with it"
        sources = [
            *(
                service.database_registry.manager(shard).primary
                for shard in service.database_registry.shards
            ),
            service.lock_data_source,
            service.jobs_data_source,
        ]
        shown = [
            settings,
            service,
            service.database_registry,
            service.executor,
            service.lock_provider,
            service.job_registry,
            *sources,
            *(source.sqlalchemy_engine for source in sources),
            *(source.sqlalchemy_engine.pool for source in sources),
        ]
        for thing in shown:
            assert SECRET not in repr(thing), type(thing).__name__
            assert SECRET not in str(thing), type(thing).__name__
    finally:
        service.close()


def test_the_migration_files_pass_the_checks_a_deploy_runs() -> None:
    """No database needed, so CI catches it: two files with one version, a file whose name would
    be skipped without a word, a rollback file. Each is refused here rather than at startup."""
    assert [script.script for script in validate_scripts(MIGRATIONS)] == [
        "V0001__eventlog.sql",
        "V0002__scheduled_tasks.sql",
        "V0003__wallets.sql",
        "V0004__notifications.sql",
    ]
