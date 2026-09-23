"""Configuration, and the mistakes it refuses to accept.

Java's `DataSourceConfigDialectTest` and `DataSourceConfigValidationTest`, and what
`ShardingConfigYamlTest` / `ShardingConfigJacksonBindingTest` assert about the layout once it is
bound. The binding itself has no counterpart - nothing here reads a property file, because there
is no DI container asking it to - so those cases build the same layout directly.
"""

from __future__ import annotations

import pytest

from ekbatan.config import DataSourceConfig, ShardGroupConfig, ShardingConfig, ShardMemberConfig
from ekbatan.errors import ValidationError
from ekbatan.sharding import ShardIdentifier
from fixtures.config import group, member

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)


def a_source(url: str = "postgresql+psycopg://u:p@h/db", **kw: object) -> DataSourceConfig:
    return DataSourceConfig(url=url, **kw)  # type: ignore[arg-type]


# -- the dialect comes from the scheme, and only from the scheme -------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql+psycopg://localhost:5432/db", "postgres"),
        ("mysql+pymysql://localhost:3306/db", "mysql"),
        ("mariadb+pymysql://localhost:3306/db", "mariadb"),
        ("sqlite:////tmp/x.db", "sqlite"),
        # The cases Java's test exists for. A team migrating MySQL to MariaDB keeps its
        # hostnames and its Kubernetes service names, so a MariaDB url pointing at a host
        # called `mysql-01` is ordinary rather than contrived - and reading the dialect by
        # searching the whole string for a driver name resolves it to MySQL, after which the
        # framework renders MySQL-flavoured SQL at MariaDB and nothing fails.
        ("mariadb+pymysql://mysql-01.prod.internal/app", "mariadb"),
        ("mariadb+pymysql://mysql.default.svc.cluster.local/db", "mariadb"),
        ("mysql+pymysql://mariadb-host/db", "mysql"),
        ("postgresql://mysql-proxy/db", "postgres"),
        ("mariadb+pymysql://host/mysql_compat", "mariadb"),
        ("mysql+pymysql://h1:3306,h2:3306/db", "mysql"),
        ("postgresql://h/db?ssl=true&application_name=x", "postgres"),
    ],
)
def test_the_dialect_is_read_from_the_scheme_not_the_rest_of_the_url(url, expected):
    assert a_source(url).dialect == expected


def test_a_url_nothing_can_be_read_out_of_says_so():
    with pytest.raises(ValidationError, match="cannot tell which database"):
        a_source("not-a-url")


def test_a_recognised_but_unsupported_database_gets_a_different_message():
    """Java draws the same line: "could not tell" and "told, but unsupported" are different
    mistakes, and one message covering both sends the reader to the wrong place."""
    with pytest.raises(ValidationError, match="unsupported database 'oracle'"):
        a_source("oracle://u:p@h/db")


def test_a_jdbc_url_is_named_for_what_it_is():
    """The likeliest mistake anyone moving between the two ports makes. Reported as an
    unsupported database it reads as a missing driver rather than a wrong string."""
    with pytest.raises(ValidationError, match="is a JDBC url"):
        a_source("jdbc:postgresql://localhost:5432/db")


# -- pool settings that could never work -------------------------------------------------------


def test_a_url_is_required():
    for blank in ("", "   "):
        with pytest.raises(ValidationError, match="url is required"):
            a_source(blank)


@pytest.mark.parametrize("size", [0, -1])
def test_a_pool_that_can_never_serve_a_connection_is_refused(size):
    """Java's `should_reject_a_pool_that_can_never_serve_a_connection`. Hikari rejects zero too,
    but only when the pool is built - so a deployment that catches startup failures sees a hang
    on the first query instead of a bad setting."""
    with pytest.raises(ValidationError, match="pool_size"):
        a_source(pool_size=size)


def test_a_negative_overflow_is_refused():
    with pytest.raises(ValidationError, match="max_overflow"):
        a_source(max_overflow=-1)


@pytest.mark.parametrize("timeout", [0, -1.0])
def test_a_timeout_that_would_never_wait_is_refused(timeout):
    with pytest.raises(ValidationError, match="pool_timeout"):
        a_source(pool_timeout=timeout)


def test_the_defaults_build():
    config = a_source()

    assert config.pool_size == 10
    assert config.dialect == "postgres"


# -- the layout ---------------------------------------------------------------------------------


