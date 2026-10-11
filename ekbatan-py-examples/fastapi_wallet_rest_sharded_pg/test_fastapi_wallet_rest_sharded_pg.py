"""Routing, isolation, and the refusal that keeps "all or nothing" honest.

    uv run pytest ekbatan-py-examples/fastapi_wallet_rest_sharded_pg

Two databases, not two schemas and not one database pretending. That matters here more than
anywhere else in the examples: a test that shared a database would pass with the routing
removed, which would make it worse than no test at all.

They are two databases on one Postgres *server*, which is a different thing - the framework
opens two pools against two urls and cannot tell that one process is behind both. Two servers
would prove nothing further and cost a second container.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from ekbatan.config import split_login
from ekbatan.errors import CrossShardError
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
from .model import Wallet


@pytest.fixture(scope="module")
def urls() -> Iterator[dict[object, str]]:
    from testcontainers.postgres import PostgresContainer

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

        head, _, _ = split_login(base)[0].rpartition("/")
        yield {GLOBAL: f"{head}/shard_global", MEXICO: f"{head}/shard_mexico"}


@pytest.fixture
def service(urls: dict[object, str]) -> Iterator[ShardedWallets]:
    built = build(urls, username="test", password="test")  # type: ignore[arg-type]
    try:
        for shard in built.registry.shards:
            with built.registry.manager(shard).connection() as conn:
                conn.execute(text("DELETE FROM wallets"))
                conn.execute(text("DELETE FROM events"))
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
        DepositMoneyAction, DepositParams(wallet_id=mexican.id, amount=Decimal("5.00"))
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
    `ekbatan-py-examples/fastapi_wallet_saga_pg`.
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
def client(urls: dict[object, str]) -> Iterator[TestClient]:
    from fastapi.testclient import TestClient

    from .api import create_app

    with TestClient(create_app(urls, username="test", password="test")) as running:  # type: ignore[arg-type]
        for shard in running.app.state.service.registry.shards:
            with running.app.state.service.registry.manager(shard).connection() as conn:
                conn.execute(text("DELETE FROM wallets"))
                conn.execute(text("DELETE FROM events"))
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

    after = client.post(f"/wallets/{mexican['id']}/deposits", json={"amount": "5.00"})

    assert after.json()["balance"] == "15.00"


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


def test_the_migration_files_pass_the_checks_a_deploy_runs():
    """No database needed, so CI catches it: two files with one version, a file whose name would
    be skipped without a word, a rollback file. Each is refused here rather than at startup."""
    assert [script.script for script in validate_scripts(MIGRATIONS)] == ["V0001__schema.sql"]
