"""The login fields, `data_source_properties`, and the url check - Java's
`DataSourceConfigCredentialsTest` and `JdbcUrlCredentialsTest`, case by case where they apply.

The ledger `tests/driver_settings.tsv` lists every setting of every driver this port connects
through and what it holds; the tests at the bottom keep it, the installed drivers and the check's
own list in step.
"""

from __future__ import annotations

import csv
import inspect
import random
import sqlite3
import string
from pathlib import Path

import pytest

from ekbatan._credentials import SETTINGS, Kind, kind_of
from ekbatan.config import DataSourceConfig, split_login
from ekbatan.errors import ValidationError

URL = "postgresql+psycopg://db:5432/wallet"
SECRET = "Zq7s3cret"
LEDGER = Path(__file__).resolve().parents[1] / "driver_settings.tsv"


# -- username and password -------------------------------------------------------------------


def test_username_and_password_may_both_be_left_out():
    config = DataSourceConfig(url=URL)

    assert config.username is None
    assert config.password is None


@pytest.mark.parametrize("blank", ["", " ", "\t"])
def test_a_blank_username_is_refused_not_taken_as_none(blank):
    with pytest.raises(ValidationError, match="username cannot be empty"):
        DataSourceConfig(url=URL, username=blank)


def test_the_empty_string_stays_a_valid_password():
    assert DataSourceConfig(url=URL, username="app", password="").password == ""


def test_the_builder_sets_both():
    config = DataSourceConfig.builder().url(URL).username("app").password(SECRET).build()

    assert (config.username, config.password) == ("app", SECRET)


def test_the_login_reaches_the_driver_through_the_url_object():
    url = DataSourceConfig(url=URL, username="app", password=SECRET).sqlalchemy_url()

    assert (url.username, url.password, url.host, url.database) == ("app", SECRET, "db", "wallet")


def test_sqlite_has_no_login_to_set():
    with pytest.raises(ValidationError, match="sqlite has no login"):
        DataSourceConfig(url="sqlite://", password=SECRET)


# -- data_source_properties ------------------------------------------------------------------


def test_driver_settings_are_kept_exactly_as_written():
    """Names keep their case; values keep their type - a number stays a number, a group a group,
    an object an object. Java turns values into text; PyMySQL fails on `connect_timeout="10"`."""
    tls = object()
    given = {"connect_timeout": 10, "sslMode": "require", "ssl": {"capath": "/etc/ssl"}, "ctx": tls}

    kept = DataSourceConfig(url=URL, data_source_properties=given).data_source_properties

    assert dict(kept) == given
    assert kept["connect_timeout"] == 10
    assert kept["ctx"] is tls


def test_a_group_and_a_list_are_accepted():
    """Option B: some driver settings take one - PyMySQL's `ssl` and `auth_plugin_map`."""
    config = DataSourceConfig(
        url="mysql+pymysql://db/w",
        data_source_properties={"ssl": {"capath": "/x"}, "auth_plugin_map": {"dialog": object}},
    )

    assert config.data_source_properties["ssl"] == {"capath": "/x"}


def test_driver_settings_cannot_be_changed_after_build():
    given = {"sslmode": "require"}
    config = DataSourceConfig(url=URL, data_source_properties=given)
    given["sslmode"] = "disable"

    assert config.data_source_properties["sslmode"] == "require", "a copy, not the caller's map"
    with pytest.raises(TypeError):
        config.data_source_properties["sslmode"] = "disable"  # type: ignore[index]


@pytest.mark.parametrize("name", ["user", "password", "passwd", "USER", "Password", "PASSWD"])
def test_the_login_cannot_hide_among_the_driver_settings(name):
    """Either would silently win over username/password - refused even with the field unset."""
    with pytest.raises(ValidationError) as refused:
        DataSourceConfig(url=URL, username="app", data_source_properties={name: SECRET})

    assert f"{name!r} does not belong in data_source_properties" in str(refused.value)
    assert SECRET not in str(refused.value)