def test_the_full_layout_reads_back_as_written():
    """`should_deserialize_full_sharding_config_from_yaml`, less the YAML: two groups, a member
    with a replica and a user-defined `lockConfig`, and one with a primary only."""
    lock = a_source(pool_size=50)
    config = ShardingConfig(
        default_shard=GLOBAL,
        groups=(
            group(
                0,
                "global",
                member(0, a_source(pool_size=20), a_source(), name="global-eu-1", lockConfig=lock),
            ),
            group(1, "mexico", member(0, a_source(), name="mexico-1")),
        ),
    )

    assert [g.name for g in config.groups] == ["global", "mexico"]
    global_member = config.groups[0].members[0]
    assert (global_member.member, global_member.name) == (0, "global-eu-1")
    assert global_member.primary_config().pool_size == 20
    assert global_member.secondary_config() is not None
    assert global_member.config_for("lockConfig") is lock
    mexico_member = config.groups[1].members[0]
    assert mexico_member.config_for("lockConfig") is None, "one member's configs are not another's"
    assert config.default_shard == GLOBAL


def test_a_member_with_only_a_primary_has_no_secondary():
    """`should_handle_member_with_only_primary_config`."""
    only = member(0, a_source())

    assert only.secondary_config() is None
    assert only.config_for("anything") is None


def test_a_member_without_a_primary_is_refused():
    """`should_throw_clear_error_when_member_omits_primary_config`, with Java's message."""
    with pytest.raises(ValidationError, match=r"configs\.primaryConfig is required"):
        ShardMemberConfig(member=0, configs={ShardMemberConfig.SECONDARY_CONFIG: a_source()})


def test_the_reserved_names_are_javas_spelling():
    """So one layout, and one YAML file, describes both ports."""
    assert ShardMemberConfig.PRIMARY_CONFIG == "primaryConfig"
    assert ShardMemberConfig.SECONDARY_CONFIG == "secondaryConfig"


def test_config_for_answers_the_reserved_names_like_any_other():
    primary, secondary = a_source(), a_source()
    both = member(0, primary, secondary)

    assert both.config_for("primaryConfig") is primary
    assert both.config_for("secondaryConfig") is secondary


@pytest.mark.parametrize("blank", ["", "   "])
def test_config_for_refuses_a_blank_name(blank):
    """Java's `Validate.notBlank(name, "name cannot be blank")`."""
    with pytest.raises(ValidationError, match="name cannot be blank"):
        member(0, a_source()).config_for(blank)


def test_a_blank_config_name_is_refused_when_it_is_configured():
    """It could never be read back, since `config_for` refuses it."""
    with pytest.raises(ValidationError, match="config name cannot be blank"):
        member(0, a_source(), **{" ": a_source()})


def test_the_configs_cannot_be_changed_afterwards():
    """Java's `Map.copyOf`: neither the member nor the caller's dict can move it."""
    configs = {ShardMemberConfig.PRIMARY_CONFIG: a_source()}
    frozen = ShardMemberConfig(member=0, configs=configs)
    configs["lockConfig"] = a_source()

    assert frozen.config_for("lockConfig") is None
    with pytest.raises(TypeError):
        frozen.configs["lockConfig"] = a_source()  # type: ignore[index]


def test_a_group_needs_a_name():
    with pytest.raises(ValidationError, match="name is required"):
        ShardGroupConfig(group=0, name=" ", members=(member(0, a_source()),))


def test_a_group_needs_at_least_one_member():
    with pytest.raises(ValidationError, match="at least one member is required"):
        ShardGroupConfig(group=0, name="global", members=())


def test_a_layout_needs_at_least_one_group():
    with pytest.raises(ValidationError, match="at least one group is required"):
        ShardingConfig(default_shard=GLOBAL, groups=())


def test_a_layout_needs_a_default_shard():
    with pytest.raises(ValidationError, match="default_shard is required"):
        ShardingConfig(default_shard=None, groups=(group(0, "g", member(0, a_source())),))  # type: ignore[arg-type]


def test_a_shard_cannot_be_configured_twice():
    """Not a Java check. There a repeated (group, member) builds a second pool, overwrites the
    first in the registry, and leaves the first open with nothing holding it."""
    with pytest.raises(ValidationError, match="configured twice"):
        ShardingConfig(
            default_shard=GLOBAL,
            groups=(group(0, "global", member(0, a_source()), member(0, a_source())),),
        )


def test_the_default_shard_has_to_name_one_of_them():
    """Checked before a single engine is built. Run afterwards - which is where Java had it
    until `EKB-0000` moved it - the realistic mistake here opens every pool and then throws,
    and nothing holds a reference to them afterwards."""
    with pytest.raises(ValidationError, match="names no configured database"):
        ShardingConfig(default_shard=MEXICO, groups=(group(0, "global", member(0, a_source())),))


