"""The connection a query runs on, and the shard it runs against.

A repository subclass writing its own query needs a sanctioned way to get a routed,
transaction-aware connection. That is what `db`, `readonly_db`, `tx_db` and `tx_db_else_db`
are; the built-in reads and writes go through exactly the same four.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Row, func, select

from ekbatan.clock import to_storage
from ekbatan.errors import CrossShardError, UnsupportedError, ValidationError
from ekbatan.ids import ShardedId
from ekbatan.repository import Repository
from ekbatan.sharding import EmbeddedBitsShardingStrategy, NoShardingStrategy, ShardIdentifier
from fixtures.domain import NOW, Wallet, WalletState
from fixtures.executing import run
from fixtures.repositories import WalletRepository
from fixtures.schema import EVENTS, WALLETS

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)


@pytest.fixture
def sharded_wallets(sharded_registry, clock):
    return WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
    )


def a_wallet(shard: ShardIdentifier, currency: str = "EUR") -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].generate(shard),
        owner_id=uuid4(),
        currency=currency,
        balance=Decimal("1.00"),
        created_date=NOW,
    )


# ------------------------------------------------------------------ picking a shard


def test_a_shard_can_be_named_by_id_by_row_or_directly(sharded_wallets):
    wallet = a_wallet(MEXICO)
    assert sharded_wallets.shard_for(wallet.id) == MEXICO
    assert sharded_wallets.shard_of(wallet) == MEXICO
    assert sharded_wallets.shard_of_all([wallet, a_wallet(MEXICO)]) == MEXICO


def test_an_id_naming_an_unconfigured_shard_falls_back(sharded_wallets):
    stranger = ShardedId[Wallet].generate(ShardIdentifier(9, 3))
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

    wallets = WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=ByTenantColumn(), clock=clock
    )

    assert wallets.shard_of(a_wallet(GLOBAL)) == MEXICO, "the row still routes"
    with pytest.raises(UnsupportedError, match="override shard_for"):
        wallets.shard_for(uuid4())


def test_without_sharding_everything_is_the_default_shard(registry, clock):
    wallets = WalletRepository(
        Wallet, WALLETS, registry, sharding=NoShardingStrategy(), clock=clock
    )
    assert wallets.shard_for(uuid4()) == registry.default_shard
    assert wallets.shard_of_all([a_wallet(GLOBAL), a_wallet(MEXICO)]) == registry.default_shard


# ------------------------------------------------------------ getting a connection


def test_tx_db_else_db_joins_an_open_transaction(registry, wallets):
    manager = registry.default
    with manager.transaction() as outer:
        assert outer.shard == manager.shard, "the block knows which shard it is on"
        assert outer.dialect == manager.dialect
        with wallets.tx_db_else_db() as joined:
            assert joined is outer.connection
        assert wallets.tx_db() is outer.connection

    assert wallets.tx_db() is None, "outside one, there is no transaction to join"
    with wallets.tx_db_else_db() as standalone:
        assert standalone is not None, "and each statement on it commits itself"


def test_db_deliberately_does_not_join(registry, wallets):
    manager = registry.default
    with manager.transaction() as outer, wallets.db() as separate:
        assert separate is not outer.connection, (
            "db() is the escape hatch; tx_db_else_db is the one repository code should use"
        )


def test_a_write_inside_a_transaction_joins_it(registry, wallets):
    wallet = Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        created_date=NOW,
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
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("7.00"),
        created_date=NOW,
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
        def to_row(self, wallet: Wallet) -> dict[str, Any]:
            return {
                "id": wallet.id.value,
                "version": wallet.version,
                "state": wallet.state.name,
                "owner_id": wallet.owner_id,
                "currency": wallet.currency,
                "balance": wallet.balance,
                "aliases": wallet.aliases,
                "reward_points": wallet.reward_points,
                "created_date": to_storage(wallet.created_date),
                "updated_date": to_storage(wallet.updated_date or wallet.created_date),
            }

        def from_row(self, row: Row[Any] | Mapping[str, Any]) -> Wallet:
            values = row._mapping if isinstance(row, Row) else row
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
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
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
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        created_date=NOW,
    )
    wallets.add_no_result(one)
    assert wallets.get(one.id).balance == Decimal("1.0000")

    many = [
        Wallet.open(
            id=ShardedId[Wallet].random(),
            owner_id=uuid4(),
            currency="EUR",
            balance=Decimal("2.00"),
            created_date=NOW,
        )
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
    assert wallets.dialect(shard=registry.default_shard) == registry.dialect


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


@dataclass(frozen=True, kw_only=True)
class OpenWalletParams:
    """A params object rather than a bare `str`, because the outbox stores these as a JSON
    object and a scalar there is a row no typed consumer can read. Java refuses the scalar for
    the same reason."""

    currency: str


def _open_wallet(currency, clock):
    from ekbatan.action import Action

    class OpenWalletAction(Action[OpenWalletParams, Wallet]):
        def perform(self, principal: str | None, params):
            return self.plan.add(
                Wallet.open(
                    id=ShardedId[Wallet].random(),
                    owner_id=uuid4(),
                    currency=params.currency,
                    balance=Decimal("1.00"),
                    created_date=self.clock.now(),
                )
            )

    return OpenWalletAction(clock), OpenWalletParams(currency=currency)


def test_a_strategy_that_cannot_route_by_id_alone_still_runs(sharded_registry, clock, outbox):
    """The repository could always route these; only the executor could not."""
    repo = WalletRepository(Wallet, WALLETS, sharded_registry, sharding=_ByCurrency(), clock=clock)
    executor = _executor_over(sharded_registry, repo, clock)

    action, params = _open_wallet("MXN", clock)
    wallet = run(executor, action, params)

    assert repo.find_where(WALLETS.c.currency == "MXN", shard=MEXICO)[0].id == wallet.id


def test_state_and_its_events_land_on_one_shard(sharded_registry, clock):
    """The id and the row can name different shards. When they do, the state and the outbox
    row must still be one transaction on one database, or the whole promise is gone."""
    repo = WalletRepository(Wallet, WALLETS, sharded_registry, sharding=_ByCurrency(), clock=clock)
    executor = _executor_over(sharded_registry, repo, clock)

    action, params = _open_wallet("MXN", clock)
    run(executor, action, params)

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

    repo = WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
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


# ------------------------------------------- the repository opens no transaction of its own


def _statements(engine):
    from sqlalchemy import event

    seen: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda c, cur, s, p, ctx, many: seen.append(s.split()[0].upper()),
    )
    return seen


def test_a_write_outside_a_unit_of_work_opens_no_transaction(registry, wallets):
    """Java's repository never opens one: inside a transaction it joins, outside one each
    statement commits itself. Opening one here would decide what is atomic on the caller's
    behalf, which is theirs to decide."""
    engine = registry.default.primary
    seen = _statements(engine)

    wallet = Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        created_date=NOW,
    )
    wallets.add(wallet)

    assert seen == ["INSERT"], "one statement, no BEGIN of the framework's own"

    from sqlalchemy import create_engine

    # `str(url)` masks the password as `***`, which connects only to a server that has
    # none - the reason this read passed on SQLite and failed on all three real servers.
    with create_engine(engine.url.render_as_string(hide_password=False)).connect() as fresh:
        found = fresh.execute(select(WALLETS.c.id).where(WALLETS.c.id == wallet.id.value)).first()
    assert found is not None, "and it committed - a separate connection sees it"


def test_a_batch_is_one_statement_however_many_rows(registry, wallets):
    """Nothing is chunked. Splitting a batch past the server's bind-parameter ceiling is the
    caller's, because only they know what the pieces mean if one of them fails."""
    seen = _statements(registry.default.primary)

    many = [
        Wallet.open(
            id=ShardedId[Wallet].random(),
            owner_id=uuid4(),
            currency="EUR",
            balance=Decimal("1.00"),
            created_date=NOW,
        )
        for _ in range(250)
    ]
    stored = wallets.add_all(many)
    assert seen.count("INSERT") == 1, "250 rows, one INSERT, even reading them back"

    seen.clear()
    wallets.update_all([w.deposit(Decimal("1.00")) for w in stored])
    assert seen.count("UPDATE") == 1, "250 rows, one UPDATE"

    seen.clear()
    assert len(wallets.find_many([w.id for w in stored])) == 250
    assert seen.count("SELECT") == 1, "250 ids, one SELECT"


