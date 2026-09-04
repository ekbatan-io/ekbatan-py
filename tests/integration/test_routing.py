"""The connection a query runs on, and the shard it runs against.

A repository subclass writing its own query needs a sanctioned way to get a routed,
transaction-aware connection. That is what `db`, `readonly_db`, `tx_db` and `tx_db_else_db`
are; the built-in reads and writes go through exactly the same four.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from ekbatan.errors import CrossShardError, UnsupportedError, ValidationError
from ekbatan.repository import Repository
from ekbatan.sharding import EmbeddedBitsSharding, NoSharding, ShardIdentifier
from fixtures.domain import Wallet, WalletId
from fixtures.schema import EVENTS, WALLETS

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)


@pytest.fixture
def sharded_wallets(sharded_registry, clock):
    return Repository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsSharding(), clock=clock
    )


def a_wallet(shard: ShardIdentifier, currency: str = "EUR") -> Wallet:
    return Wallet.open(
        id=WalletId.generate(shard),
        owner_id=uuid4(),
        currency=currency,
        balance=Decimal("1.00"),
    )


# ------------------------------------------------------------------ picking a shard


def test_a_shard_can_be_named_by_id_by_row_or_directly(sharded_wallets):
    wallet = a_wallet(MEXICO)
    assert sharded_wallets.shard_for(wallet.id) == MEXICO
    assert sharded_wallets.shard_of(wallet) == MEXICO
    assert sharded_wallets.shard_of_all([wallet, a_wallet(MEXICO)]) == MEXICO


def test_an_id_naming_an_unconfigured_shard_falls_back(sharded_wallets):
    stranger = WalletId.generate(ShardIdentifier(9, 3))
    assert sharded_wallets.shard_for(stranger) == GLOBAL, (
        "a staging database with one shard still has to read production's ids"
    )


def test_a_batch_spanning_shards_is_refused(sharded_wallets):
    with pytest.raises(CrossShardError, match="no cross-shard transaction"):
        sharded_wallets.shard_of_all([a_wallet(GLOBAL), a_wallet(MEXICO)])

    with pytest.raises(CrossShardError):
        sharded_wallets.add_all([a_wallet(GLOBAL), a_wallet(MEXICO)])

    assert sharded_wallets.count() == 0, "nothing was written before the refusal"


def test_a_strategy_that_cannot_route_from_an_id_says_so(sharded_registry, clock):
    class ByTenantColumn:
        uses_shard_aware_id = False

        def shard_for_id(self, key: object) -> ShardIdentifier | None:
            return None

        def shard_for(self, persistable: object) -> ShardIdentifier | None:
            return MEXICO

    wallets = Repository(Wallet, WALLETS, sharded_registry, sharding=ByTenantColumn(), clock=clock)

    assert wallets.shard_of(a_wallet(GLOBAL)) == MEXICO, "the row still routes"
    with pytest.raises(UnsupportedError, match="override shard_for"):
        wallets.shard_for(uuid4())


def test_without_sharding_everything_is_the_default_shard(registry, clock):
    wallets = Repository(Wallet, WALLETS, registry, sharding=NoSharding(), clock=clock)
    assert wallets.shard_for(uuid4()) == registry.default_shard
    assert wallets.shard_of_all([a_wallet(GLOBAL), a_wallet(MEXICO)]) == registry.default_shard


# ------------------------------------------------------------ getting a connection


def test_tx_db_else_db_joins_an_open_transaction(registry, wallets):
    manager = registry.default
    with manager.transaction() as outer:
        with wallets.tx_db_else_db() as joined:
            assert joined is outer
        assert wallets.tx_db() is outer

    assert wallets.tx_db() is None, "outside one, there is no transaction to join"
    with wallets.tx_db_else_db() as standalone:
        assert standalone is not None, "and it opens a short one of its own"


def test_db_deliberately_does_not_join(registry, wallets):
    manager = registry.default
    with manager.transaction() as outer, wallets.db() as separate:
        assert separate is not outer, (
            "db() is the escape hatch; tx_db_else_db is the one repository code should use"
        )


def test_a_write_inside_a_transaction_joins_it(registry, wallets):
    wallet = Wallet.open(
        id=WalletId.random(), owner_id=uuid4(), currency="EUR", balance=Decimal("1.00")
    )
    try:
        with registry.default.transaction():
            wallets.add(wallet)
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert wallets.find(wallet.id) is None, (
        "the repository wrote inside the caller's transaction, so the rollback took it"
    )


def test_readonly_db_inside_a_transaction_sees_its_writes(registry, wallets):
    wallet = Wallet.open(
        id=WalletId.random(), owner_id=uuid4(), currency="EUR", balance=Decimal("7.00")
    )
    with registry.default.transaction():
        wallets.add(wallet)
        assert wallets.find(wallet.id) is not None, (
            "a read inside a transaction must see what that transaction wrote, so it cannot "
            "be served by a replica"
        )


def test_dbs_visits_every_shard(sharded_registry, sharded_wallets):
    sharded_wallets.add(a_wallet(GLOBAL))
    sharded_wallets.add(a_wallet(MEXICO, "MXN"))

    counts = {
        shard: conn.execute(select(func.count()).select_from(WALLETS)).scalar_one()
        for shard, conn in sharded_wallets.readonly_dbs()
    }
    assert counts == {GLOBAL: 1, MEXICO: 1}

    visited = [shard for shard, _ in sharded_wallets.dbs()]
    assert visited == list(sharded_registry.shards)


def test_a_subclass_can_write_its_own_routed_query(sharded_registry, clock):
    class WalletRepository(Repository[Wallet, UUID]):
        def total_balance_on(self, shard: ShardIdentifier) -> Decimal:
            with self.readonly_db(shard=shard) as conn:
                return conn.execute(
                    select(func.coalesce(func.sum(WALLETS.c.balance), 0)).where(*self._live())
                ).scalar_one()

        def currency_of(self, key: UUID) -> str | None:
            with self.readonly_db(key=key) as conn:
                return conn.execute(
                    select(self.column("currency")).where(self.id_matches(key))
                ).scalar_one_or_none()

    wallets = WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsSharding(), clock=clock
    )
    here, there = a_wallet(GLOBAL), a_wallet(MEXICO, "MXN")
    wallets.add(here)
    wallets.add(there)

    assert wallets.total_balance_on(GLOBAL) == Decimal("1.0000")
    assert wallets.total_balance_on(MEXICO) == Decimal("1.0000")
    assert wallets.currency_of(there.id) == "MXN", "routed by the id, with no shard named"


def test_column_refuses_a_name_the_table_does_not_have(wallets):
    assert wallets.column("balance") is WALLETS.c.balance
    with pytest.raises(ValidationError, match="no column 'nonsense'"):
        wallets.column("nonsense")


# ------------------------------------------------------------------ no-result writes


def test_the_no_result_writes_write(wallets):
    one = Wallet.open(
        id=WalletId.random(), owner_id=uuid4(), currency="EUR", balance=Decimal("1.00")
    )
    wallets.add_no_result(one)
    assert wallets.get(one.id).balance == Decimal("1.0000")

    many = [
        Wallet.open(id=WalletId.random(), owner_id=uuid4(), currency="EUR", balance=Decimal("2.00"))
        for _ in range(3)
    ]
    wallets.add_all_no_result(many)
    assert wallets.count() == 4

    wallets.update_no_result(wallets.get(one.id).evolve(balance=Decimal("9.00")))
    assert wallets.get(one.id).balance == Decimal("9.0000")

    refreshed = [wallets.get(m.id).evolve(balance=Decimal("5.00")) for m in many]
    wallets.update_all_no_result(refreshed)
    assert {w.balance for w in wallets.find_many([m.id for m in many])} == {Decimal("5.0000")}


def test_the_dialect_is_reachable_for_the_query_that_has_to_differ(wallets, registry):
    assert wallets.dialect() == registry.dialect
    assert wallets.dialect(shard=registry.default_shard) == "sqlite"


# ------------------------------------------------- the executor asks the same question


class _ByCurrency:
    """Routes on a field the id says nothing about - charter rule 7's extension point."""

    uses_shard_aware_id = False

    def shard_for_id(self, key):
        return None

    def shard_for(self, persistable):
        return MEXICO if getattr(persistable, "currency", "EUR") == "MXN" else GLOBAL


