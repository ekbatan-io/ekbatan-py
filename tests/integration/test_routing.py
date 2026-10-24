"""The connection a query runs on, and the shard it runs against.

A repository subclass writing its own query needs a sanctioned way to get a routed,
transaction-aware connection. That is what `db`, `readonly_db`, `tx_db` and `tx_db_else_db`
are; the built-in reads and writes go through exactly the same four.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import sqlalchemy.exc
from sqlalchemy import create_engine, event, func, select

from ekbatan import Transaction
from ekbatan.action import Action, ActionExecutor
from ekbatan.config import DataSourceConfig
from ekbatan.errors import CrossShardError, UnsupportedError, ValidationError
from ekbatan.ids import ShardedId
from ekbatan.persistence import DatabaseRegistry, DataSource, TransactionManager
from ekbatan.repository import AbstractRepository
from ekbatan.sharding import EmbeddedBitsShardingStrategy, NoShardingStrategy, ShardIdentifier
from fixtures.domain import NOW, Wallet, WalletState
from fixtures.executing import run
from fixtures.repositories import WalletRepository
from fixtures.schema import EVENTS, WALLETS, WalletsRecord

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")

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

    wallet_repository = WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=ByTenantColumn(), clock=clock
    )

    assert wallet_repository.shard_of(a_wallet(GLOBAL)) == MEXICO, "the row still routes"
    with pytest.raises(UnsupportedError, match="override shard_for"):
        wallet_repository.shard_for(uuid4())


def test_without_sharding_everything_is_the_default_shard(registry, clock):
    wallet_repository = WalletRepository(
        Wallet, WALLETS, registry, sharding=NoShardingStrategy(), clock=clock
    )
    assert wallet_repository.shard_for(uuid4()) == registry.default_shard
    assert (
        wallet_repository.shard_of_all([a_wallet(GLOBAL), a_wallet(MEXICO)])
        == registry.default_shard
    )


# ------------------------------------------------------------ getting a connection


def test_tx_db_else_db_joins_an_open_transaction(registry, wallet_repository):
    manager = registry.default
    with manager.transaction() as outer:
        assert outer.shard == manager.shard, "the block knows which shard it is on"
        assert outer.dialect == manager.dialect
        with wallet_repository.tx_db_else_db() as joined:
            assert joined is outer.connection
        assert wallet_repository.tx_db() is outer.connection

    assert wallet_repository.tx_db() is None, "outside one, there is no transaction to join"
    with wallet_repository.tx_db_else_db() as standalone:
        assert standalone is not None, "and each statement on it commits itself"


def test_db_deliberately_does_not_join(registry, wallet_repository):
    manager = registry.default
    with manager.transaction() as outer, wallet_repository.db() as separate:
        assert separate is not outer.connection, (
            "db() is the escape hatch; tx_db_else_db is the one repository code should use"
        )


def test_a_write_inside_a_transaction_joins_it(registry, wallet_repository):
    wallet = Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        created_date=NOW,
    )
    try:
        with registry.default.transaction():
            wallet_repository.add(wallet)
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert wallet_repository.find(wallet.id) is None, (
        "the repository wrote inside the caller's transaction, so the rollback took it"
    )


def test_a_built_in_read_inside_a_transaction_does_not_see_its_writes(registry, wallet_repository):
    """As Java's: `findById` and `existsById` go to `db()`, a connection of their own."""
    wallet = Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("7.00"),
        created_date=NOW,
    )
    with registry.default.transaction():
        wallet_repository.add(wallet)
        assert wallet_repository.find(wallet.id) is None
        assert not wallet_repository.exists(wallet.id)
        # A query of your own on `tx_db_else_db()` joins the transaction and sees the row.
        with wallet_repository.tx_db_else_db(key=wallet.id) as conn:
            assert conn.execute(select(WALLETS).where(WALLETS.c.id == wallet.id.value)).first()
    assert wallet_repository.find(wallet.id) is not None


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
    class WalletRepository(AbstractRepository[Wallet, WalletsRecord, UUID]):
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

    wallet_repository = WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
    )
    here, there = a_wallet(GLOBAL), a_wallet(MEXICO, "MXN")
    wallet_repository.add(here)
    wallet_repository.add(there)

    assert wallet_repository.total_balance_on(GLOBAL) == Decimal("1.0000")
    assert wallet_repository.total_balance_on(MEXICO) == Decimal("1.0000")
    assert wallet_repository.currency_of(there.id) == "MXN", "routed by the id, with no shard named"