def test_the_block_is_told_which_shard_it_is_on(sharded_registry, clock):
    """Java hands its block a DSLContext bound to one shard's connection. The counterpart here
    is a Transaction, which says the same and can also be asked which shard that is."""
    from ekbatan import Transaction

    repo = WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
    )

    for shard in sharded_registry.shards:
        with sharded_registry.manager(shard).transaction() as tx:
            assert isinstance(tx, Transaction)
            assert tx.shard == shard
            assert tx.dialect == sharded_registry.manager(shard).dialect

            # a raw statement runs on it directly, the way jOOQ's context does
            assert tx.execute(select(func.count()).select_from(WALLETS)).scalar_one() == 0

            # and a repository call joins it without being handed anything
            repo.add(a_wallet(shard))
            assert repo.tx_db(shard=shard) is tx.connection

    counts = {
        shard: conn.execute(select(func.count()).select_from(WALLETS)).scalar_one()
        for shard, conn in repo.readonly_dbs()
    }
    assert counts == {GLOBAL: 1, MEXICO: 1}, "each landed on the shard its block was for"


def test_a_manager_filed_under_the_wrong_shard_is_refused(registry):
    """Two answers to 'which shard is this?' - the key routing uses, and the one every
    transaction stamps on its outbox row. Nothing downstream could tell them apart."""
    from sqlalchemy import create_engine

    from ekbatan import DatabaseRegistry, TransactionManager
    from ekbatan.errors import ValidationError

    with pytest.raises(ValidationError, match="registered under the wrong key"):
        DatabaseRegistry({GLOBAL: TransactionManager(MEXICO, create_engine("sqlite://"))}, GLOBAL)