def test_every_shard_has_to_speak_the_same_dialect():
    """Not something Java checks, and it should: the framework picks its SQL, its lock provider
    and its upsert form from one dialect, so a layout mixing two has already decided wrongly for
    one of them."""
    with pytest.raises(ValidationError, match="same dialect"):
        ShardingConfig(
            default_shard=GLOBAL,
            groups=(
                group(0, "global", member(0, a_source("postgresql://u:p@h/db"))),
                group(1, "mexico", member(0, a_source("mysql+pymysql://u:p@h/db"))),
            ),
        )


def test_members_walks_groups_then_members_in_the_order_configured():
    config = ShardingConfig(
        default_shard=GLOBAL,
        groups=(
            group(0, "global", member(0, a_source()), member(1, a_source())),
            group(1, "mexico", member(0, a_source())),
        ),
    )

    assert [str(shard) for shard, _ in config.members()] == [
        str(ShardIdentifier(0, 0)),
        str(ShardIdentifier(0, 1)),
        str(ShardIdentifier(1, 0)),
    ]
    assert config.member(MEXICO) is config.groups[1].members[0]
    with pytest.raises(ValidationError, match="no member is configured for shard"):
        config.member(ShardIdentifier(9, 9))


def test_the_single_database_shortcut_needs_nothing_else():
    config = ShardingConfig.single("sqlite:///:memory:")

    assert config.default_shard == GLOBAL
    assert config.dialect == "sqlite"
    assert len(list(config.members())) == 1


# -- the namespace, which is written into every row this executor ever produces ------------------
#
# Java's ActionExecutorNamespaceValidationTest, case for case.


@pytest.mark.parametrize(
    "namespace", ["com.example", "a", "my_service.wallet", "_leading", "a.b.c.d", "x1.y2"]
)
def test_a_namespace_shaped_like_a_package_is_accepted(namespace):
    from ekbatan.action.executor import _valid_namespace

    assert _valid_namespace(namespace) == namespace


@pytest.mark.parametrize("namespace", ["my-service", "wallet-api.core"])
def test_a_hyphenated_namespace_is_rejected_and_says_what_to_write(namespace):
    """A hyphen is not legal in a protobuf package or an Avro namespace, and this value is used
    as one. The message names the value and shows the shape that works."""
    from ekbatan.action.executor import _valid_namespace

    with pytest.raises(ValidationError) as raised:
        _valid_namespace(namespace)
    assert namespace in str(raised.value)
    assert "my_service" in str(raised.value), "an error that does not say what to write instead"


@pytest.mark.parametrize("namespace", ["1abc", "a..b", "a.", ".a", "a b", "a/b", "a.b-c"])
def test_anything_else_that_is_not_an_identifier_is_rejected(namespace):
    from ekbatan.action.executor import _valid_namespace

    with pytest.raises(ValidationError):
        _valid_namespace(namespace)


def test_a_valid_prefix_does_not_smuggle_in_an_invalid_remainder():
    """The regex is anchored at both ends. Unanchored, `valid.prefix-then-rubbish` matches on
    its first two segments and the rest rides along into every row."""
    from ekbatan.action.executor import _valid_namespace

    with pytest.raises(ValidationError):
        _valid_namespace("valid.prefix-then-rubbish")


@pytest.mark.parametrize("namespace", ["", "   ", "\t"])
def test_a_blank_namespace_still_says_it_is_required(namespace):
    from ekbatan.action.executor import _valid_namespace

    with pytest.raises(ValidationError, match="namespace is required"):
        _valid_namespace(namespace)


# -- the unregistered shard, and what a deployment wants done about it ---------------------------


def test_unknown_shard_policy_defaults_to_use_default_shard_when_nobody_names_one():
    """`EKB-0200`. Omitting it has to mean `use_default_shard`: Java's `effectiveShard` falls
    back, and a deployment holding a subset of the layout reads the rest's ids because of it."""
    layout = ShardingConfig(default_shard=GLOBAL, groups=(group(0, "g", member(0, a_source())),))

    assert layout.unknown_shard_policy == "use_default_shard"


def test_unknown_shard_policy_refuses_a_value_that_is_neither():
    with pytest.raises(ValidationError, match="must be 'use_default_shard' or 'fail'"):
        ShardingConfig(
            default_shard=GLOBAL,
            groups=(group(0, "g", member(0, a_source())),),
            unknown_shard_policy="raise",  # type: ignore[arg-type]
        )
