"""Configuration, and the mistakes it refuses to accept.

Java's `DataSourceConfigDialectTest` and `DataSourceConfigValidationTest`. The Jackson- and
YAML-binding tests beside them have no counterpart: nothing here binds configuration from a
property file, because there is no DI container asking it to.
"""

from __future__ import annotations

import pytest

from ekbatan.config import DataSourceConfig, ShardConfig, ShardingConfig
from ekbatan.errors import ValidationError
from ekbatan.sharding import ShardIdentifier

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


def test_a_layout_needs_at_least_one_shard():
    with pytest.raises(ValidationError, match="at least one shard"):
        ShardingConfig(shards=(), default_shard=GLOBAL)


def test_a_shard_cannot_be_configured_twice():
    twice = ShardConfig(shard=GLOBAL, primary=a_source())
    with pytest.raises(ValidationError, match="configured twice"):
        ShardingConfig(shards=(twice, twice), default_shard=GLOBAL)


def test_the_default_shard_has_to_name_one_of_them():
    """Checked before a single engine is built. Run afterwards - which is where Java had it
    until `EKB-0000` moved it - the realistic mistake here opens every pool and then throws,
    and nothing holds a reference to them afterwards."""
    with pytest.raises(ValidationError, match="names no configured database"):
        ShardingConfig(
            shards=(ShardConfig(shard=GLOBAL, primary=a_source()),), default_shard=MEXICO
        )


def test_every_shard_has_to_speak_the_same_dialect():
    """Not something Java checks, and it should: the framework picks its SQL, its lock provider
    and its upsert form from one dialect, so a layout mixing two has already decided wrongly for
    one of them."""
    with pytest.raises(ValidationError, match="same dialect"):
        ShardingConfig(
            shards=(
                ShardConfig(shard=GLOBAL, primary=a_source("postgresql://u:p@h/db")),
                ShardConfig(shard=MEXICO, primary=a_source("mysql+pymysql://u:p@h/db")),
            ),
            default_shard=GLOBAL,
        )


def test_the_single_database_shortcut_needs_nothing_else():
    config = ShardingConfig.single("sqlite:///:memory:")

    assert config.default_shard == GLOBAL
    assert config.dialect == "sqlite"
    assert len(config.shards) == 1


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