@pytest.mark.parametrize(
    ("name", "field"), [("user", "username"), ("password", "password"), ("passwd", "password")]
)
def test_the_refusal_says_which_field_to_use(name, field):
    with pytest.raises(ValidationError, match=f"set {field} instead"):
        DataSourceConfig(url=URL, data_source_properties={name: "x"})


def test_a_driver_setting_must_have_a_value():
    with pytest.raises(ValidationError, match="'sslmode' has no value"):
        DataSourceConfig(url=URL, data_source_properties={"sslmode": None})


@pytest.mark.parametrize("name", ["", "  "])
def test_a_driver_setting_must_have_a_name(name):
    with pytest.raises(ValidationError, match="name cannot be blank"):
        DataSourceConfig(url=URL, data_source_properties={name: "x"})


def test_a_login_file_setting_is_accepted():
    """Decided 2026-10-11: only keys that set the login are refused, not those that fetch one."""
    config = DataSourceConfig(url=URL, data_source_properties={"passfile": "/run/pgpass"})

    assert config.data_source_properties["passfile"] == "/run/pgpass"


# -- the url check -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "says"),
    [
        (f"postgresql+psycopg://app:{SECRET}@db/w", "a user name and a password before an '@'"),
        ("postgresql+psycopg://app@db/w", "a user name before an '@'"),
        (f"postgresql+psycopg://:{SECRET}@db/w", "a password before an '@'"),
        (f"mysql+pymysql://root:{SECRET}@h1:3306,h2:3306/db", "a user name and a password"),
    ],
)
def test_a_login_before_an_at_sign_is_refused(url, says):
    with pytest.raises(ValidationError) as refused:
        DataSourceConfig(url=url)

    assert says in str(refused.value)
    assert SECRET not in str(refused.value)


@pytest.mark.parametrize("name", sorted(SETTINGS))
def test_every_setting_that_holds_a_login_or_a_secret_is_refused_in_the_query(name):
    for written in (name, name.upper()):
        with pytest.raises(ValidationError) as refused:
            DataSourceConfig(url=f"{URL}?{written}={SECRET}")
        assert f"'{written}' setting" in str(refused.value)
        assert SECRET not in str(refused.value)


def test_each_refusal_says_where_the_value_goes():
    with pytest.raises(ValidationError) as refused:
        DataSourceConfig(url=f"{URL}?user=a&password=b&sslpassword=c")

    message = str(refused.value)
    assert "the login user (its 'user' setting) - give it to username(...)" in message
    assert "the login password (its 'password' setting) - give it to password(...)" in message
    assert "a secret (its 'sslpassword' setting) - put it under data_source_properties" in message


def test_a_login_before_an_at_and_other_secrets_are_named_together():
    with pytest.raises(ValidationError) as refused:
        DataSourceConfig(url=f"postgresql+psycopg://app:{SECRET}@db/w?sslpassword={SECRET}")

    assert "before an '@'" in str(refused.value)
    assert "'sslpassword' setting" in str(refused.value)


def test_every_refused_setting_is_named_once():
    with pytest.raises(ValidationError) as refused:
        DataSourceConfig(url=f"{URL}?sslpassword=a&SSLPASSWORD=b")

    assert str(refused.value).lower().count("sslpassword") == 1


def test_a_setting_with_no_value_carries_nothing_and_is_accepted():
    assert DataSourceConfig(url=f"{URL}?password=").url.endswith("?password=")


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://password-host:5432/password",
        "postgresql+psycopg://db/w?application_name=password",
        "postgresql+psycopg://db/w?options=-c%20user%3Dx",
    ],
    ids=["in the host and the database", "inside a value", "inside an encoded value"],
)
def test_a_watched_word_outside_a_setting_name_is_not_a_setting(url):
    DataSourceConfig(url=url)


def test_a_url_without_a_login_or_a_secret_is_accepted():
    DataSourceConfig(url=f"{URL}?sslmode=require&application_name=svc&connect_timeout=5")


def test_a_name_with_spaces_inside_is_refused():
    assert kind_of("ssl password") is Kind.SECRET
    assert kind_of(" User ") is Kind.LOGIN_USER


