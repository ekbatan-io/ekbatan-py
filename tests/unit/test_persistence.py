"""How many connection pools a shard opens, and when they are built.

Java's `DatabaseRegistrySecondaryPoolTest`. No server: `create_engine` resolves a dialect and
builds a pool object without contacting anything, which is the same property Java's
`initializationFailTimeout = -1` gives Hikari - so the hosts below deliberately do not resolve.

The regression this exists for: falling back on the primary *config* when a member declares no
read replica, then handing that config to the engine factory a second time. Identical settings
do not produce the same pool - they produce a second one against the same database, with its own
connections and its own housekeeping. Every no-replica member then held twice the configured
maximum: eight shards at 20 is 320 connections where the operator budgeted 160, against a
PostgreSQL whose `max_connections` is commonly 200. Nothing failed and nothing was logged, both
pools being perfectly healthy, which is why it needs a test rather than an integration check.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import NoSuchModuleError

from ekbatan.config import DataSourceConfig, ShardConfig, ShardingConfig
from ekbatan.errors import ValidationError
from ekbatan.persistence import DatabaseRegistry
from ekbatan.sharding import ShardIdentifier

PRIMARY = DataSourceConfig(url="postgresql+psycopg://user:pass@primary-9e3f:5432/db", pool_size=20)
REPLICA = DataSourceConfig(url="postgresql+psycopg://user:pass@replica-9e3f:5432/db", pool_size=20)

FIRST = ShardIdentifier(0, 0)
SECOND = ShardIdentifier(0, 1)


def config(replica: DataSourceConfig | None) -> ShardingConfig:
    return ShardingConfig(
        shards=(ShardConfig(shard=FIRST, primary=PRIMARY, replica=replica, name="g0"),),
        default_shard=FIRST,
    )


class TestDatabaseRegistrySecondaryPool:
    def test_a_member_with_no_replica_shares_one_pool(self) -> None:
        """The regression: no replica configured means one pool, shared - not two identical
        ones against the same database."""
        with DatabaseRegistry.from_config(config(None)) as registry:
            manager = registry.default

            assert manager.replica is manager.primary
            assert manager.shares_one_pool

    def test_a_member_with_a_replica_keeps_two_pools(self) -> None:
        """A real replica is still its own pool - the fix must not collapse a genuine second
        database."""
        with DatabaseRegistry.from_config(config(REPLICA)) as registry:
            manager = registry.default

            assert manager.replica is not manager.primary
            assert not manager.shares_one_pool
            assert manager.replica.url.host == "replica-9e3f"

    def test_closing_a_shared_provider_happens_once_and_stays_closed(self) -> None:
        """Sharing is what `TransactionManager.close` already assumed: it disposes the replica
        only when it owns one. Disposing a shared pool twice would be the bug's mirror image."""
        registry = DatabaseRegistry.from_config(config(None))
        manager = registry.default

        registry.close()

        assert manager.primary is manager.replica
        # Idempotent by SQLAlchemy's contract; the point is that close() completed without
        # attempting a second dispose on a pool it does not separately own.
        registry.close()

    def test_separate_members_never_share_a_pool(self) -> None:
        """Every member gets its own primary - sharing is within a member, never across them."""
        layout = ShardingConfig(
            shards=(
                ShardConfig(shard=FIRST, primary=PRIMARY, name="g0"),
                ShardConfig(shard=SECOND, primary=REPLICA, name="g0"),
            ),
            default_shard=FIRST,
        )

        with DatabaseRegistry.from_config(layout) as registry:
            assert registry.manager(FIRST).primary is not registry.manager(SECOND).primary

    def test_a_default_shard_naming_no_member_builds_no_pools(self) -> None:
        """A bad default shard must be refused before any pool is built.

        In Java this check used to run *after* the loop that builds them, so a mistyped default
        shard - the realistic mistake here - built every pool and then threw, and nothing held a
        reference to them afterwards, so each kept a housekeeping thread alive for the life of
        the JVM.

        The ordering has no direct observable, so it is read off the exception instead: these
        urls name a driver that does not exist, which makes SQLAlchemy raise the moment an
        engine is constructed. Reaching the default-shard error proves no engine was built;
        reaching `NoSuchModuleError` proves one was.

        This port refuses earlier than Java does - `ShardingConfig` rejects the layout on
        construction, so `from_config` is never reached at all. Both orderings are asserted, so
        this still fails if the check ever moves after the engine loop.
        """
        unloadable = DataSourceConfig(url="postgresql+nosuchdriver://u:p@primary-9e3f:5432/db")
        shards = (
            ShardConfig(shard=FIRST, primary=unloadable),
            ShardConfig(shard=SECOND, primary=unloadable),
        )

        with pytest.raises(ValidationError) as refused:
            ShardingConfig(shards=shards, default_shard=ShardIdentifier(9, 9))

        assert "default_shard" in str(refused.value)
        assert "Can't load plugin" not in str(refused.value)

        # And the url really would have raised had an engine been built, so the assertion above
        # is evidence rather than an accident of ordering.
        with pytest.raises(NoSuchModuleError):
            DatabaseRegistry.from_config(ShardingConfig(shards=shards, default_shard=FIRST))


