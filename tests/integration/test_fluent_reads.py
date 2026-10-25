"""A repository that writes its own query, against a real database.

`tests/unit/test_fetching.py` pins what `FetchResult` does to rows. This pins the part that
only a repository can show: that every accessor really does hand back a `Db`, that the mapper
a repository passes is its own `from_record`, handed each row as the table's record - a
`WalletsRecord`, not the row's mapping - and that the
scatter form reads across shards the way Java's `readonlyDbs().stream().flatMap(...)` does.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

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
        # The table's record, built from the row - never the row, never its mapping - on every
        # path that reads: the repository's own finders and `rows(...)`'s terminals alike.
        assert type(values) is WalletsRecord, type(values)
        return Wallet(
            id=ShardedId[Wallet](values.column_id()),
            version=values.column_version(),
            state=WalletState[values.column_state()],
            owner_id=values.column_owner_id(),
            currency=values.column_currency(),
            balance=values.column_balance(),
            aliases=values.column_aliases() or [],
            reward_points=values.column_reward_points(),
            created_date=values.column_created_date(),
            updated_date=values.column_updated_date(),
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
            return conn.rows(statement).fetch_any(self.from_record)

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
def wallet_repository(registry, clock):
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

    def test_db(self, wallet_repository):
        with wallet_repository.db() as conn:
            assert isinstance(conn, Db)

    def test_readonly_db(self, wallet_repository):
        with wallet_repository.readonly_db() as conn:
            assert isinstance(conn, Db)

    def test_tx_db_else_db(self, wallet_repository):
        with wallet_repository.tx_db_else_db() as conn:
            assert isinstance(conn, Db)

    def test_dbs(self, wallet_repository):
        assert all(isinstance(conn, Db) for _, conn in wallet_repository.dbs())

    def test_readonly_dbs(self, wallet_repository):
        assert all(isinstance(conn, Db) for _, conn in wallet_repository.readonly_dbs())

    def test_tx_db_inside_a_transaction(self, wallet_repository, registry):
        with registry.default.transaction():
            assert isinstance(wallet_repository.tx_db(), Db)

    def test_the_transaction_itself(self, registry):
        with registry.default.transaction() as tx:
            assert isinstance(tx.connection, Db)
            assert tx.rows(select(WALLETS)).fetch(lambda row: row["id"]) == []


class TestAQueryARepositoryWrites:
    def test_it_maps_with_from_record(self, wallet_repository):
        owner = uuid4()
        wallet_repository.add(a_wallet(owner))
        wallet_repository.add(a_wallet(owner, currency="USD", balance="50.00"))
        wallet_repository.add(a_wallet(uuid4()))

        found = wallet_repository.find_all_by_owner(owner)

        assert len(found) == 2
        assert all(isinstance(w, Wallet) for w in found)
        assert {w.currency for w in found} == {"EUR", "USD"}
        assert sorted(w.balance for w in found) == [Decimal("50.0000"), Decimal("100.0000")]

    def test_a_soft_deleted_row_is_excluded_by_the_query_that_says_so(self, wallet_repository):
        """A repository writing its own query writes its own filter, as the Java one does.
        Nothing applies it on the caller's behalf here."""
        owner = uuid4()
        stored = wallet_repository.add(a_wallet(owner))
        wallet_repository.update(stored.delete())

        assert wallet_repository.find_all_by_owner(owner) == []

    def test_fetch_any_on_an_empty_table(self, wallet_repository):
        assert wallet_repository.find_richest_on(HERE) is None

    def test_fetch_any_takes_the_top_row(self, wallet_repository):
        owner = uuid4()
        wallet_repository.add(a_wallet(owner, balance="10.00"))
        wallet_repository.add(a_wallet(owner, balance="999.00"))

        richest = wallet_repository.find_richest_on(HERE)

        assert richest is not None
        assert richest.balance == Decimal("999.0000")

    def test_a_mapper_that_is_not_from_record(self, wallet_repository):
        owner = uuid4()
        wallet_repository.add(a_wallet(owner, currency="EUR"))
        wallet_repository.add(a_wallet(owner, currency="USD"))
        wallet_repository.add(a_wallet(owner, currency="EUR"))

        assert wallet_repository.currencies_on(HERE) == ["EUR", "USD"]

    def test_batches(self, wallet_repository):
        owner = uuid4()
        for _ in range(5):
            wallet_repository.add(a_wallet(owner))

        assert wallet_repository.in_batches_on(HERE, 2) == [2, 2, 1]

    def test_execute_still_answers_what_has_no_rows(self, wallet_repository):
        owner = uuid4()
        wallet_repository.add(a_wallet(owner, balance="1.50"))
        wallet_repository.add(a_wallet(owner, balance="2.50"))

        assert wallet_repository.total_on(HERE) == Decimal("4.0000")


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
