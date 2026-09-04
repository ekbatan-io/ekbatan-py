from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from ekbatan.clock import FixedClock
from ekbatan.errors import EntityNotFoundError, StaleRecordError
from ekbatan.persistence import DatabaseRegistry
from ekbatan.repository import Repository
from ekbatan.sharding import EmbeddedBitsSharding, ShardIdentifier
from fixtures.domain import Notification, NotificationId, Wallet, WalletId, WalletState
from fixtures.schema import NOTIFICATIONS, WALLETS


@pytest.fixture
def wallets(registry: DatabaseRegistry, clock: FixedClock) -> Repository[Wallet, UUID]:
    return Repository(Wallet, WALLETS, registry, clock=clock)


def a_wallet(balance: str = "100.00") -> Wallet:
    return Wallet.open(
        id=WalletId.random(), owner_id=uuid4(), currency="EUR", balance=Decimal(balance)
    )


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
    written = a_wallet()
    assert written.events, "the model handed to the repository had an event attached"

    stored = wallets.add(written)

    assert stored.events == (), (
        "what comes back is the database's view of the row, and events are not a column"
    )
    assert wallets.get(written.id).events == (), "nor on a later read"


def test_a_missing_row_is_absent_or_an_error(wallets):
    assert wallets.find(uuid4()) is None
    with pytest.raises(EntityNotFoundError):
        wallets.get(uuid4())


def test_an_update_moves_the_version_by_one(wallets):
    w = wallets.add(a_wallet("0.00"))
    w = wallets.update(w.deposit(Decimal("1.00")))
    w = wallets.update(w.deposit(Decimal("2.00")))

    found = wallets.get(w.id)
    assert found.balance == Decimal("3.0000")
    assert found.version == 3


def test_update_takes_the_row_as_it_was_read(wallets):
    """The repository owns the optimistic lock, so the repository moves the version. Asking
    the caller to move it first makes the obvious call the one that fails - and fail as a lost
    race, which it is not."""
    stored = wallets.add(a_wallet("100.00"))
    assert stored.version == 1

    returned = wallets.update(stored.deposit(Decimal("50.00")))

    assert returned.version == 2, "handed back one version on, like the row now in the table"
    assert wallets.get(stored.id).version == 2
    assert wallets.get(stored.id).balance == Decimal("150.0000")


def test_a_stale_update_is_refused(wallets):
    w = wallets.add(a_wallet("100.00"))
    wallets.update(w.deposit(Decimal("10.00")))

    with pytest.raises(StaleRecordError, match="concurrently modified"):
        wallets.update(w.deposit(Decimal("99.00")))

    assert wallets.get(w.id).balance == Decimal("110.0000"), "the first writer's value stands"


def test_a_batch_update_is_all_or_nothing(wallets):
    stored = wallets.add_all([a_wallet("10.00") for _ in range(3)])
    wallets.update(stored[1].deposit(Decimal("5.00")))  # someone else moved it

    with pytest.raises(StaleRecordError):
        wallets.update_all([w.deposit(Decimal("1.00")) for w in stored])

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
    wallets.delete(gone)

    assert wallets.find(gone.id) is None
    assert wallets.exists(gone.id) is False
    assert wallets.count() == 1
    assert [w.id for w in wallets.all()] == [kept.id]
    with pytest.raises(EntityNotFoundError):
        wallets.get(gone.id)


def test_predicate_reads_apply_the_same_filter(wallets):
    _ada, grace, gone = (
        wallets.add(
            Wallet.open(id=WalletId.random(), owner_id=uuid4(), currency=c, balance=Decimal("1.00"))
        )
        for c in ("EUR", "USD", "EUR")
    )
    wallets.delete(gone)

    assert len(wallets.find_where(WALLETS.c.currency == "EUR")) == 1
    assert wallets.count(WALLETS.c.currency == "EUR") == 1
    assert wallets.exists_where(WALLETS.c.owner_id == gone.owner_id) is False
    assert wallets.find_one_where(WALLETS.c.currency == "USD").id == grace.id


def test_an_entity_round_trips_without_events(registry, clock):
    notes = Repository(Notification, NOTIFICATIONS, registry, clock=clock)
    stored = notes.add(
        Notification.create(
            id=NotificationId.random(),
            wallet_id=WalletId.random(),
            kind="MONEY_DEPOSITED",
            recipient="ada@x",
        )
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
        id=WalletId.generate(ShardIdentifier(0, 0)),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
    )
    there = Wallet.open(
        id=WalletId.generate(mexico),
        owner_id=uuid4(),
        currency="MXN",
        balance=Decimal("2.00"),
    )
    # One call per shard: a batch spanning two databases would need a transaction that spans
    # them, and there isn't one.
    wallets.add(here)
    wallets.add(there)

    assert wallets.count(shard=ShardIdentifier(0, 0)) == 1
    assert wallets.count(shard=mexico) == 1
    assert wallets.find(there.id).currency == "MXN", "a lookup by id finds it wherever it lives"
    assert {w.id for w in wallets.find_many([here.id, there.id])} == {here.id, there.id}


# ---------------------------------------------------------- saying which kind of thing it is