def test_column_refuses_a_name_the_table_does_not_have(wallet_repository):
    assert wallet_repository.column("balance") is WALLETS.c.balance
    with pytest.raises(ValidationError, match="no column 'nonsense'"):
        wallet_repository.column("nonsense")


# ------------------------------------------------------------------ no-result writes


def test_the_no_result_writes_write(wallet_repository):
    one = Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        created_date=NOW,
    )
    wallet_repository.add_no_result(one)
    assert wallet_repository.get(one.id).balance == Decimal("1.0000")

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
    wallet_repository.add_all_no_result(many)
    assert wallet_repository.count() == 4

    wallet_repository.update_no_result(
        wallet_repository.get(one.id).evolve(balance=Decimal("9.00"))
    )
    assert wallet_repository.get(one.id).balance == Decimal("9.0000")

    refreshed = [wallet_repository.get(m.id).evolve(balance=Decimal("5.00")) for m in many]
    wallet_repository.update_all_no_result(refreshed)
    assert {w.balance for w in wallet_repository.find_many([m.id for m in many])} == {
        Decimal("5.0000")
    }


def test_the_dialect_is_reachable_for_the_query_that_has_to_differ(wallet_repository, registry):
    assert wallet_repository.dialect() == registry.dialect
    assert wallet_repository.dialect(shard=registry.default_shard) == registry.dialect


# ------------------------------------------------- the executor asks the same question


class _ByCurrency:
    """Routes on a field the id says nothing about - charter rule 7's extension point."""

    uses_shard_aware_id = False

    def shard_for_id(self, key):
        return None

    def shard_for(self, persistable):
        return MEXICO if getattr(persistable, "currency", "EUR") == "MXN" else GLOBAL


def _executor_over(registry, repo, clock):
    return ActionExecutor(
        namespace="example.wallet",
        registry=registry,
        repositories=[repo],
        clock=clock,
    )


@dataclass(frozen=True, kw_only=True)
class WalletCreateParams:
    """A params object rather than a bare `str`, because the outbox stores these as a JSON
    object and a scalar there is a row no typed consumer can read. Java refuses the scalar for
    the same reason."""

    currency: str


def _open_wallet(currency, clock):
    class WalletCreateAction(Action[WalletCreateParams, Wallet]):
        def perform(self, principal: str | None, params):
            return self.plan.add(
                Wallet.open(
                    id=ShardedId[Wallet].random(),
                    owner_id=uuid4(),
                    currency=params.currency,
                    balance=Decimal("1.00"),
                    created_date=self.clock.instant(),
                )
            )

    return WalletCreateAction(clock), WalletCreateParams(currency=currency)


def test_a_strategy_that_cannot_route_by_id_alone_still_runs(sharded_registry, clock, outbox):
    """The repository could always route these; only the executor could not."""
    wallet_repository = WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=_ByCurrency(), clock=clock
    )
    executor = _executor_over(sharded_registry, wallet_repository, clock)

    action, params = _open_wallet("MXN", clock)
    wallet = run(executor, action, params)

    assert (
        wallet_repository.find_where(WALLETS.c.currency == "MXN", shard=MEXICO)[0].id == wallet.id
    )


def test_state_and_its_events_land_on_one_shard(sharded_registry, clock):
    """The id and the row can name different shards. When they do, the state and the outbox
    row must still be one transaction on one database, or the whole promise is gone."""
    wallet_repository = WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=_ByCurrency(), clock=clock
    )
    executor = _executor_over(sharded_registry, wallet_repository, clock)

    action, params = _open_wallet("MXN", clock)
    run(executor, action, params)

    rows = {
        shard: (
            conn.execute(select(func.count()).select_from(WALLETS)).scalar_one(),
            conn.execute(select(func.count()).select_from(EVENTS)).scalar_one(),
        )
        for shard, conn in wallet_repository.readonly_dbs()
    }
    assert rows == {GLOBAL: (0, 0), MEXICO: (1, 1)}, (
        "the row went one way and its events the other, in two transactions"
    )


