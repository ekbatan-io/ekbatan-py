"""A repository that writes its own query, against a real database.

`tests/unit/test_fetching.py` pins what `FetchResult` does to rows. This pins the part that
only a repository can show: that every accessor really does hand back a `Db`, that the mapper
a repository passes is its own `from_record`, handed each row as its mapping, and that the
scatter form reads across shards the way Java's `readonlyDbs().stream().flatMap(...)` does.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Row, func, select

from ekbatan.domain import DELETED
from ekbatan.fetching import Db
from ekbatan.ids import ShardedId
from ekbatan.repository import AbstractRepository
from ekbatan.sharding import EmbeddedBitsShardingStrategy, ShardIdentifier
from fixtures.domain import NOW, Wallet, WalletState
from fixtures.schema import WALLETS, WalletsRecord
from modes import rebased

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")

HERE = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)


@rebased
class WalletRepository(AbstractRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
    """What a user writes when the built-in finders run out."""

    def to_record(self, wallet: Wallet) -> WalletsRecord:
        return WalletsRecord(
            id=wallet.id.value,
            version=wallet.version,
            state=wallet.state.name,
            owner_id=wallet.owner_id,
            currency=wallet.currency,
            balance=wallet.balance,
            aliases=wallet.aliases,
            reward_points=wallet.reward_points,
            created_date=wallet.created_date,
            updated_date=wallet.updated_date or wallet.created_date,
        )

    def from_record(self, values: WalletsRecord) -> Wallet:
        # Read by column name: the framework hands a row's mapping, never the row itself.
        assert isinstance(values, Mapping) and not isinstance(values, Row), type(values)
        return Wallet(
            id=ShardedId[Wallet](values["id"]),
            version=values["version"],
            state=WalletState[values["state"]],
            owner_id=values["owner_id"],
            currency=values["currency"],
            balance=values["balance"],
            aliases=values["aliases"] or [],
            reward_points=values["reward_points"],
            created_date=values["created_date"],
            updated_date=values["updated_date"],
        )

    def find_all_by_owner(self, owner_id):
        """Scatter, mapped and flattened - the shape of Java's `findAllByOwnerId`."""
        statement = select(WALLETS).where(
            WALLETS.c.owner_id == owner_id, WALLETS.c.state != DELETED
        )
        return [
            wallet
            for _, conn in self.readonly_dbs()
            for wallet in conn.rows(statement).each(self.from_record)
        ]

    def find_richest_on(self, shard):
        statement = (
            select(WALLETS)
            .where(WALLETS.c.state != DELETED)
            .order_by(WALLETS.c.balance.desc())
            .limit(1)
        )
        with self.readonly_db(shard=shard) as conn:
            return conn.rows(statement).fetch_first(self.from_record)

    def currencies_on(self, shard):
        """A projection - the mapper is whatever the caller wants, not `from_record`."""
        statement = select(WALLETS.c.currency).where(WALLETS.c.state != DELETED).distinct()
        with self.readonly_db(shard=shard) as conn:
            return sorted(conn.rows(statement).fetch(lambda row: str(row["currency"])))

    def in_batches_on(self, shard, size):
        with self.readonly_db(shard=shard) as conn:
            statement = select(WALLETS).where(WALLETS.c.state != DELETED)
            return [
                len(batch)
                for batch in conn.rows(statement).yield_per(size).partitions(self.from_record)
            ]

    def total_on(self, shard):
        """No rows to map, so `execute` - unchanged, and the reason `rows` is an addition."""
        with self.readonly_db(shard=shard) as conn:
            statement = select(func.coalesce(func.sum(WALLETS.c.balance), 0)).where(
                WALLETS.c.state != DELETED
            )
            return Decimal(str(conn.execute(statement).scalar_one()))


@pytest.fixture
def wallets(registry, clock):
    return WalletRepository(Wallet, WALLETS, registry, clock=clock)


@pytest.fixture
def sharded_wallets(sharded_registry, clock):
    return WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
    )


def a_wallet(owner, currency="EUR", balance="100.00", shard=HERE):
    return Wallet.open(
        id=ShardedId[Wallet].generate(shard),
        owner_id=owner,
        currency=currency,
        balance=Decimal(balance),
        created_date=NOW,
    )