def _executor_over(registry, repo, clock):
    from ekbatan.action import ActionExecutor
    from fixtures.schema import EVENTS

    return ActionExecutor(
        namespace="example.wallet",
        registry=registry,
        repositories=[repo],
        events=EVENTS,
        clock=clock,
    )


def _open_wallet(currency):
    from ekbatan.action import Action

    class OpenWallet(Action[str, Wallet]):
        def perform(self, params):
            return self.plan.add(
                Wallet.open(
                    id=WalletId.random(),
                    owner_id=uuid4(),
                    currency=params,
                    balance=Decimal("1.00"),
                )
            )

    return OpenWallet(), currency


def test_a_strategy_that_cannot_route_by_id_alone_still_runs(sharded_registry, clock, outbox):
    """The repository could always route these; only the executor could not."""
    repo = Repository(Wallet, WALLETS, sharded_registry, sharding=_ByCurrency(), clock=clock)
    executor = _executor_over(sharded_registry, repo, clock)

    action, params = _open_wallet("MXN")
    wallet = executor.execute(action, params)

    assert repo.find_where(WALLETS.c.currency == "MXN", shard=MEXICO)[0].id == wallet.id


def test_state_and_its_events_land_on_one_shard(sharded_registry, clock):
    """The id and the row can name different shards. When they do, the state and the outbox
    row must still be one transaction on one database, or the whole promise is gone."""
    repo = Repository(Wallet, WALLETS, sharded_registry, sharding=_ByCurrency(), clock=clock)
    executor = _executor_over(sharded_registry, repo, clock)

    action, params = _open_wallet("MXN")
    executor.execute(action, params)

    rows = {
        shard: (
            conn.execute(select(func.count()).select_from(WALLETS)).scalar_one(),
            conn.execute(select(func.count()).select_from(EVENTS)).scalar_one(),
        )
        for shard, conn in repo.readonly_dbs()
    }
    assert rows == {GLOBAL: (0, 0), MEXICO: (1, 1)}, (
        "the row went one way and its events the other, in two transactions"
    )


def test_a_scatter_read_stops_at_the_first_shard_that_answers(sharded_registry, clock):
    """Java's findOneWhere short-circuits; a fan-out that always visits every shard turns a
    one-row lookup into one query per database for the whole life of the deployment."""
    from sqlalchemy import event

    repo = Repository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsSharding(), clock=clock
    )
    repo.add(a_wallet(GLOBAL, "SEK"))  # the only match, on the first shard visited

    selects = dict.fromkeys(sharded_registry.shards, 0)

    def watch(shard):
        def listener(conn, cursor, statement, params, context, many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects[shard] += 1

        return listener

    for shard in sharded_registry.shards:
        event.listen(sharded_registry.manager(shard).primary, "before_cursor_execute", watch(shard))

    assert repo.find_one_where(WALLETS.c.currency == "SEK") is not None
    assert selects == {GLOBAL: 1, MEXICO: 0}

    selects[GLOBAL] = 0
    assert repo.exists_where(WALLETS.c.currency == "SEK") is True
    assert selects == {GLOBAL: 1, MEXICO: 0}
