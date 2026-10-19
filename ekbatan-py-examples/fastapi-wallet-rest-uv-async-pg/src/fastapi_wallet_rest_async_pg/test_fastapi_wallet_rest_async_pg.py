"""The example's own test, against a real Postgres.

Lives with the example, runs separately from the framework's suite, and exists for one reason:
an example nobody runs is an example that rots. It asserts what the README claims, so a change
that makes the prose false makes this red.

    make

It drives the REST endpoints against a database in a container, started by Testcontainers.
The app's async objects live on the event loop the test client runs the app on; a test that
reaches past HTTP into the service awaits on that same loop, through `client.portal`.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

from ekbatan.config import split_login
from ekbatan.ids import ShardedId
from ekbatan.migrations import validate_scripts

from .app import MIGRATIONS, Settings, WalletService
from .model import Currency, EmailAddress, Wallet
from .schema import EVENTS

OWNER = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        # A `postgresql+psycopg://` url, as `ekbatan.yaml` holds one: the sync pools use it as
        # it is, the async registry connects to the same database through asyncpg.
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

    app = create_app(Settings(environ=environ_for(database), job_workers=3))
    # `with` runs the lifespan: the migrations, the pools and the scheduler.
    with TestClient(app) as running:
        yield running


def service(client: TestClient) -> WalletService:
    found: WalletService = client.app.state.service  # type: ignore[attr-defined]
    return found


def on_the_app_loop[T](client: TestClient, work: Callable[[], Awaitable[T]]) -> T:
    """Await `work` on the loop the app runs on - where its async pools live."""
    assert client.portal is not None
    return client.portal.call(work)


def scalar(client: TestClient, statement: Any, **params: Any) -> Any:
    """One value, read on the request pool - asyncpg - inside the app's loop."""

    async def read() -> Any:
        async with service(client).registry.default.connection() as conn:
            return (await conn.execute(statement, params)).scalar_one()

    return on_the_app_loop(client, read)


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
    assert client.get(f"/wallets/{wallet_id}").json()["balance"] == "125.50"


def test_the_deposit_event_reaches_the_async_handler_and_writes_an_alert(
    client: TestClient,
) -> None:
    """The async request commits the deposit and its event; after it, fan-out and dispatch - on
    their own threads - run the async handler on the handler loop, and it awaits its own unit of
    work. Polled, because those are real background jobs on a real schedule."""
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


def test_the_currency_is_a_currency_in_python_and_text_in_the_database(
    client: TestClient,
) -> None:
    """The forced type that picks every `CHAR(3)` column, end to end, on asyncpg."""
    wallet_id = open_wallet(client)
    wallets = service(client).wallets

    wallet = on_the_app_loop(client, lambda: wallets.get(ShardedId[Wallet](UUID(wallet_id))))
    assert wallet.currency == Currency("EUR"), "read back through CurrencyConverter"
    stored = scalar(client, text("SELECT currency FROM wallets WHERE id = :id"), id=UUID(wallet_id))
    opened = scalar(
        client,
        select(EVENTS.c.payload).where(
            EVENTS.c.model_id == wallet_id, EVENTS.c.event_type == "WalletOpenedEvent"
        ),
    )
    assert stored == "EUR", "the column holds the plain code"
    assert opened["currency"] == "EUR", "and so does the event"
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
    alerts_of = service(client).alerts

    deadline = time.monotonic() + 30
    alerts: list[Any] = []
    while not alerts and time.monotonic() < deadline:
        alerts = on_the_app_loop(
            client, lambda: alerts_of.find_for_wallet(ShardedId[Wallet](UUID(wallet_id)))
        )
        time.sleep(0.2)

    assert alerts, "the handler never ran"
    assert alerts[0].recipient == EmailAddress("ada@example.com"), "read back as an EmailAddress"
    stored = scalar(
        client, text("SELECT recipient FROM alerts WHERE wallet_id = :id"), id=UUID(wallet_id)
    )
    assert stored == "ada@example.com", "the column holds the plain address"
    assert client.get(f"/wallets/{wallet_id}/alerts").json()[0]["recipient"] == "ada@example.com"


