"""What the repository costs, in statements.

Not timings. A timing test measures the machine it ran on and fails on a loaded CI box; a
statement count measures the framework and is the same everywhere. These are the properties an
implementation loses first, and loses silently - the behaviour stays correct while the cost
goes from one round trip to N.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from ekbatan.ids import ShardedId
from fixtures.actions import (
    WalletCreateAction,
)
from fixtures.domain import NOW, Wallet
from fixtures.executing import run
from fixtures.repositories import WalletRepository
from fixtures.schema import WALLETS

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")


@pytest.fixture
def sql(registry):
    """Every statement the primary sends, in order."""
    seen: list[tuple[str, bool, int]] = []

    @event.listens_for(registry.default.primary.sqlalchemy_engine, "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append((statement, executemany, len(parameters) if executemany else 1))

    return seen


def kind(seen: Sequence[tuple[str, bool, int]], verb: str) -> list[tuple[str, bool, int]]:
    return [row for row in seen if row[0].lstrip().upper().startswith(verb)]


def some_wallets(n: int) -> list[Wallet]:
    return [
        Wallet.open(
            id=ShardedId[Wallet].random(),
            owner_id=uuid4(),
            currency="EUR",
            balance=Decimal("1.00"),
            created_date=NOW,
        )
        for _ in range(n)
    ]


def test_inserting_many_rows_is_one_statement(wallet_repository, sql):
    wallet_repository.add_all(some_wallets(50))
    assert len(kind(sql, "INSERT")) == 1, "fifty rows, one round trip"


def test_updating_many_rows_is_one_statement_not_one_each(wallet_repository, sql):
    stored = wallet_repository.add_all(some_wallets(50))
    sql.clear()

    wallet_repository.update_all([w.evolve(balance=Decimal("2.00")) for w in stored])

    assert len(kind(sql, "UPDATE")) == 1, (
        "an executemany UPDATE is N statements - SQLAlchemy has no updatemanyvalues - so the "
        "batch has to become one statement joined against a derived table, or fifty rows cost "
        "fifty round trips"
    )
    assert {w.balance for w in wallet_repository.all()} == {Decimal("2.0000")}


def test_a_large_batch_is_still_one_statement(registry, clock, sql):
    """The framework sends one statement, however many rows, and never splits a batch by
    itself - splitting one that exceeds the server's bind-parameter ceiling is the caller's,
    because only the caller knows what the pieces mean if one of them fails."""
    wallet_repository = WalletRepository(Wallet, WALLETS, registry, clock=clock)
    stored = wallet_repository.add_all(some_wallets(35))
    sql.clear()

    wallet_repository.update_all([w.evolve(balance=Decimal("3.00")) for w in stored])

    assert len(kind(sql, "UPDATE")) == 1, "35 rows, one UPDATE"
    assert wallet_repository.count(WALLETS.c.balance == Decimal("3.00")) == 35, (
        "and every one landed"
    )


def test_one_row_does_not_build_a_derived_table(wallet_repository, sql):
    stored = wallet_repository.add(some_wallets(1)[0])
    sql.clear()

    wallet_repository.update(stored.evolve(balance=Decimal("9.00")))

    statements = kind(sql, "UPDATE")
    assert len(statements) == 1
    assert "SELECT" not in statements[0][0].upper(), (
        "one row needs no derived table, and building one would cost more than it saves"
    )


def test_a_lookup_of_many_ids_is_one_statement_per_shard(wallet_repository, sql):
    stored = wallet_repository.add_all(some_wallets(30))
    sql.clear()

    found = wallet_repository.find_many([w.id for w in stored])

    assert len(found) == 30
    assert len(kind(sql, "SELECT")) == 1, "not one query per id"


def test_a_lookup_collapses_duplicate_ids(wallet_repository, sql):
    stored = wallet_repository.add_all(some_wallets(3))
    asked = [w.id for w in stored] * 4
    sql.clear()

    found = wallet_repository.find_many(asked)

    assert len(found) == 3, "asking four times for the same id is an accident, not an instruction"
    statement = kind(sql, "SELECT")[0][0]
    assert statement.count("?") <= 4 or "ANY" in statement.upper()


def test_a_lookup_of_many_ids_is_one_query_per_shard(registry, clock, sql):
    wallet_repository = WalletRepository(Wallet, WALLETS, registry, clock=clock)
    stored = wallet_repository.add_all(some_wallets(25))
    sql.clear()

    assert len(wallet_repository.find_many([w.id for w in stored])) == 25
    assert len(kind(sql, "SELECT")) == 1, "25 ids, one SELECT - as findAllByIds does"


def test_postgres_matches_a_list_of_ids_with_any_and_others_with_in(wallet_repository):
    """``IN`` expands to one placeholder per id, so the statement text changes with the length
    of the list and a server-side plan cache misses on every new length. ``ANY`` is one text,
    one parameter, one plan."""
    ids = [uuid4() for _ in range(3)]

    def rendered(dialect_name: str, compile_to: str) -> str:
        from ekbatan.migrations import _dialect

        conn = type("C", (), {"dialect": type("D", (), {"name": dialect_name})})()
        predicate = wallet_repository._id_among(conn, ids)
        return str(select(WALLETS.c.id).where(predicate).compile(dialect=_dialect(compile_to)))

    assert "= ANY" in rendered("postgresql", "postgres")
    assert " IN " in rendered("mysql", "mysql")


def test_reads_do_not_touch_a_shard_they_have_no_business_on(sharded_registry, clock):
    """A read narrowed by id goes to one database. Fanning out would work and would double the
    load for nothing."""
    from ekbatan.sharding import EmbeddedBitsShardingStrategy, ShardIdentifier

    wallet_repository = WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
    )
    mexico = ShardIdentifier(1, 0)
    wallet = Wallet.open(
        id=ShardedId[Wallet].generate(mexico),
        owner_id=uuid4(),
        currency="MXN",
        balance=Decimal("1.00"),
        created_date=clock.instant(),
    )
    wallet_repository.add(wallet)

    touched: list[str] = []
    for shard in sharded_registry.shards:
        engine = sharded_registry.manager(shard).primary.sqlalchemy_engine
        event.listen(
            engine,
            "before_cursor_execute",
            lambda conn, cur, stmt, params, ctx, many, s=str(shard): touched.append(s),
        )

    wallet_repository.find(wallet.id)
    assert touched == ["1:0"], "the id said which database; nothing else was asked"


def test_asking_whether_a_row_exists_does_not_count_them_all(wallet_repository, sql):
    wallet_repository.add_all(some_wallets(20))
    sql.clear()

    assert wallet_repository.exists_where(WALLETS.c.currency == "EUR") is True

    statement = kind(sql, "SELECT")[0][0].upper()
    assert "COUNT" not in statement, (
        "counting reads every matching row to answer yes or no; on a predicate matching a "
        "million rows that is a million rows of work for one bit"
    )
    assert "LIMIT" in statement


def test_a_column_that_needs_no_conversion_is_not_inspected_per_row(wallet_repository):
    """Which conversion a column needs is a property of the model, not of the row.

    Deciding it per field per row means ninety thousand isinstance chains to read ten thousand
    rows, all reaching the same conclusion.
    """
    from ekbatan.repository import _identity

    assert wallet_repository._to_col["currency"] is _identity
    assert wallet_repository._to_col["balance"] is _identity
    assert wallet_repository._to_col["version"] is _identity
    assert wallet_repository._to_col["state"] is not _identity, "an enum is stored by name"
    assert wallet_repository._to_col["id"] is not _identity, "a typed id is unwrapped"
    assert wallet_repository._to_col["created_date"] is _identity, "a time goes in exactly as it is"
    assert wallet_repository._from_col["created_date"] is _identity, (
        "and comes back exactly as read"
    )


def test_a_batch_statement_is_composed_once_per_size(wallet_repository):
    stored = wallet_repository.add_all(some_wallets(6))
    stamped = [w.next_version() for w in stored]

    first, binds = wallet_repository._update_statement("mysql", stamped, returning=False)
    again, _ = wallet_repository._update_statement("mysql", stamped, returning=False)

    assert again is first, (
        "a 500-row UNION ALL costs about 25ms to build, against 5ms for the VALUES form, so it "
        "is composed once per batch size and then only re-bound"
    )
    columns = wallet_repository.to_record(stored[0]).keys()
    assert binds is not None
    assert len(binds) == len(stored) * (len(columns) + 1), (
        "every column the record carries, and the version each row was read at"
    )


# --------------------------------------------------- reading the written rows back, or not


def test_a_reading_write_asks_for_returning_and_a_no_result_one_does_not(
    wallet_repository, sql, registry
):
    """The pair exists so the caller can decide. Making both behave the same would remove the
    choice and leave two names for one thing.

    MySQL has no ``RETURNING`` at all, so there the reading variants rebuild what they wrote
    rather than asking for it back - the same values, one statement either way. Java takes the
    same branch (``if MYSQL: insert.execute(); return List.copyOf(...)``). What has to hold on
    every database is the half that is a promise: the no-result variants never put the rows on
    the wire.
    """
    engine = registry.default.primary.sqlalchemy_engine
    # After a connection, not before: SQLAlchemy's MySQL dialect only learns whether it is
    # talking to MariaDB - and so whether RETURNING exists - in `initialize()`, on first
    # connect. Read cold, both flags are the conservative class default and MariaDB looks like
    # MySQL. MariaDB has had INSERT ... RETURNING since 10.5.
    with engine.connect():
        pass
    reads_back = engine.dialect.insert_returning
    updates_back = engine.dialect.update_returning

    wallet_repository.add_all(some_wallets(3))
    assert ("RETURNING" in kind(sql, "INSERT")[0][0].upper()) is reads_back

    sql.clear()
    wallet_repository.add_all_no_result(some_wallets(3))
    assert "RETURNING" not in kind(sql, "INSERT")[0][0].upper(), (
        "the thousand-row import whose rows nobody reads must not put them on the wire"
    )

    stored = wallet_repository.all()
    sql.clear()
    wallet_repository.update_all([w.evolve(balance=Decimal("2.00")) for w in stored])
    assert ("RETURNING" in kind(sql, "UPDATE")[0][0].upper()) is updates_back

    stored = wallet_repository.all()
    sql.clear()
    wallet_repository.update_all_no_result([w.evolve(balance=Decimal("3.00")) for w in stored])
    assert "RETURNING" not in kind(sql, "UPDATE")[0][0].upper()


def test_a_reading_write_hands_back_the_database_view_in_the_order_asked_for(wallet_repository):
    written = some_wallets(25)
    stored = wallet_repository.add_all(written)

    assert [w.id for w in stored] == [w.id for w in written], (
        "RETURNING makes no promise about order, and a caller handing in a list expects the "
        "same list back"
    )
    assert all(w.events == () for w in stored), "events are not a column"
    assert all(w.balance == Decimal("1.0000") for w in stored), (
        "and the scale is the column's, which is the point of reading it back"
    )

    updated = wallet_repository.update_all([w.evolve(balance=Decimal("4.00")) for w in stored])
    assert [w.id for w in updated] == [w.id for w in stored]
    assert all(w.version == 2 for w in updated)


def test_a_write_with_no_returning_still_costs_one_statement(wallet_repository, sql):
    wallet_repository.add_all_no_result(some_wallets(40))
    assert len(kind(sql, "INSERT")) == 1

    stored = wallet_repository.all()
    sql.clear()
    wallet_repository.update_all_no_result([w.evolve(balance=Decimal("5.00")) for w in stored])
    assert len(kind(sql, "UPDATE")) == 1


def test_a_unit_of_work_does_not_pay_to_read_its_own_writes_back(executor, wallet_repository, sql):
    """The plan already holds what was staged and the events came off those, so the executor
    has no use for the written rows."""
    sql.clear()
    run(
        executor,
        WalletCreateAction(executor.clock),
        WalletCreateAction.Params(
            owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1.00")
        ),
    )

    writes = [s for s, _, _ in sql if s.lstrip().upper().startswith(("INSERT", "UPDATE"))]
    assert writes, "it did write something"
    assert not any("RETURNING" in s.upper() for s in writes), (
        "paying for a result set on every unit of work, for values nobody reads"
    )
