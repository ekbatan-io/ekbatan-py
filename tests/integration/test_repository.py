from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from ekbatan.clock import VirtualClock, to_storage
from ekbatan.errors import EntityNotFoundError, StaleRecordError
from ekbatan.ids import Id, ShardedId
from ekbatan.persistence import DatabaseRegistry
from ekbatan.repository import Repository
from ekbatan.sharding import EmbeddedBitsShardingStrategy, ShardIdentifier
from fixtures.domain import NOW, Notification, Wallet, WalletState
from fixtures.repositories import (
    NotificationEntityRepository,
    NotificationModelRepository,
    NotificationRepository,
    WalletEntityRepository,
    WalletModelRepository,
    WalletRepository,
)
from fixtures.schema import NOTIFICATIONS, WALLETS


@pytest.fixture
def wallets(registry: DatabaseRegistry, clock: VirtualClock) -> Repository[Wallet, UUID]:
    return WalletRepository(Wallet, WALLETS, registry, clock=clock)


def a_wallet(balance: str = "100.00") -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal(balance),
        created_date=NOW,
    )


def a_wallet_with(**fields: object) -> Wallet:
    """A wallet with one of the two columns that exist only to be caught with."""
    return a_wallet().evolve(**fields)


def test_an_inserted_row_reads_back_exactly(wallets, clock):
    stored = wallets.add(a_wallet())
    found = wallets.get(stored.id)

    assert found == stored.without_events()
    assert found.balance == Decimal("100.0000")
    assert found.currency == "EUR"
    assert found.state is WalletState.OPENED
    assert found.version == 1
    assert found.created_date == stored.created_date, (
        "the instant the model was built with is the instant that was written - the "
        "repository does not supply one, as Java's does not"
    )
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


def test_a_stale_batch_update_raises_and_keeps_what_it_matched(wallets):
    """One statement, no transaction of the framework's own - so the rows that did match are
    written and the call still raises. Java's
    `should_throw_optimistic_lock_exception_when_updateAll_with_stale_versions` asserts exactly
    this. Inside a unit of work the executor's transaction takes the whole thing back; outside
    one there is nothing to take it back, which is the reason to run a batch inside one when
    the pieces belong together.
    """
    stored = wallets.add_all([a_wallet("10.00") for _ in range(3)])
    wallets.update(stored[1].deposit(Decimal("5.00")))  # someone else moved it

    with pytest.raises(StaleRecordError, match="but matched 2"):
        wallets.update_all([w.deposit(Decimal("1.00")) for w in stored])

    balances = {w.id: w.balance for w in wallets.find_many([w.id for w in stored])}
    assert balances[stored[0].id] == Decimal("11.0000"), "matched, so written"
    assert balances[stored[2].id] == Decimal("11.0000"), "matched, so written"
    assert balances[stored[1].id] == Decimal("15.0000"), "the other writer's value stands"


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


def test_deleting_is_a_domain_event(wallets):
    """The framework has no delete of its own. The model says what deletion means for it, and
    a deletion nobody downstream hears about is a row that silently stops existing."""
    wallet = wallets.add(a_wallet())

    gone = wallet.delete()

    assert [type(e).__name__ for e in gone.events] == ["WalletDeletedEvent"]
    assert gone.state is WalletState.DELETED
    assert gone.delete() is gone, "deleting a deleted row changes nothing and emits nothing"


def test_a_deleted_row_is_invisible_to_every_read(wallets):
    kept, gone = wallets.add_all([a_wallet(), a_wallet()])
    wallets.update(gone.delete())

    assert wallets.find(gone.id) is None
    assert wallets.exists(gone.id) is False
    assert wallets.count() == 1
    assert [w.id for w in wallets.all()] == [kept.id]
    with pytest.raises(EntityNotFoundError):
        wallets.get(gone.id)


