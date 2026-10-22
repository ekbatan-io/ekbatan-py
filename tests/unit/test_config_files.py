"""The layout read from YAML, TOML, JSON and INI files - the same `ShardingConfig` code builds.

Every rule of `ekbatan.config_files`, case by case: the four formats, the key spellings, each
format's environment placeholders, INI's types and sections, and that no error ever shows a value.
Against real databases: `tests/integration/test_config_files_connect.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ekbatan.config import (
    DataSourceConfig,
    ShardGroupConfig,
    ShardingConfig,
    ShardMemberConfig,
)
from ekbatan.config_files import load_sharding_config
from ekbatan.errors import ValidationError
from ekbatan.sharding import ShardIdentifier

SECRET = "Zq7-s3cret-pw"
ENV = {"DB_PASSWORD": SECRET, "DB_HOST": "db1"}

EXPECTED = ShardingConfig(
    default_shard=ShardIdentifier(0, 0),
    unknown_shard_policy="fail",
    groups=(
        ShardGroupConfig(
            group=0,
            name="global",
            members=(
                ShardMemberConfig(
                    member=0,
                    name="global-1",
                    configs={
                        "primaryConfig": DataSourceConfig(
                            url="mariadb+pymysql://db1:3306/wallet",
                            username="app",
                            password=SECRET,
                            pool_size=20,
                            pool_pre_ping=False,
                            data_source_properties={
                                "connect_timeout": 10,
                                "ssl": {"capath": "/etc/ssl/certs"},
                            },
                        ),
                        "jobsConfig": DataSourceConfig(
                            url="mariadb+pymysql://db1:3306/wallet",
                            username="app",
                            password=SECRET,
                            pool_size=4,
                        ),
                    },
                ),
            ),
        ),
    ),
)

YAML = """\
other-app-settings: {left: alone}
ekbatan:
  sharding:
    default-shard: {group: 0, member: 0}
    unknown-shard-policy: fail
    groups:
      - group: 0
        name: global
        members:
          - member: 0
            name: global-1
            configs:
              primary-config:
                url: mariadb+pymysql://${DB_HOST}:3306/wallet
                username: app
                password: ${DB_PASSWORD}
                pool-size: 20
                pool-pre-ping: false
                data-source-properties:
                  connect_timeout: 10
                  ssl: {capath: /etc/ssl/certs}
              jobsConfig:
                url: mariadb+pymysql://${DB_HOST}:3306/wallet
                username: app
                password: ${DB_PASSWORD}
                poolSize: 4
"""

TOML = """\
[ekbatan.sharding]
unknown-shard-policy = "fail"
default-shard = { group = 0, member = 0 }

[[ekbatan.sharding.groups]]
group = 0
name = "global"

[[ekbatan.sharding.groups.members]]
member = 0
name = "global-1"

[ekbatan.sharding.groups.members.configs.primary-config]
url = "mariadb+pymysql://${DB_HOST}:3306/wallet"
username = "app"
password = "${DB_PASSWORD}"
pool_size = 20
pool_pre_ping = false

[ekbatan.sharding.groups.members.configs.primary-config.data-source-properties]
connect_timeout = 10
ssl = { capath = "/etc/ssl/certs" }

[ekbatan.sharding.groups.members.configs.jobsConfig]
url = "mariadb+pymysql://${DB_HOST}:3306/wallet"
username = "app"
password = "${DB_PASSWORD}"
pool_size = 4
"""

JSON = json.dumps(
    {
        "ekbatan": {
            "sharding": {
                "defaultShard": {"group": 0, "member": 0},
                "unknownShardPolicy": "fail",
                "groups": [
                    {
                        "group": 0,
                        "name": "global",
                        "members": [
                            {
                                "member": 0,
                                "name": "global-1",
                                "configs": {
                                    "primaryConfig": {
                                        "url": "mariadb+pymysql://${DB_HOST}:3306/wallet",
                                        "username": "app",
                                        "password": "${DB_PASSWORD}",
                                        "poolSize": 20,
                                        "poolPrePing": False,
                                        "dataSourceProperties": {
                                            "connect_timeout": 10,
                                            "ssl": {"capath": "/etc/ssl/certs"},
                                        },
                                    },
                                    "jobsConfig": {
                                        "url": "mariadb+pymysql://${DB_HOST}:3306/wallet",
                                        "username": "app",
                                        "password": "${DB_PASSWORD}",
                                        "poolSize": 4,
                                    },
                                },
                            }
                        ],
                    }
                ],
            }
        }
    }
)

INI = """\
[other-app]
left = alone

