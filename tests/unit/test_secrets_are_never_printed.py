"""A database password is never printed - not by an object, not in an error.

The login lives in `username` and `password`, and a url carrying one is refused - but the
password still sits in a config, in every layout and result that holds one, and in the arguments
handed to a driver. Every way those can turn into text is a way for it to reach a log line, a
traceback, a test report or an exception tracker. Each is checked here. What the framework writes
while it runs, with a real password against a real database, is
`tests/integration/test_secrets_stay_secret.py`.
"""

from __future__ import annotations

import dataclasses
import importlib
import pkgutil
import pprint
from pathlib import Path

import pytest

import ekbatan
from ekbatan._migration_backend import connection_arguments
from ekbatan._redact import redact, redact_url
from ekbatan.config import DataSourceConfig, ShardingConfig
from ekbatan.errors import ValidationError
from ekbatan.migrations import (
    BaselineResult,
    InfoResult,
    MigrateResult,
    RepairResult,
    Result,
    target,
)

SECRET = "Zq7-s3cret-pw"

#: Urls as they are written now: no login in them.
PLAIN_URLS = [
    "postgresql+psycopg://db1:5432/orders",
    "postgresql://db1/orders?sslmode=require",
    "mysql+pymysql://db1:3306/orders",
    "mariadb+pymysql://db1/orders",
]

#: Every place a url can carry a password - each one refused, and never repeated.
URLS_WITH_A_SECRET = [
    f"postgresql+psycopg://app:{SECRET}@db1:5432/orders",
    f"postgresql://app:{SECRET}@db1/orders?sslmode=require",
    f"mysql+pymysql://root:{SECRET}@db1:3306/orders",
    f"mariadb+pymysql://root:{SECRET}@db1/orders",
    f"postgresql+psycopg://db1/orders?password={SECRET}",
    f"postgresql+psycopg://db1/orders?application_name=svc&sslpassword={SECRET}",
    f"mysql+pymysql://db1/orders?passwd={SECRET}",
]


def every_text(value: object) -> list[str]:
    """Every way Python turns a value into text."""
    return [
        repr(value),
        str(value),
        f"{value}",
        f"{value!r}",
        "%s" % (value,),  # noqa: UP031 - the old form is still how `logging` formats
        "%r" % (value,),  # noqa: UP031
        pprint.pformat(value),
    ]


def configured(url: str, **settings: object) -> DataSourceConfig:
    return DataSourceConfig(url=url, username="app", password=SECRET, **settings)  # type: ignore[arg-type]


@pytest.mark.parametrize("url", PLAIN_URLS)
def test_a_data_source_config_never_prints_its_password(url):
    config = configured(url)

    for text in every_text(config):
        assert SECRET not in text, text
    assert "password='***'" in repr(config)
    assert "db1" in repr(config), "still says which database - only the password is hidden"
    assert "username='app'" in repr(config), "a user name is not a secret"


def test_a_password_with_special_characters_is_hidden():
    config = DataSourceConfig(url=PLAIN_URLS[0], username="app", password="p@ss:w0rd/x%40")

    assert "p@ss" not in repr(config)
    assert "w0rd" not in repr(config)


def test_the_password_is_hidden_when_printed_not_when_used():
    """The engine and the migrator connect with the url the config builds: the real password is
    on it, and it prints as `***`."""
    url = configured(PLAIN_URLS[0]).sqlalchemy_url()

    assert url.password == SECRET
    for text in every_text(url):
        assert SECRET not in text, text


def test_every_data_source_properties_value_is_hidden_and_every_name_shown():
    config = configured(
        PLAIN_URLS[0],
        data_source_properties={
            "sslpassword": SECRET,
            "ssl": {"key": "/etc/client.key", "password": SECRET},
            "options": f"-c search_path=app password={SECRET}",
            "dsn": f"postgresql://app:{SECRET}@db1/orders",
            "connect_timeout": 5,
        },
    )

    for text in every_text(config):
        assert SECRET not in text, text
    assert "'connect_timeout': '***'" in repr(config), "every value is a secret, as in Java"
    assert "'sslpassword'" in repr(config), "the names are shown"


@pytest.mark.parametrize("url", URLS_WITH_A_SECRET)
def test_a_url_with_a_secret_is_refused_and_the_secret_never_repeated(url):
    with pytest.raises(ValidationError) as refused:
        DataSourceConfig(url=url)

    assert SECRET not in str(refused.value)
    assert SECRET not in repr(refused.value)


