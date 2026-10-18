"""A real password, against a real database, and everything the framework writes while it runs.

The unit tests prove each object prints without its password. This proves the sum: a user with a
password nobody else has, every logger at DEBUG, the migrator through migrate, info, repair and a
failure, the engine with SQLAlchemy's echo on - and then the password is looked for in all of it:
the log, stdout, stderr, every result and every error. SQLite has no passwords to leak.
"""

from __future__ import annotations

import logging
import traceback
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from conftest import of_mode
from ekbatan.config import DataSourceConfig, ShardingConfig, split_login
from ekbatan.errors import LockAcquisitionError
from ekbatan.locks import (
    MariaDBKeyedLockProvider,
    MySQLKeyedLockProvider,
    PostgresKeyedLockProvider,
)
from ekbatan.migrations import MigrationError, info, migrate, repair
from ekbatan.persistence import DatabaseRegistry, engine_for
from ekbatan.sharding import DEFAULT_SHARD
from fixtures.migrating import fresh, query

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")

SECRET = "Zq7-s3cret-pw-for-tests"
WRONG = "Wr0ng-Zq7-pw-for-tests"
USER = "ekbatan_secret_user"


def with_secret_user(admin_url: str, dialect: str) -> str:
    """The same database, for a user whose password is `SECRET` - its url, with no login in it:
    the login goes in `username` and `password`."""
    database = make_url(admin_url).database
    if dialect == "postgres":
        query(
            admin_url,
            f"DO $$ BEGIN CREATE ROLE {USER} LOGIN PASSWORD '{SECRET}'; "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$",
        )
        query(admin_url, f"ALTER DATABASE {database} OWNER TO {USER}")
    else:
        query(admin_url, f"CREATE USER IF NOT EXISTS '{USER}'@'%' IDENTIFIED BY '{SECRET}'")
        query(admin_url, f"GRANT ALL PRIVILEGES ON `{database}`.* TO '{USER}'@'%'")
    return split_login(admin_url)[0]


def test_a_real_password_never_reaches_a_log_a_result_or_an_error(
    backend, request, tmp_path, caplog, capsys, mode
):
    if backend.dialect == "sqlite":
        pytest.skip("SQLite has no passwords")
    url = with_secret_user(fresh(backend, request), backend.dialect)
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "V1__runs.sql").write_text("CREATE TABLE runs (name VARCHAR(20));")
    (migrations / "V2__look.sql").write_text("SELECT 'looked' AS seen;")
    (migrations / "R__view.sql").write_text(
        "DROP VIEW IF EXISTS seen;\nCREATE VIEW seen AS SELECT 1 AS n;"
    )
    (migrations / "post-apply.sql").write_text("SELECT count(*) FROM runs;")
    caplog.set_level(logging.DEBUG)  # every logger, everything it says
    layout = ShardingConfig.single(url, username=USER, password=SECRET, echo=True)
    written: list[str] = [repr(layout), str(layout)]

    written.append(repr(migrate(layout, migrations)))
    written.append(repr(info(layout, migrations)))
    written.append(repr(repair(layout, migrations)))

    registry = DatabaseRegistry.from_config(layout)
    try:
        with registry.manager(DEFAULT_SHARD).primary.connect() as connection:
            written.append(repr(connection.execute(text("SELECT name FROM runs")).all()))
        written.append(repr(registry))
    finally:
        registry.close() if mode == "sync" else registry.aclose()

    (migrations / "V3__broken.sql").write_text("INSERT INTO nowhere VALUES (1);")
    with pytest.raises(MigrationError) as failed:
        migrate(layout, migrations)
    written += [str(failed.value), repr(failed.value)]

    with pytest.raises(MigrationError) as denied:
        migrate(ShardingConfig.single(url, username=USER, password=WRONG), migrations)
    written += [str(denied.value), repr(denied.value)]

    printed = capsys.readouterr()
    everything = "\n".join([caplog.text, printed.out, printed.err, *written])
    assert "looked" in caplog.text, "the log was captured: a migration's query output is in it"
    assert "V3__broken.sql" in str(failed.value), "and the failure was the migration's"
    assert SECRET not in everything
    assert WRONG not in everything, "nor a wrong password a login was refused with"


#: The SQL keyed lock provider each database implements; each has an async twin.
LOCKS = {
    "postgres": PostgresKeyedLockProvider,
    "mysql": MySQLKeyedLockProvider,
    "mariadb": MariaDBKeyedLockProvider,
}


def test_a_keyed_lock_on_that_login_prints_no_password(
    backend, request, caplog, capsys, mode, portal
):
    """The lock providers - sync, and `ekbatan.aio.locks`' async ones - over a pool whose login
    has the password, and over one with a wrong password: what they log, return and raise."""
    if backend.dialect == "sqlite":
        pytest.skip("SQLite has no passwords, and no keyed lock")
    url = with_secret_user(fresh(backend, request), backend.dialect)
    caplog.set_level(logging.DEBUG)
    provider = of_mode(mode, LOCKS[backend.dialect])
    right = engine_for(DataSourceConfig(url=url, username=USER, password=SECRET, echo=True))
    wrong = engine_for(DataSourceConfig(url=url, username=USER, password=WRONG))
    written: list[str] = []
    try:
        locks = provider(right)
        with locks.acquire("wallet:secret", timedelta(seconds=10)) as lease:
            written += [repr(locks), repr(lease), str(lease)]
        with pytest.raises(LockAcquisitionError) as refused:
            provider(wrong).acquire("wallet:secret", timedelta(seconds=10))
        written += [
            str(refused.value),
            repr(refused.value),
            "".join(traceback.format_exception(refused.value)),
        ]
    finally:
        for engine in (right, wrong):
            engine.dispose()

    printed = capsys.readouterr()
    everything = "\n".join([caplog.text, printed.out, printed.err, *written])
    assert "could not obtain a connection" in str(refused.value), "the login was refused"
    assert SECRET not in everything
    assert WRONG not in everything