G0_M0 = DataSourceConfig(url="postgresql+psycopg://user:pass@host-g0-m0:5432/db", pool_size=5)
G0_M1 = DataSourceConfig(url="postgresql+psycopg://user:pass@host-g0-m1:5432/db", pool_size=5)
G1_M0 = DataSourceConfig(url="postgresql+psycopg://user:pass@host-g1-m0:5432/db", pool_size=5)

GLOBAL = ShardIdentifier(0, 0)
GLOBAL_2 = ShardIdentifier(0, 1)
MEXICO = ShardIdentifier(1, 0)


class TestDatabaseRegistryFromConfig:
    """Java's `DatabaseRegistryFromConfigTest`.

    Java's layout nests groups inside the config and members inside groups; this port keeps a
    flat tuple of `ShardConfig`, so the shapes below are the same layouts written the shorter
    way. What is being tested - how many managers come out, which one is the default, and where
    the dialect comes from - is unchanged.
    """

    def test_should_create_registry_with_two_shards_across_two_groups(self) -> None:
        registry = DatabaseRegistry.from_config(
            ShardingConfig(
                shards=(
                    ShardConfig(shard=GLOBAL, primary=G0_M0, replica=G0_M0, name="default"),
                    ShardConfig(shard=MEXICO, primary=G1_M0, replica=G1_M0, name="mexico-1"),
                ),
                default_shard=GLOBAL,
            )
        )

        assert registry.default_shard == GLOBAL
        assert len(registry.shards) == 2
        assert registry.manager(GLOBAL) is not None
        assert registry.manager(MEXICO) is not None
        assert registry.manager(GLOBAL).dialect == "postgres"

    def test_should_create_registry_with_single_shard(self) -> None:
        registry = DatabaseRegistry.from_config(
            ShardingConfig(
                shards=(ShardConfig(shard=GLOBAL, primary=G0_M0, replica=G0_M0),),
                default_shard=GLOBAL,
            )
        )

        assert len(registry.shards) == 1
        assert registry.default is not None

    def test_should_create_registry_with_multiple_members_in_one_group(self) -> None:
        registry = DatabaseRegistry.from_config(
            ShardingConfig(
                shards=(
                    ShardConfig(shard=GLOBAL, primary=G0_M0, replica=G0_M0),
                    ShardConfig(shard=GLOBAL_2, primary=G0_M1, replica=G0_M1),
                ),
                default_shard=GLOBAL,
            )
        )

        assert len(registry.shards) == 2
        assert registry.manager(GLOBAL) is not registry.manager(GLOBAL_2)

    def test_should_derive_dialect_from_member_config(self) -> None:
        """The dialect is read off the url, not declared beside it."""
        registry = DatabaseRegistry.from_config(
            ShardingConfig(
                shards=(ShardConfig(shard=GLOBAL, primary=G0_M0, replica=G0_M0),),
                default_shard=GLOBAL,
            )
        )

        assert registry.manager(GLOBAL).dialect == "postgres"

    def test_should_fail_when_default_shard_not_in_config(self) -> None:
        """Refused on both paths into a registry: building the layout, and constructing one
        from managers directly. Java has only the second, because its config carries no
        validation of its own."""
        with pytest.raises(ValidationError) as from_layout:
            ShardingConfig(
                shards=(ShardConfig(shard=GLOBAL, primary=G0_M0, replica=G0_M0),),
                default_shard=ShardIdentifier(9, 9),
            )
        assert "names no configured database" in str(from_layout.value)

        registry = DatabaseRegistry.from_config(
            ShardingConfig(
                shards=(ShardConfig(shard=GLOBAL, primary=G0_M0),), default_shard=GLOBAL
            )
        )
        with pytest.raises(ValidationError) as direct:
            DatabaseRegistry({GLOBAL: registry.default}, ShardIdentifier(9, 9))
        assert "names no registered database" in str(direct.value)

    def test_should_set_default_transaction_manager_correctly(self) -> None:
        """The default is what the layout says, not the first shard registered."""
        registry = DatabaseRegistry.from_config(
            ShardingConfig(
                shards=(
                    ShardConfig(shard=GLOBAL, primary=G0_M0, replica=G0_M0),
                    ShardConfig(shard=MEXICO, primary=G1_M0, replica=G1_M0),
                ),
                default_shard=MEXICO,
            )
        )

        assert registry.default_shard == MEXICO
        assert registry.default is registry.manager(MEXICO)