def test_a_scatter_read_stops_at_the_first_shard_that_answers(sharded_registry, clock, mode):
    """Java's findOneWhere short-circuits; a fan-out that always visits every shard turns a
    one-row lookup into one query per database for the whole life of the deployment.

    `ekbatan.aio` asks every shard at once instead - one round trip's wait - and takes the
    answers in shard order, so the row found is the same; the reads after the shard that
    answered are cancelled, so a later shard may or may not have run its query."""
    wallet_repository = WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
    )
    wallet_repository.add(a_wallet(GLOBAL, "SEK"))  # the only match, on the first shard visited

    selects = dict.fromkeys(sharded_registry.shards, 0)

    def watch(shard):
        def listener(conn, cursor, statement, params, context, many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects[shard] += 1

        return listener

    for shard in sharded_registry.shards:
        event.listen(
            sharded_registry.manager(shard).primary.sqlalchemy_engine,
            "before_cursor_execute",
            watch(shard),
        )

    later = {0} if mode == "sync" else {0, 1}
    assert wallet_repository.find_one_where(WALLETS.c.currency == "SEK") is not None
    assert selects[GLOBAL] == 1 and selects[MEXICO] in later

    selects.update(dict.fromkeys(selects, 0))
    assert wallet_repository.exists_where(WALLETS.c.currency == "SEK") is True
    assert selects[GLOBAL] == 1 and selects[MEXICO] in later


@pytest.fixture
def mexico_down(backend, clock, mode, portal):
    """Two shards, the second one broken: its database has no tables, so any read there fails
    with the database's own error. The first shard is migrated and empty."""
    from ekbatan.config import ShardingConfig
    from fixtures.config import group, member
    from fixtures.logins import data_source
    from fixtures.schema import metadata

    backend.prepare("ekbatan_down_global", metadata)
    backend.truncate("ekbatan_down_global", metadata)
    registry = DatabaseRegistry.from_config(
        ShardingConfig(
            default_shard=GLOBAL,
            groups=(
                group(
                    0,
                    "global",
                    member(0, data_source(backend.url("ekbatan_down_global")), name="global"),
                ),
                group(
                    1,
                    "mexico",
                    member(0, data_source(backend.url("ekbatan_down_mexico")), name="mexico"),
                ),
            ),
        )
    )
    try:
        yield WalletRepository(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )
    finally:
        registry.close() if mode == "sync" else registry.aclose()


def test_a_shard_after_the_one_that_answers_cannot_fail_the_lookup(mexico_down):
    """`find_one_where` and `exists_where` answer from the first shard, in shard order, that
    has the row. A broken shard after it is never reached by the sync call, and the async call,
    which asks every shard at once, gives the same answer rather than the broken shard's error."""
    found = mexico_down.add(a_wallet(GLOBAL, "SEK"))

    one = mexico_down.find_one_where(WALLETS.c.currency == "SEK")
    assert one is not None and one.id == found.id
    assert mexico_down.exists_where(WALLETS.c.currency == "SEK") is True


def test_a_broken_shard_the_lookup_reaches_fails_it_and_is_named(mexico_down):
    """When every shard before the broken one says "not here", the broken one is reached - in
    both modes - and its own error is raised, with a note naming the shard. So is a read that
    needs every shard."""
    for read in (
        lambda: mexico_down.find_one_where(WALLETS.c.currency == "SEK"),
        lambda: mexico_down.exists_where(WALLETS.c.currency == "SEK"),
        lambda: mexico_down.find_where(),
        lambda: mexico_down.count(),
    ):
        with pytest.raises(sqlalchemy.exc.DBAPIError) as failed:
            read()
        notes = "\n".join(getattr(failed.value, "__notes__", []))
        assert "ekbatan: reading shard 1:0 (mexico) failed, so the whole read failed" in notes
        assert not isinstance(failed.value, BaseExceptionGroup)

    assert mexico_down.find_where(shard=GLOBAL) == [], "the first shard still reads"


# ------------------------------------------- the repository opens no transaction of its own


def _statements(engine):
    seen: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda c, cur, s, p, ctx, many: seen.append(s.split()[0].upper()),
    )
    return seen


