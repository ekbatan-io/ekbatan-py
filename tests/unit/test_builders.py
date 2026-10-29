"""The builders Java wires everything with, and what each setter does.

Every one of them builds through the constructor, so what the constructor refuses the builder
refuses too, and those refusals are tested where the constructor is. What is tested here is the
builder's own behaviour: which setters add one and which replace the lot, what an uncalled
setter leaves, and the few refusals that happen before the constructor is reached.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ekbatan.action import ActionExecutor, ExecutionConfiguration, RetryConfig
from ekbatan.action.executor import DEFAULT_RETRIES
from ekbatan.config import DataSourceConfig, ShardGroupConfig, ShardingConfig, ShardMemberConfig
from ekbatan.errors import StaleRecordError, ValidationError
from ekbatan.events import (
    EventEnvelope,
    EventHandlerRegistry,
    HandlingConfig,
    LocalEventHandlerConfig,
)
from ekbatan.locks import (
    MariaDBKeyedLockProvider,
    MySQLKeyedLockProvider,
    PostgresKeyedLockProvider,
)
from ekbatan.locks.redis import DEFAULT_NAMESPACE, RedisKeyedLockProvider
from ekbatan.migrations import YoyoMigrator
from ekbatan.persistence import DatabaseRegistry, DataSource, TransactionManager
from ekbatan.sharding import ShardIdentifier, UnknownShardPolicy
from ekbatan.streaming import ActionEvent
from fixtures.domain import WalletMoneyDepositedEvent
from fixtures.handlers import RecordingHandler

URL = "postgresql+psycopg://h/db"
OTHER_URL = "postgresql+psycopg://other/db"
FIRST = ShardIdentifier(0, 0)
SECOND = ShardIdentifier(0, 1)


def a_source(url: str = URL) -> DataSourceConfig:
    return DataSourceConfig.builder().url(url).build()


def a_member(number: int = 0) -> ShardMemberConfig:
    return ShardMemberConfig.builder().member(number).primary_config(a_source()).build()


def a_group(number: int = 0) -> ShardGroupConfig:
    return (
        ShardGroupConfig.builder().group(number).name(f"g{number}").with_member(a_member()).build()
    )


# -- DataSourceConfig ---------------------------------------------------------------------------


def test_a_data_source_built_with_every_setter_matches_the_constructor():
    built = (
        DataSourceConfig.builder()
        .url(URL)
        .pool_size(20)
        .max_overflow(5)
        .pool_timeout(3.0)
        .pool_recycle(60)
        .pool_pre_ping(False)
        .data_source_properties({"connect_timeout": 2})
        .echo(True)
        .build()
    )

    assert built == DataSourceConfig(
        url=URL,
        pool_size=20,
        max_overflow=5,
        pool_timeout=3.0,
        pool_recycle=60,
        pool_pre_ping=False,
        data_source_properties={"connect_timeout": 2},
        echo=True,
    )


def test_an_uncalled_setter_leaves_the_default():
    assert DataSourceConfig.builder().url(URL).build() == DataSourceConfig(url=URL)


def test_a_data_source_without_a_url_is_refused():
    with pytest.raises(ValidationError, match="url is required"):
        DataSourceConfig.builder().pool_size(5).build()


def test_the_constructors_checks_still_run():
    with pytest.raises(ValidationError, match="pool_size must be at least 1"):
        DataSourceConfig.builder().url(URL).pool_size(0).build()


# -- ShardMemberConfig --------------------------------------------------------------------------


def test_primary_secondary_and_with_config_each_put_one_entry():
    lock = a_source(OTHER_URL)
    built = (
        ShardMemberConfig.builder()
        .member(3)
        .name("eu-1")
        .primary_config(a_source())
        .secondary_config(a_source(OTHER_URL))
        .with_config("lockConfig", lock)
        .build()
    )

    assert built.member == 3
    assert built.name == "eu-1"
    assert built.primary_config() == a_source()
    assert built.secondary_config() == a_source(OTHER_URL)
    assert built.config_for("lockConfig") is lock


def test_configs_replaces_every_entry_set_before_it():
    built = (
        ShardMemberConfig.builder()
        .member(0)
        .with_config("lockConfig", a_source())
        .configs({ShardMemberConfig.PRIMARY_CONFIG: a_source(OTHER_URL)})
        .build()
    )

    assert dict(built.configs) == {ShardMemberConfig.PRIMARY_CONFIG: a_source(OTHER_URL)}


def test_with_config_refuses_a_blank_name_when_it_is_called():
    with pytest.raises(ValidationError, match="name cannot be blank"):
        ShardMemberConfig.builder().with_config(" ", a_source())


def test_a_member_without_a_number_is_refused():
    with pytest.raises(ValidationError, match="member is required"):
        ShardMemberConfig.builder().primary_config(a_source()).build()


def test_a_member_without_a_primary_is_refused_by_the_constructor():
    with pytest.raises(ValidationError, match="primaryConfig is required"):
        ShardMemberConfig.builder().member(0).build()


# -- ShardGroupConfig ---------------------------------------------------------------------------


def test_with_member_adds_one_and_members_replaces_them():
    first, second, third = a_member(0), a_member(1), a_member(2)

    added = ShardGroupConfig.builder().group(0).name("g").with_member(first).with_member(second)
    assert added.build().members == (first, second)

    assert added.members([third]).build().members == (third,)


def test_a_group_without_a_number_is_refused():
    with pytest.raises(ValidationError, match="group is required"):
        ShardGroupConfig.builder().name("g").with_member(a_member()).build()


# -- ShardingConfig -----------------------------------------------------------------------------


def test_with_group_adds_one_and_groups_replaces_them():
    first, second = a_group(0), a_group(1)

    added = ShardingConfig.builder().default_shard(FIRST).with_group(first).with_group(second)
    assert added.build().groups == (first, second)

    assert added.groups([first]).build().groups == (first,)


def test_the_unknown_shard_policy_defaults_and_can_be_set():
    builder = ShardingConfig.builder().default_shard(FIRST).with_group(a_group())
    assert builder.build().unknown_shard_policy is UnknownShardPolicy.USE_DEFAULT_SHARD
    assert (
        builder.unknown_shard_policy(UnknownShardPolicy.FAIL).build().unknown_shard_policy
        is UnknownShardPolicy.FAIL
    )


def test_a_layout_without_a_default_shard_is_refused():
    with pytest.raises(ValidationError, match="default_shard is required"):
        ShardingConfig.builder().with_group(a_group()).build()


# -- ExecutionConfiguration ---------------------------------------------------------------------


def test_the_builder_starts_from_one_retry_on_a_lost_version_race():
    assert ExecutionConfiguration.builder().build() == ExecutionConfiguration()


def test_with_retry_for_a_class_already_held_replaces_it():
    """Java's map is keyed by the class, so a second policy for it replaces the first. Appended
    instead, it would never be reached - the first match wins."""
    three = RetryConfig(on=StaleRecordError, max_retries=3, delay=timedelta(milliseconds=100))

    built = ExecutionConfiguration.builder().with_retry(three).build()

    assert built.retry_configs == (three,)


def test_with_retry_for_a_new_class_adds_it_after_the_ones_held():
    timeouts = RetryConfig(on=TimeoutError, max_retries=2, delay=timedelta(seconds=1))

    built = ExecutionConfiguration.builder().with_retry(timeouts).build()

    assert built.retry_configs == (*DEFAULT_RETRIES, timeouts)


def test_no_retry_drops_the_default_too():
    assert ExecutionConfiguration.builder().no_retry().build().retry_configs == ()


def test_retry_configs_replaces_every_policy():
    timeouts = RetryConfig(on=TimeoutError, max_retries=1, delay=timedelta(seconds=1))
    built = ExecutionConfiguration.builder().retry_configs([timeouts]).build()
    assert built.retry_configs == (timeouts,)


def test_allow_cross_shard_is_off_unless_set():
    assert not ExecutionConfiguration.builder().build().allow_cross_shard
    assert ExecutionConfiguration.builder().allow_cross_shard(True).build().allow_cross_shard


# -- DatabaseRegistry ---------------------------------------------------------------------------


MEMORY = DataSourceConfig(url="sqlite://")


def a_manager(shard: ShardIdentifier) -> TransactionManager:
    return TransactionManager(shard, DataSource.from_config(MEMORY))


def test_with_default_database_registers_it_and_makes_it_the_default():
    first, second = a_manager(FIRST), a_manager(SECOND)

    with (
        DatabaseRegistry.builder().with_database(first).with_default_database(second).build()
    ) as registry:
        assert registry.default_shard == SECOND
        assert registry.manager(FIRST) is first
        assert registry.manager(SECOND) is second


def test_one_database_needs_no_default_named():
    with DatabaseRegistry.builder().with_database(a_manager(FIRST)).build() as registry:
        assert registry.default_shard == FIRST


def test_the_unknown_shard_policy_none_means_the_default():
    with (
        DatabaseRegistry.builder()
        .with_database(a_manager(FIRST))
        .with_unknown_shard_policy(UnknownShardPolicy.FAIL)
        .with_unknown_shard_policy(None)
        .build()
    ) as registry:
        assert registry.unknown_shard_policy is UnknownShardPolicy.USE_DEFAULT_SHARD


def test_a_registry_with_no_database_is_refused():
    with pytest.raises(ValidationError, match="at least one database is required"):
        DatabaseRegistry.builder().build()


# -- ActionExecutor -----------------------------------------------------------------------------


def test_an_executor_without_an_event_persister_gets_the_default_one():
    """Java's builder makes a `SingleTableJsonEventPersister` when none is set (Dracula 14)."""
    from ekbatan.action import SingleTableJsonEventPersister
    from ekbatan.repository import RepositoryRegistry
    from fixtures.domain import Wallet
    from fixtures.repositories import WalletRepository
    from fixtures.schema import WALLETS

    source = DataSource.from_config(MEMORY)
    registry = DatabaseRegistry(
        {ShardIdentifier(0, 0): TransactionManager(ShardIdentifier(0, 0), source)}
    )
    executor = (
        ActionExecutor.builder()
        .namespace("example.wallet")
        .database_registry(registry)
        .repository_registry(RepositoryRegistry([WalletRepository(Wallet, WALLETS, registry)]))
        .build()
    )
    assert isinstance(executor.outbox, SingleTableJsonEventPersister)
    assert executor.outbox._event_repository._table.name == "events"
    source.close()


