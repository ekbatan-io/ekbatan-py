"""Where the dialects genuinely differ, and nowhere else.

A port of `JdbcCustomization` and its per-database subclasses. There is exactly one thing in
this scheduler that dialects disagree about: whether a row can be claimed in the same statement
that selects it, and what that statement looks like.

    single-statement   Postgres: UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)
                       RETURNING *  - one round trip, one lock, no second UPDATE
    generic            MySQL 8: the same idea across two statements inside one transaction
    neither            MariaDB, SQLite: fall back to fetch-then-pick, which needs no locking
                       primitives at all

db-scheduler refuses lock-and-fetch on a dialect that cannot do it rather than emulating it, and
so does this - an emulation would be a claim that does not hold, which is the one failure this
library exists to prevent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

__all__ = [
    "DefaultJdbcCustomization",
    "JdbcCustomization",
    "MariaDBJdbcCustomization",
    "MySQLJdbcCustomization",
    "PostgreSqlJdbcCustomization",
    "SqliteJdbcCustomization",
    "autodetect",
]


class JdbcCustomization(ABC):
    """What one database can do that another cannot."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def supports_single_statement_lock_and_fetch(self) -> bool:
        """Claim and return the rows in one statement."""
        return False

    @property
    def supports_generic_lock_and_fetch(self) -> bool:
        """Claim with `SELECT ... FOR UPDATE SKIP LOCKED` inside a transaction, then update."""
        return False

    @property
    def supports_lock_and_fetch(self) -> bool:
        return self.supports_single_statement_lock_and_fetch or self.supports_generic_lock_and_fetch

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


class DefaultJdbcCustomization(JdbcCustomization):
    """Fetch-then-pick only. What an unrecognised database gets.

    Not a degraded mode: fetch-then-pick is correct everywhere, because the claim is the
    version guard on the UPDATE rather than a row lock. Lock-and-fetch saves a round trip; it
    does not add a guarantee.
    """

    @property
    def name(self) -> str:
        return "default"


class PostgreSqlJdbcCustomization(JdbcCustomization):
    __slots__ = ("_use_generic",)

    def __init__(self, use_generic_lock_and_fetch: bool = False) -> None:
        self._use_generic = use_generic_lock_and_fetch

    @property
    def name(self) -> str:
        return "postgresql"

    @property
    def supports_single_statement_lock_and_fetch(self) -> bool:
        return not self._use_generic

    @property
    def supports_generic_lock_and_fetch(self) -> bool:
        return self._use_generic


class MySQLJdbcCustomization(JdbcCustomization):
    """MySQL 8. `SKIP LOCKED` arrived in 8.0.1, so this is the 8-and-later customization."""

    @property
    def name(self) -> str:
        return "mysql"

    @property
    def supports_generic_lock_and_fetch(self) -> bool:
        return True


class MariaDBJdbcCustomization(JdbcCustomization):
    """MariaDB has `SKIP LOCKED` from 10.6, but db-scheduler does not use it here and neither
    does this - matching the reference implementation's own choice rather than second-guessing
    it."""

    @property
    def name(self) -> str:
        return "mariadb"


class SqliteJdbcCustomization(JdbcCustomization):
    """One writer at a time, so there is nothing for `SKIP LOCKED` to do."""

    @property
    def name(self) -> str:
        return "sqlite"


def autodetect(dialect_name: str) -> JdbcCustomization:
    """The customization for a SQLAlchemy dialect name.

    An unknown database gets the default rather than a failure: fetch-then-pick works
    everywhere, so a database this port has not been tested against still coordinates
    correctly - it just does not get the round-trip saving.
    """
    match dialect_name:
        case "postgresql":
            return PostgreSqlJdbcCustomization()
        case "mysql":
            return MySQLJdbcCustomization()
        case "mariadb":
            return MariaDBJdbcCustomization()
        case "sqlite":
            return SqliteJdbcCustomization()
        case _:
            return DefaultJdbcCustomization()