# -- the two shard-strategy details that had drifted from Java ---------------------------------


def test_no_sharding_says_it_resolves_nothing_from_an_id_and_still_reads_by_id(registry, clock):
    """Java's `NoShardingStrategy.usesShardAwareId()` returns false, and its repository guard
    opens with `instanceof NoShardingStrategy` so that reads by id keep working anyway.

    The flag means "is a shard encoded in the id", not "may I read by id". A single-database
    deployment encodes nothing and needs to: there is one shard and every row is on it.
    """
    strategy = NoShardingStrategy()
    assert strategy.uses_shard_aware_id is False
    assert strategy.shard_for_id(uuid4()) is None
    assert strategy.shard_for(a_wallet(MEXICO)) is None

    wallets = WalletRepository(Wallet, WALLETS, registry, sharding=strategy, clock=clock)
    stored = wallets.add(a_wallet(GLOBAL))

    assert wallets.get(stored.id).id == stored.id, "reads by id are not refused"


def test_a_cross_shard_refusal_names_the_two_shards_that_disagreed(sharded_registry, clock):
    """Java's `CrossShardException` carries `activeShard` and `requestedShard` as fields, so a
    caller can branch on them rather than parse the message."""
    wallets = WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
    )

    with pytest.raises(CrossShardError) as raised:
        wallets.add_all([a_wallet(GLOBAL), a_wallet(MEXICO)])

    assert {raised.value.active_shard, raised.value.requested_shard} == {GLOBAL, MEXICO}
    assert "allow_cross_shard=True" in str(raised.value), "and says what to do instead"


def test_one_database_needs_no_election_and_two_do(registry):
    """Java's builder makes `withDefaultDatabase` optional for a single database and mandatory
    beyond that. Saying which of one is the default is a line that can only be written
    correctly; saying which of two is a decision only the operator can make."""
    from ekbatan.persistence import DatabaseRegistry

    only = registry.manager(registry.default_shard)
    assert DatabaseRegistry({only.shard: only}).default_shard == only.shard

    with pytest.raises(ValidationError, match="default_shard is required"):
        DatabaseRegistry({GLOBAL: only, MEXICO: only})


# -- an id naming a shard this deployment does not have -----------------------------------------
#
# Eleven of Java's BaseShardedRepositoryTest cases are about this. Shard layouts differ between
# environments and grow over time, so an id minted elsewhere has to route somewhere rather than
# fail - and where it routes has to be decided before anything else looks at the shard.

UNREGISTERED = ShardIdentifier(9, 3)