def test_an_executor_without_a_database_registry_is_refused_by_name():
    with pytest.raises(ValidationError, match="registry is required"):
        ActionExecutor.builder().namespace("example.wallet").event_persister(
            object()  # type: ignore[arg-type]
        ).build()


# -- the lock providers -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider", [PostgresKeyedLockProvider, MySQLKeyedLockProvider, MariaDBKeyedLockProvider]
)
def test_each_sql_lock_provider_builds_its_own_class(provider):
    with DataSource.from_config(MEMORY) as source:
        built = provider.builder().data_source(source).build()

        assert type(built) is provider
        assert built._data_source is source


def test_a_sql_lock_provider_without_a_data_source_is_refused():
    with pytest.raises(ValidationError, match=r"^data_source is required$"):
        PostgresKeyedLockProvider.builder().build()


class FakeRedis:
    def register_script(self, _script: str) -> object:
        return object()


def test_the_redis_lock_provider_keeps_javas_default_namespace():
    built = RedisKeyedLockProvider.builder().client(FakeRedis()).build()
    assert built._prefix == f"{DEFAULT_NAMESPACE}:"


def test_the_redis_lock_provider_takes_a_namespace():
    built = RedisKeyedLockProvider.builder().client(FakeRedis()).namespace("wallets").build()
    assert built._prefix == "wallets:"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_redis_namespace_is_refused_when_it_is_set(blank):
    with pytest.raises(ValidationError, match="namespace cannot be blank"):
        RedisKeyedLockProvider.builder().namespace(blank)


