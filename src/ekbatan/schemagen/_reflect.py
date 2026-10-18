"""Reading a migrated database: its tables and views, and what SQLAlchemy's reflection misses.

``MetaData.reflect`` gives the tables, keys, indexes, checks, defaults and comments. Two things
it does not give, read here from the database directly:

- **the type as the database spells it** - ``character varying(36)``, ``tinyint(1)``,
  ``int unsigned``, and the names of types SQLAlchemy cannot read at all (``xml``, ``inet6``) -
  which is what a forced type's ``include-types`` is matched against, as jOOQ matches the type
  its database reports;
- **MariaDB's JSON**, which is ``LONGTEXT`` with a ``CHECK (json_valid(...))`` on the column.
  Reflection says ``LONGTEXT`` and does not list the check, so a JSON column would be generated
  as text. The check is in ``information_schema.CHECK_CONSTRAINTS``, and it is read there.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, MetaData, Table, inspect, text
from sqlalchemy.exc import SAWarning

from ._config import SchemagenConfig

#: Tables this framework owns. Emitted as calls to `ekbatan.schema` rather than reflected,
#: because their shape is a contract other languages bind to.
FRAMEWORK_TABLES = {
    "events": "outbox_table",
    "event_notifications": "notifications_table",
    "scheduled_tasks": "scheduled_tasks_table",
}

#: Tables that belong to the migration machinery and to nobody else - this framework's history,
#: and Flyway's, for a database the Java port migrated. Always left out, as Java's builds leave
#: Flyway's out with `includeFlywayTable(false)`.
BOOKKEEPING = frozenset({"ekbatan_schema_history", "flyway_schema_history"})

#: Schemas every server has and no application owns.
SYSTEM_SCHEMAS = frozenset(
    {"information_schema", "pg_catalog", "pg_toast", "mysql", "sys", "performance_schema"}
)

#: (schema, table, column), where schema is ``None`` for the connection's own.
ColumnKey = tuple[str | None, str, str]


@dataclass
class Relation:
    """One table or view to generate."""

    table: Table
    #: ``None`` for the connection's own schema, written unqualified; else the schema's name.
    schema: str | None
    #: The schema's real name, for matching ``schema.table.column`` - the connection's own too.
    schema_name: str | None
    is_view: bool = False

    @property
    def key(self) -> str:
        """How a foreign key names this table: ``table`` or ``schema.table``."""
        return self.table.name if self.schema is None else f"{self.schema}.{self.table.name}"


@dataclass
class Reflected:
    """Everything a run read from the database."""

    dialect_name: str
    tables: list[Relation] = field(default_factory=list)
    views: list[Relation] = field(default_factory=list)
    framework: list[Relation] = field(default_factory=list)
    raw_types: dict[ColumnKey, str] = field(default_factory=dict)
    json_columns: set[ColumnKey] = field(default_factory=set)
    #: Column-level CHECKs reflection does not list (MariaDB), by (schema, table): sql texts.
    column_checks: dict[tuple[str | None, str], list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def reflect(engine: Engine, config: SchemagenConfig) -> Reflected:
    """Read every table and view the configuration asks for, and what is needed to type them."""
    inspector = inspect(engine)
    default = inspector.default_schema_name
    result = Reflected(dialect_name=engine.dialect.name)

    # The connection's own schema first, unqualified; then the others, qualified. Naming the
    # connection's own schema in `schemas` - `public`, as Java's builds do - means the same.
    wanted: list[str | None] = [None]
    for name in config.schemas:
        if name != default and name not in wanted:
            wanted.append(name)

    for schema in wanted:
        metadata = MetaData()
        with warnings.catch_warnings():
            # "Did not recognize type 'xml'": said again, better, as a note naming the fix.
            warnings.filterwarnings("ignore", "Did not recognize type", SAWarning)
            metadata.reflect(bind=engine, schema=schema, views=config.views, resolve_fks=False)
        view_names = set(inspector.get_view_names(schema=schema)) if config.views else set()
        schema_name = schema if schema is not None else default
        for table in sorted(metadata.tables.values(), key=lambda t: t.name):
            if table.schema != schema:
                continue
            if table.name in BOOKKEEPING or not _included(table.name, schema_name, config):
                continue
            relation = Relation(table, schema, schema_name, is_view=table.name in view_names)
            if relation.is_view:
                result.views.append(relation)
            elif table.name in FRAMEWORK_TABLES:
                result.framework.append(relation)
            else:
                result.tables.append(relation)
        _raw_types(engine, schema, result)
        if engine.dialect.name == "mariadb":
            _mariadb_checks(engine, schema, result)

    if not config.schemas:
        _note_unread_schemas(inspector, default, result)
    return result


def _included(table: str, schema_name: str | None, config: SchemagenConfig) -> bool:
    names = [table] + ([f"{schema_name}.{table}"] if schema_name else [])
    if not any(re.fullmatch(config.includes, n) for n in names):
        return False
    return not (config.excludes and any(re.fullmatch(config.excludes, n) for n in names))


def _raw_types(engine: Engine, schema: str | None, result: Reflected) -> None:
    """Each column's type as the database itself spells it."""
    dialect = engine.dialect.name
    rows: list[Any] = []  # (table, column, spelled)
    with engine.connect() as connection:
        if dialect == "postgresql":
            rows = list(
                connection.execute(
                    text(
                        "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod) "
                        "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = COALESCE(:schema, current_schema()) "
                        "AND a.attnum > 0 AND NOT a.attisdropped"
                    ),
                    {"schema": schema},
                ).all()
            )
        elif dialect in ("mysql", "mariadb"):
            rows = list(
                connection.execute(
                    text(
                        "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE "
                        "FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = COALESCE(:schema, DATABASE())"
                    ),
                    {"schema": schema},
                ).all()
            )
        elif dialect == "sqlite":
            names = connection.execute(
                text("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
            ).scalars()
            for table in list(names):
                quoted = '"' + table.replace('"', '""') + '"'
                for info in connection.exec_driver_sql(f"PRAGMA table_info({quoted})").all():
                    rows.append((table, info[1], info[2]))
    for table, column, spelled in rows:
        result.raw_types[(schema, str(table), str(column))] = str(spelled)


_JSON_VALID = re.compile(r"json_valid\(`?([^`)]+)`?\)", re.I)


def _mariadb_checks(engine: Engine, schema: str | None, result: Reflected) -> None:
    """MariaDB's column-level checks: its JSON columns, and the checks reflection skips."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT TABLE_NAME, CONSTRAINT_NAME, CHECK_CLAUSE FROM "
                "information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = "
                "COALESCE(:schema, DATABASE()) AND LEVEL = 'Column'"
            ),
            {"schema": schema},
        ).all()
    for table, column, clause in rows:
        key = (schema, str(table), str(column))
        found = _JSON_VALID.fullmatch(str(clause).strip())
        if (
            found is not None
            and found.group(1) == column
            and result.raw_types.get(key, "").lower() == "longtext"
        ):
            result.json_columns.add(key)
        else:
            result.column_checks.setdefault((schema, str(table)), []).append(str(clause))


def _note_unread_schemas(inspector: object, default: str | None, result: Reflected) -> None:
    """Say so when another schema holds tables - round 2 found them skipped without a word."""
    try:
        names = inspector.get_schema_names()  # type: ignore[attr-defined]
    except NotImplementedError:  # pragma: no cover
        return
    for name in sorted(names):
        if name == default or name in SYSTEM_SCHEMAS or name.startswith("pg_"):
            continue
        tables = inspector.get_table_names(schema=name)  # type: ignore[attr-defined]
        if tables:
            result.notes.append(
                f"schema {name} has {len(tables)} table(s), not generated: add it to "
                f'`schemas` to include it (schemas = ["{name}"])'
            )
