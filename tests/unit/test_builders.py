"""The builders Java wires everything with, and what each setter does.

Every one of them builds through the constructor, so what the constructor refuses the builder
refuses too, and those refusals are tested where the constructor is. What is tested here is the
builder's own behaviour: which setters add one and which replace the lot, what an uncalled
setter leaves, and the few refusals that happen before the constructor is reached.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from ekbatan.action import ActionExecutor, ExecutionConfiguration, RetryConfig
from ekbatan.config import DataSourceConfig, ShardGroupConfig, ShardingConfig, ShardMemberConfig
from ekbatan.errors import StaleRecordError, ValidationError
from ekbatan.persistence import DatabaseRegistry, TransactionManager
from ekbatan.sharding import ShardIdentifier

URL = "postgresql+psycopg://u:p@h/db"
OTHER_URL = "postgresql+psycopg://u:p@other/db"
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
        .connect_args({"connect_timeout": 2})
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
        connect_args={"connect_timeout": 2},
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
    assert builder.build().unknown_shard_policy == "use_default_shard"
    assert builder.unknown_shard_policy("fail").build().unknown_shard_policy == "fail"


def test_a_layout_without_a_default_shard_is_refused():
    with pytest.raises(ValidationError, match="default_shard is required"):
        ShardingConfig.builder().with_group(a_group()).build()


# -- ExecutionConfiguration ---------------------------------------------------------------------


def test_the_builder_starts_from_one_retry_on_a_lost_version_race():
    assert ExecutionConfiguration.builder().build() == ExecutionConfiguration()


def test_with_retry_for_a_class_already_held_replaces_it():
    """Java's map is keyed by the class, so a second policy for it replaces the first. Appended
    instead, it would never be reached - the first match wins."""
    three = RetryConfig(on=StaleRecordError, max_retries=3)

    built = ExecutionConfiguration.builder().with_retry(three).build()

    assert built.retry_configs == (three,)


def test_with_retry_for_a_new_class_adds_it_after_the_ones_held():
    timeouts = RetryConfig(on=TimeoutError, max_retries=2)

    built = ExecutionConfiguration.builder().with_retry(timeouts).build()

    assert built.retry_configs == (RetryConfig(), timeouts)


def test_no_retry_drops_the_default_too():
    assert ExecutionConfiguration.builder().no_retry().build().retry_configs == ()


def test_retry_configs_replaces_every_policy():
    timeouts = RetryConfig(on=TimeoutError)
    built = ExecutionConfiguration.builder().retry_configs([timeouts]).build()
    assert built.retry_configs == (timeouts,)


def test_allow_cross_shard_is_off_unless_set():
    assert not ExecutionConfiguration.builder().build().allow_cross_shard
    assert ExecutionConfiguration.builder().allow_cross_shard(True).build().allow_cross_shard


# -- DatabaseRegistry ---------------------------------------------------------------------------


def a_manager(shard: ShardIdentifier) -> TransactionManager:
    return TransactionManager(shard, create_engine("sqlite://"), dialect="sqlite")


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
        .with_unknown_shard_policy("fail")
        .with_unknown_shard_policy(None)
        .build()
    ) as registry:
        assert registry.unknown_shard_policy == "use_default_shard"


def test_a_registry_with_no_database_is_refused():
    with pytest.raises(ValidationError, match="at least one database is required"):
        DatabaseRegistry.builder().build()


# -- ActionExecutor -----------------------------------------------------------------------------


def test_an_executor_without_an_event_persister_is_refused_by_name():
    with pytest.raises(ValidationError, match="event_persister is required"):
        ActionExecutor.builder().namespace("example.wallet").build()


def test_an_executor_without_a_database_registry_is_refused_by_name():
    with pytest.raises(ValidationError, match="registry is required"):
        ActionExecutor.builder().namespace("example.wallet").event_persister(
            object()  # type: ignore[arg-type]
        ).build()
