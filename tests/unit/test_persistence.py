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

import logging

import pytest
from sqlalchemy.exc import NoSuchModuleError

from ekbatan.config import DataSourceConfig, ShardingConfig
from ekbatan.errors import ValidationError
from ekbatan.persistence import DatabaseRegistry
from ekbatan.sharding import ShardIdentifier
from fixtures.config import group, member

PRIMARY = DataSourceConfig(url="postgresql+psycopg://user:pass@primary-9e3f:5432/db", pool_size=20)
REPLICA = DataSourceConfig(url="postgresql+psycopg://user:pass@replica-9e3f:5432/db", pool_size=20)

FIRST = ShardIdentifier(0, 0)
SECOND = ShardIdentifier(0, 1)


def config(replica: DataSourceConfig | None) -> ShardingConfig:
    return ShardingConfig(
        default_shard=FIRST, groups=(group(0, "g0", member(0, PRIMARY, replica, name="g0")),)
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
            default_shard=FIRST,
            groups=(group(0, "g0", member(0, PRIMARY), member(1, REPLICA)),),
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
        groups = (group(0, "g0", member(0, unloadable), member(1, unloadable)),)

        with pytest.raises(ValidationError) as refused:
            ShardingConfig(default_shard=ShardIdentifier(9, 9), groups=groups)

        assert "default_shard" in str(refused.value)
        assert "Can't load plugin" not in str(refused.value)

        # And the url really would have raised had an engine been built, so the assertion above
        # is evidence rather than an accident of ordering.
        with pytest.raises(NoSuchModuleError):
            DatabaseRegistry.from_config(ShardingConfig(default_shard=FIRST, groups=groups))


G0_M0 = DataSourceConfig(url="postgresql+psycopg://user:pass@host-g0-m0:5432/db", pool_size=5)
G0_M1 = DataSourceConfig(url="postgresql+psycopg://user:pass@host-g0-m1:5432/db", pool_size=5)
G1_M0 = DataSourceConfig(url="postgresql+psycopg://user:pass@host-g1-m0:5432/db", pool_size=5)

GLOBAL = ShardIdentifier(0, 0)
GLOBAL_2 = ShardIdentifier(0, 1)
MEXICO = ShardIdentifier(1, 0)


class TestDatabaseRegistryFromConfig:
    """Java's `DatabaseRegistryFromConfigTest`, layout for layout: groups of members, each
    member a named map of datasources."""

    def test_should_create_registry_with_two_shards_across_two_groups(self) -> None:
        registry = DatabaseRegistry.from_config(
            ShardingConfig(
                default_shard=GLOBAL,
                groups=(
                    group(0, "global", member(0, G0_M0, G0_M0, name="default")),
                    group(1, "mexico", member(0, G1_M0, G1_M0, name="mexico-1")),
                ),
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
                default_shard=GLOBAL, groups=(group(0, "global", member(0, G0_M0, G0_M0)),)
            )
        )

        assert len(registry.shards) == 1
        assert registry.default is not None

    def test_should_create_registry_with_multiple_members_in_one_group(self) -> None:
        registry = DatabaseRegistry.from_config(
            ShardingConfig(
                default_shard=GLOBAL,
                groups=(group(0, "global", member(0, G0_M0, G0_M0), member(1, G0_M1, G0_M1)),),
            )
        )

        assert len(registry.shards) == 2
        assert registry.manager(GLOBAL) is not registry.manager(GLOBAL_2)

    def test_should_derive_dialect_from_member_config(self) -> None:
        """The dialect is read off the url, not declared beside it."""
        registry = DatabaseRegistry.from_config(
            ShardingConfig(
                default_shard=GLOBAL, groups=(group(0, "global", member(0, G0_M0, G0_M0)),)
            )
        )

        assert registry.manager(GLOBAL).dialect == "postgres"

    def test_should_fail_when_default_shard_not_in_config(self) -> None:
        """Refused on both paths into a registry: building the layout, and constructing one
        from managers directly. Java has only the second, because its config carries no
        validation of its own."""
        with pytest.raises(ValidationError) as from_layout:
            ShardingConfig(
                default_shard=ShardIdentifier(9, 9),
                groups=(group(0, "global", member(0, G0_M0, G0_M0)),),
            )
        assert "names no configured database" in str(from_layout.value)

        registry = DatabaseRegistry.from_config(
            ShardingConfig(default_shard=GLOBAL, groups=(group(0, "global", member(0, G0_M0)),))
        )
        with pytest.raises(ValidationError) as direct:
            DatabaseRegistry({GLOBAL: registry.default}, ShardIdentifier(9, 9))
        assert "names no registered database" in str(direct.value)

    def test_should_set_default_transaction_manager_correctly(self) -> None:
        """The default is what the layout says, not the first shard registered."""
        registry = DatabaseRegistry.from_config(
            ShardingConfig(
                default_shard=MEXICO,
                groups=(
                    group(0, "global", member(0, G0_M0, G0_M0)),
                    group(1, "mexico", member(0, G1_M0, G1_M0)),
                ),
            )
        )

        assert registry.default_shard == MEXICO
        assert registry.default is registry.manager(MEXICO)


# -- an unregistered shard says so, and can be made to refuse -----------------------------------


def _two_shards(**kw: object) -> DatabaseRegistry:
    """Two sqlite shards. Nothing is queried here - only routing is under test."""
    return DatabaseRegistry.from_config(
        ShardingConfig(
            default_shard=ShardIdentifier(0, 0),
            groups=(
                group(0, "global", member(0, DataSourceConfig(url="sqlite://"))),
                group(1, "mexico", member(0, DataSourceConfig(url="sqlite://"))),
            ),
            **kw,  # type: ignore[arg-type]
        )
    )


def test_an_unregistered_shard_falls_back_to_the_default_and_says_so(caplog):
    """`EKB-0200`, the half that is not configurable.

    The fallback matches Java and is staying. What was wrong with it is that it happened in
    silence: a mistyped shard identifier and a legitimately foreign id look identical here, and
    only one of them is a bug. Nothing can tell them apart, so this tells you it happened.
    """
    registry = _two_shards()
    try:
        with caplog.at_level(logging.WARNING, logger="ekbatan.persistence"):
            assert registry.effective_shard(ShardIdentifier(9, 9)) == ShardIdentifier(0, 0)

        assert "shard 9:9 is not registered, routing to default 0:0" in caplog.text
        assert "'0:0', '1:0'" in caplog.text, "the message names what is registered"
    finally:
        registry.close()


def test_a_shard_nobody_proposed_is_not_a_fallback_and_says_nothing(caplog):
    """`None` is an unsharded repository taking the default, which is the ordinary path. A
    warning on every read there would be noise, and noise is how a real warning gets ignored."""
    registry = _two_shards()
    try:
        with caplog.at_level(logging.WARNING, logger="ekbatan.persistence"):
            assert registry.effective_shard(None) == ShardIdentifier(0, 0)

        assert caplog.text == ""
    finally:
        registry.close()


def test_unknown_shard_policy_fail_raises_instead_of_routing_somewhere_else():
    """For a deployment that holds the whole layout, where an unregistered shard can only be a
    bug. Off by default, because turning it on where the layout *is* a subset breaks reads that
    Java would have served."""
    registry = _two_shards(unknown_shard_policy="fail")
    try:
        with pytest.raises(
            ValidationError, match="not registered and unknown_shard_policy is 'fail'"
        ):
            registry.effective_shard(ShardIdentifier(9, 9))

        assert registry.effective_shard(ShardIdentifier(1, 0)) == ShardIdentifier(1, 0)
        assert registry.effective_shard(None) == ShardIdentifier(0, 0)
    finally:
        registry.close()


# -- the other named configs on a member are the application's --------------------------------


def test_the_registry_builds_pools_for_the_primary_and_secondary_only():
    """Java's `fromConfig` reads `primaryConfig()` and `secondaryConfig()` and nothing else. A
    `jobsConfig` or `lockConfig` beside them is there for the application to build - the
    scheduler and the lock provider own those pools, not the registry."""
    from ekbatan.persistence import engine_for

    jobs = DataSourceConfig(url="sqlite://", pool_size=3)
    layout = ShardingConfig(
        default_shard=FIRST,
        groups=(group(0, "g0", member(0, DataSourceConfig(url="sqlite://"), jobsConfig=jobs)),),
    )
    created: list[DataSourceConfig] = []

    import ekbatan.persistence as persistence

    real = persistence.engine_for

    def recording(config: DataSourceConfig):  # type: ignore[no-untyped-def]
        created.append(config)
        return real(config)

    persistence.engine_for = recording  # type: ignore[assignment]
    try:
        with DatabaseRegistry.from_config(layout):
            pass
    finally:
        persistence.engine_for = real  # type: ignore[assignment]

    assert jobs not in created, "the registry built a pool for the application's jobsConfig"
    assert created == [layout.member(FIRST).primary_config()]

    # And the application builds it with the same function the registry uses.
    engine = engine_for(layout.member(FIRST).config_for("jobsConfig"))  # type: ignore[arg-type]
    try:
        assert str(engine.url) == "sqlite://"
    finally:
        engine.dispose()


def test_engine_for_honours_the_pool_it_is_given():
    """Java's `hikariConnectionProvider(config)`: the settings on the config are the pool's."""
    from ekbatan.persistence import engine_for

    engine = engine_for(
        DataSourceConfig(url="postgresql+psycopg://u:p@h-9e3f/db", pool_size=7, max_overflow=2)
    )
    try:
        assert engine.pool.size() == 7  # type: ignore[attr-defined]
        assert engine.pool._max_overflow == 2  # type: ignore[attr-defined]
    finally:
        engine.dispose()
