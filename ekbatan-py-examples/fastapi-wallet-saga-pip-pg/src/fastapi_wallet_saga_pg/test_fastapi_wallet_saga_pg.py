"""The saga's own test, against a real Postgres.

    make

It asks what the Java example's test asks: a transfer credits the destination through the event
handler, and a replayed step two credits nothing more; a transfer to a closed wallet, or to one
that does not exist, refunds the source, and a replayed refund refunds nothing more; and the
driver settings in `ekbatan.yaml` reach the database. Two checks are its own: no object of the
service shows the database password, and the migration files pass the checks a deploy runs.

The saga runs in the background - the endpoint answers 202 once step one has committed - so the
test waits for the chain to converge.
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
from ekbatan.ids import Id
from ekbatan.migrations import validate_scripts

from .actions import CompleteTransferAction, RefundTransferAction
from .app import MIGRATIONS, Settings, WalletService
from .model import Wallet

#: The container's password: a marker no object of the service may show.
SECRET = "Mk-7f3a-SECRET"


@pytest.fixture(scope="module")
def database() -> Iterator[dict[str, str]]:
    with PostgresContainer(
        "postgres:17-alpine", driver="psycopg", password=SECRET, dbname="wallet"
    ) as server:
        url, username, password = split_login(server.get_connection_url())
        yield {
            "WALLET_DATABASE_URL": url,
            "WALLET_DATABASE_USERNAME": username or "",
            "WALLET_DATABASE_PASSWORD": password or "",
        }


def use(monkeypatch: pytest.MonkeyPatch, database: dict[str, str]) -> None:
    for name, value in database.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def client(database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from .api import create_app

    use(monkeypatch, database)
    with TestClient(create_app(Settings(job_workers=3))) as running:
        yield running


def service_of(client: TestClient) -> WalletService:
    service: WalletService = cast(Any, client.app).state.service
    return service


def create_wallet(client: TestClient, currency: str, initial_balance: str) -> UUID:
    response = client.post(
        "/wallets",
        json={"ownerId": str(uuid4()), "currency": currency, "initialBalance": initial_balance},
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


def transfer(client: TestClient, from_id: UUID, to_id: UUID, amount: str) -> Any:
    return client.post(
        "/wallets/transfers",
        json={"fromWalletId": str(from_id), "toWalletId": str(to_id), "amount": amount},
    )


def balance(client: TestClient, wallet_id: UUID) -> Decimal:
    return Decimal(client.get(f"/wallets/{wallet_id}").json()["balance"])


def eventually(check: Callable[[], bool], within: float = 30.0) -> bool:
    """Polled: the saga's later steps run on fan-out and dispatch, real jobs on a real
    schedule."""
    deadline = time.monotonic() + within
    while not check():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.2)
    return True


def test_happy_path_transfer_credits_destination_via_the_local_event_handler(
    client: TestClient,
) -> None:
    a_id = create_wallet(client, "USD", "100.00")
    b_id = create_wallet(client, "USD", "0.00")

    response = transfer(client, a_id, b_id, "25.00")

    # Step one has committed: the debit and the TransferInitiatedEvent.
    assert response.status_code == 202
    body = response.json()
    transfer_id = UUID(body["transferId"])
    assert Decimal(body["fromWallet"]["balance"]) == Decimal("75.00")

    # And the event handler has run step two, which credits B.
    assert eventually(
        lambda: (
            balance(client, b_id) == Decimal("25.00") and balance(client, a_id) == Decimal("75.00")
        )
    ), "step two never credited the destination"

    # Replaying step two with the same transfer id is a no-op, not a second credit.
    service_of(client).executor.execute(
        "test",
        CompleteTransferAction,
        CompleteTransferAction.Params(
            transfer_id=transfer_id,
            from_wallet_id=Id[Wallet].of(a_id),
            to_wallet_id=Id[Wallet].of(b_id),
            amount=Decimal("25.00"),
        ),
    )
    assert balance(client, b_id) == Decimal("25.00")
    assert balance(client, a_id) == Decimal("75.00")


def test_compensation_path_refunds_source_when_destination_is_closed(client: TestClient) -> None:
    a_id = create_wallet(client, "USD", "100.00")
    b_id = create_wallet(client, "USD", "0.00")
    closed = client.post(f"/wallets/{b_id}/close")
    assert closed.status_code == 200
    assert closed.json()["state"] == "CLOSED"

    response = transfer(client, a_id, b_id, "25.00")

    assert response.status_code == 202
    transfer_id = UUID(response.json()["transferId"])
    assert Decimal(response.json()["fromWallet"]["balance"]) == Decimal("75.00")

    # Step two finds the destination closed and marks the transfer failed; the failure handler
    # refunds A.
    def refunded() -> bool:
        b = client.get(f"/wallets/{b_id}").json()
        return (
            balance(client, a_id) == Decimal("100.00")
            and Decimal(b["balance"]) == Decimal("0.00")
            and b["state"] == "CLOSED"
        )

    assert eventually(refunded), "the source was never refunded"

    # Replaying the refund with the same transfer id is a no-op, not a second refund.
    service_of(client).executor.execute(
        "test",
        RefundTransferAction,
        RefundTransferAction.Params(
            transfer_id=transfer_id, from_wallet_id=Id[Wallet].of(a_id), amount=Decimal("25.00")
        ),
    )
    assert balance(client, a_id) == Decimal("100.00")


def test_compensation_path_refunds_source_when_destination_does_not_exist(
    client: TestClient,
) -> None:
    a_id = create_wallet(client, "USD", "100.00")

    response = transfer(client, a_id, uuid4(), "25.00")

    assert response.status_code == 202
    assert eventually(lambda: balance(client, a_id) == Decimal("100.00")), (
        "the source was never refunded"
    )


def test_driver_settings_from_the_configuration_reach_the_database(client: TestClient) -> None:
    """`ekbatan.yaml` gives the primaryConfig a dataSourceProperties entry; the primary pool
    must have handed it to the driver."""
    with service_of(client).database_registry.default.connection() as connection:
        name = connection.execute(text("SELECT current_setting('application_name')"))
        assert name.scalar() == "wallet-service"


def test_no_service_object_shows_the_password(
    database: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The container's password is the marker `SECRET`. The service is built from the
    environment and really connects with it - and not one object it holds shows it."""
    from .app import build

    use(monkeypatch, database)
    settings = Settings()
    service = build(settings)
    try:
        with service.database_registry.default.connection() as connection:
            assert connection.execute(text("SELECT 1")).scalar() == 1, "connected with it"
        sources = [
            service.database_registry.default.primary,
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
        "V0004__transfer_steps.sql",
    ]
