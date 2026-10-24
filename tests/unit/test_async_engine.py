"""`ekbatan.aio` without a database: which driver it connects with, how libpq's settings reach
asyncpg, that an async pool is configured as a sync one is - and that the async classes keep
the sync ones' public surface, so the two hierarchies cannot drift apart unnoticed.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

from ekbatan.aio import (
    ASYNC_DEFAULT_DRIVERS,
    AbstractAsyncRepository,
    AsyncActionExecutor,
    AsyncActionExecutorBuilder,
    AsyncActionSpec,
    AsyncDatabaseRegistry,
    AsyncDatabaseRegistryBuilder,
    AsyncDataSource,
    AsyncTransactionManager,
    async_url,
)
from ekbatan.aio.locks import (
    AsyncLease,
    AsyncSqlKeyedLockProvider,
    AsyncSqlKeyedLockProviderBuilder,
)
from ekbatan.aio.locks.redis import AsyncRedisKeyedLockProvider, AsyncRedisKeyedLockProviderBuilder
from ekbatan.aio.persistence import _asyncpg_connect_args
from ekbatan.config import DataSourceConfig
from ekbatan.errors import ValidationError

MARKER = "Mk-41c9-SECRET"

# `--async-driver` swaps the PostgreSQL default for the whole run; these pin the shipped one.
SHIPPED_DEFAULTS = pytest.mark.skipif(
    ASYNC_DEFAULT_DRIVERS["postgres"] != "asyncpg",
    reason="--async-driver swapped the default PostgreSQL driver",
)


# -- the driver --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "connects_with"),
    [
        ("postgresql+psycopg://db/w", "postgresql+asyncpg"),
        ("postgresql://db/w", "postgresql+asyncpg"),
        ("postgresql+psycopg2://db/w", "postgresql+asyncpg"),
        ("mysql+pymysql://db/w", "mysql+aiomysql"),
        ("mysql://db/w", "mysql+aiomysql"),
        ("mariadb+pymysql://db/w", "mariadb+aiomysql"),
        ("sqlite:///tmp/w.db", "sqlite+aiosqlite"),
        ("sqlite://", "sqlite+aiosqlite"),
    ],
)
@SHIPPED_DEFAULTS
def test_a_url_naming_a_sync_driver_or_none_connects_through_the_async_default(url, connects_with):
    assert async_url(DataSourceConfig(url=url)).drivername == connects_with


@pytest.mark.parametrize(
    "url",
    ["postgresql+asyncpg://db/w", "postgresql+psycopg_async://db/w", "mysql+asyncmy://db/w"],
)
def test_a_url_naming_an_async_driver_keeps_it(url):
    assert async_url(DataSourceConfig(url=url)).render_as_string() == url


@SHIPPED_DEFAULTS
def test_each_database_has_a_default():
    assert dict(ASYNC_DEFAULT_DRIVERS) == {
        "postgres": "asyncpg",
        "mysql": "aiomysql",
        "mariadb": "aiomysql",
        "sqlite": "aiosqlite",
    }


def test_the_login_is_set_on_the_async_url_and_never_printed():
    config = DataSourceConfig(url="postgresql+psycopg://db/w", username="app", password=MARKER)
    url = async_url(config)
    source = AsyncDataSource.from_config(config)

    assert (url.username, url.password) == ("app", MARKER)
    for shown in (
        str(url),
        repr(url),
        repr(source),
        repr(source.sqlalchemy_engine),
        str(source.url),
        repr(config),
    ):
        assert MARKER not in shown


# -- the pool ----------------------------------------------------------------------------------


def test_an_async_pool_is_sized_and_timed_as_a_sync_one():
    """The pool is what caps the requests waiting on a database, so it must be the one the
    configuration says - not an async default."""
    config = DataSourceConfig(
        url="postgresql+psycopg://db/w", pool_size=3, max_overflow=2, pool_timeout=0.75
    )
    pool = AsyncDataSource.from_config(config).sqlalchemy_engine.sync_engine.pool

    assert pool.size() == 3  # type: ignore[attr-defined]
    assert pool._max_overflow == 2  # type: ignore[attr-defined]
    assert pool._timeout == 0.75  # type: ignore[attr-defined]


# -- libpq's settings, on asyncpg --------------------------------------------------------------


def test_options_become_server_settings():
    assert _asyncpg_connect_args({"options": "-c TimeZone=UTC -c statement_timeout=5000"}) == {
        "server_settings": {"TimeZone": "UTC", "statement_timeout": "5000"}
    }


def test_every_spelling_of_a_dash_c_setting_is_read():
    assert _asyncpg_connect_args({"options": "-cTimeZone=UTC --search-path=app"}) == {
        "server_settings": {"TimeZone": "UTC", "search_path": "app"}
    }


def test_the_libpq_names_with_an_asyncpg_twin_are_renamed():
    assert _asyncpg_connect_args(
        {"application_name": "svc", "connect_timeout": 5, "sslmode": "require"}
    ) == {"server_settings": {"application_name": "svc"}, "timeout": 5.0, "ssl": "require"}


def test_server_settings_given_directly_are_kept_and_merged():
    assert _asyncpg_connect_args(
        {"options": "-c TimeZone=UTC", "server_settings": {"jit": "off"}, "command_timeout": 9}
    ) == {"server_settings": {"jit": "off", "TimeZone": "UTC"}, "command_timeout": 9}


def test_other_settings_pass_through_untouched():
    assert _asyncpg_connect_args({"statement_cache_size": 0}) == {"statement_cache_size": 0}


@pytest.mark.parametrize(
    ("given", "says"),
    [
        ({"options": f"-c TimeZone=UTC --{MARKER}"}, "without 'name=value'"),
        ({"options": f"-x {MARKER}"}, "something other than '-c name=value'"),
        ({"options": f"-c a='{MARKER}"}, "unclosed quote"),
        ({"options": 7}, "must be text"),
        ({"options": f"-c x={MARKER}", "server_settings": {"x": MARKER}}, "x both as a libpq"),
        ({"connect_timeout": 5, "timeout": 5}, "both 'connect_timeout' and 'timeout'"),
        ({"application_name": MARKER, "server_settings": MARKER}, "must be a group"),
    ],
)
def test_a_setting_asyncpg_cannot_take_is_refused_without_repeating_it(given, says):
    with pytest.raises(ValidationError) as refused:
        _asyncpg_connect_args(given)

    assert says in str(refused.value)
    assert MARKER not in str(refused.value)


def test_the_translation_is_only_for_asyncpg():
    """aiomysql takes PyMySQL's names, and psycopg's async mode takes libpq's, as given."""
    for url in ("mysql+pymysql://db/w", "postgresql+psycopg_async://db/w"):
        config = DataSourceConfig(url=url, data_source_properties={"options": "-c x=1"})
        source = AsyncDataSource.from_config(config)
        assert source.url.get_driver_name() != "asyncpg"