def test_a_redis_lock_provider_without_a_client_is_refused():
    with pytest.raises(ValidationError, match="client is required"):
        RedisKeyedLockProvider.builder().build()


# -- the local event handler --------------------------------------------------------------------


def test_with_handler_and_with_handlers_register_each_one():
    first = RecordingHandler("first", WalletMoneyDepositedEvent)
    second = RecordingHandler("second", WalletMoneyDepositedEvent)
    third = RecordingHandler("third", WalletMoneyDepositedEvent)

    built = (
        EventHandlerRegistry.builder().with_handler(first).with_handlers([second, third]).build()
    )

    assert built.subscribers("WalletMoneyDepositedEvent") == [first, second, third]


def test_a_duplicate_handler_name_is_refused_on_the_line_that_adds_it():
    builder = EventHandlerRegistry.builder().with_handler(
        RecordingHandler("same", WalletMoneyDepositedEvent)
    )
    with pytest.raises(ValidationError, match="Handler name or alias already registered: same"):
        builder.with_handler(RecordingHandler("same", WalletMoneyDepositedEvent))


def test_the_handler_registry_builder_can_build_more_than_once():
    handler = RecordingHandler("only", WalletMoneyDepositedEvent)
    builder = EventHandlerRegistry.builder().with_handler(handler)

    assert len(builder.build()) == len(builder.build()) == 1