class TestTheAccessorsHandBackADb:
    """Every public accessor, one assertion each - because a single one left behind would be
    found at the call site, in an editor, long after this change."""

    def test_db(self, wallets):
        with wallets.db() as conn:
            assert isinstance(conn, Db)

    def test_readonly_db(self, wallets):
        with wallets.readonly_db() as conn:
            assert isinstance(conn, Db)

    def test_tx_db_else_db(self, wallets):
        with wallets.tx_db_else_db() as conn:
            assert isinstance(conn, Db)

    def test_dbs(self, wallets):
        assert all(isinstance(conn, Db) for _, conn in wallets.dbs())

    def test_readonly_dbs(self, wallets):
        assert all(isinstance(conn, Db) for _, conn in wallets.readonly_dbs())

    def test_tx_db_inside_a_transaction(self, wallets, registry):
        with registry.default.transaction():
            assert isinstance(wallets.tx_db(), Db)

    def test_the_transaction_itself(self, registry):
        with registry.default.transaction() as tx:
            assert isinstance(tx.connection, Db)
            assert tx.rows(select(WALLETS)).fetch(lambda row: row["id"]) == []


class TestAQueryARepositoryWrites:
    def test_it_maps_with_from_record(self, wallets):
        owner = uuid4()
        wallets.add(a_wallet(owner))
        wallets.add(a_wallet(owner, currency="USD", balance="50.00"))
        wallets.add(a_wallet(uuid4()))

        found = wallets.find_all_by_owner(owner)

        assert len(found) == 2
        assert all(isinstance(w, Wallet) for w in found)
        assert {w.currency for w in found} == {"EUR", "USD"}
        assert sorted(w.balance for w in found) == [Decimal("50.0000"), Decimal("100.0000")]

    def test_a_soft_deleted_row_is_excluded_by_the_query_that_says_so(self, wallets):
        """A repository writing its own query writes its own filter, as the Java one does.
        Nothing applies it on the caller's behalf here."""
        owner = uuid4()
        stored = wallets.add(a_wallet(owner))
        wallets.update(stored.delete())

        assert wallets.find_all_by_owner(owner) == []

    def test_fetch_first_on_an_empty_table(self, wallets):
        assert wallets.find_richest_on(HERE) is None

    def test_fetch_first_takes_the_top_row(self, wallets):
        owner = uuid4()
        wallets.add(a_wallet(owner, balance="10.00"))
        wallets.add(a_wallet(owner, balance="999.00"))

        richest = wallets.find_richest_on(HERE)

        assert richest is not None
        assert richest.balance == Decimal("999.0000")

    def test_a_mapper_that_is_not_from_record(self, wallets):
        owner = uuid4()
        wallets.add(a_wallet(owner, currency="EUR"))
        wallets.add(a_wallet(owner, currency="USD"))
        wallets.add(a_wallet(owner, currency="EUR"))

        assert wallets.currencies_on(HERE) == ["EUR", "USD"]

    def test_batches(self, wallets):
        owner = uuid4()
        for _ in range(5):
            wallets.add(a_wallet(owner))

        assert wallets.in_batches_on(HERE, 2) == [2, 2, 1]

    def test_execute_still_answers_what_has_no_rows(self, wallets):
        owner = uuid4()
        wallets.add(a_wallet(owner, balance="1.50"))
        wallets.add(a_wallet(owner, balance="2.50"))

        assert wallets.total_on(HERE) == Decimal("4.0000")


class TestScatter:
    def test_it_reads_from_every_shard(self, sharded_wallets):
        owner = uuid4()
        sharded_wallets.add(a_wallet(owner, currency="EUR", shard=HERE))
        sharded_wallets.add(a_wallet(owner, currency="MXN", shard=MEXICO))

        found = sharded_wallets.find_all_by_owner(owner)

        assert {w.currency for w in found} == {"EUR", "MXN"}, (
            "a predicate says nothing about where its rows are, so the query asks all of them"
        )

    def test_each_shard_answers_only_for_itself(self, sharded_wallets):
        owner = uuid4()
        sharded_wallets.add(a_wallet(owner, currency="EUR", balance="7.00", shard=HERE))
        sharded_wallets.add(a_wallet(owner, currency="MXN", balance="11.00", shard=MEXICO))

        assert sharded_wallets.total_on(HERE) == Decimal("7.0000")
        assert sharded_wallets.total_on(MEXICO) == Decimal("11.0000")
