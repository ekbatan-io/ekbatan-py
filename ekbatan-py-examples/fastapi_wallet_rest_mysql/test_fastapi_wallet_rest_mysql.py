"""The example's own test, against a real MySQL.

Lives with the example, runs separately from the framework's suite, and exists for one reason:
an example nobody runs is an example that rots. It asserts what the README claims, so a change
that makes the prose false makes this red.

    uv run pytest ekbatan-py-examples/fastapi_wallet_rest_mysql

Java's grid examples each carry the same thing - a Testcontainers integration test that
drives the REST endpoints - and for the same reason. A real MySQL server rather than
SQLite, because this example is about what a deployment looks like: migrations from
files, a keyed lock taken on a real server-side lock, three separate pools. None of
those are exercised honestly by SQLite.

"""

from __future__ import annotations

import time
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from testcontainers.community.mysql import MySqlContainer

from ekbatan.config import split_login
from ekbatan.migrations import validate_scripts

from .app import MIGRATIONS, Settings

OWNER = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    # `dialect=` is the *driver* only - testcontainers prefixes `mysql+` itself and, since 4.15,
    # refuses a value that already carries the prefix. `username="root"` because the default
    # `test` user has rights on the `test` schema alone, and the migration creates tables.
    with MySqlContainer("mysql:8.4", dialect="pymysql", username="root") as container:
        yield container.get_connection_url()


@pytest.fixture
def client(database: str) -> Iterator[TestClient]:
    from .api import create_app

    # The container's url carries its login; the settings keep it apart, as a deployment does.
    url, username, password = split_login(database)
    app = create_app(Settings(url=url, username=username, password=password, job_workers=3))
    # `with TestClient(...)` is what runs the lifespan - and therefore the migrations, the
    # pools and the scheduler. Without the `with`, none of that happens and every test here
    # would fail against an empty database.
    with TestClient(app) as running:
        yield running


def open_wallet(client: TestClient, balance: str = "100.00") -> str:
    created = client.post(
        "/wallets",
        json={"owner_id": OWNER, "currency": "EUR", "opening_balance": balance},
    )
    assert created.status_code == 201, created.text
    wallet_id: str = created.json()["id"]
    return wallet_id


def test_the_service_comes_up_and_answers(client: TestClient) -> None:
    """Which also proves the migrations ran: nothing below works without them."""
    assert client.get("/health").json() == {"status": "ok"}


def test_opening_a_wallet_returns_it(client: TestClient) -> None:
    body = client.post(
        "/wallets",
        json={"owner_id": OWNER, "currency": "EUR", "opening_balance": "100.00"},
    ).json()

    assert body["balance"] == "100.00", "money on the wire is quantized by the DTO"
    assert body["currency"] == "EUR"
    assert body["state"] == "OPENED"
    assert "version" not in body, "the response is a DTO, not the model"


def test_a_deposit_moves_the_balance(client: TestClient) -> None:
    wallet_id = open_wallet(client)

    body = client.post(
        f"/wallets/{wallet_id}/deposits",
        json={"amount": "25.50", "recipient": "ada@example.com"},
    ).json()

    assert body["balance"] == "125.50"
    assert client.get(f"/wallets/{wallet_id}").json()["balance"] == "125.50", (
        "and a read agrees with the write, which is the point of quantizing in one place"
    )


def test_the_deposit_event_reaches_the_handler_and_writes_an_alert(client: TestClient) -> None:
    """The listen-to-yourself loop, end to end and through HTTP.

    Nothing in the deposit endpoint mentions alerts. The deposit emits an event, the fan-out
    job turns it into a delivery, the handling job runs the handler, and the handler starts its
    own unit of work. Polled rather than asserted immediately because those are real background
    jobs on a real schedule - Java's examples use Awaitility here for the same reason.
    """
    wallet_id = open_wallet(client)
    client.post(
        f"/wallets/{wallet_id}/deposits",
        json={"amount": "10.00", "recipient": "ada@example.com"},
    )

    deadline = time.monotonic() + 30
    alerts: list[dict[str, str]] = []
    while time.monotonic() < deadline:
        alerts = client.get(f"/wallets/{wallet_id}/alerts").json()
        if alerts:
            break
        time.sleep(0.2)

    assert alerts, "the handler never ran; fan-out or dispatch is not running"
    assert alerts[0]["recipient"] == "ada@example.com", "read off the action's parameters"
    assert "10.00" in alerts[0]["message"]


def test_a_missing_wallet_is_a_404_not_a_500(client: TestClient) -> None:
    assert client.get(f"/wallets/{uuid4()}").status_code == 404


def test_a_bad_deposit_is_a_400(client: TestClient) -> None:
    """The domain refuses it, and the refusal reaches the caller as their mistake."""
    wallet_id = open_wallet(client)

    # Pydantic rejects a non-positive amount before the domain sees it; 422 is FastAPI's own
    # validation status. Both are "your request", which is the distinction that matters.
    assert (
        client.post(
            f"/wallets/{wallet_id}/deposits", json={"amount": "-1.00", "recipient": "a@b.com"}
        ).status_code
        == 422
    )


def test_closing_a_wallet_twice_is_not_an_error(client: TestClient) -> None:
    """Closing a closed wallet changes nothing, so it emits nothing - and is still a 200."""
    wallet_id = open_wallet(client)

    first = client.post(f"/wallets/{wallet_id}/closure")
    second = client.post(f"/wallets/{wallet_id}/closure")

    assert first.json()["state"] == "CLOSED"
    assert second.status_code == 200
    assert second.json()["state"] == "CLOSED"


def test_concurrent_deposits_are_serialised_by_the_keyed_lock(client: TestClient) -> None:
    """The pattern this example exists to show.

    Optimistic locking alone would be correct here - a concurrent deposit loses the version
    check and raises - but correct by *failing*. Taking the wallet's key first means the second
    caller waits and then succeeds. So every deposit must land, and the balance must be exact.
    """
    from concurrent.futures import ThreadPoolExecutor

    wallet_id = open_wallet(client, "0.00")
    deposits = 8

    def deposit(_: int) -> int:
        response = client.post(
            f"/wallets/{wallet_id}/deposits",
            json={"amount": "1.00", "recipient": "ada@example.com"},
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=deposits) as pool:
        codes = list(pool.map(deposit, range(deposits)))

    assert codes == [200] * deposits, "the lock made every caller wait rather than lose"
    assert client.get(f"/wallets/{wallet_id}").json()["balance"] == "8.00"


def test_it_runs_on_the_database_this_example_is_named_for(client: TestClient) -> None:
    """The one assertion that stops this project quietly becoming a copy of another.

    Three of these differ only in url, lock provider and migration DDL, so a wrong url is
    invisible - every other test here would still pass. The framework picks its keyed-lock
    provider off `engine.dialect.name`, so getting this wrong means the mysql example
    exercises somebody else's lock.

    It is a real trap rather than a hypothetical one: `MySqlContainer` hands back a
    `mysql+pymysql` url even when it is running MariaDB, and the framework's own suite tested
    the MySQL paths under the name "mariadb" for a while before anyone noticed.
    """
    service = client.app.state.service

    assert service.registry.dialect == "mysql"
    assert type(service.locks).__name__ == "MySQLKeyedLockProvider"


def test_the_migration_files_pass_the_checks_a_deploy_runs():
    """No database needed, so CI catches it: two files with one version, a file whose name would
    be skipped without a word, a rollback file. Each is refused here rather than at startup."""
    assert [script.script for script in validate_scripts(MIGRATIONS)] == [
        "V0001__wallet_schema.sql"
    ]
