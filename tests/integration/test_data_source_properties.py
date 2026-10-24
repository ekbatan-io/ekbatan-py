"""`data_source_properties` reach the driver - for the application's pool and for migrations alike.

Each dialect gets a setting whose effect the database itself reports: PostgreSQL's
`application_name`, and MySQL's and MariaDB's `init_command`, which sets a session variable. The
pool reads it back with a query; the migrator records it from inside a migration. A number is
passed as a number (`connect_timeout`), as the values are handed over as given.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from ekbatan.migrations import migrate
from ekbatan.persistence import DatabaseRegistry
from ekbatan.sharding import DEFAULT_SHARD
from fixtures.logins import single
from fixtures.migrating import fresh, query

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")

PROBE = "ekbatan-probe-7"

#: The setting, the query that reads it back, and the migration that records it - per dialect.
SETTINGS = {
    "postgres": (
        {"application_name": PROBE, "connect_timeout": 5},
        "SELECT current_setting('application_name')",
        "CREATE TABLE seen (what VARCHAR(40));\n"
        "INSERT INTO seen SELECT current_setting('application_name');",
    ),
    "mysql": (
        {"init_command": f"SET @ekbatan_probe = '{PROBE}'", "connect_timeout": 5},
        "SELECT @ekbatan_probe",
        "CREATE TABLE seen (what VARCHAR(40));\nINSERT INTO seen SELECT @ekbatan_probe;",
    ),
}
SETTINGS["mariadb"] = SETTINGS["mysql"]


def test_driver_settings_reach_the_pool_and_the_migrator(backend, request, tmp_path, mode):
    if backend.dialect == "sqlite":
        pytest.skip("no setting of sqlite3 that the database reports back")
    properties, read_back, migration = SETTINGS[backend.dialect]
    url = fresh(backend, request)
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "V1__seen.sql").write_text(migration)
    layout = single(url, data_source_properties=properties)

    migrate(layout, migrations)
    registry = DatabaseRegistry.from_config(layout)
    try:
        with registry.manager(DEFAULT_SHARD).primary.sqlalchemy_engine.connect() as connection:
            in_the_pool = connection.execute(text(read_back)).scalar()
    finally:
        registry.close() if mode == "sync" else registry.aclose()

    assert in_the_pool == PROBE
    assert [row[0] for row in query(url, "SELECT what FROM seen")] == [PROBE], "the migrator too"


def test_without_the_settings_neither_sees_the_probe(backend, request, tmp_path, mode):
    """The control: the probe comes from the settings, not from the database's defaults."""
    if backend.dialect == "sqlite":
        pytest.skip("no setting of sqlite3 that the database reports back")
    _, read_back, migration = SETTINGS[backend.dialect]
    url = fresh(backend, request)
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "V1__seen.sql").write_text(migration)
    layout = single(url)

    migrate(layout, migrations)
    registry = DatabaseRegistry.from_config(layout)
    try:
        with registry.manager(DEFAULT_SHARD).primary.sqlalchemy_engine.connect() as connection:
            in_the_pool = connection.execute(text(read_back)).scalar()
    finally:
        registry.close() if mode == "sync" else registry.aclose()

    assert in_the_pool != PROBE
    assert [row[0] for row in query(url, "SELECT what FROM seen")] != [PROBE]