@pytest.mark.parametrize("url", PLAIN_URLS[:3])
def test_nothing_that_holds_a_config_prints_its_password(url):
    layout = ShardingConfig.single(url, username="app", password=SECRET)
    member = layout.groups[0].members[0]
    each = target(member.primary_config())
    holders = [
        layout,
        layout.groups[0],
        member,
        member.configs,
        each,
        Result(each, MigrateResult(None, "1", ("V1__a.sql",))),
        InfoResult(each, "1", ()),
        RepairResult(each),
        BaselineResult(each, "1", "<< Baseline >>"),
    ]

    for holder in holders:
        for text in every_text(holder):
            assert SECRET not in text, f"{type(holder).__name__}: {text}"


@pytest.mark.parametrize(
    "url",
    [
        f"app:{SECRET}@db1/orders",
        f"jdbc:postgresql://db1/orders?user=app&password={SECRET}",
        f"jdbc:mysql://app:{SECRET}@db1/orders",
        f"oracle://app:{SECRET}@db1/orders",
        f"app:{SECRET}@oracle://db1/orders",
    ],
    ids=[
        "no scheme",
        "jdbc, password in the query",
        "jdbc, password in the url",
        "oracle",
        "garbled",
    ],
)
def test_a_refused_url_is_not_repeated_in_the_error(url):
    with pytest.raises(ValidationError) as refused:
        DataSourceConfig(url=url)

    assert SECRET not in str(refused.value)
    assert SECRET not in repr(refused.value)


def test_the_refusals_still_say_what_is_wrong():
    for url, says in [
        (f"app:{SECRET}@db1/orders", "it has no '://'"),
        (f"jdbc:postgresql://db1/orders?password={SECRET}", "(jdbc:postgresql://...)"),
        (f"oracle://app:{SECRET}@db1/orders", "unsupported database 'oracle'"),
    ]:
        with pytest.raises(ValidationError) as refused:
            DataSourceConfig(url=url)
        assert says in str(refused.value)


def test_the_arguments_a_driver_is_given_never_print_the_password():
    found = connection_arguments(
        "orders", configured(PLAIN_URLS[0]).sqlalchemy_url(), {"sslpassword": SECRET}
    )

    for text in every_text(found):
        assert SECRET not in text, text
    assert found.kwargs["password"] == SECRET, "the driver still gets it"
    assert found.kwargs["sslpassword"] == SECRET


def test_a_url_that_cannot_be_read_is_not_shown_at_all():
    assert SECRET not in redact_url(f"not a url at all {SECRET}")
    assert SECRET not in redact_url(f"postgresql+psycopg://app:{SECRET}@db1:notaport/orders")


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "PASSWORD",
        "sslpassword",
        "passwd",
        "pwd",
        "api_token",
        "client_secret",
        "credentials",
        "auth",
        "sslkey",
    ],
)
def test_keys_that_name_a_secret_are_hidden(key):
    assert redact({key: SECRET}) == {key: "***"}


def test_text_that_carries_a_password_is_cleaned():
    assert SECRET not in redact(f"host=db1 user=app password={SECRET} sslmode=require")
    assert SECRET not in redact(f"password = '{SECRET}'")
    assert SECRET not in redact(f"mysql://root:{SECRET}@db1/x")
    assert redact("host=db1 sslmode=require") == "host=db1 sslmode=require"


# -- the guard -------------------------------------------------------------------------------

SENSITIVE_FIELDS = {
    "url",
    "password",
    "dsn",
    "data_source_properties",
    "secret",
    "token",
    "credentials",
}


def _modules() -> list[str]:
    root = Path(ekbatan.__file__).parent
    return [m.name for m in pkgutil.walk_packages([str(root)], "ekbatan.")]


def test_no_dataclass_prints_a_field_that_can_hold_a_password():
    """A dataclass prints every field by default. One with a url, a password or driver options
    among its fields has to say how it prints them - its own `__repr__` (`repr=False` on the
    class) or `field(repr=False)` - so a class added later cannot leak by default."""
    offenders = []
    for name in _modules():
        try:
            module = importlib.import_module(name)
        except ImportError:  # an optional extra this environment does not have
            continue
        for value in vars(module).values():
            if not (isinstance(value, type) and dataclasses.is_dataclass(value)):
                continue
            if value.__module__ != name:
                continue
            printed = value.__dataclass_params__.repr  # type: ignore[attr-defined]
            for each in dataclasses.fields(value):
                if each.name in SENSITIVE_FIELDS and printed and each.repr:
                    offenders.append(f"{name}.{value.__qualname__}.{each.name}")

    assert offenders == []
