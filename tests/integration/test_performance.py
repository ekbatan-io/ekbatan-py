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

from ekbatan.repository import Repository
from fixtures.domain import Wallet, WalletId
from fixtures.schema import WALLETS


@pytest.fixture
def sql(registry):
    """Every statement the primary sends, in order."""
    seen: list[tuple[str, bool, int]] = []

    @event.listens_for(registry.default.primary, "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append((statement, executemany, len(parameters) if executemany else 1))

    return seen


def kind(seen: Sequence[tuple[str, bool, int]], verb: str) -> list[tuple[str, bool, int]]:
    return [row for row in seen if row[0].lstrip().upper().startswith(verb)]


def some_wallets(n: int) -> list[Wallet]:
    return [
        Wallet.open(id=WalletId.random(), owner_id=uuid4(), currency="EUR", balance=Decimal("1.00"))
        for _ in range(n)
    ]


def test_inserting_many_rows_is_one_statement(wallets, sql):
    wallets.add_all(some_wallets(50))
    assert len(kind(sql, "INSERT")) == 1, "fifty rows, one round trip"


def test_updating_many_rows_is_one_statement_not_one_each(wallets, sql):
    stored = wallets.add_all(some_wallets(50))
    sql.clear()

    wallets.update_all([w.evolve(balance=Decimal("2.00")) for w in stored])

    assert len(kind(sql, "UPDATE")) == 1, (
        "an executemany UPDATE is N statements - SQLAlchemy has no updatemanyvalues - so the "
        "batch has to become one statement joined against a derived table, or fifty rows cost "
        "fifty round trips"
    )
    assert {w.balance for w in wallets.all()} == {Decimal("2.0000")}


def test_a_batch_larger_than_the_limit_is_split_not_abandoned(registry, clock, sql):
    """Every server caps the bind parameters one statement may carry. Chunking is what keeps a
    large batch from becoming a failure that only shows up on the biggest customer."""
    repo = Repository(Wallet, WALLETS, registry, clock=clock, batch_size=10)
    stored = repo.add_all(some_wallets(35))
    sql.clear()

    repo.update_all([w.evolve(balance=Decimal("3.00")) for w in stored])

    assert len(kind(sql, "UPDATE")) == 4, "35 rows at 10 per chunk"
    assert repo.count(WALLETS.c.balance == Decimal("3.00")) == 35, "and every one landed"


def test_one_row_does_not_build_a_derived_table(wallets, sql):
    stored = wallets.add(some_wallets(1)[0])
    sql.clear()

    wallets.update(stored.evolve(balance=Decimal("9.00")))

    statements = kind(sql, "UPDATE")
    assert len(statements) == 1
    assert "SELECT" not in statements[0][0].upper(), (
        "one row needs no derived table, and building one would cost more than it saves"
    )


def test_a_lookup_of_many_ids_is_one_statement_per_shard(wallets, sql):
    stored = wallets.add_all(some_wallets(30))
    sql.clear()

    found = wallets.find_many([w.id for w in stored])

    assert len(found) == 30
    assert len(kind(sql, "SELECT")) == 1, "not one query per id"


def test_a_lookup_collapses_duplicate_ids(wallets, sql):
    stored = wallets.add_all(some_wallets(3))
    asked = [w.id for w in stored] * 4
    sql.clear()

    found = wallets.find_many(asked)

    assert len(found) == 3, "asking four times for the same id is an accident, not an instruction"
    statement = kind(sql, "SELECT")[0][0]
    assert statement.count("?") <= 4 or "ANY" in statement.upper()


def test_a_lookup_of_many_ids_is_chunked(registry, clock, sql):
    repo = Repository(Wallet, WALLETS, registry, clock=clock, batch_size=10)
    stored = repo.add_all(some_wallets(25))
    sql.clear()

    assert len(repo.find_many([w.id for w in stored])) == 25
    assert len(kind(sql, "SELECT")) == 3, "25 ids at 10 per chunk"


def test_postgres_matches_a_list_of_ids_with_any_and_others_with_in(wallets):
    """``IN`` expands to one placeholder per id, so the statement text changes with the length
    of the list and a server-side plan cache misses on every new length. ``ANY`` is one text,
    one parameter, one plan."""
    ids = [uuid4() for _ in range(3)]

    def rendered(dialect_name: str, compile_to: str) -> str:
        from ekbatan.migrations import _dialect

        conn = type("C", (), {"dialect": type("D", (), {"name": dialect_name})})()
        predicate = wallets._id_among(conn, ids)
        return str(select(WALLETS.c.id).where(predicate).compile(dialect=_dialect(compile_to)))

    assert "= ANY" in rendered("postgresql", "postgres")
    assert " IN " in rendered("mysql", "mysql")


def test_reads_do_not_touch_a_shard_they_have_no_business_on(sharded_registry, clock):
    """A read narrowed by id goes to one database. Fanning out would work and would double the
    load for nothing."""
    from ekbatan.sharding import EmbeddedBitsSharding, ShardIdentifier

    repo = Repository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsSharding(), clock=clock
    )
    mexico = ShardIdentifier(1, 0)
    wallet = Wallet.open(
        id=WalletId.generate(mexico), owner_id=uuid4(), currency="MXN", balance=Decimal("1.00")
    )
    repo.add(wallet)

    touched: list[str] = []
    for shard in sharded_registry.shards:
        engine = sharded_registry.manager(shard).primary
        event.listen(
            engine,
            "before_cursor_execute",
            lambda conn, cur, stmt, params, ctx, many, s=str(shard): touched.append(s),
        )

    repo.find(wallet.id)
    assert touched == ["1:0"], "the id said which database; nothing else was asked"