[ekbatan.sharding]
unknown-shard-policy = fail

[ekbatan.sharding.default-shard]
group = 0
member = 0

[ekbatan.sharding.groups.0]
group = 0
name = global

[ekbatan.sharding.groups.0.members.0]
member = 0
name = global-1

[ekbatan.sharding.groups.0.members.0.configs.primaryConfig]
url = mariadb+pymysql://%(ENV_DB_HOST)s:3306/wallet
username = app
password = %(ENV_DB_PASSWORD)s
pool_size = 20
pool_pre_ping = false

[ekbatan.sharding.groups.0.members.0.configs.primaryConfig.data-source-properties]
connect_timeout = 10

[ekbatan.sharding.groups.0.members.0.configs.primaryConfig.data-source-properties.ssl]
capath = /etc/ssl/certs

[ekbatan.sharding.groups.0.members.0.configs.jobsConfig]
url = mariadb+pymysql://%(ENV_DB_HOST)s:3306/wallet
username = app
password = %(ENV_DB_PASSWORD)s
pool_size = 4
"""


def written(tmp_path: Path, name: str, text: str) -> Path:
    file = tmp_path / name
    file.write_text(text)
    return file


def loaded(tmp_path: Path, name: str, text: str, environ: dict[str, str] | None = None):
    return load_sharding_config(
        written(tmp_path, name, text), environ=ENV if environ is None else environ
    )


def refused(tmp_path: Path, name: str, text: str, environ: dict[str, str] | None = None) -> str:
    with pytest.raises(ValidationError) as caught:
        loaded(tmp_path, name, text, environ)
    message = str(caught.value)
    assert SECRET not in message, message
    return message


# -- the four formats ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "text"),
    [("ekbatan.yaml", YAML), ("ekbatan.yml", YAML), ("ekbatan.toml", TOML), ("e.json", JSON)],
)
def test_each_structured_format_builds_the_same_layout_as_code(tmp_path, name, text):
    assert loaded(tmp_path, name, text) == EXPECTED


def test_ini_builds_the_same_layout_as_code(tmp_path):
    """Every INI value is text; the datasource's keys get their declared types, and an unquoted
    number in data-source-properties becomes a number."""
    layout = loaded(tmp_path, "ekbatan.ini", INI)

    primary = layout.groups[0].members[0].primary_config()
    assert primary.pool_size == 20
    assert primary.pool_pre_ping is False
    assert dict(primary.data_source_properties) == {
        "connect_timeout": 10,
        "ssl": {"capath": "/etc/ssl/certs"},
    }
    assert layout.groups[0].members[0].config_for("jobsConfig").pool_size == 4
    assert layout.default_shard == EXPECTED.default_shard
    assert layout.unknown_shard_policy == "fail"


def test_an_unknown_extension_is_refused(tmp_path):
    assert "cannot tell the format" in refused(tmp_path, "ekbatan.conf", YAML)


def test_a_file_without_the_layout_says_so(tmp_path):
    assert "no 'ekbatan.sharding'" in refused(tmp_path, "e.yaml", "ekbatan: {other: 1}\n")


# -- keys ------------------------------------------------------------------------------------


def test_an_unknown_key_is_refused_with_its_path(tmp_path):
    message = refused(tmp_path, "e.yaml", YAML.replace("poolSize: 4", "poolsizee: 4"))

    assert "configs.jobsConfig: unknown key 'poolsizee'" in message


def test_a_key_given_twice_in_two_spellings_is_refused(tmp_path):
    message = refused(
        tmp_path, "e.yaml", YAML.replace("poolSize: 4", "poolSize: 4\n                pool-size: 5")
    )

    assert "given twice" in message


def test_driver_setting_names_are_kept_exactly_as_written(tmp_path):
    text = YAML.replace(
        "connect_timeout: 10", "connect_timeout: 10\n                  ha.enableJMX: true"
    )
    primary = loaded(tmp_path, "e.yaml", text).groups[0].members[0].primary_config()

    assert primary.data_source_properties["ha.enableJMX"] is True


# -- placeholders in YAML, TOML, JSON --------------------------------------------------------


def test_a_missing_variable_stops_the_load_naming_it(tmp_path):
    message = refused(tmp_path, "e.yaml", YAML, {"DB_HOST": "db1"})

    assert "environment variable 'DB_PASSWORD' is not set" in message
    assert "primary-config.password" in message


def test_a_variable_set_but_empty_is_used_as_given(tmp_path):
    layout = loaded(tmp_path, "e.yaml", YAML, {"DB_HOST": "db1", "DB_PASSWORD": ""})

    assert layout.groups[0].members[0].primary_config().password == ""


def test_a_default_is_used_when_the_variable_is_not_set(tmp_path):
    text = YAML.replace("pool-size: 20", "pool-size: ${POOL:7}")

    assert loaded(tmp_path, "e.yaml", text).groups[0].members[0].primary_config().pool_size == 7


def test_a_set_variable_wins_over_the_default(tmp_path):
    text = YAML.replace("pool-size: 20", "pool-size: ${POOL:7}")
    layout = loaded(tmp_path, "e.yaml", text, {**ENV, "POOL": "9"})

    assert layout.groups[0].members[0].primary_config().pool_size == 9


def test_an_empty_default_is_allowed(tmp_path):
    text = YAML.replace("${DB_PASSWORD}", "${NO_SUCH_VARIABLE:}")

    assert loaded(tmp_path, "e.yaml", text).groups[0].members[0].primary_config().password == ""


def test_a_filled_value_is_inserted_as_is_and_never_read_again(tmp_path):
    layout = loaded(tmp_path, "e.yaml", YAML, {"DB_HOST": "db1", "DB_PASSWORD": "a${B}%(ENV_C)s"})

    assert layout.groups[0].members[0].primary_config().password == "a${B}%(ENV_C)s"


def test_a_doubled_dollar_is_a_literal_placeholder_sign(tmp_path):
    text = YAML.replace("${DB_PASSWORD}", "$${NOT_A_VARIABLE}", 1)

    assert (
        loaded(tmp_path, "e.yaml", text).groups[0].members[0].primary_config().password
        == "${NOT_A_VARIABLE}"
    )


def test_a_value_filled_in_driver_settings_stays_text(tmp_path):
    text = YAML.replace("connect_timeout: 10", "application_name: ${APP}")
    layout = loaded(tmp_path, "e.yaml", text, {**ENV, "APP": "123"})

    assert (
        layout.groups[0].members[0].primary_config().data_source_properties["application_name"]
        == "123"
    )


def test_the_ini_style_in_yaml_is_refused_with_a_hint(tmp_path):
    message = refused(
        tmp_path, "e.yaml", YAML.replace("${DB_PASSWORD}", '"%(ENV_DB_PASSWORD)s"', 1)
    )

    assert "'%(ENV_...)s' is the INI style" in message


def test_a_broken_placeholder_is_refused(tmp_path):
    assert "not a placeholder" in refused(
        tmp_path, "e.yaml", YAML.replace("${DB_PASSWORD}", "${bad name}", 1)
    )


# -- INI -------------------------------------------------------------------------------------


def test_ini_reads_environment_variables_and_keys_of_the_same_section(tmp_path):
    primary = loaded(tmp_path, "e.ini", INI).groups[0].members[0].primary_config()

    assert primary.url == "mariadb+pymysql://db1:3306/wallet"
    assert primary.password == SECRET


def test_ini_a_reference_reads_another_key_of_the_same_section(tmp_path):
    text = INI.replace(
        "connect_timeout = 10", "connect_timeout = 10\nread_timeout = %(connect_timeout)s"
    )
    primary = loaded(tmp_path, "e.ini", text).groups[0].members[0].primary_config()

    assert primary.data_source_properties["read_timeout"] == 10


def test_ini_a_doubled_percent_is_a_literal_percent(tmp_path):
    text = INI.replace(
        "username = app\npassword = %(ENV_DB_PASSWORD)s\npool_size = 20",
        "username = app\npassword = 100%%-sure\npool_size = 20",
    )

    assert (
        loaded(tmp_path, "e.ini", text).groups[0].members[0].primary_config().password
        == "100%-sure"
    )


def test_ini_a_percent_in_an_environment_value_is_kept(tmp_path):
    layout = loaded(tmp_path, "e.ini", INI, {"DB_HOST": "db1", "DB_PASSWORD": "p%ss"})

    assert layout.groups[0].members[0].primary_config().password == "p%ss"


def test_ini_a_lone_percent_is_refused_without_showing_the_value(tmp_path):
    text = INI.replace(
        "password = %(ENV_DB_PASSWORD)s\npool_size = 20", f"password = {SECRET}%x\npool_size = 20"
    )

    assert "a '%' must be written '%%'" in refused(tmp_path, "e.ini", text)


def test_ini_a_missing_environment_variable_stops_the_load(tmp_path):
    assert "environment variable 'DB_PASSWORD' is not set" in refused(
        tmp_path, "e.ini", INI, {"DB_HOST": "db1"}
    )


def test_ini_a_reference_to_no_key_suggests_the_environment_form(tmp_path):
    text = INI.replace("%(ENV_DB_PASSWORD)s", "%(DB_PASSWORD)s", 1)

    assert "for an environment variable write '%(ENV_DB_PASSWORD)s'" in refused(
        tmp_path, "e.ini", text
    )


def test_ini_the_yaml_style_is_refused_with_a_hint(tmp_path):
    text = INI.replace("%(ENV_DB_PASSWORD)s", "${DB_PASSWORD}", 1)

    assert "in an INI file an environment variable is written '%(ENV_NAME)s'" in refused(
        tmp_path, "e.ini", text
    )


def test_ini_keys_keep_their_case(tmp_path):
    text = INI.replace("connect_timeout = 10", "connect_timeout = 10\nsslMode = require")
    primary = loaded(tmp_path, "e.ini", text).groups[0].members[0].primary_config()

    assert primary.data_source_properties["sslMode"] == "require"


def test_ini_a_default_section_is_not_copied_into_every_section(tmp_path):
    text = "[DEFAULT]\nsurprise = 1\n\n" + INI

    assert (
        loaded(tmp_path, "e.ini", text).groups[0].members[0].config_for("jobsConfig").pool_size == 4
    )


@pytest.mark.parametrize(
    ("written_as", "becomes"),
    [
        ("10", 10),
        ('"10"', "10"),
        ("'10'", "10"),
        ("1.5", 1.5),
        ("true", True),
        ("false", False),
        ("True", "True"),
        ("yes", "yes"),
        ("require", "require"),
    ],
)
def test_ini_driver_settings_follow_the_quote_rule(tmp_path, written_as, becomes):
    text = INI.replace("connect_timeout = 10", f"probe = {written_as}")
    primary = loaded(tmp_path, "e.ini", text).groups[0].members[0].primary_config()

    assert primary.data_source_properties["probe"] == becomes
    assert type(primary.data_source_properties["probe"]) is type(becomes)


def test_ini_an_environment_value_in_driver_settings_stays_text(tmp_path):
    text = INI.replace("connect_timeout = 10", "application_name = %(ENV_APP)s")
    layout = loaded(tmp_path, "e.ini", text, {**ENV, "APP": "123"})

    assert (
        layout.groups[0].members[0].primary_config().data_source_properties["application_name"]
        == "123"
    )


def test_ini_a_declared_type_is_converted_strictly(tmp_path):
    assert "must be true or false" in refused(
        tmp_path, "e.ini", INI.replace("pool_pre_ping = false", "pool_pre_ping = yes")
    )
    assert "must be a whole number" in refused(
        tmp_path, "e.ini", INI.replace("pool_size = 20", "pool_size = twenty")
    )


def test_ini_list_items_must_be_numbered_without_gaps(tmp_path):
    text = INI.replace("groups.0", "groups.1")

    assert "numbered 0, 1, 2" in refused(tmp_path, "e.ini", text)


# -- types in structured formats -------------------------------------------------------------


def test_a_value_of_the_wrong_type_is_refused_not_guessed(tmp_path):
    assert "must be a whole number" in refused(
        tmp_path, "e.yaml", YAML.replace("pool-size: 20", 'pool-size: "20"')
    )
    assert "must be a whole number" in refused(
        tmp_path, "e.yaml", YAML.replace("pool-size: 20", "pool-size: true")
    )
    assert "must be text - quote it" in refused(
        tmp_path, "e.yaml", YAML.replace("username: app", "username: 1234", 1)
    )


# -- errors never show a value ---------------------------------------------------------------


def test_the_config_classes_refusals_carry_the_path(tmp_path):
    text = YAML.replace(
        "url: mariadb+pymysql://${DB_HOST}", f"url: mariadb+pymysql://app:{SECRET}@db1", 1
    )
    message = refused(tmp_path, "e.yaml", text)

    assert "configs.primary-config" in message
    assert "a user name and a password before an '@'" in message


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("e.yaml", f"a: b\npassword: {SECRET}: c\n"),
        ("e.yaml", f'key: "{SECRET}\n'),
        ("e.toml", f'ekbatan = "{SECRET}\n'),
        ("e.json", f'{{"ekbatan": "{SECRET}"'),
        ("e.ini", f"no section header {SECRET}\n"),
        ("e.ini", f"[a]\nb = {SECRET}\nb = {SECRET}\n"),
    ],
    ids=[
        "yaml mapping",
        "yaml unclosed quote",
        "toml",
        "json",
        "ini without a section",
        "ini duplicate key",
    ],
)
def test_a_broken_file_is_refused_without_quoting_it(tmp_path, name, text):
    message = refused(tmp_path, name, text)

    assert "not valid" in message or "not a valid" in message


@pytest.mark.parametrize(
    ("written_as", "read"),
    [
        ("useDefaultShard", "use_default_shard"),
        ("UseDefaultShard", "use_default_shard"),
        ("' fail '", "fail"),
        ("''", "use_default_shard"),
        ("", "use_default_shard"),
    ],
)
def test_the_policy_in_a_file_is_read_as_java_reads_it(tmp_path, written_as, read):
    """A Java `application.yml` written as Java documents it - `useDefaultShard` - loads, and a
    padded or empty value reads as Java's does."""
    text = YAML.replace("unknown-shard-policy: fail", f"unknown-shard-policy: {written_as}")
    assert loaded(tmp_path, "ekbatan.yaml", text).unknown_shard_policy == read