# -- one public surface for both hierarchies ---------------------------------------------------


def public(cls: type) -> set[str]:
    return {name for name in dir(cls) if not name.startswith("_")}


@pytest.mark.parametrize(
    ("sync_path", "async_cls", "differ"),
    [
        ("ekbatan.repository.AbstractRepository", AbstractAsyncRepository, set()),
        ("ekbatan.action.ActionExecutor", AsyncActionExecutor, set()),
        ("ekbatan.action.ActionExecutorBuilder", AsyncActionExecutorBuilder, set()),
        # `close` and the sync context manager become `aclose` and `async with`.
        ("ekbatan.persistence.DatabaseRegistry", AsyncDatabaseRegistry, {"close", "aclose"}),
        ("ekbatan.persistence.DatabaseRegistryBuilder", AsyncDatabaseRegistryBuilder, set()),
        ("ekbatan.persistence.TransactionManager", AsyncTransactionManager, {"close", "aclose"}),
        ("ekbatan.persistence.DataSource", AsyncDataSource, {"close", "aclose"}),
        ("ekbatan.testing.ActionSpec", AsyncActionSpec, set()),
        ("ekbatan.locks.base.Lease", AsyncLease, set()),
        ("ekbatan.locks.sql.SqlKeyedLockProvider", AsyncSqlKeyedLockProvider, set()),
        ("ekbatan.locks.sql.SqlKeyedLockProviderBuilder", AsyncSqlKeyedLockProviderBuilder, set()),
        ("ekbatan.locks.redis.RedisKeyedLockProvider", AsyncRedisKeyedLockProvider, set()),
        (
            "ekbatan.locks.redis.RedisKeyedLockProviderBuilder",
            AsyncRedisKeyedLockProviderBuilder,
            set(),
        ),
    ],
)
def test_the_async_class_has_the_sync_ones_public_surface(sync_path, async_cls, differ):
    """A method added to one and not the other fails here, not in a user's editor."""
    module, _, name = sync_path.rpartition(".")
    sync_cls = getattr(__import__(module, fromlist=[name]), name)

    assert public(sync_cls) ^ public(async_cls) == differ


def test_what_waits_on_the_database_is_a_coroutine_and_nothing_else_is():
    from ekbatan.repository import AbstractRepository

    waits = {
        "add", "add_all", "add_all_no_result", "add_no_result", "all", "count", "exists",
        "exists_where", "find", "find_many", "find_one_where", "find_where", "get", "update",
        "update_all", "update_all_no_result", "update_no_result",
    }  # fmt: skip
    for name in public(AbstractRepository):
        member = getattr(AbstractAsyncRepository, name)
        if not callable(member):
            continue
        assert inspect.iscoroutinefunction(member) == (name in waits), name


def _twin_name(name: str) -> str:
    """The async twin's name: ``Async`` in front, ``AbstractAsync`` for an abstract class,
    ``async_`` for a function."""
    if name.startswith("Abstract"):
        return "AbstractAsync" + name.removeprefix("Abstract")
    if name[0].islower():
        return "async_" + name
    return "Async" + name


def _untwinned(name: str) -> str | None:
    """The sync name an ``ekbatan.aio`` name is the twin of, or ``None`` if it is not one."""
    for prefix, sync in (("AbstractAsync", "Abstract"), ("Async", ""), ("async_", "")):
        if name.startswith(prefix):
            return sync + name.removeprefix(prefix)
    return None


def test_ekbatan_aio_exports_the_twin_of_everything_ekbatan_exports():
    """A sync class exported from ``ekbatan`` whose async twin exists is exported from
    ``ekbatan.aio`` too, and the other way round - so an import line changes only by its
    module and the ``Async`` prefix. The keyed locks were once missed here."""
    import ekbatan
    import ekbatan.aio

    twins: set[str] = set()
    for info in pkgutil.walk_packages(ekbatan.aio.__path__, "ekbatan.aio."):
        twins |= set(vars(importlib.import_module(info.name)))

    exported_sync, exported_async = set(ekbatan.__all__), set(ekbatan.aio.__all__)
    missing = {
        _twin_name(name) for name in exported_sync if _twin_name(name) in twins
    } - exported_async
    assert missing == set(), f"exported from ekbatan, their twins not from ekbatan.aio: {missing}"

    # `async_url` turns a url into its async form; it has no sync counterpart.
    unmatched = {
        name
        for name in exported_async
        if _untwinned(name) is not None and _untwinned(name) not in exported_sync
    } - {"async_url"}
    assert unmatched == set(), f"exported from ekbatan.aio, their sync twins not: {unmatched}"