def test_a_row_whose_id_names_an_unregistered_shard_is_written_to_the_default(sharded_wallets):
    """Java's `should_add_unregistered_shard_to_default_shard`."""
    stranger = a_wallet(UNREGISTERED)

    stored = sharded_wallets.add(stranger)

    assert sharded_wallets.find(stored.id) is not None, "and it reads back from there"
    assert sharded_wallets.exists(stored.id)


def test_reads_across_shards_see_the_row_that_fell_back(sharded_wallets, sharded_registry):
    """Java's `should_findAll_includes_unregistered_shard_data` and
    `should_count_includes_unregistered_shard_data`."""
    here = sharded_wallets.add(a_wallet(GLOBAL))
    stranger = sharded_wallets.add(a_wallet(UNREGISTERED))

    assert {w.id for w in sharded_wallets.all()} == {here.id, stranger.id}
    assert sharded_wallets.count() == 2
    assert {w.id for w in sharded_wallets.find_many([here.id, stranger.id])} == {
        here.id,
        stranger.id,
    }


def test_a_batch_mixing_a_registered_shard_with_an_unregistered_one_is_refused(sharded_wallets):
    """Java's `should_reject_batch_mixing_registered_and_unregistered_shards`.

    The unregistered id falls back to the default, which is a *different* database from the
    registered shard the other row names - so the batch spans two, and there is no transaction
    that spans two.
    """
    with pytest.raises(CrossShardError):
        sharded_wallets.add_all([a_wallet(MEXICO), a_wallet(UNREGISTERED)])


def test_a_batch_mixing_the_default_shard_with_an_unregistered_one_is_allowed(sharded_wallets):
    """Java's `should_allow_batch_mixing_default_and_unregistered_shards`.

    The pair that pins the ordering: the fallback is applied first, so both rows resolve to the
    default and the batch is one database after all. Checked the other way round - cross-shard
    first, fallback second - this would be refused.
    """
    here, stranger = sharded_wallets.add_all([a_wallet(GLOBAL), a_wallet(UNREGISTERED)])

    assert sharded_wallets.find(here.id) is not None
    assert sharded_wallets.find(stranger.id) is not None


# -- unknown_shard_policy is about routing, so it covers reads and writes alike ------------------


@pytest.fixture
def strict_wallets(clock):
    """Two sqlite shards that refuse a third, rather than quietly widening the default.

    Its own registry rather than the shared fixture, because the policy is fixed when the
    registry is built - which is the point of it: an operator decides once, per deployment.
    """
    from ekbatan.config import DataSourceConfig, ShardingConfig
    from ekbatan.persistence import DatabaseRegistry
    from fixtures.config import group, member
    from fixtures.schema import metadata

    registry = DatabaseRegistry.from_config(
        ShardingConfig(
            default_shard=GLOBAL,
            groups=(
                group(0, "global", member(0, DataSourceConfig(url="sqlite://"), name="global")),
                group(1, "mexico", member(0, DataSourceConfig(url="sqlite://"), name="mexico")),
            ),
            unknown_shard_policy="fail",
        )
    )
    for shard in (GLOBAL, MEXICO):
        with registry.manager(shard).primary.begin() as connection:
            metadata.create_all(connection)
    try:
        yield WalletRepository(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )
    finally:
        registry.close()


def test_unknown_shard_policy_fail_refuses_a_write_to_an_unregistered_shard(strict_wallets):
    with pytest.raises(ValidationError, match="shard 9:3 is not registered"):
        strict_wallets.add(a_wallet(UNREGISTERED))


def test_unknown_shard_policy_fail_refuses_a_read_of_an_unregistered_shard(strict_wallets):
    """The half worth pinning. The policy is applied in `effective_shard`, which every routed
    accessor calls - so it is not a write-side guard that a read can slip past.

    It is also the reason not to turn this on where the deployment holds a *subset* of the
    layout: the foreign-id read the fallback exists for is refused too, by design.
    """
    with pytest.raises(ValidationError, match="shard 9:3 is not registered"):
        strict_wallets.find(ShardedId[Wallet].generate(UNREGISTERED))


def test_a_registered_shard_is_untouched_by_the_policy(strict_wallets):
    stored = strict_wallets.add(a_wallet(MEXICO))

    assert strict_wallets.find(stored.id) is not None