def test_a_write_outside_a_unit_of_work_opens_no_transaction(registry, wallet_repository):
    """Java's repository never opens one: inside a transaction it joins, outside one each
    statement commits itself. Opening one here would decide what is atomic on the caller's
    behalf, which is theirs to decide."""
    engine = registry.default.primary.sqlalchemy_engine
    seen = _statements(engine)

    wallet = Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        created_date=NOW,
    )
    wallet_repository.add(wallet)

    assert seen == ["INSERT"], "one statement, no BEGIN of the framework's own"

    # `str(url)` masks the password as `***`, which connects only to a server that has
    # none - the reason this read passed on SQLite and failed on all three real servers.
    with create_engine(engine.url.render_as_string(hide_password=False)).connect() as fresh:
        found = fresh.execute(select(WALLETS.c.id).where(WALLETS.c.id == wallet.id.value)).first()
    assert found is not None, "and it committed - a separate connection sees it"


def test_a_batch_is_one_statement_however_many_rows(registry, wallet_repository):
    """Nothing is chunked. Splitting a batch past the server's bind-parameter ceiling is the
    caller's, because only they know what the pieces mean if one of them fails."""
    seen = _statements(registry.default.primary.sqlalchemy_engine)

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
    stored = wallet_repository.add_all(many)
    assert seen.count("INSERT") == 1, "250 rows, one INSERT, even reading them back"

    seen.clear()
    wallet_repository.update_all([w.deposit(Decimal("1.00")) for w in stored])
    assert seen.count("UPDATE") == 1, "250 rows, one UPDATE"

    seen.clear()
    assert len(wallet_repository.find_many([w.id for w in stored])) == 250
    assert seen.count("SELECT") == 1, "250 ids, one SELECT"


def test_the_block_is_told_which_shard_it_is_on(sharded_registry, clock):
    """Java hands its block a DSLContext bound to one shard's connection. The counterpart here
    is a Transaction, which says the same and can also be asked which shard that is."""
    wallet_repository = WalletRepository(
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
            wallet_repository.add(a_wallet(shard))
            assert wallet_repository.tx_db(shard=shard) is tx.connection

    counts = {
        shard: conn.execute(select(func.count()).select_from(WALLETS)).scalar_one()
        for shard, conn in wallet_repository.readonly_dbs()
    }
    assert counts == {GLOBAL: 1, MEXICO: 1}, "each landed on the shard its block was for"


def test_a_manager_filed_under_the_wrong_shard_is_refused(registry):
    """Two answers to 'which shard is this?' - the key routing uses, and the one every
    transaction stamps on its outbox row. Nothing downstream could tell them apart."""
    with pytest.raises(ValidationError, match="registered under the wrong key"):
        misfiled = TransactionManager(
            MEXICO, DataSource.from_config(DataSourceConfig(url="sqlite://"))
        )
        DatabaseRegistry({GLOBAL: misfiled}, GLOBAL)


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

    wallet_repository = WalletRepository(Wallet, WALLETS, registry, sharding=strategy, clock=clock)
    stored = wallet_repository.add(a_wallet(GLOBAL))

    assert wallet_repository.get(stored.id).id == stored.id, "reads by id are not refused"


def test_a_cross_shard_refusal_names_the_two_shards_that_disagreed(sharded_registry, clock):
    """Java's `CrossShardException` carries `activeShard` and `requestedShard` as fields, so a
    caller can branch on them rather than parse the message."""
    wallet_repository = WalletRepository(
        Wallet, WALLETS, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
    )

    with pytest.raises(CrossShardError) as raised:
        wallet_repository.add_all([a_wallet(GLOBAL), a_wallet(MEXICO)])

    assert {raised.value.active_shard, raised.value.requested_shard} == {GLOBAL, MEXICO}
    assert "allow_cross_shard=True" in str(raised.value), "and says what to do instead"


def test_one_database_needs_no_election_and_two_do(registry):
    """Java's builder makes `withDefaultDatabase` optional for a single database and mandatory
    beyond that. Saying which of one is the default is a line that can only be written
    correctly; saying which of two is a decision only the operator can make."""
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
def strict_wallets(clock, mode, portal):
    """Two sqlite shards that refuse a third, rather than quietly widening the default.

    Its own registry rather than the shared fixture, because the policy is fixed when the
    registry is built - which is the point of it: an operator decides once, per deployment.
    """
    from ekbatan.config import DataSourceConfig, ShardingConfig
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
        with registry.manager(shard).primary.sqlalchemy_engine.begin() as connection:
            if mode == "sync":
                metadata.create_all(connection)
            else:
                connection.run_sync(metadata.create_all)
    try:
        yield WalletRepository(
            Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
        )
    finally:
        registry.close() if mode == "sync" else registry.aclose()


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
