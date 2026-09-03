from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from examples.wallet.model import Notification, Wallet, WalletState
from examples.wallet.schema import NOTIFICATIONS, WALLETS

from ekbatan.clock import FixedClock
from ekbatan.errors import EntityNotFoundError, StaleRecordError
from ekbatan.persistence import DatabaseRegistry
from ekbatan.repository import Repository
from ekbatan.sharding import EmbeddedBitsSharding, ShardIdentifier, sharded_uuid


@pytest.fixture
def wallets(registry: DatabaseRegistry, clock: FixedClock) -> Repository[Wallet, UUID]:
    return Repository(Wallet, WALLETS, registry, clock=clock)


def a_wallet(balance: str = "100.00") -> Wallet:
    return Wallet.open(id=uuid4(), owner_id=uuid4(), currency="EUR", balance=Decimal(balance))


def test_an_inserted_row_reads_back_exactly(wallets, clock):
    stored = wallets.add(a_wallet())
    found = wallets.get(stored.id)

    assert found == stored.without_events()
    assert found.balance == Decimal("100.0000")
    assert found.currency == "EUR"
    assert found.state is WalletState.OPENED
    assert found.version == 1
    assert found.created_date == datetime(2026, 1, 1, 10, 0, 0)
    assert found.created_date.tzinfo is None, "timestamps are stored zone-less, in UTC"


def test_a_row_read_back_carries_no_events(wallets):
    stored = wallets.add(a_wallet())
    assert stored.events, "the model that was written had an event attached"
    assert wallets.get(stored.id).events == (), "the row read back must not re-emit it"


def test_a_missing_row_is_absent_or_an_error(wallets):
    assert wallets.find(uuid4()) is None
    with pytest.raises(EntityNotFoundError):
        wallets.get(uuid4())


def test_an_update_moves_the_version_by_one(wallets):
    w = wallets.add(a_wallet("0.00"))
    w = wallets.update(w.deposit(Decimal("1.00")).next_version())
    w = wallets.update(w.deposit(Decimal("2.00")).next_version())

    found = wallets.get(w.id)
    assert found.balance == Decimal("3.0000")
    assert found.version == 3


def test_a_stale_update_is_refused(wallets):
    w = wallets.add(a_wallet("100.00"))
    wallets.update(w.deposit(Decimal("10.00")).next_version())

    with pytest.raises(StaleRecordError, match="concurrently modified"):
        wallets.update(w.deposit(Decimal("99.00")).next_version())

    assert wallets.get(w.id).balance == Decimal("110.0000"), "the first writer's value stands"


def test_a_batch_update_is_all_or_nothing(wallets):
    stored = wallets.add_all([a_wallet("10.00") for _ in range(3)])
    wallets.update(stored[1].deposit(Decimal("5.00")).next_version())  # someone else moved it

    with pytest.raises(StaleRecordError):
        wallets.update_all([w.deposit(Decimal("1.00")).next_version() for w in stored])

    balances = {w.id: w.balance for w in wallets.find_many([w.id for w in stored])}
    assert balances[stored[0].id] == Decimal("10.0000")
    assert balances[stored[2].id] == Decimal("10.0000")


def test_a_batch_is_one_statement(wallets, registry):
    seen: list[tuple[str, bool]] = []
    from sqlalchemy import event

    @event.listens_for(registry.default.primary, "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append((statement.split()[0].upper(), executemany))

    wallets.add_all([a_wallet() for _ in range(10)])
    inserts = [s for s in seen if s[0] == "INSERT"]
    assert inserts == [("INSERT", True)], "ten staged rows must cost one statement, not ten"


def test_an_empty_batch_is_a_no_op(wallets):
    assert wallets.add_all([]) == []
    assert wallets.update_all([]) == []
    assert wallets.find_many([]) == []


def test_a_deleted_row_is_invisible_to_every_read(wallets):
    kept, gone = wallets.add_all([a_wallet(), a_wallet()])
    wallets.delete(gone.next_version())

    assert wallets.find(gone.id) is None
    assert wallets.exists(gone.id) is False
    assert wallets.count() == 1
    assert [w.id for w in wallets.all()] == [kept.id]
    with pytest.raises(EntityNotFoundError):
        wallets.get(gone.id)


def test_predicate_reads_apply_the_same_filter(wallets):
    _ada, grace, gone = (
        wallets.add(Wallet.open(id=uuid4(), owner_id=uuid4(), currency=c, balance=Decimal("1.00")))
        for c in ("EUR", "USD", "EUR")
    )
    wallets.delete(gone.next_version())

    assert len(wallets.find_where(WALLETS.c.currency == "EUR")) == 1
    assert wallets.count(WALLETS.c.currency == "EUR") == 1
    assert wallets.exists_where(WALLETS.c.owner_id == gone.owner_id) is False
    assert wallets.find_one_where(WALLETS.c.currency == "USD").id == grace.id


def test_an_entity_round_trips_without_events(registry, clock):
    notes = Repository(Notification, NOTIFICATIONS, registry, clock=clock)
    stored = notes.add(
        Notification(id=uuid4(), wallet_id=uuid4(), kind="MONEY_DEPOSITED", recipient="ada@x")
    )
    found = notes.get(stored.id)
    assert found == stored
    assert found.message is None, "an unset nullable column comes back null, not empty"
    assert not found.emits_events


def test_rows_route_to_the_shard_their_id_names(sharded_registry, clock):
    wallets = Repository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsSharding(), clock=clock
    )
    mexico = ShardIdentifier(1, 0)
    here = Wallet.open(
        id=sharded_uuid(ShardIdentifier(0, 0)),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
    )
    there = Wallet.open(
        id=sharded_uuid(mexico), owner_id=uuid4(), currency="MXN", balance=Decimal("2.00")
    )
    wallets.add_all([here, there])

    assert wallets.count(shard=ShardIdentifier(0, 0)) == 1
    assert wallets.count(shard=mexico) == 1
    assert wallets.find(there.id).currency == "MXN", "a lookup by id finds it wherever it lives"
    assert {w.id for w in wallets.find_many([here.id, there.id])} == {here.id, there.id}
