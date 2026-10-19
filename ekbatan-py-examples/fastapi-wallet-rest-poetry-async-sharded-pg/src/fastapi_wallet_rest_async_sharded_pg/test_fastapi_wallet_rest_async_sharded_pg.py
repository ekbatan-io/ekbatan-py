"""Routing, isolation, and the refusal that keeps "all or nothing" honest - on `ekbatan.aio`.

    make

Two databases, not two schemas and not one database pretending. That matters here more than
anywhere else in the examples: a test that shared a database would pass with the routing
removed, which would make it worse than no test at all.

They are two databases on one Postgres *server*, which is a different thing - the framework
opens two pools against two urls and cannot tell that one process is behind both. Two servers
would prove nothing further and cost a second container.

The service's async pools live on the loop they are used on. A test that drives the service
directly awaits on one loop of its own (`run`, a `HandlerLoop`: an event loop on a thread, and a
blocking way to run work on it); a test through HTTP awaits on the app's loop, `client.portal`.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from functools import partial
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from ekbatan import DatabaseRegistry
from ekbatan.config import split_login
from ekbatan.errors import CrossShardError
from ekbatan.events import HandlerLoop
from ekbatan.ids import ShardedId
from ekbatan.migrations import validate_scripts

from .actions import (
    GLOBAL,
    MEXICO,
    DepositMoneyAction,
    DepositParams,
    OpenWalletAction,
    OpenWalletParams,
    TransferParams,
    TransferWithinShardAction,
)
from .app import MIGRATIONS, ShardedWallets, build
from .model import Currency, EmailAddress, Wallet

TABLES = ("wallets", "events", "event_notifications", "alerts")


@pytest.fixture(scope="module")
def environ() -> Iterator[dict[str, str]]:
    """What fills `ekbatan.yaml`'s placeholders: one url per shard, and the login."""
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        base = container.get_connection_url()
        admin = create_engine(base, isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as conn:
                for name in ("shard_global", "shard_mexico"):
                    conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
                    conn.execute(text(f"CREATE DATABASE {name}"))
        finally:
            admin.dispose()

        url, username, password = split_login(base)
        head, _, _ = url.rpartition("/")
        yield {
            "WALLET_GLOBAL_URL": f"{head}/shard_global",
            "WALLET_MEXICO_URL": f"{head}/shard_mexico",
            "WALLET_DATABASE_USERNAME": username or "",
            "WALLET_DATABASE_PASSWORD": password or "",
        }


class Run:
    """Await work on the loop the service's async pools belong to, from a plain test."""

    def __init__(self, loop: HandlerLoop) -> None:
        self.loop = loop

    def __call__[T](self, work: Callable[[], Awaitable[T]]) -> T:
        async def awaited() -> T:
            return await work()

        return self.loop.run(awaited())


@pytest.fixture(scope="module")
def run() -> Iterator[Run]:
    loop = HandlerLoop(name="test-loop")
    try:
        yield Run(loop)
    finally:
        loop.close()


def emptied(registry: DatabaseRegistry) -> None:
    """Every shard's tables, emptied through the sync registry fan-out and dispatch use."""
    for shard in registry.shards:
        with registry.manager(shard).connection() as conn:
            for table in TABLES:
                conn.execute(text(f"DELETE FROM {table}"))


@pytest.fixture
def service(environ: dict[str, str], run: Run) -> Iterator[ShardedWallets]:
    built = build(environ)
    try:
        emptied(built.events_registry)
        yield built
    finally:
        run(built.aclose)


def a_wallet(
    service: ShardedWallets, run: Run, country: str = "GL", balance: str = "100.00"
) -> Wallet:
    return run(
        lambda: service.executor.execute(
            OpenWalletAction,
            OpenWalletParams(
                owner_id=uuid4(),
                currency="EUR",
                opening_balance=Decimal(balance),
                country=country,
            ),
        )
    )


def test_the_country_decides_the_database_and_the_id_remembers_it(
    service: ShardedWallets, run: Run
) -> None:
    """The one place a shard is chosen, and the last place anything has to know."""
    mexican = a_wallet(service, run, "MX")
    other = a_wallet(service, run, "GL")

    assert mexican.id.shard == MEXICO
    assert other.id.shard == GLOBAL


def test_a_row_is_only_on_its_own_database(service: ShardedWallets, run: Run) -> None:
    """Isolation, checked from the outside rather than through the framework - if routing
    stopped working, going through the repository would hide it."""
    mexican = a_wallet(service, run, "MX")

    assert [w.id for w in run(lambda: service.wallets.on_shard(MEXICO))] == [mexican.id]
    assert run(lambda: service.wallets.on_shard(GLOBAL)) == []


def test_a_read_by_id_needs_no_hint(service: ShardedWallets, run: Run) -> None:
    """No lookup table, no tenant column, no argument saying where to look."""
    mexican = a_wallet(service, run, "MX")

    assert run(lambda: service.wallets.get(mexican.id)).id == mexican.id
    assert run(lambda: service.wallets.find(mexican.id)) is not None


def test_an_action_never_names_a_shard(service: ShardedWallets, run: Run) -> None:
    """`DepositMoneyAction` takes an id and nothing else. That is what stops business logic from
    having to change when the layout does."""
    mexican = a_wallet(service, run, "MX", "10.00")

    after = run(
        lambda: service.executor.execute(
            DepositMoneyAction,
            DepositParams(wallet_id=mexican.id, amount=Decimal("5.00"), recipient="mx@example.com"),
        )
    )

    assert after.balance == Decimal("15.00")
    assert run(lambda: service.wallets.get(mexican.id)).balance == Decimal("15.0000")


def test_a_query_without_an_id_asks_every_shard(service: ShardedWallets, run: Run) -> None:
    """And the cost is one query per database - asked at the same time on `ekbatan.aio` - which
    is why a question asked often should be answerable from one."""
    owner = uuid4()
    for country in ("MX", "GL"):
        params = OpenWalletParams(
            owner_id=owner, currency="EUR", opening_balance=Decimal("1.00"), country=country
        )
        run(partial(service.executor.execute, OpenWalletAction, params))

    found = run(lambda: service.wallets.find_all_by_owner(owner))

    assert len(found) == 2
    assert {w.id.shard for w in found} == {GLOBAL, MEXICO}


def test_each_shard_keeps_its_own_outbox(service: ShardedWallets, run: Run) -> None:
    """A row and its events commit together, so the events land on the row's database - which
    is what lets one CDC connector per shard read them independently."""
    a_wallet(service, run, "MX")

    async def counts() -> dict[Any, int]:
        found = {}
        for shard in service.registry.shards:
            async with service.registry.manager(shard).connection(readonly=True) as conn:
                found[shard] = (
                    await conn.execute(text("SELECT count(*) FROM events"))
                ).scalar_one()
        return found

    counted = run(counts)
    assert counted[MEXICO] == 1
    assert counted[GLOBAL] == 0


def test_two_wallets_on_one_shard_move_together(service: ShardedWallets, run: Run) -> None:
    source = a_wallet(service, run, "MX", "50.00")
    destination = a_wallet(service, run, "MX", "0.00")

    run(
        lambda: service.executor.execute(
            TransferWithinShardAction,
            TransferParams(
                from_wallet_id=source.id, to_wallet_id=destination.id, amount=Decimal("20.00")
            ),
        )
    )

    assert run(lambda: service.wallets.get(source.id)).balance == Decimal("30.0000")
    assert run(lambda: service.wallets.get(destination.id)).balance == Decimal("20.0000")


def test_reaching_a_second_shard_is_refused_rather_than_half_committed(
    service: ShardedWallets, run: Run
) -> None:
    """The refusal this design depends on.

    There is no two-phase commit here. Committing each shard separately would turn "all or
    nothing" into a lie the first time an action reached two databases by accident, so the
    framework stops instead - and says which two disagreed.

    Moving money between shards is a saga, not a transaction. See
    `fastapi-wallet-saga-uv-async-pg`.
    """
    source = a_wallet(service, run, "MX", "50.00")
    destination = a_wallet(service, run, "GL", "0.00")

    with pytest.raises(CrossShardError) as refused:
        run(
            lambda: service.executor.execute(
                TransferWithinShardAction,
                TransferParams(
                    from_wallet_id=source.id, to_wallet_id=destination.id, amount=Decimal("20.00")
                ),
            )
        )

    # The pair, not which is which: *which* shard the unit of work settles on first follows
    # the order the persister writes the types in, and that is not promised. What is promised
    # is that the two disagreeing shards are both named.
    assert {refused.value.active_shard, refused.value.requested_shard} == {GLOBAL, MEXICO}
    assert run(lambda: service.wallets.get(source.id)).balance == Decimal("50.0000"), (
        "nothing was written"
    )
    assert run(lambda: service.wallets.get(destination.id)).balance == Decimal("0.0000")


def test_the_currency_is_a_currency_in_python_and_text_on_its_database(
    service: ShardedWallets, run: Run
) -> None:
    """The forced type that picks every `CHAR(3)` column, on the shard the wallet lives on."""
    mexican = a_wallet(service, run, "MX")

    assert run(lambda: service.wallets.get(mexican.id)).currency == Currency("EUR")

    async def stored() -> Any:
        async with service.registry.manager(MEXICO).connection(readonly=True) as conn:
            return (await conn.execute(text("SELECT currency FROM wallets"))).scalar_one()

    assert run(stored) == "EUR", "the column holds the plain code"


def test_a_per_shard_total_is_a_question_about_databases(service: ShardedWallets, run: Run) -> None:
    a_wallet(service, run, "MX", "10.00")
    a_wallet(service, run, "GL", "25.00")

    assert run(service.wallets.total_per_shard) == {
        GLOBAL: Decimal("25.0000"),
        MEXICO: Decimal("10.0000"),
    }


# -- the same claims, through HTTP -------------------------------------------------------------
#
# The endpoints are where routing is easiest to check: a client hands over an id and nothing
# else, and the right row still comes back.


@pytest.fixture
def client(environ: dict[str, str]) -> Iterator[TestClient]:
    from .api import create_app

    with TestClient(create_app(environ)) as running:
        emptied(running.app.state.service.events_registry)  # type: ignore[attr-defined]
        yield running


def on_the_app_loop[T](client: TestClient, work: Callable[[], Awaitable[T]]) -> T:
    """Await `work` on the loop the app runs on - where its async pools live."""
    assert client.portal is not None
    return client.portal.call(work)


def open_via_http(client: TestClient, country: str, balance: str = "100.00") -> dict[str, str]:
    created = client.post(
        "/wallets",
        json={
            "owner_id": str(uuid4()),
            "currency": "EUR",
            "opening_balance": balance,
            "country": country,
        },
    )
    assert created.status_code == 201, created.text
    body: dict[str, str] = created.json()
    return body


def test_the_country_on_the_request_decides_the_database(client: TestClient) -> None:
    assert open_via_http(client, "MX")["shard"] == "1:0"
    assert open_via_http(client, "GL")["shard"] == "0:0"


def test_a_client_reads_a_wallet_back_with_nothing_but_its_id(client: TestClient) -> None:
    """No shard in the path, no query parameter, no lookup. This is the whole claim."""
    mexican = open_via_http(client, "MX")

    read = client.get(f"/wallets/{mexican['id']}")

    assert read.status_code == 200
    assert read.json()["shard"] == "1:0"


def test_a_deposit_endpoint_names_no_database(client: TestClient) -> None:
    mexican = open_via_http(client, "MX", "10.00")

    after = client.post(
        f"/wallets/{mexican['id']}/deposits",
        json={"amount": "5.00", "recipient": "mexico@example.com"},
    )

    assert after.json()["balance"] == "15.00"


def test_a_deposit_on_any_shard_reaches_the_async_handler(client: TestClient) -> None:
    """The deposit commits on its wallet's shard; fan-out and dispatch - one scheduler for every
    shard, on threads - run the async handler on the handler loop, and it writes an alert.
    Waited for up to 15 seconds; the jobs run in the background."""
    global_wallet = open_via_http(client, "GL", "10.00")
    mexican = open_via_http(client, "MX", "20.00")
    for wallet, amount, recipient in (
        (global_wallet, "5.00", "global@example.com"),
        (mexican, "7.00", "mexico@example.com"),
    ):
        deposited = client.post(
            f"/wallets/{wallet['id']}/deposits", json={"amount": amount, "recipient": recipient}
        )
        assert deposited.status_code == 200, deposited.text

    deadline = time.monotonic() + 15
    alerts: list[dict[str, str]] = []
    while not alerts and time.monotonic() < deadline:
        alerts = client.get(f"/wallets/{mexican['id']}/alerts").json()
        time.sleep(0.2)

    assert len(alerts) == 1, "the handler never ran; fan-out or handling is not running"
    assert alerts[0]["recipient"] == "mexico@example.com"
    # The forced type that picks `alerts.recipient` by name: an EmailAddress in Python, the
    # plain address in the table. Alerts all live on the default shard.
    service: ShardedWallets = client.app.state.service  # type: ignore[attr-defined]
    [alert] = on_the_app_loop(
        client, lambda: service.alerts.find_for_wallet(ShardedId[Wallet](UUID(mexican["id"])))
    )
    assert alert.recipient == EmailAddress("mexico@example.com")
    with service.events_registry.manager(GLOBAL).connection(readonly=True) as conn:
        stored = conn.execute(
            text("SELECT recipient FROM alerts WHERE wallet_id = :id"), {"id": mexican["id"]}
        ).scalar_one()
    assert stored == "mexico@example.com"
    assert alerts[0]["message"].startswith("Deposit of 7.00 received.")


def test_a_bad_currency_or_a_bad_address_is_a_400(client: TestClient) -> None:
    refused = client.post(
        "/wallets", json={"owner_id": str(uuid4()), "currency": "eur", "opening_balance": "1.00"}
    )
    assert refused.status_code == 400, refused.text

    mexican = open_via_http(client, "MX", "10.00")
    refused = client.post(
        f"/wallets/{mexican['id']}/deposits", json={"amount": "1.00", "recipient": "nobody"}
    )
    assert refused.status_code == 400, refused.text
    assert client.get(f"/wallets/{mexican['id']}").json()["balance"] == "10.00", "nothing moved"


def test_a_transfer_within_one_shard_succeeds(client: TestClient) -> None:
    source, destination = open_via_http(client, "MX", "50.00"), open_via_http(client, "MX", "0.00")

    moved = client.post(
        f"/wallets/{source['id']}/transfers",
        json={"to_wallet_id": destination["id"], "amount": "20.00"},
    )

    assert moved.status_code == 200
    assert [w["balance"] for w in moved.json()] == ["30.00", "20.00"]


def test_a_transfer_across_shards_is_a_422_not_a_500(client: TestClient) -> None:
    """The framework refused it rather than failing at it, so it is the caller's request that
    was wrong - and the reply says so, and points at the saga."""
    source, destination = open_via_http(client, "MX", "50.00"), open_via_http(client, "GL", "0.00")

    refused = client.post(
        f"/wallets/{source['id']}/transfers",
        json={"to_wallet_id": destination["id"], "amount": "20.00"},
    )

    assert refused.status_code == 422
    assert "different databases" in refused.json()["detail"]
    assert "saga" in refused.json()["detail"]
    assert client.get(f"/wallets/{source['id']}").json()["balance"] == "50.00", "nothing moved"


def test_the_totals_endpoint_asks_every_database(client: TestClient) -> None:
    open_via_http(client, "MX", "10.00")
    open_via_http(client, "GL", "25.00")

    assert client.get("/shards").json() == {"0:0": "25.0000", "1:0": "10.0000"}


def test_concurrent_deposits_on_a_mexican_wallet_are_serialised_by_the_keyed_lock(
    client: TestClient,
) -> None:
    """The async keyed lock around the unit of work, on a wallet on the second shard: eight
    deposits at once, each request's task waiting its turn on the event loop. Every one lands -
    none is a 409 - and the balance is exact."""
    wallet_id = open_via_http(client, "MX", "0.00")["id"]

    def deposit(_: int) -> int:
        response = client.post(
            f"/wallets/{wallet_id}/deposits",
            json={"amount": "1.00", "recipient": "mexico@example.com"},
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(deposit, range(8)))

    assert codes == [200] * 8, "the lock made every caller wait rather than lose"
    assert client.get(f"/wallets/{wallet_id}").json()["balance"] == "8.00"


def test_requests_and_the_handler_run_on_asyncpg_and_the_jobs_on_psycopg(
    service: ShardedWallets,
) -> None:
    """One url per shard in `ekbatan.yaml`, two drivers: the async default for the requests on
    every shard, the lock and the handler; the sync driver the urls name for what polls."""
    for shard in (GLOBAL, MEXICO):
        assert service.registry.manager(shard).primary.url.get_driver_name() == "asyncpg"
        assert service.events_registry.manager(shard).primary.url.get_driver_name() == "psycopg"
    assert service.lock_engine.url.get_driver_name() == "asyncpg"
    assert service.handler_registry.default.primary.url.get_driver_name() == "asyncpg"
    assert type(service.locks).__name__ == "AsyncPostgresKeyedLockProvider"


def test_the_service_runs_on_the_layout_in_ekbatan_yaml(service: ShardedWallets, run: Run) -> None:
    """`ekbatan.yaml` is the service's Ekbatan config: both shards, their names, the namespace,
    the pools, and the driver settings reaching each server - the UTC session on every pool, on
    both drivers. The handler's pool is asked on the handler loop, where it lives."""
    assert service.executor.namespace == "example.fastapi_wallet_rest_async_sharded_pg"
    assert sorted(service.registry.shards) == [GLOBAL, MEXICO]
    assert sorted(service.events_registry.shards) == [GLOBAL, MEXICO]

    async def settings_on(engine: Any) -> tuple[Any, Any]:
        async with engine.connect() as conn:
            name = (await conn.execute(text("SELECT current_setting('application_name')"))).scalar()
            zone = (await conn.execute(text("SELECT current_setting('TimeZone')"))).scalar()
            return name, zone

    for shard in (GLOBAL, MEXICO):
        primary = service.registry.manager(shard).primary
        assert primary.sync_engine.pool.size() == 10  # type: ignore[attr-defined]
        # `options: -c TimeZone=UTC`: every session on UTC, whatever the server's default.
        assert run(partial(settings_on, primary)) == ("wallet-service", "UTC")
        with service.events_registry.manager(shard).primary.connect() as conn:
            assert conn.execute(text("SELECT current_setting('TimeZone')")).scalar() == "UTC"
    assert run(lambda: settings_on(service.lock_engine))[1] == "UTC"
    handler_engine = service.handler_registry.default.primary
    assert service.handler_loop.run(settings_on(handler_engine)) == ("wallet-handlers", "UTC")
    sizes = [engine.pool.size() for engine in service.jobs_engines]  # type: ignore[attr-defined]
    assert sizes == [8, 4]


def test_the_migration_files_pass_the_checks_a_deploy_runs() -> None:
    """No database needed, so CI catches it: two files with one version, a file whose name would
    be skipped without a word, a rollback file. Each is refused here rather than at startup."""
    assert [script.script for script in validate_scripts(MIGRATIONS)] == [
        "V0001__schema.sql",
        "V0002__event_handler_and_alerts.sql",
    ]