def test_predicate_reads_apply_the_same_filter(wallets):
    _ada, grace, gone = (
        wallets.add(
            Wallet.open(
                id=ShardedId[Wallet].random(),
                owner_id=uuid4(),
                currency=c,
                balance=Decimal("1.00"),
                created_date=NOW,
            )
        )
        for c in ("EUR", "USD", "EUR")
    )
    wallets.update(gone.delete())

    assert len(wallets.find_where(WALLETS.c.currency == "EUR")) == 1
    assert wallets.count(WALLETS.c.currency == "EUR") == 1
    assert wallets.exists_where(WALLETS.c.owner_id == gone.owner_id) is False
    assert wallets.find_one_where(WALLETS.c.currency == "USD").id == grace.id


def test_an_entity_round_trips_without_events(registry, clock):
    notes = NotificationRepository(Notification, NOTIFICATIONS, registry, clock=clock)
    stored = notes.add(
        Notification.create(
            id=Id[Notification].random(),
            wallet_id=ShardedId[Wallet].random(),
            kind="MONEY_DEPOSITED",
            recipient="ada@x",
        )
    )
    found = notes.get(stored.id)
    assert found == stored
    assert found.message is None, "an unset nullable column comes back null, not empty"
    assert not found.is_model


def test_rows_route_to_the_shard_their_id_names(sharded_registry, clock):
    wallets = WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
    )
    mexico = ShardIdentifier(1, 0)
    here = Wallet.open(
        id=ShardedId[Wallet].generate(ShardIdentifier(0, 0)),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        created_date=clock.now(),
    )
    there = Wallet.open(
        id=ShardedId[Wallet].generate(mexico),
        owner_id=uuid4(),
        currency="MXN",
        balance=Decimal("2.00"),
        created_date=clock.now(),
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
        WalletRepository(Wallet, timeless, registry)


def test_an_entity_needs_no_timestamps(registry, clock):
    from fixtures.domain import Notification
    from fixtures.schema import NOTIFICATIONS

    notes = NotificationEntityRepository(Notification, NOTIFICATIONS, registry, clock=clock)
    stored = notes.add(
        Notification.create(
            wallet_id=ShardedId[Wallet].random(),
            kind="X",
            recipient="ada@x",
            id=Id[Notification].random(),
        )
    )
    assert notes.get(stored.id) == stored


def test_declaring_the_wrong_kind_is_refused(registry):
    from ekbatan.errors import ValidationError
    from fixtures.domain import Notification
    from fixtures.schema import NOTIFICATIONS

    with pytest.raises(ValidationError, match="is not a Model"):
        NotificationModelRepository(Notification, NOTIFICATIONS, registry)

    with pytest.raises(ValidationError, match="is not an Entity"):
        WalletEntityRepository(Wallet, WALLETS, registry)


def test_the_declared_kinds_are_otherwise_the_same_repository(registry, clock):
    """They add a declaration, not behaviour. Everything is inherited."""

    plain = WalletRepository(Wallet, WALLETS, registry, clock=clock)
    declared = WalletModelRepository(Wallet, WALLETS, registry, clock=clock)

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
        WalletRepository(Wallet, shaped("stateless", drop_state=True), registry)

    with pytest.raises(ValidationError, match="optimistic lock"):
        WalletRepository(Wallet, shaped("wordy_version", version=String(32)), registry)


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
        wallets = WalletRepository(Wallet, WALLETS, reg, clock=clock)
        stored = wallets.add(a_wallet())

        assert wallets.find(stored.id) is not None
        assert wallets.get(stored.id).id == stored.id
        assert wallets.count() == 1
        assert wallets.exists(stored.id) is True
        assert [w.id for w in wallets.find_many([stored.id])] == [stored.id]
        assert wallets.find_one_where(WALLETS.c.currency == "EUR") is not None

        with wallets.readonly_db() as conn:
            from sqlalchemy import func, select

            assert conn.execute(select(func.count()).select_from(WALLETS)).scalar_one() == 0, (
                "readonly_db() is the replica - the same meaning as Java's readonlyDb() - so "
                "it sees the lagging database, while every built-in read above saw the primary"
            )


def test_readonly_db_does_not_join_an_open_transaction(backend, clock):
    """Java's `readonlyDb()` is a plain context on the secondary; it is not transaction-aware.

    Joining would be a promise the connection cannot keep - a replica has not seen what the
    open transaction wrote - so it must read the replica even from inside one.
    """
    from sqlalchemy import create_engine, func, select

    from ekbatan.persistence import TransactionManager
    from ekbatan.sharding import ShardIdentifier
    from fixtures.schema import metadata

    written, lagging = "ekbatan_written", "ekbatan_lagging"
    for name in (written, lagging):
        backend.prepare(name, metadata)
        backend.truncate(name, metadata)

    shard = ShardIdentifier(0, 0)
    manager = TransactionManager(
        shard, create_engine(backend.url(written)), create_engine(backend.url(lagging))
    )
    with DatabaseRegistry({shard: manager}, shard) as reg:
        wallets = WalletRepository(Wallet, WALLETS, reg, clock=clock)
        with manager.transaction():
            wallets.add(a_wallet())
            # The built-in read joins the transaction and sees the uncommitted row.
            assert wallets.count() == 1
            # `readonly_db()` does not, and the replica has nothing.
            with wallets.readonly_db() as conn:
                on_replica = conn.execute(select(func.count()).select_from(WALLETS)).scalar_one()
    assert on_replica == 0, "readonly_db() joined the transaction; Java's readonlyDb() does not"


def test_an_unsharded_repository_does_not_scatter_across_a_sharded_registry(backend, clock):
    """Java's `dbs()` and `readonlyDbs()` return only the default shard under NoShardingStrategy.

    Without the guard, a repository that opted out of sharding would still visit every database
    a sibling repository happens to use - and would find rows that are none of its business.
    """
    from sqlalchemy import create_engine

    from ekbatan.persistence import TransactionManager
    from ekbatan.sharding import ShardIdentifier
    from fixtures.schema import metadata

    here, elsewhere = "ekbatan_default_shard", "ekbatan_other_shard"
    for name in (here, elsewhere):
        backend.prepare(name, metadata)
        backend.truncate(name, metadata)

    default, other = ShardIdentifier(0, 0), ShardIdentifier(1, 0)
    managers = {
        default: TransactionManager(default, create_engine(backend.url(here))),
        other: TransactionManager(other, create_engine(backend.url(elsewhere))),
    }
    with DatabaseRegistry(managers, default) as reg:
        wallets = WalletRepository(
            Wallet, WALLETS, reg, clock=clock
        )  # NoShardingStrategy by default
        wallets.add(a_wallet())

        # Put a row on the other shard behind the repository's back. Stamped by hand because
        # `add()` would route it back to the default shard, which is the thing being tested.
        row = wallets.to_row(a_wallet("99.00"))
        row["created_date"] = row["updated_date"] = clock.now()
        with managers[other].connection() as conn:
            conn.execute(WALLETS.insert(), row)

        assert wallets.count() == 1, "an unsharded repository scattered onto another shard"
        assert [shard for shard, _ in wallets.dbs()] == [default]


def test_find_one_where_refuses_a_predicate_that_is_not_unique(wallets):
    from ekbatan.errors import ValidationError

    wallets.add_all([a_wallet("1.00"), a_wallet("2.00")])

    assert wallets.find_one_where(WALLETS.c.balance == Decimal("1.00")).balance == Decimal("1.00")

    with pytest.raises(ValidationError, match="not unique"):
        wallets.find_one_where(WALLETS.c.currency == "EUR")


# -- what a write hands back, on every dialect --------------------------------------------------


def test_an_updated_row_comes_back_without_the_events_that_changed_it(wallets):
    """The counterpart of `test_a_row_read_back_carries_no_events`, for the update path.

    MySQL has no `UPDATE ... RETURNING`, so there is nothing to read back and the obvious
    shortcut is to hand the caller their own object. That object still carries the event the
    change emitted, and the next update writes it a second time - one deposit, two rows in the
    stream. Java takes the same branch (`if MYSQL: ... return domainObject.nextVersion()`) and
    is protected from this only by `nextVersion()` going through a builder.
    """
    stored = wallets.add(a_wallet())
    changed = stored.deposit(Decimal("5.00"))
    assert changed.events, "the model handed to the repository had an event attached"

    written = wallets.update(changed)

    assert written.events == (), "what comes back is the database's view of the row"
    assert written.balance == Decimal("105.0000")
    assert written.version == changed.version + 1


def test_a_write_hands_back_the_same_shape_on_every_dialect(wallets, registry):
    """A raw uuid passed where a typed id belongs comes back typed, whether or not the
    database can RETURNING. Java cannot be handed one at all; here it has to be normalised."""
    engine = registry.default.primary
    with engine.connect():
        pass

    stored = wallets.add(a_wallet())
    updated = wallets.update(stored.deposit(Decimal("1.00")))

    assert isinstance(stored.id, ShardedId)
    assert isinstance(updated.id, ShardedId)
    assert type(stored) is type(updated) is Wallet


def test_matching_more_rows_than_were_asked_for_is_not_a_lost_race(wallets, monkeypatch):
    """Java raises IllegalStateException rather than StaleRecordException here.

    The distinction is load-bearing: the executor retries a stale record, and an update keyed
    on `id = ? AND version = ?` that matches twice means the primary key is not unique. Retrying
    that only burns the attempt. Reached with a stubbed result because a real database will not
    produce it - which is the point of the check.
    """
    from sqlalchemy import Connection

    from ekbatan.errors import EkbatanError, StaleRecordError

    class TwoRowsMatched:
        rowcount = 2

    stored = wallets.add(a_wallet())
    monkeypatch.setattr(Connection, "execute", lambda self, *a, **k: TwoRowsMatched())

    with pytest.raises(EkbatanError) as raised:
        wallets.update_no_result(stored.deposit(Decimal("1.00")))

    assert not isinstance(raised.value, StaleRecordError), "retrying would not help"
    assert "is not unique" in str(raised.value)


# -- what a batched write has to get right ------------------------------------------------------
#
# Java's BaseRepositoryTest devotes four cases to this, and each of them is a shape that only
# breaks in a batch: a derived VALUES table has to carry a type for every column, and the row
# it joins on has to be the row it updates.


def test_all_null_column_in_a_batch(registry, clock):
    """Java's `should_updateAll_when_a_nullable_column_is_null_in_every_row`.

    A derived VALUES table whose column is NULL in every row has no inferable type.
    """
    from fixtures.schema import NOTIFICATIONS

    notes = NotificationEntityRepository(Notification, NOTIFICATIONS, registry, clock=clock)
    stored = notes.add_all(
        [
            Notification.create(
                id=Id[Notification].random(),
                wallet_id=ShardedId[Wallet].random(),
                kind="K",
                recipient=f"a{n}@x",
                message=None,
            )
            for n in range(3)
        ]
    )
    assert all(s.message is None for s in stored)

    back = notes.update_all([s.evolve(kind="CHANGED") for s in stored])
    assert [b.kind for b in back] == ["CHANGED"] * 3
    assert all(b.message is None for b in back)


def test_converted_values_survive_a_batch(wallets, clock):
    """Java's `should_preserve_converted_values_when_updateAll_batches_multiple_rows`.

    Asserted by re-reading, as Java's does. What a batch update *returns* differs by dialect -
    there is no `UPDATE ... RETURNING` on MySQL, so the values come back at the scale they were
    handed in rather than the scale the column stores. What is stored is the same everywhere,
    and that is the part that is a promise.
    """
    made = wallets.add_all(
        [
            Wallet.open(
                id=ShardedId[Wallet].random(),
                owner_id=uuid4(),
                currency=c,
                balance=Decimal(b),
                created_date=clock.now(),
            )
            for c, b in [("EUR", "1.25"), ("USD", "2.50"), ("MXN", "3.75")]
        ]
    )
    wallets.update_all([w.evolve(balance=w.balance * 2) for w in made])

    back = wallets.find_many([w.id for w in made])

    assert len(back) == 3
    assert sorted(str(w.balance) for w in back) == ["2.5000", "5.0000", "7.5000"]
    assert all(isinstance(w.id, ShardedId) for w in back)
    assert sorted(w.currency for w in back) == ["EUR", "MXN", "USD"]
    assert all(w.version == 2 for w in back)


def test_an_update_leaves_updated_date_alone(wallets, clock):
    """Java's `should_updateAll_and_update_store_identical_timestamps`."""
    a, b = wallets.add_all(
        [
            Wallet.open(
                id=ShardedId[Wallet].random(),
                owner_id=uuid4(),
                currency="EUR",
                balance=Decimal("1"),
                created_date=clock.now(),
            )
            for _ in range(2)
        ]
    )
    clock.advance(timedelta(hours=3))

    one = wallets.update(a.evolve(balance=Decimal("2")))
    many = wallets.update_all([b.evolve(balance=Decimal("2"))])

    # Java writes `updatedDate` nowhere in `ekbatan-core`, and neither does this. A framework
    # that quietly rewrote a column the application set is one you cannot use to record when
    # *your* change happened.
    assert one.updated_date == a.updated_date, "the clock moved; the row's timestamp did not"
    assert many[0].updated_date == b.updated_date
    assert one.version == 2, "the version still moves - that is the optimistic lock"

    # Setting it is the caller's, and it survives the write untouched.
    at = clock.now()
    marked = wallets.update(wallets.get(a.id).copy().updated_date(at).build())
    assert wallets.get(a.id).updated_date == to_storage(at)
    assert marked.updated_date == to_storage(at)


# A value that only reaches the database through a type mapper. Java's `Dummy` carries an
# `aliases` column for exactly this and says why: "a write path that binds from the value's
# runtime class rather than from the target field discards that converter, which is precisely
# what these helpers make observable". Nothing else in this suite round-trips a structured
# value - the conversions covered above are all scalar (Decimal scale, a TypedValue id, an
# Enum state), and a scalar survives a binder that ignores the declared type.


def test_a_json_list_column_round_trips(wallets):
    """Java's `Dummy.aliases`, whole purpose."""
    stored = wallets.add(a_wallet_with(aliases=["primary", "billing", "éè"]))

    found = wallets.get(stored.id)

    assert found.aliases == ["primary", "billing", "éè"]
    assert isinstance(found.aliases, list)


def test_an_empty_json_list_is_not_confused_with_a_missing_one(wallets):
    """Java maps an empty list to a null column and back to an empty list, so the two are
    deliberately indistinguishable there. Here they are stored as they are given, which means
    the distinction survives - and a reader has to be able to tell them apart."""
    empty = wallets.get(wallets.add(a_wallet_with(aliases=[])).id)
    absent = wallets.get(wallets.add(a_wallet()).id)

    assert empty.aliases == []
    assert absent.aliases == [], "the field's own default, not a null read back as one"


def test_json_lists_survive_a_batch(wallets):
    """The batch path builds a derived VALUES table by hand, so it is a different binder from
    the single-row one and can lose a mapper the single-row path keeps."""
    made = wallets.add_all([a_wallet_with(aliases=[f"alias-{n}", f"other-{n}"]) for n in range(3)])
    assert sorted(w.aliases[0] for w in made) == ["alias-0", "alias-1", "alias-2"]

    wallets.update_all([w.evolve(aliases=[*w.aliases, "added"]) for w in made])

    back = wallets.find_many([w.id for w in made])
    assert all(w.aliases[-1] == "added" for w in back)
    assert sorted(len(w.aliases) for w in back) == [3, 3, 3]


def test_an_integer_column_null_in_every_row_of_a_batch(wallets):
    """The untyped-NULL bind, on an integer rather than the text column
    `test_all_null_column_in_a_batch` already covers. A derived VALUES table whose column is
    NULL in every row has no inferable type, and which types a driver will refuse to guess
    differs between them - so the case is worth having on more than one."""
    made = wallets.add_all([a_wallet() for _ in range(3)])
    assert all(w.reward_points is None for w in made)

    wallets.update_all([w.evolve(balance=Decimal("5")) for w in made])

    back = wallets.find_many([w.id for w in made])
    assert all(w.reward_points is None for w in back)
    assert all(w.balance == Decimal("5.0000") for w in back)


def test_an_integer_column_set_in_some_rows_and_null_in_others(wallets):
    """The mixed shape, which is what makes a per-column type decision necessary rather than a
    per-batch one."""
    made = wallets.add_all([a_wallet_with(reward_points=points) for points in (10, None, 30)])

    back = sorted(
        wallets.find_many([w.id for w in made]),
        key=lambda w: (w.reward_points is None, w.reward_points or 0),
    )
    assert [w.reward_points for w in back] == [10, 30, None]