# -- the environment is read by name, never held whole (EKB-0436-) ----------------------------

OTHER_SECRET = "Mk-7f3a-OTHER-SECRET"


def _holds(value: object, marker: str, depth: int = 0) -> bool:
    """Whether ``value`` - or what it holds, a few levels down - is the environment or has
    ``marker`` in it."""
    import os

    if value is os.environ or depth > 4:
        return value is os.environ
    if isinstance(value, str):
        return marker in value
    # Plain dicts only: a ConfigParser is a mapping too, and reading it would interpolate.
    if isinstance(value, dict):
        return any(_holds(v, marker, depth + 1) for v in list(value.values()))
    if isinstance(value, (list, tuple, set)):
        return any(_holds(v, marker, depth + 1) for v in value)
    if callable(value) and getattr(value, "__self__", None) is os.environ:
        return True
    held = getattr(value, "__dict__", None)
    return isinstance(held, dict) and _holds(held, marker, depth + 1)


@pytest.mark.parametrize(
    ("name", "text"),
    [
        (
            "ekbatan.yaml",
            "ekbatan:\n  sharding:\n    default-shard: {group: 0, member: 0}\n"
            "    groups:\n      - group: 0\n        name: g\n        members:\n"
            "          - member: 0\n            name: m\n            primary-config:\n"
            "              url: postgresql://h/db\n              password: ${DB_PASSWORD}\n"
            "              username: ${NOT_SET_ANYWHERE}\n",
        ),
        (
            "ekbatan.ini",
            "[ekbatan.sharding.groups.0]\ngroup = 0\nname = g\n"
            "[ekbatan.sharding.groups.0.members.0]\nmember = 0\nname = m\n"
            "[ekbatan.sharding.groups.0.members.0.primary-config]\nurl = postgresql://h/db\n"
            "password = %(ENV_DB_PASSWORD)s\nusername = %(ENV_NOT_SET_ANYWHERE)s\n",
        ),
    ],
    ids=["yaml", "ini"],
)
def test_loading_never_holds_the_environment_and_its_error_shows_no_secret(
    tmp_path, monkeypatch, name, text
):
    """No ``environ=``: each placeholder is looked up by name. A file whose placeholder is not
    set fails while it is being filled in, and leaves a traceback whose frames hold neither
    ``os.environ`` nor a variable the file never named."""
    monkeypatch.setenv("DB_PASSWORD", SECRET)
    monkeypatch.setenv("SOME_OTHER_PASSWORD", OTHER_SECRET)
    path = tmp_path / name
    path.write_text(text)

    with pytest.raises(ValidationError) as raised:
        load_sharding_config(path)

    assert SECRET not in str(raised.value) and OTHER_SECRET not in str(raised.value)
    frames = []
    tb = raised.value.__traceback__
    while tb is not None:
        frames.append(tb.tb_frame)
        tb = tb.tb_next
    assert any(f.f_code.co_filename.endswith("ekbatan/config_files.py") for f in frames)
    holding = [
        f"{frame.f_code.co_name}.{local}"
        for frame in frames
        if frame.f_code.co_filename.endswith("ekbatan/config_files.py")
        for local, value in frame.f_locals.items()
        if _holds(value, OTHER_SECRET)
    ]
    assert holding == [], f"these hold the environment: {holding}"
