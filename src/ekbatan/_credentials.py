"""Refuses a url that carries a user name or a secret - Java's ``JdbcUrlCredentials``.

A url is logged and repeated - drivers repeat it in their errors, SQLAlchemy's echo prints it,
people paste it into tickets - so it carries neither: the login user goes to ``username``, the
login password to ``password``, and every other secret to ``data_source_properties``.

The url is read with SQLAlchemy's own parser, which is what turns it into the driver's
arguments: a user and a password before the ``@``, and the settings of the query string. Java
reads the url's raw text instead, because JDBC urls come in a dozen forms; a SQLAlchemy url comes
in one, and reading it the way SQLAlchemy reads it means a setting is refused exactly when it
would reach the driver.

Which setting names hold a user name or a secret is decided once, for every setting of every
driver this port connects through - psycopg (and the libpq under it), PyMySQL and sqlite3, and
``ekbatan.aio``'s asyncpg, aiomysql and aiosqlite - in the ledger ``tests/driver_settings.tsv``.
The names here are its refused part, and a test fails when the two disagree. Names are matched
ignoring case and spaces, as Java matches them; a setting with no value carries nothing.
"""

from __future__ import annotations

import enum
import re
from typing import Any
from urllib.parse import unquote

from .errors import ValidationError

__all__ = ["Kind", "kind_of", "require_none_in"]


class Kind(enum.Enum):
    """What a refused setting holds, which decides where its value goes instead."""

    #: The user this configuration logs in as.
    LOGIN_USER = "login-user"
    #: This configuration's login password.
    LOGIN_PASSWORD = "login-password"
    #: Any other user name. None of this port's drivers has one yet; kept so the kinds match Java's.
    USER_NAME = "user-name"
    #: Any other secret: a key's password, a client secret, a key that logs in like a password.
    SECRET = "secret"


#: The refused setting names of the ledger, lower-cased.
SETTINGS: dict[str, Kind] = {
    # every driver that logs in
    "user": Kind.LOGIN_USER,
    "password": Kind.LOGIN_PASSWORD,
    # PyMySQL's old name for the password, read when `password` is empty
    "passwd": Kind.LOGIN_PASSWORD,
    # libpq, under psycopg: the client key's password, OAuth, and two SCRAM keys that log in
    # like a password although libpq does not mark them as secret
    "sslpassword": Kind.SECRET,
    "oauth_client_secret": Kind.SECRET,
    "scram_client_key": Kind.SECRET,
    "scram_server_key": Kind.SECRET,
    # PyMySQL: the client key's password
    "ssl_key_password": Kind.SECRET,
}

_SPACES = re.compile(r"\s+")

_PROPERTIES = "data_source_properties"

_WHY = (
    ": a url is logged and repeated where user names and secrets must never be. "
    "The url is not repeated here."
)


def kind_of(name: str) -> Kind | None:
    """What the setting of this name holds, if it is refused. Spaces inside the name and its
    case are ignored."""
    return SETTINGS.get(_SPACES.sub("", name).lower())


def require_none_in(url: str) -> None:
    """Raise when the url carries a user name or a secret.

    The message names every one - each setting once - and where each goes instead, and never
    repeats the url or a value.
    """
    from sqlalchemy.engine import make_url

    try:
        parsed = make_url(url)
        user, password = bool(parsed.username), bool(parsed.password)
        query: list[tuple[str, Any]] = list(parsed.query.items())
    except Exception:
        # SQLAlchemy cannot read it - two hosts written `h1:3306,h2:3306`, say - so read its
        # text the way Java reads every url: the part before an `@`, and the query's names.
        user, password, query = _read_text(url)

    refusals: list[tuple[str, str]] = []
    if user and password:
        refusals.append(
            (
                "a user name and a password before an '@'",
                "give them to username(...) and password(...)",
            )
        )
    elif user:
        refusals.append(("a user name before an '@'", "give it to username(...)"))
    elif password:
        refusals.append(("a password before an '@'", "give it to password(...)"))

    named: set[str] = set()
    for name, value in query:
        if not _has_value(value):
            continue
        kind = kind_of(name)
        if kind is None or name.lower() in named:
            continue
        named.add(name.lower())
        refusals.append(_refusal(kind, name))

    if len(refusals) == 1:
        what, move = refusals[0]
        raise ValidationError(f"the url carries {what}. {move[0].upper()}{move[1:]} instead{_WHY}")
    if refusals:
        listed = "; ".join(f"{what} - {move}" for what, move in refusals)
        raise ValidationError(f"the url carries {listed}{_WHY}")


def _read_text(url: str) -> tuple[bool, bool, list[tuple[str, Any]]]:
    """A url SQLAlchemy cannot parse, read from its text: a user and a password before an ``@`` in
    the host part, and the names of the query's settings. Values are never kept."""
    _, _, rest = url.partition("://")
    host_end = min(
        (i for i in (rest.find("/"), rest.find("?"), rest.find("#")) if i >= 0), default=len(rest)
    )
    host_part = rest[:host_end]
    user = password = False
    if "@" in host_part:
        info = host_part.rpartition("@")[0]
        name, colon, secret = info.partition(":")
        user, password = bool(name.strip()), bool(colon and secret.strip())
    query: list[tuple[str, Any]] = []
    _, question, after = rest.partition("?")
    if question:
        for piece in after.split("#", 1)[0].split("&"):
            name, equals, value = piece.partition("=")
            if name.strip():
                query.append((unquote(name).strip(), value if equals else ""))
    return user, password, query


def _has_value(value: Any) -> bool:
    """A setting written with no value carries nothing; a repeated one is a tuple of values."""
    values = value if isinstance(value, tuple) else (value,)
    return any(str(each).strip() for each in values)


def _refusal(kind: Kind, setting: str) -> tuple[str, str]:
    if kind is Kind.LOGIN_USER:
        return f"the login user (its '{setting}' setting)", "give it to username(...)"
    if kind is Kind.LOGIN_PASSWORD:
        return f"the login password (its '{setting}' setting)", "give it to password(...)"
    if kind is Kind.USER_NAME:
        return f"a user name (its '{setting}' setting)", f"put it under {_PROPERTIES}"
    return f"a secret (its '{setting}' setting)", f"put it under {_PROPERTIES}"
