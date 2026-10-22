"""The example's own test, against a real MySQL.

Lives with the example, runs separately from the framework's suite, and exists for one reason:
an example nobody runs is an example that rots. It asserts what the README claims, so a change
that makes the prose false makes this red.

    make

It drives the REST endpoints against a database in a container, started by Testcontainers. A
real MySQL server rather than SQLite, because this example is about what a deployment looks
like: migrations from files, a keyed lock taken on a real server-side lock, three separate
pools. None of those are exercised honestly by SQLite.

"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import NullPool
from testcontainers.community.mysql import MySqlContainer

from ekbatan.config import split_login
from ekbatan.ids import ShardedId
from ekbatan.migrations import validate_scripts
from ekbatan.sharding import DEFAULT_SHARD

from .app import MIGRATIONS, Settings
from .model import Currency, EmailAddress, Wallet
from .schema import EVENTS

OWNER = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    # `dialect=` is the *driver* only - testcontainers prefixes `mysql+` itself and, since 4.15,
    # refuses a value that already carries the prefix. `username="root"` because the default
    # `test` user has rights on the `test` schema alone, and the migration creates tables.
    with MySqlContainer("mysql:8.4", dialect="pymysql", username="root") as container:
        yield container.get_connection_url()


def environ_for(database: str) -> dict[str, str]:
    """The placeholders `ekbatan.yaml` reads, filled from the container: its url and login."""
    url, username, password = split_login(database)
    return {
        "WALLET_DATABASE_URL": url,
        "WALLET_DATABASE_USERNAME": username or "",
        "WALLET_DATABASE_PASSWORD": password or "",
    }


@pytest.fixture
def client(database: str) -> Iterator[TestClient]:
    from .api import create_app

    # The container's url carries its login; `ekbatan.yaml` keeps it apart, as a deployment does,
    # and reads all three from the placeholders filled here.
    app = create_app(Settings(environ=environ_for(database), job_workers=3))
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


def stipend_has_run(jobs_engine: Any, within: float = 15.0) -> None:
    """Wait for the stipend's run at startup: a `FixedDelay` job is due the moment it is
    registered, and its row records the run once it is over."""
    deadline = time.monotonic() + within
    query = text("SELECT last_success FROM scheduled_tasks WHERE task_name = 'wallet-stipend'")
    while True:
        with jobs_engine.connect() as connection:
            if connection.execute(query).scalar_one_or_none() is not None:
                return
        if time.monotonic() > deadline:
            raise AssertionError("the stipend's run at startup did not finish")
        time.sleep(0.1)


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
    jobs on a real schedule, and a background job finishes when it finishes.
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
    # "10.0", not "10.00": the payload carries the amount as a JSON number, and MySQL's JSON
    # column keeps a number with a fraction as a double. A Java service reading this row (see
    # the website's "Running beside a Java service") sees the same.
    assert alerts[0]["message"].startswith("10.0 received")


def test_the_currency_is_a_currency_in_python_and_text_in_the_database(
    client: TestClient,
) -> None:
    """The forced type that picks every `CHAR(3)` column, end to end: the repository reads a
    `Currency`, the table holds `EUR`, and the outbox and the API both say `EUR`."""
    wallet_id = open_wallet(client)
    service = client.app.state.service

    wallet = service.wallets.get(ShardedId[Wallet](UUID(wallet_id)))
    assert wallet.currency == Currency("EUR"), "read back through CurrencyConverter"
    with service.registry.manager(DEFAULT_SHARD).primary.connect() as connection:
        stored = connection.execute(
            text("SELECT currency FROM wallets WHERE id = :id"), {"id": wallet_id}
        ).scalar_one()
        opened = connection.execute(
            select(EVENTS.c.payload).where(
                EVENTS.c.model_id == wallet_id, EVENTS.c.event_type == "WalletOpenedEvent"
            )
        ).scalar_one()
    assert stored == "EUR", "the column holds the plain code"
    assert opened["currency"] == "EUR", "and so does the event"
    # The event's time is an Instant: the wallet's created_date, UTC digits, with Java's `Z`.
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{3}|\.\d{6})?Z", opened["openedAt"])
    assert opened["openedAt"].startswith(wallet.created_date.isoformat(timespec="seconds"))
    assert client.get(f"/wallets/{wallet_id}").json()["currency"] == "EUR"


def test_a_currency_that_is_not_three_capitals_is_a_400(client: TestClient) -> None:
    refused = client.post(
        "/wallets", json={"owner_id": OWNER, "currency": "eur", "opening_balance": "1.00"}
    )
    assert refused.status_code == 400, refused.text
    assert "three capital letters" in refused.json()["detail"]


def test_the_recipient_is_an_email_address_in_python_and_text_in_the_database(
    client: TestClient,
) -> None:
    """The forced type that picks `alerts.recipient` by name, end to end."""
    wallet_id = open_wallet(client)
    client.post(
        f"/wallets/{wallet_id}/deposits", json={"amount": "1.00", "recipient": "ada@example.com"}
    )
    service = client.app.state.service

    deadline = time.monotonic() + 30
    alerts = []
    while not alerts and time.monotonic() < deadline:
        alerts = service.alerts.find_for_wallet(ShardedId[Wallet](UUID(wallet_id)))
        time.sleep(0.2)

    assert alerts, "the handler never ran"
    assert alerts[0].recipient == EmailAddress("ada@example.com"), "read back as an EmailAddress"
    with service.registry.manager(DEFAULT_SHARD).primary.connect() as connection:
        stored = connection.execute(
            text("SELECT recipient FROM alerts WHERE wallet_id = :id"), {"id": wallet_id}
        ).scalar_one()
    assert stored == "ada@example.com", "the column holds the plain address"
    assert client.get(f"/wallets/{wallet_id}/alerts").json()[0]["recipient"] == "ada@example.com"


def test_a_deposit_for_a_bad_address_is_a_400_and_moves_nothing(client: TestClient) -> None:
    """Refused at the deposit, not later in the handler where it would fail on every retry."""
    wallet_id = open_wallet(client)

    refused = client.post(
        f"/wallets/{wallet_id}/deposits", json={"amount": "1.00", "recipient": "nobody"}
    )

    assert refused.status_code == 400, refused.text
    assert client.get(f"/wallets/{wallet_id}").json()["balance"] == "100.00"


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


def test_the_service_runs_on_the_settings_in_ekbatan_yaml(client: TestClient) -> None:
    """`ekbatan.yaml` is the service's Ekbatan config: the namespace, the three pools and the
    driver setting all come from it. (The handling job it turns on is proven by the deposit
    tests above: without it no alert is ever raised.)"""
    service = client.app.state.service
    primary = service.registry.manager(DEFAULT_SHARD).primary

    assert service.executor.namespace == "example.fastapi_wallet_rest_mysql"
    assert (primary.pool.size(), service.jobs_engine.pool.size()) == (10, 8)
    assert service.lock_engine.pool.size() == 5
    with primary.connect() as connection:
        # `init_command` under `dataSourceProperties`, reaching the server.
        assert connection.execute(text("SELECT @@SESSION.innodb_lock_wait_timeout")).scalar() == 10


def test_the_stipend_job_pays_every_open_wallet(client: TestClient) -> None:
    """The stipend ran at startup and is next due in a day. A scheduler whose clock says a day
    has passed picks the same row from `scheduled_tasks` and runs it - once."""
    from datetime import timedelta

    from db_scheduler.clock import SettableClock

    from ekbatan.clock import SystemClock
    from ekbatan.distributedjobs import job_registry

    from .jobs import PayStipendJob

    service = client.app.state.service
    stipend_has_run(service.jobs_engine)
    wallet_id = open_wallet(client, "0.00")

    a_day_later = SettableClock(SystemClock().instant() + timedelta(days=1, seconds=1))
    stipend_only = (
        job_registry()
        .engine(service.jobs_engine)
        .with_jobs([PayStipendJob(service.executor, service.wallets)])
        .build_manual(a_day_later)
    )
    stipend_only.start()

    stipend_only.scheduler.run_any_due_executions()
    assert client.get(f"/wallets/{wallet_id}").json()["balance"] == "1.00"
    stipend_only.scheduler.run_any_due_executions()  # its slot rescheduled; nothing due
    assert client.get(f"/wallets/{wallet_id}").json()["balance"] == "1.00"


def test_every_pool_runs_in_utc_even_on_a_server_that_does_not(database: str) -> None:
    """The third part of the UTC rule: each session is put on UTC by `ekbatan.yaml`
    (`time_zone = '+00:00'` in every pool's `init_command`), not by the server's default. The
    server's own default is moved to Tehran's offset first: a plain connection then reports it -
    the control - and every pool the service builds reports UTC."""
    from .app import build

    admin = create_engine(database, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as connection:
        previous = connection.execute(text("SELECT @@global.time_zone")).scalar()
        connection.execute(text("SET GLOBAL time_zone = '+03:30'"))
    try:
        with admin.connect() as connection:
            control = connection.execute(text("SELECT @@session.time_zone")).scalar()
        assert control == "+03:30", "the control: off UTC without the setting"

        service = build(Settings(environ=environ_for(database)))
        try:
            pools = [
                service.registry.manager(DEFAULT_SHARD).primary,
                service.jobs_engine,
                service.lock_engine,
            ]
            for pool in pools:
                with pool.connect() as connection:
                    zone = connection.execute(text("SELECT @@session.time_zone")).scalar()
                    assert zone == "+00:00", pool
        finally:
            service.close()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f"SET GLOBAL time_zone = '{previous}'"))
        admin.dispose()


def test_a_request_that_waits_out_the_pool_is_a_503(client: TestClient) -> None:
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
    assert [script.script for script in validate_scripts(MIGRATIONS)] == [
        "V0001__eventlog.sql",
        "V0002__scheduled_tasks.sql",
        "V0003__wallets.sql",
        "V0004__alerts.sql",
    ]