def test_a_deposit_for_a_bad_address_is_a_400_and_moves_nothing(client: TestClient) -> None:
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
    wallet_id = open_wallet(client)

    first = client.post(f"/wallets/{wallet_id}/closure")
    second = client.post(f"/wallets/{wallet_id}/closure")

    assert first.json()["state"] == "CLOSED"
    assert second.status_code == 200
    assert second.json()["state"] == "CLOSED"


def test_concurrent_deposits_are_serialised_by_the_keyed_lock(client: TestClient) -> None:
    """The async keyed lock around the unit of work: eight deposits at once on one wallet, each
    request's task waiting its turn on the event loop. Every one lands - none is a 409 - and
    the balance is exact."""
    wallet_id = open_wallet(client, "0.00")

    def deposit(_: int) -> int:
        response = client.post(
            f"/wallets/{wallet_id}/deposits",
            json={"amount": "1.00", "recipient": "ada@example.com"},
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(deposit, range(8)))

    assert codes == [200] * 8, "the lock made every caller wait rather than lose"
    assert client.get(f"/wallets/{wallet_id}").json()["balance"] == "8.00"


def test_many_requests_at_once_each_commit(client: TestClient) -> None:
    """Thirty opens at once, more than the ten-connection pool: they queue for a connection on
    the event loop and every one commits."""
    with ThreadPoolExecutor(max_workers=30) as pool:
        created = list(pool.map(lambda _: open_wallet(client, "1.00"), range(30)))

    assert len(set(created)) == 30


def test_requests_and_the_handler_run_on_asyncpg_and_the_jobs_on_psycopg(
    client: TestClient,
) -> None:
    """One url in `ekbatan.yaml`, two drivers: the async default for the requests, the lock and
    the handler, the sync driver the url names for what polls."""
    running = service(client)

    assert running.registry.dialect == "postgres"
    assert running.registry.default.primary.url.get_driver_name() == "asyncpg"
    assert running.lock_engine.url.get_driver_name() == "asyncpg"
    assert running.handler_registry.default.primary.url.get_driver_name() == "asyncpg"
    assert running.events_registry.default.primary.url.get_driver_name() == "psycopg"
    assert type(running.locks).__name__ == "AsyncPostgresKeyedLockProvider"


def test_the_service_runs_on_the_settings_in_ekbatan_yaml(client: TestClient) -> None:
    """The namespace, the four pools and the driver settings all come from `ekbatan.yaml` -
    including the setting that puts each session on UTC, on every pool and both drivers. The
    handler's pool is asked on the handler loop, where it lives."""
    running = service(client)
    requests = running.registry.default.primary.sync_engine.pool
    locks = running.lock_engine.sync_engine.pool
    handlers = running.handler_registry.default.primary.sync_engine.pool

    assert running.executor.namespace == "example.fastapi_wallet_rest_async_pg"
    sizes = (requests.size(), locks.size(), handlers.size(), running.jobs_engine.pool.size())  # type: ignore[attr-defined]
    assert sizes == (10, 5, 5, 8)
    assert scalar(client, text("SELECT current_setting('TimeZone')")) == "UTC"

    async def zone_on(engine: Any) -> Any:
        async with engine.connect() as connection:
            return (await connection.execute(text("SELECT current_setting('TimeZone')"))).scalar()

    assert on_the_app_loop(client, lambda: zone_on(running.lock_engine)) == "UTC"
    handler_engine = running.handler_registry.default.primary
    assert running.handler_loop.run(zone_on(handler_engine)) == "UTC"
    with running.jobs_engine.connect() as connection:
        assert connection.execute(text("SELECT current_setting('TimeZone')")).scalar() == "UTC"