def test_a_model_needs_a_table_that_can_hold_its_timestamps(registry):
    """Caught where the repository is built, because the alternative is a row written without
    them - or an insert that fails on the first write of the day, far from the wrong mapping."""
    from sqlalchemy import BigInteger, Column, MetaData, String, Table, Uuid

    from ekbatan.errors import ValidationError

    md = MetaData()
    timeless = Table(
        "timeless",
        md,
        Column("id", Uuid, primary_key=True),
        Column("version", BigInteger, nullable=False),
        Column("state", String(24), nullable=False),
    )

    with pytest.raises(ValidationError, match="created_date"):
        Repository(Wallet, timeless, registry)


def test_an_entity_needs_no_timestamps(registry, clock):
    from ekbatan import EntityRepository
    from fixtures.domain import Notification
    from fixtures.schema import NOTIFICATIONS

    notes = EntityRepository(Notification, NOTIFICATIONS, registry, clock=clock)
    stored = notes.add(
        Notification.create(wallet_id=uuid4(), kind="X", recipient="ada@x", id=uuid4())
    )
    assert notes.get(stored.id) == stored


def test_declaring_the_wrong_kind_is_refused(registry):
    from ekbatan import EntityRepository, ModelRepository
    from ekbatan.errors import ValidationError
    from fixtures.domain import Notification
    from fixtures.schema import NOTIFICATIONS

    with pytest.raises(ValidationError, match="is not a Model"):
        ModelRepository(Notification, NOTIFICATIONS, registry)

    with pytest.raises(ValidationError, match="is not an Entity"):
        EntityRepository(Wallet, WALLETS, registry)


def test_the_declared_kinds_are_otherwise_the_same_repository(registry, clock):
    """They add a declaration, not behaviour. Everything is inherited."""
    from ekbatan import ModelRepository

    plain = Repository(Wallet, WALLETS, registry, clock=clock)
    declared = ModelRepository(Wallet, WALLETS, registry, clock=clock)

    stored = declared.add(a_wallet())
    assert plain.get(stored.id) == declared.get(stored.id)
    assert declared.count() == plain.count() == 1


# ------------------------------------------------- what a read is allowed to be served by


def test_a_table_must_be_able_to_hold_the_row_it_is_given(registry):
    """A column the model needs and the table has not is dropped from every INSERT in
    silence. Caught here, not on the read that later cannot rebuild the row."""
    from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, Uuid

    from ekbatan.errors import ValidationError

    md = MetaData()

    def shaped(name, **overrides):
        return Table(
            name,
            md,
            Column("id", Uuid, primary_key=True),
            Column("version", overrides.get("version", BigInteger), nullable=False),
            *([] if overrides.get("drop_state") else [Column("state", String(24))]),
            Column("created_date", DateTime, nullable=False),
            Column("updated_date", DateTime, nullable=False),
        )

    with pytest.raises(ValidationError, match="no column named 'state'"):
        Repository(Wallet, shaped("stateless", drop_state=True), registry)

    with pytest.raises(ValidationError, match="optimistic lock"):
        Repository(Wallet, shaped("wordy_version", version=String(32)), registry)


def test_reads_are_served_by_the_primary_not_a_replica(backend, clock):
    """A replica answers with what it has replicated so far. `add()` then `find()` has to
    find the row, so no built-in read is allowed to be served by one."""
    from ekbatan.persistence import TransactionManager
    from ekbatan.sharding import ShardIdentifier
    from fixtures.schema import metadata

    written, lagging = "ekbatan_written", "ekbatan_lagging"
    for name in (written, lagging):
        backend.prepare(name, metadata)
        backend.truncate(name, metadata)

    from sqlalchemy import create_engine

    shard = ShardIdentifier(0, 0)
    manager = TransactionManager(
        shard, create_engine(backend.url(written)), create_engine(backend.url(lagging))
    )
    with DatabaseRegistry({shard: manager}, shard) as reg:
        wallets = Repository(Wallet, WALLETS, reg, clock=clock)
        stored = wallets.add(a_wallet())

        assert wallets.find(stored.id) is not None
        assert wallets.get(stored.id).id == stored.id
        assert wallets.count() == 1
        assert wallets.exists(stored.id) is True
        assert [w.id for w in wallets.find_many([stored.id])] == [stored.id]
        assert wallets.find_one_where(WALLETS.c.currency == "EUR") is not None

        with wallets.readonly_db(replica=True) as conn:
            from sqlalchemy import func, select

            assert conn.execute(select(func.count()).select_from(WALLETS)).scalar_one() == 0, (
                "the replica really is a different database, so the assertions above mean "
                "what they say"
            )


def test_find_one_where_refuses_a_predicate_that_is_not_unique(wallets):
    from ekbatan.errors import ValidationError

    wallets.add_all([a_wallet("1.00"), a_wallet("2.00")])

    assert wallets.find_one_where(WALLETS.c.balance == Decimal("1.00")).balance == Decimal("1.00")

    with pytest.raises(ValidationError, match="not unique"):
        wallets.find_one_where(WALLETS.c.currency == "EUR")
