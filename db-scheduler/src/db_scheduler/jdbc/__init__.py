"""The table, and the SQL that coordinates a cluster through it.

A port of `com.github.kagkarlsson.scheduler.jdbc`. The schema is db-scheduler's `scheduled_tasks`
unchanged - same columns, same types, same indexes - so a Java instance and a Python instance
can run against one table and see each other's rows.

The name is `jdbc` even though there is no JDBC here. It is where the Java reader will look, and
the package is about the same thing: everything that knows what the database looks like.
"""

from __future__ import annotations

from .customization import (
    DefaultJdbcCustomization,
    JdbcCustomization,
    MariaDBJdbcCustomization,
    MySQLJdbcCustomization,
    PostgreSqlJdbcCustomization,
    SqliteJdbcCustomization,
    autodetect,
)
from .repository import JdbcTaskRepository
from .schema import scheduled_tasks_table

__all__ = [
    "DefaultJdbcCustomization",
    "JdbcCustomization",
    "JdbcTaskRepository",
    "MariaDBJdbcCustomization",
    "MySQLJdbcCustomization",
    "PostgreSqlJdbcCustomization",
    "SqliteJdbcCustomization",
    "autodetect",
    "scheduled_tasks_table",
]
