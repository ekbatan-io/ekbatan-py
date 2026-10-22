"""Routing, isolation, and the refusal that keeps "all or nothing" honest.

    make

Two databases, not two schemas and not one database pretending. That matters here more than
anywhere else in the examples: a test that shared a database would pass with the routing
removed, which would make it worse than no test at all.

They are two databases on one Postgres *server*, which is a different thing - the framework
opens two pools against two urls and cannot tell that one process is behind both. Two servers
would prove nothing further and cost a second container.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from ekbatan.config import split_login
from ekbatan.errors import CrossShardError, ValidationError
from ekbatan.ids import ShardedId
from ekbatan.migrations import validate_scripts
from ekbatan.time import Instant

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

#: The database's password: a marker, so a test can look for it where it must not be.
SECRET = "Mk-7f3a-SECRET"


@pytest.fixture(scope="module")
def variables() -> Iterator[dict[str, str]]:
    """What fills `ekbatan.yaml`'s placeholders: one url per shard, and the login."""
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:17-alpine", driver="psycopg", password=SECRET) as container:
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


def use(monkeypatch: pytest.MonkeyPatch, variables: dict[str, str]) -> None:
    """Put the variables in the process environment, as a deployment does; `monkeypatch`
    restores what was there when the test ends."""
    for name, value in variables.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def service(variables: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[ShardedWallets]:
    use(monkeypatch, variables)
    built = build()
    try:
        for shard in built.registry.shards:
            with built.registry.manager(shard).connection() as conn:
                for table in ("wallets", "events", "event_notifications", "alerts"):
                    conn.execute(text(f"DELETE FROM {table}"))
        yield built
    finally:
        built.close()


def a_wallet(service: ShardedWallets, country: str = "GL", balance: str = "100.00") -> Wallet:
    return service.executor.execute(
        OpenWalletAction,
        OpenWalletParams(
            owner_id=uuid4(),
            currency="EUR",
            opening_balance=Decimal(balance),
            country=country,
        ),
    )


def test_the_country_decides_the_database_and_the_id_remembers_it(
    service: ShardedWallets,
) -> None:
    """The one place a shard is chosen, and the last place anything has to know."""
    mexican = a_wallet(service, "MX")
    other = a_wallet(service, "GL")

    assert mexican.id.shard == MEXICO
    assert other.id.shard == GLOBAL


def test_a_row_is_only_on_its_own_database(service: ShardedWallets) -> None:
    """Isolation, checked from the outside rather than through the framework - if routing
    stopped working, going through the repository would hide it."""
    mexican = a_wallet(service, "MX")

    assert [w.id for w in service.wallets.on_shard(MEXICO)] == [mexican.id]
    assert service.wallets.on_shard(GLOBAL) == []


def test_a_read_by_id_needs_no_hint(service: ShardedWallets) -> None:
    """No lookup table, no tenant column, no argument saying where to look."""
    mexican = a_wallet(service, "MX")

    assert service.wallets.get(mexican.id).id == mexican.id
    assert service.wallets.find(mexican.id) is not None


def test_an_action_never_names_a_shard(service: ShardedWallets) -> None:
    """`DepositMoneyAction` takes an id and nothing else. That is what stops business logic from
    having to change when the layout does."""
    mexican = a_wallet(service, "MX", "10.00")

    after = service.executor.execute(
        DepositMoneyAction,
        DepositParams(wallet_id=mexican.id, amount=Decimal("5.00"), recipient="mx@example.com"),
    )

    assert after.balance == Decimal("15.00")
    assert service.wallets.get(mexican.id).balance == Decimal("15.0000")


def test_a_query_without_an_id_asks_every_shard(service: ShardedWallets) -> None:
    """And the cost is one query per database, which is why a question asked often should be
    answerable from one."""
    owner = uuid4()
    for country in ("MX", "GL"):
        service.executor.execute(
            OpenWalletAction,
            OpenWalletParams(
                owner_id=owner,
                currency="EUR",
                opening_balance=Decimal("1.00"),
                country=country,
            ),
        )

    found = service.wallets.find_all_by_owner(owner)

    assert len(found) == 2
    assert {w.id.shard for w in found} == {GLOBAL, MEXICO}


def test_each_shard_keeps_its_own_outbox(service: ShardedWallets) -> None:
    """A row and its events commit together, so the events land on the row's database - which
    is what lets one CDC connector per shard read them independently."""
    a_wallet(service, "MX")

    counts = {}
    for shard in service.registry.shards:
        with service.registry.manager(shard).connection(readonly=True) as conn:
            counts[shard] = conn.execute(text("SELECT count(*) FROM events")).scalar_one()

    assert counts[MEXICO] == 1
    assert counts[GLOBAL] == 0
    with service.registry.manager(MEXICO).connection(readonly=True) as conn:
        opened_at = conn.execute(text("SELECT payload->>'openedAt' FROM events")).scalar_one()
    assert opened_at.endswith("Z"), "the event's Instant: UTC digits with Java's Z"


def test_two_wallets_on_one_shard_move_together(service: ShardedWallets) -> None:
    source, destination = a_wallet(service, "MX", "50.00"), a_wallet(service, "MX", "0.00")

    service.executor.execute(
        TransferWithinShardAction,
        TransferParams(
            from_wallet_id=source.id, to_wallet_id=destination.id, amount=Decimal("20.00")
        ),
    )

    assert service.wallets.get(source.id).balance == Decimal("30.0000")
    assert service.wallets.get(destination.id).balance == Decimal("20.0000")


def test_reaching_a_second_shard_is_refused_rather_than_half_committed(
    service: ShardedWallets,
) -> None:
    """The refusal this design depends on.

    There is no two-phase commit here. Committing each shard separately would turn "all or
    nothing" into a lie the first time an action reached two databases by accident, so the
    framework stops instead - and says which two disagreed.

    Moving money between shards is a saga, not a transaction. See
    `fastapi-wallet-saga-uv-pg`.
    """
    source, destination = a_wallet(service, "MX", "50.00"), a_wallet(service, "GL", "0.00")

    with pytest.raises(CrossShardError) as refused:
        service.executor.execute(
            TransferWithinShardAction,
            TransferParams(
                from_wallet_id=source.id, to_wallet_id=destination.id, amount=Decimal("20.00")
            ),
        )

    # The pair, not which is which: *which* shard the unit of work settles on first follows
    # the order the persister writes the types in, and neither port promises that. What is
    # promised is that the two disagreeing shards are both named, so the line to look at is
    # findable.
    assert {refused.value.active_shard, refused.value.requested_shard} == {GLOBAL, MEXICO}
    assert service.wallets.get(source.id).balance == Decimal("50.0000"), "nothing was written"
    assert service.wallets.get(destination.id).balance == Decimal("0.0000")


def test_the_currency_is_a_currency_in_python_and_text_on_its_database(
    service: ShardedWallets,
) -> None:
    """The forced type that picks every `CHAR(3)` column, on the shard the wallet lives on."""
    mexican = a_wallet(service, "MX")

    assert service.wallets.get(mexican.id).currency == Currency("EUR")
    with service.registry.manager(MEXICO).connection(readonly=True) as conn:
        stored = conn.execute(text("SELECT currency FROM wallets")).scalar_one()
    assert stored == "EUR", "the column holds the plain code"


def test_a_per_shard_total_is_a_question_about_databases(service: ShardedWallets) -> None:
    a_wallet(service, "MX", "10.00")
    a_wallet(service, "GL", "25.00")

    assert service.wallets.total_per_shard() == {
        GLOBAL: Decimal("25.0000"),
        MEXICO: Decimal("10.0000"),
    }


# -- the same claims, through HTTP -------------------------------------------------------------
#
# The endpoints are where routing is easiest to check: a client hands over an id and nothing
# else, and the right row still comes back. Java's `-sharded-pg` is a REST project for the same
# reason, which is why this one is too.


@pytest.fixture
def client(variables: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from fastapi.testclient import TestClient

    from .api import create_app

    use(monkeypatch, variables)
    with TestClient(create_app()) as running:
        for shard in running.app.state.service.registry.shards:
            with running.app.state.service.registry.manager(shard).connection() as conn:
                for table in ("wallets", "events", "event_notifications", "alerts"):
                    conn.execute(text(f"DELETE FROM {table}"))
        yield running


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


def test_a_deposit_on_any_shard_reaches_the_handler(client: TestClient) -> None:
    """Java's sharded example asserts the same: the deposit commits on its wallet's shard, the
    fan-out and handling jobs - one scheduler for every shard - run the handler, and it writes
    an alert. Waited for up to 15 seconds, as Java's test does; the jobs run in the background."""
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
    service = client.app.state.service
    [alert] = service.alerts.find_for_wallet(ShardedId[Wallet](UUID(mexican["id"])))
    assert alert.recipient == EmailAddress("mexico@example.com")
    with service.registry.manager(GLOBAL).connection(readonly=True) as conn:
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
    """The keyed lock around the unit of work, on a wallet on the second shard: eight deposits
    at once, each request waiting its turn. Every one lands - none is a 409 - and the balance is
    exact."""
    from concurrent.futures import ThreadPoolExecutor

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


def test_the_service_runs_on_the_layout_in_ekbatan_yaml(service: ShardedWallets) -> None:
    """`ekbatan.yaml` is the service's Ekbatan config, as `application.yml` is in Java: both
    shards, their names, the namespace, and the driver setting reaching each server."""
    assert service.executor.namespace == "example.fastapi_wallet_rest_sharded_pg"
    assert sorted(service.registry.shards) == [GLOBAL, MEXICO]
    for shard in (GLOBAL, MEXICO):
        with service.registry.manager(shard).primary.connect() as conn:
            reported = conn.execute(text("SELECT current_setting('application_name')"))
            assert reported.scalar() == "wallet-service"
            # `options: -c TimeZone=UTC`: every session on UTC, whatever the server's default.
            zone = conn.execute(text("SELECT current_setting('TimeZone')")).scalar()
            assert zone == "UTC"
    assert type(service.locks).__name__ == "PostgresKeyedLockProvider"
    assert service.lock_engine.pool.size() == 5
    with service.lock_engine.connect() as conn:
        assert conn.execute(text("SELECT current_setting('TimeZone')")).scalar() == "UTC"


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
        "V0001__schema.sql",
        "V0002__event_handler_and_alerts.sql",
    ]


def test_no_service_object_shows_the_password(service: ShardedWallets) -> None:
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


def test_a_wallet_never_holds_a_negative_balance() -> None:
    """`Wallet.validate()` is the wallet's one place for its own rules, and ekbatan runs it
    every time a wallet is made - here, opened with less than nothing."""
    with pytest.raises(ValidationError, match="balance cannot be negative"):
        Wallet.open(
            id=ShardedId[Wallet].random(),
            owner_id=uuid4(),
            currency=Currency("EUR"),
            balance=Decimal("-0.01"),
            created_date=Instant(datetime(2026, 1, 1, 12, 0)),
        )