def test_the_stipend_job_pays_every_open_wallet(client: TestClient) -> None:
    """The stipend ran at startup and is next due in a day. A scheduler whose clock says a day
    has passed picks the same row from `scheduled_tasks` and runs it - once. The job is sync;
    its work is a coroutine, run on the handler loop's thread - recorded from inside it."""
    from datetime import timedelta

    from db_scheduler.clock import SettableClock

    from ekbatan.clock import SystemClock
    from ekbatan.distributedjobs import job_registry

    from .jobs import PayStipendJob

    running = service(client)
    stipend_has_run(running.jobs_engine)
    wallet_id = open_wallet(client, "0.00")

    job = PayStipendJob(running.handler_loop, running.handler_executor, running.handler_wallets)
    seen: list[str] = []
    pay = job.pay

    async def recorded() -> None:
        seen.append(threading.current_thread().name)
        await pay()

    job.pay = recorded  # type: ignore[method-assign]
    a_day_later = SettableClock(SystemClock().now() + timedelta(days=1, seconds=1))
    stipend_only = (
        job_registry().engine(running.jobs_engine).with_jobs([job]).build_manual(a_day_later)
    )
    stipend_only.start()

    stipend_only.scheduler.run_any_due_executions()
    assert client.get(f"/wallets/{wallet_id}").json()["balance"] == "1.00"
    assert seen == ["ekbatan-handler-loop"], "the work ran on the handler loop"
    stipend_only.scheduler.run_any_due_executions()  # its slot rescheduled; nothing due
    assert client.get(f"/wallets/{wallet_id}").json()["balance"] == "1.00"


def test_every_pool_runs_in_utc_even_on_a_server_that_does_not(database: str) -> None:
    """The third part of the UTC rule: each session is put on UTC by `ekbatan.yaml`
    (`options: -c TimeZone=UTC` on every pool), not by the server's default. The database's own
    default is moved to Tehran first: a plain connection then reports Tehran - the control - and
    every pool the service builds reports UTC: the requests', the lock's and the handler's on
    asyncpg, the jobs' on psycopg."""
    from .app import build

    async def zone_on(engine: Any) -> Any:
        async with engine.connect() as connection:
            return (await connection.execute(text("SELECT current_setting('TimeZone')"))).scalar()

    def sync_zone_on(engine: Any) -> Any:
        with engine.connect() as connection:
            return connection.execute(text("SELECT current_setting('TimeZone')")).scalar()

    async def every_pool(service: WalletService) -> list[Any]:
        # Each async pool is asked on the loop it belongs to - the handler's on its own - and
        # the service is closed on this one, where the requests' and the lock's pools were used.
        try:
            return [
                await zone_on(service.registry.default.primary),
                await zone_on(service.lock_engine),
                await asyncio.to_thread(
                    service.handler_loop.run, zone_on(service.handler_registry.default.primary)
                ),
                await asyncio.to_thread(sync_zone_on, service.jobs_engine),
            ]
        finally:
            await service.aclose()

    name = make_url(database).database
    admin = create_engine(database, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as connection:
        connection.execute(text(f"ALTER DATABASE {name} SET TimeZone = 'Asia/Tehran'"))
    try:
        with admin.connect() as connection:
            control = connection.execute(text("SELECT current_setting('TimeZone')")).scalar()
        assert control == "Asia/Tehran", "the control: off UTC without the setting"

        service = build(Settings(environ=environ_for(database)))
        assert asyncio.run(every_pool(service)) == ["UTC"] * 4
    finally:
        with admin.connect() as connection:
            connection.execute(text(f"ALTER DATABASE {name} RESET TimeZone"))
        admin.dispose()


def test_the_migration_files_pass_the_checks_a_deploy_runs() -> None:
    assert [script.script for script in validate_scripts(MIGRATIONS)] == [
        "V0001__eventlog.sql",
        "V0002__scheduled_tasks.sql",
        "V0003__wallets.sql",
        "V0004__alerts.sql",
    ]