def test_random_text_never_breaks_the_check_and_never_shows_a_value():
    """Whatever comes after the scheme, the check either accepts or refuses with a
    ValidationError - and the refusal never quotes the text."""
    rng = random.Random(20261011)
    alphabet = string.ascii_letters + string.digits + ":@/?&=%#;,.+- "
    for _ in range(2_000):
        tail = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        marker = "QX9marker"
        try:
            DataSourceConfig(url=f"postgresql+psycopg://{tail}{marker}")
        except ValidationError as refused:
            assert marker not in str(refused)


def test_split_login_takes_the_login_out_of_a_url():
    assert split_login("postgresql+psycopg://us%40er:p%3Aw@db:5432/w?sslmode=require") == (
        "postgresql+psycopg://db:5432/w?sslmode=require",
        "us@er",
        "p:w",
    )


# -- the ledger --------------------------------------------------------------------------------


def ledger() -> list[dict[str, str]]:
    with LEDGER.open(newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def test_the_check_refuses_exactly_what_the_ledger_marks():
    refused = {row["setting"].lower(): row["kind"] for row in ledger() if row["kind"] != "other"}

    assert refused == {name: kind.value for name, kind in SETTINGS.items()}


def test_no_name_holds_a_secret_for_one_driver_and_nothing_for_another():
    kinds: dict[str, set[str]] = {}
    for row in ledger():
        kinds.setdefault(row["setting"].lower(), set()).add(row["kind"])

    assert {name: k for name, k in kinds.items() if len(k) > 1} == {}


def test_the_ledger_covers_every_libpq_setting_and_libpqs_own_secrets_are_refused():
    """libpq marks its password-like settings with `*`: a newer libpq adding one fails here."""
    from psycopg import pq

    rows = {row["setting"] for row in ledger() if row["driver"] == "psycopg (libpq)"}
    options = pq.Conninfo.get_defaults()

    assert {o.keyword.decode() for o in options} <= rows
    for option in options:
        if option.dispchar == b"*":
            assert kind_of(option.keyword.decode()) is not None, option.keyword


def test_the_ledger_covers_every_psycopg_and_pymysql_setting():
    import psycopg
    import pymysql

    def named(function: object) -> set[str]:
        return {
            p.name
            for p in inspect.signature(function).parameters.values()  # type: ignore[arg-type]
            if p.name not in ("self", "cls") and p.kind is not p.VAR_KEYWORD
        }

    by_driver: dict[str, set[str]] = {}
    for row in ledger():
        by_driver.setdefault(row["driver"], set()).add(row["setting"])

    assert named(psycopg.Connection.connect) <= by_driver["psycopg"]
    assert named(pymysql.connections.Connection.__init__) <= by_driver["pymysql"]


def test_the_ledger_covers_every_async_driver_setting():
    """asyncpg, aiomysql and aiosqlite: `ekbatan.aio`'s default drivers. aiosqlite hands every
    setting but its own to sqlite3.connect(), which the test below covers."""
    import aiomysql
    import aiosqlite
    import asyncpg

    def named(function: object) -> set[str]:
        return {
            p.name
            for p in inspect.signature(function).parameters.values()  # type: ignore[arg-type]
            if p.kind is not p.VAR_KEYWORD
        }

    by_driver: dict[str, set[str]] = {}
    for row in ledger():
        by_driver.setdefault(row["driver"], set()).add(row["setting"])

    assert named(asyncpg.connect) <= by_driver["asyncpg"]
    assert named(aiomysql.connect) <= by_driver["aiomysql"]
    assert named(aiosqlite.connect) <= by_driver["aiosqlite"]


def test_the_ledger_covers_every_sqlite3_setting():
    """sqlite3 refuses an unknown keyword, so each ledger name is proved by being accepted."""
    names = {row["setting"] for row in ledger() if row["driver"] == "sqlite3"}
    values = {"factory": sqlite3.Connection, "autocommit": False, "isolation_level": "DEFERRED"}

    for name in names - {"database"}:
        sqlite3.connect(":memory:", **{name: values.get(name, 1)}).close()  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        sqlite3.connect(":memory:", not_a_setting=1)  # type: ignore[call-arg]