def test_the_event_handler_config_built_with_every_setter_matches_the_constructor():
    built = (
        LocalEventHandlerConfig.builder()
        .fanout_poll_delay(timedelta(seconds=1))
        .fanout_batch_size(10)
        .handling_poll_delay(timedelta(seconds=2))
        .handling_batch_size(20)
        .handling_max_backoff_cap(timedelta(minutes=5))
        .handling_retention_window(timedelta(days=1))
        .handling(HandlingConfig.builder().enabled(True).build())
        .build()
    )

    assert built == LocalEventHandlerConfig(
        fanout_poll_delay=timedelta(seconds=1),
        fanout_batch_size=10,
        handling_poll_delay=timedelta(seconds=2),
        handling_batch_size=20,
        handling_max_backoff_cap=timedelta(minutes=5),
        handling_retention_window=timedelta(days=1),
        handling=HandlingConfig(enabled=True),
    )


def test_an_empty_event_handler_config_builder_gives_the_defaults():
    assert LocalEventHandlerConfig.builder().build() == LocalEventHandlerConfig.defaults()
    assert HandlingConfig.builder().build() == HandlingConfig.defaults()


def test_handling_none_means_the_default_handling_config():
    built = LocalEventHandlerConfig.builder().handling(None).build()
    assert built.handling == HandlingConfig.defaults()


def test_the_event_handler_configs_checks_still_run():
    with pytest.raises(ValidationError, match="fanout_batch_size must be at least 1"):
        LocalEventHandlerConfig.builder().fanout_batch_size(0).build()


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_an_envelope_leaves_model_id_and_type_none_unless_set():
    event = WalletMoneyDepositedEvent(model_id="w-1", amount=Decimal(1), new_balance=Decimal(1))
    built = (
        EventEnvelope.builder()
        .event(event)
        .event_id(uuid.UUID(int=1))
        .namespace("example.wallet")
        .action_id(uuid.UUID(int=2))
        .action_name("WalletDepositMoneyAction")
        .action_params({"amount": "1"})
        .started_date(NOW)
        .completion_date(NOW)
        .event_date(NOW)
        .build()
    )

    assert built.event is event
    assert built.model_id is None
    assert built.model_type is None
    assert built.attempt == 1


def test_an_envelope_names_every_field_it_is_missing():
    with pytest.raises(ValidationError, match="event_id, namespace, action_id"):
        EventEnvelope.builder().event(object()).build()  # type: ignore[arg-type]