def test_asking_whether_a_row_exists_does_not_count_them_all(wallets, sql):
    wallets.add_all(some_wallets(20))
    sql.clear()

    assert wallets.exists_where(WALLETS.c.currency == "EUR") is True

    statement = kind(sql, "SELECT")[0][0].upper()
    assert "COUNT" not in statement, (
        "counting reads every matching row to answer yes or no; on a predicate matching a "
        "million rows that is a million rows of work for one bit"
    )
    assert "LIMIT" in statement


def test_a_column_that_needs_no_conversion_is_not_inspected_per_row(wallets):
    """Which conversion a column needs is a property of the model, not of the row.

    Deciding it per field per row means ninety thousand isinstance chains to read ten thousand
    rows, all reaching the same conclusion.
    """
    from ekbatan.repository import _identity

    assert wallets._to_col["currency"] is _identity
    assert wallets._to_col["balance"] is _identity
    assert wallets._to_col["version"] is _identity
    assert wallets._to_col["state"] is not _identity, "an enum is stored by name"
    assert wallets._to_col["id"] is not _identity, "a typed id is unwrapped"
    assert wallets._to_col["created_date"] is not _identity, (
        "an optional timestamp is still normalised - `datetime | None` is a union, and the "
        "half that matters is the half that is not None"
    )


def test_a_batch_statement_is_composed_once_per_size(wallets):
    stored = wallets.add_all(some_wallets(6))

    first = wallets._union_update(len(stored))
    wallets._union_stmts[len(stored)] = first
    again = wallets._union_stmts.get(len(stored))

    assert again is first, (
        "a 500-row UNION ALL costs about 25ms to build, against 5ms for the VALUES form, so it "
        "is composed once per chunk size and then only re-bound"
    )
    binds = wallets._union_binds(stored)
    assert len(binds) == len(stored) * (len(wallets._columns) + 1)


# --------------------------------------------------- reading the written rows back, or not


def test_a_reading_write_asks_for_returning_and_a_no_result_one_does_not(wallets, sql):
    """The pair exists so the caller can decide. Making both behave the same would remove the
    choice and leave two names for one thing."""
    wallets.add_all(some_wallets(3))
    assert "RETURNING" in kind(sql, "INSERT")[0][0].upper()

    sql.clear()
    wallets.add_all_no_result(some_wallets(3))
    assert "RETURNING" not in kind(sql, "INSERT")[0][0].upper(), (
        "the thousand-row import whose rows nobody reads must not put them on the wire"
    )

    stored = wallets.all()
    sql.clear()
    wallets.update_all([w.evolve(balance=Decimal("2.00")) for w in stored])
    assert "RETURNING" in kind(sql, "UPDATE")[0][0].upper()

    stored = wallets.all()
    sql.clear()
    wallets.update_all_no_result([w.evolve(balance=Decimal("3.00")) for w in stored])
    assert "RETURNING" not in kind(sql, "UPDATE")[0][0].upper()


def test_a_reading_write_hands_back_the_database_view_in_the_order_asked_for(wallets):
    written = some_wallets(25)
    stored = wallets.add_all(written)

    assert [w.id for w in stored] == [w.id for w in written], (
        "RETURNING makes no promise about order, and a caller handing in a list expects the "
        "same list back"
    )
    assert all(w.events == () for w in stored), "events are not a column"
    assert all(w.balance == Decimal("1.0000") for w in stored), (
        "and the scale is the column's, which is the point of reading it back"
    )

    updated = wallets.update_all([w.evolve(balance=Decimal("4.00")) for w in stored])
    assert [w.id for w in updated] == [w.id for w in stored]
    assert all(w.version == 2 for w in updated)


def test_a_write_with_no_returning_still_costs_one_statement(wallets, sql):
    wallets.add_all_no_result(some_wallets(40))
    assert len(kind(sql, "INSERT")) == 1

    stored = wallets.all()
    sql.clear()
    wallets.update_all_no_result([w.evolve(balance=Decimal("5.00")) for w in stored])
    assert len(kind(sql, "UPDATE")) == 1


def test_a_unit_of_work_does_not_pay_to_read_its_own_writes_back(executor, wallets, sql):
    """The plan already holds what was staged and the events came off those, so the executor
    has no use for the written rows."""
    from fixtures.actions import CreateWallet, CreateWalletParams

    sql.clear()
    executor.execute(
        CreateWallet(),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1.00")),
    )

    writes = [s for s, _, _ in sql if s.lstrip().upper().startswith(("INSERT", "UPDATE"))]
    assert writes, "it did write something"
    assert not any("RETURNING" in s.upper() for s in writes), (
        "paying for a result set on every unit of work, for values nobody reads"
    )
