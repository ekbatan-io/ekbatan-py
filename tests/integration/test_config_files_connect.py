"""A layout read from a real file in each format, against a real database.

The password comes from an environment variable through the format's own placeholder, a driver
setting rides along, and the result migrates and serves a query - the whole path a deployment takes.
"""

from __future__ import annotations

import json

import pytest
import yaml
from sqlalchemy import text

from ekbatan.config import split_login
from ekbatan.config_files import load_sharding_config
from ekbatan.migrations import migrate
from ekbatan.persistence import DatabaseRegistry
from ekbatan.sharding import DEFAULT_SHARD
from fixtures.migrating import fresh

PROBE = "ekbatan-file-probe"

#: A setting each dialect reports back, as in `test_data_source_properties.py`.
SETTING = {
    "postgres": ("application_name", PROBE, "SELECT current_setting('application_name')"),
    "mysql": ("init_command", f"SET @ekbatan_probe = '{PROBE}'", "SELECT @ekbatan_probe"),
    "mariadb": ("init_command", f"SET @ekbatan_probe = '{PROBE}'", "SELECT @ekbatan_probe"),
}


def file_for(form: str, url: str, username: str | None, setting: tuple[str, str] | None) -> str:
    """The same layout in `form`, its password a placeholder for `TEST_DB_PASSWORD`."""
    source: dict[str, object] = {"url": url, "pool-size": 3}
    if username is not None:
        source["username"] = username
        source["password"] = "%(ENV_TEST_DB_PASSWORD)s" if form == "ini" else "${TEST_DB_PASSWORD}"
    if form == "ini":
        lines = [
            "[ekbatan.sharding.default-shard]",
            "group = 0",
            "member = 0",
            "",
            "[ekbatan.sharding.groups.0]",
            "group = 0",
            "name = default",
            "",
            "[ekbatan.sharding.groups.0.members.0]",
            "member = 0",
            "",
            "[ekbatan.sharding.groups.0.members.0.configs.primaryConfig]",
            *(f"{key} = {value}" for key, value in source.items()),
            "",
        ]
        if setting:
            # Quoted: an INI value is text unless it looks like a number - this one is text.
            lines += [
                "[ekbatan.sharding.groups.0.members.0.configs.primaryConfig.data-source-properties]",
                f'{setting[0]} = "{setting[1]}"',
            ]
        return "\n".join(lines) + "\n"
    if setting:
        source["data-source-properties"] = {setting[0]: setting[1]}
    layout = {
        "ekbatan": {
            "sharding": {
                "default-shard": {"group": 0, "member": 0},
                "groups": [
                    {
                        "group": 0,
                        "name": "default",
                        "members": [{"member": 0, "configs": {"primary-config": source}}],
                    }
                ],
            }
        }
    }
    if form == "yaml":
        return yaml.safe_dump(layout)
    if form == "json":
        return json.dumps(layout)
    lines = [
        "[ekbatan.sharding]",
        "default-shard = { group = 0, member = 0 }",
        "",
        "[[ekbatan.sharding.groups]]",
        "group = 0",
        'name = "default"',
        "",
        "[[ekbatan.sharding.groups.members]]",
        "member = 0",
        "",
        "[ekbatan.sharding.groups.members.configs.primary-config]",
        *(
            f"{key} = {json.dumps(value)}"
            for key, value in source.items()
            if key != "data-source-properties"
        ),
    ]
    if setting:
        lines += [
            "",
            "[ekbatan.sharding.groups.members.configs.primary-config.data-source-properties]",
            f"{setting[0]} = {json.dumps(setting[1])}",
        ]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("form", ["yaml", "toml", "json", "ini"])
def test_a_layout_from_a_file_logs_in_migrates_and_connects(backend, request, tmp_path, form):
    url, username, password = split_login(fresh(backend, request, f"_{form}"))
    name, value, read_back = SETTING.get(backend.dialect, (None, None, "SELECT 1"))
    setting = (name, value) if name else None
    config_file = tmp_path / f"ekbatan.{form}"
    config_file.write_text(file_for(form, url, username, setting))
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "V1__seen.sql").write_text("CREATE TABLE seen (n INTEGER);")

    layout = load_sharding_config(config_file, environ={"TEST_DB_PASSWORD": password or ""})
    migrate(layout, migrations)
    registry = DatabaseRegistry.from_config(layout)
    try:
        with registry.manager(DEFAULT_SHARD).primary.connect() as connection:
            reported = connection.execute(text(read_back)).scalar()
            assert connection.execute(text("SELECT count(*) FROM seen")).scalar() == 0
    finally:
        registry.close()

    assert reported == (PROBE if name else 1)