def test_an_action_event_can_be_a_sentinel_row():
    built = (
        ActionEvent.builder()
        .id(uuid.UUID(int=1))
        .namespace("example.wallet")
        .action_id(uuid.UUID(int=2))
        .action_name("WalletCloseAction")
        .action_params({})
        .started_date(NOW)
        .completion_date(NOW)
        .event_date(NOW)
        .build()
    )

    assert built.is_sentinel
    assert not built.delivered


def test_an_action_event_built_with_every_setter_matches_the_constructor():
    fields = {
        "id": uuid.UUID(int=1),
        "namespace": "example.wallet",
        "action_id": uuid.UUID(int=2),
        "action_name": "WalletDepositMoneyAction",
        "action_params": {"amount": "1"},
        "started_date": NOW,
        "completion_date": NOW,
        "model_id": "w-1",
        "model_type": "Wallet",
        "event_type": "WalletMoneyDepositedEvent",
        "payload": {"amount": "1"},
        "event_date": NOW,
        "delivered": True,
    }
    builder = ActionEvent.builder()
    for name, value in fields.items():
        getattr(builder, name)(value)

    assert builder.build() == ActionEvent(**fields)


def test_an_action_event_without_an_id_is_refused():
    with pytest.raises(ValidationError, match="id, namespace"):
        ActionEvent.builder().build()


# -- the migrator -------------------------------------------------------------------------------
#
# Only what the builder decides before any database is reached; migrating is in
# tests/integration/test_migrations.py.


def test_the_migrator_needs_at_least_one_target():
    """Java's `IllegalStateException("at least one Flyway migration target is required")`."""
    with pytest.raises(ValidationError, match="at least one migration target is required"):
        YoyoMigrator.builder().locations(Path(".")).migrate()


def test_the_migrator_needs_at_least_one_location():
    with pytest.raises(ValidationError, match="at least one migrations location is required"):
        YoyoMigrator.builder().with_data_source(a_source()).migrate()


def test_a_missing_location_is_refused_before_any_database_is_reached(tmp_path):
    with pytest.raises(ValidationError, match="no migrations directory"):
        YoyoMigrator.builder().with_data_source(a_source()).locations(tmp_path / "nope").migrate()


def test_with_data_source_is_javas_default_target():
    built = YoyoMigrator.builder().with_data_source(a_source())
    assert built._targets == [YoyoMigrator.Target(ShardIdentifier(0, 0), "default", a_source())]


def test_with_sharding_config_adds_a_target_per_member_named_as_java_names_them():
    layout = (
        ShardingConfig.builder()
        .default_shard(FIRST)
        .with_group(
            ShardGroupConfig.builder()
            .group(0)
            .name("global")
            .with_member(
                ShardMemberConfig.builder().member(0).name("eu").primary_config(a_source()).build()
            )
            .with_member(
                ShardMemberConfig.builder().member(1).primary_config(a_source(OTHER_URL)).build()
            )
            .build()
        )
        .build()
    )

    built = YoyoMigrator.builder().with_sharding_config(layout)

    assert [(t.shard, t.name, t.data_source.url) for t in built._targets] == [
        (FIRST, "global/eu", URL),
        (SECOND, "global/member-1", OTHER_URL),
    ]


def test_a_none_target_is_refused_where_it_is_added():
    with pytest.raises(ValidationError, match="target is required"):
        YoyoMigrator.builder().with_target(None)  # type: ignore[arg-type]


def test_the_registry_builder_refuses_a_policy_given_as_a_string():
    """Java's `withUnknownShardPolicy` takes the enum; a string is refused, as Java's compiler
    refuses one, naming the members and `UnknownShardPolicy.from_`."""
    with pytest.raises(TypeError, match=r"takes an UnknownShardPolicy, not a str \('fail'\)"):
        DatabaseRegistry.builder().with_unknown_shard_policy("fail")  # type: ignore[arg-type]
