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
#: (schema, table, index or constraint).
IndexKey = tuple[str | None, str, str]


@dataclass(frozen=True)
class IndexSpec:
    """An index reflection does not hand back at all - SQLite's on an expression - read from
    the database's own text of it."""

    name: str
    unique: bool
    #: Each key element as the database writes it: ``lower(name)``, ``a DESC``.
    elements: list[str]
    where: str | None


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
    #: What SQLite's reflection does not read from each table's text, by (schema, table).
    sqlite_tables: dict[tuple[str | None, str], SqliteTable] = field(default_factory=dict)
    #: MySQL's spatial columns restricted to one spatial reference system: ``SRID 4326``.
    srids: dict[ColumnKey, int] = field(default_factory=dict)
    #: Column-level CHECKs reflection does not list (MariaDB), by (schema, table): sql texts.
    column_checks: dict[tuple[str | None, str], list[str]] = field(default_factory=dict)
    #: Each index's key elements as the database writes them - ``a DESC NULLS LAST``,
    #: ``lower(name) COLLATE "C"`` - for the indexes reflection reads with something missing.
    index_elements: dict[IndexKey, list[str]] = field(default_factory=dict)
    #: Indexes reflection skips (SQLite's on expressions), by (schema, table).
    extra_indexes: dict[tuple[str | None, str], list[IndexSpec]] = field(default_factory=dict)
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
            # SQLite's index on an expression: read from its own text instead (_index_elements).
            warnings.filterwarnings("ignore", "Skipped unsupported reflection", SAWarning)
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
        _index_elements(engine, schema, result)

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
            srid = "SRS_ID" if dialect == "mysql" else "NULL"
            found = connection.execute(
                text(
                    f"SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, {srid} "
                    "FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = COALESCE(:schema, DATABASE())"
                ),
                {"schema": schema},
            ).all()
            rows = [r[:3] for r in found]
            for table, column, _, srs_id in found:
                if srs_id is not None:
                    result.srids[(schema, str(table), str(column))] = int(srs_id)
        elif dialect == "sqlite":
            found_tables = connection.execute(
                text("SELECT name, type, sql FROM sqlite_master WHERE type IN ('table', 'view')")
            ).all()
            for table, kind, sql in found_tables:
                if kind == "table" and sql is not None:
                    result.sqlite_tables[(schema, str(table))] = sqlite_table_parts(str(sql))
            for table in [str(t[0]) for t in found_tables]:
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


# -- index elements -----------------------------------------------------------------------------


def split_top_level(text: str, separator: str = ",") -> list[str]:
    """``text`` split on ``separator`` where it is outside parentheses and quotes."""
    parts: list[str] = []
    depth, quote, start = 0, "", 0
    for at, character in enumerate(text):
        if quote:
            if character == quote:
                quote = ""
        elif character in "'\"`":
            quote = character
        elif character == "[":
            quote = "]"
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == separator and depth == 0:
            parts.append(text[start:at].strip())
            start = at + 1
    parts.append(text[start:].strip())
    return parts


def index_parts(sql: str) -> tuple[list[str], str] | None:
    """``CREATE INDEX n ON t USING m (a, lower(b) DESC) WHERE x`` -> the key elements, and what
    follows their list. ``None`` when the text is not one this can read."""
    quote, depth, seen_on, opened = "", 0, False, -1
    upper = sql.upper()
    for at, character in enumerate(sql):
        if quote:
            if character == quote:
                quote = ""
            continue
        if character in "'\"`":
            quote = character
        elif character == "[":
            quote = "]"
        elif character == "(":
            if seen_on and depth == 0 and opened < 0:
                opened = at
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0 and opened >= 0:
                return split_top_level(sql[opened + 1 : at]), sql[at + 1 :].strip()
        elif depth == 0 and not seen_on and character.isspace():
            seen_on = re.match(r"\s+ON\s", upper[at:]) is not None
    return None


def _index_elements(engine: Engine, schema: str | None, result: Reflected) -> None:
    """Each index's key elements as the database itself writes them.

    Reflection reads an index's columns, but not all of what is said about each: PostgreSQL's
    sort order is kept only for a plain column, and its ``COLLATE`` not at all; MySQL's
    ``DESC`` and its functional key parts are lost; SQLite skips an index on an expression
    altogether, with only a warning. A column written as a plain name is enough to rebuild the
    index; anything more is kept here, as text, and written as the database wrote it.
    """
    dialect = engine.dialect.name
    with engine.connect() as connection:
        if dialect == "postgresql":
            rows = connection.execute(
                text(
                    "SELECT c.relname, i.relname, pg_get_indexdef(i.oid) FROM pg_index x "
                    "JOIN pg_class i ON i.oid = x.indexrelid "
                    "JOIN pg_class c ON c.oid = x.indrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = COALESCE(:schema, current_schema())"
                ),
                {"schema": schema},
            ).all()
            for table, index, definition in rows:
                parts = index_parts(str(definition))
                if parts is not None:
                    result.index_elements[(schema, str(table), str(index))] = parts[0]
        elif dialect in ("mysql", "mariadb"):
            expression = "EXPRESSION" if dialect == "mysql" else "NULL"
            rows = connection.execute(
                text(
                    "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, SUB_PART, COLLATION, "
                    f"{expression}, INDEX_TYPE FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA = COALESCE(:schema, DATABASE()) "
                    "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"
                ),
                {"schema": schema},
            ).all()
            quote = engine.dialect.identifier_preparer.quote
            for table, index, column, sub_part, collation, functional, kind in rows:
                if column is not None:
                    element = quote(str(column))
                    # A SPATIAL index reports a prefix of 32 that nobody wrote and none may.
                    if sub_part is not None and str(kind).upper() != "SPATIAL":
                        element += f"({int(sub_part)})"
                else:
                    element = f"({functional})"
                if collation == "D":
                    element += " DESC"
                key = (schema, str(table), str(index))
                result.index_elements.setdefault(key, []).append(element)
        elif dialect == "sqlite":
            rows = connection.execute(
                text(
                    "SELECT tbl_name, name, sql FROM sqlite_master "
                    "WHERE type = 'index' AND sql IS NOT NULL"
                )
            ).all()
            for table, index, sql in rows:
                parts = index_parts(str(sql))
                if parts is None:
                    continue
                elements, tail = parts
                result.index_elements[(schema, str(table), str(index))] = elements
                result.extra_indexes.setdefault((schema, str(table)), []).append(
                    IndexSpec(
                        str(index),
                        str(sql).lstrip().upper().startswith("CREATE UNIQUE"),
                        elements,
                        _where(tail),
                    )
                )


def _where(tail: str) -> str | None:
    found = re.match(r"\s*WHERE\s+(.*)$", tail, re.I | re.S)
    return found.group(1).strip() if found else None


# -- SQLite's table text ------------------------------------------------------------------------


def _tokens(text_: str) -> list[str]:
    """SQL text as tokens: a quoted name or string, a word, a parenthesised group, a character."""
    found: list[str] = []
    at = 0
    closing = {"'": "'", '"': '"', "`": "`", "[": "]"}
    while at < len(text_):
        character = text_[at]
        if character.isspace():
            at += 1
        elif character in closing:
            end = at + 1
            while end < len(text_):
                if text_[end] == closing[character]:
                    # A doubled quote is the quote itself.
                    if character != "[" and text_[end + 1 : end + 2] == closing[character]:
                        end += 2
                        continue
                    break
                end += 1
            found.append(text_[at : end + 1])
            at = end + 1
        elif character == "(":
            depth, end = 0, at
            while end < len(text_):
                if text_[end] in closing:
                    end += len(_tokens(text_[end:])[0])
                    continue
                depth += {"(": 1, ")": -1}.get(text_[end], 0)
                if depth == 0:
                    break
                end += 1
            found.append(text_[at : end + 1])
            at = end + 1
        elif character.isalnum() or character == "_":
            end = at
            while end < len(text_) and (text_[end].isalnum() or text_[end] in "_$"):
                end += 1
            found.append(text_[at:end])
            at = end
        else:
            found.append(character)
            at += 1
    return found


def unquote(token: str) -> str:
    """A name as SQL wrote it, without its quotes."""
    if len(token) >= 2 and token[0] in "\"'`" and token[-1] == token[0]:
        return token[1:-1].replace(token[0] * 2, token[0])
    if len(token) >= 2 and token[0] == "[" and token[-1] == "]":
        return token[1:-1]
    return token


@dataclass(frozen=True)
class SqliteTable:
    """What SQLite's reflection does not read from a table's ``CREATE TABLE`` text."""

    #: Each column's ``COLLATE``, by column name.
    collations: dict[str, str]
    #: The column declared ``INTEGER PRIMARY KEY`` - the rowid's alias, which SQLite fills.
    rowid_alias: str | None
    autoincrement: bool
    strict: bool
    without_rowid: bool

    @property
    def options(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, on in (
                ("AUTOINCREMENT", self.autoincrement),
                ("STRICT", self.strict),
                ("WITHOUT ROWID", self.without_rowid),
            )
            if on
        )


_TABLE_CONSTRAINTS = frozenset({"CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN"})


def sqlite_table_parts(sql: str) -> SqliteTable:
    tokens = _tokens(sql)
    body = next((t for t in tokens if t.startswith("(")), "()")
    tail = [t.upper() for t in tokens[tokens.index(body) + 1 :]] if body in tokens else []
    collations: dict[str, str] = {}
    autoincrement = False
    primary: list[str] = []
    declared: dict[str, str] = {}
    for definition in split_top_level(body[1:-1]):
        words = _tokens(definition)
        if not words:
            continue
        upper = [w.upper() for w in words]
        if upper[0] in _TABLE_CONSTRAINTS:
            if "PRIMARY" in upper and "KEY" in upper:
                group = next((w for w in words[upper.index("KEY") + 1 :] if w.startswith("(")), "")
                primary = [unquote(p.split()[0]) for p in split_top_level(group[1:-1]) if p]
                if len(primary) == 1 and "AUTOINCREMENT" in group.upper():
                    autoincrement = True
            continue
        name = unquote(words[0])
        # The declared type: the words up to the first constraint keyword.
        stop = next(
            (
                i
                for i, w in enumerate(upper[1:], 1)
                if w
                in (
                    "CONSTRAINT",
                    "PRIMARY",
                    "NOT",
                    "NULL",
                    "UNIQUE",
                    "CHECK",
                    "DEFAULT",
                    "COLLATE",
                    "REFERENCES",
                    "GENERATED",
                    "AS",
                )
            ),
            len(words),
        )
        declared[name] = " ".join(upper[1:stop])
        for i, word in enumerate(upper):
            if word == "COLLATE" and i + 1 < len(words):
                collations[name] = unquote(words[i + 1])
            elif word == "AUTOINCREMENT":
                autoincrement = True
            elif word == "PRIMARY" and upper[i + 1 : i + 2] == ["KEY"]:
                order = upper[i + 2 : i + 3]
                primary = [name] if order != ["DESC"] else []
    without_rowid = "WITHOUT" in tail and "ROWID" in tail
    alias = None
    if len(primary) == 1 and declared.get(primary[0]) == "INTEGER" and not without_rowid:
        alias = primary[0]
    return SqliteTable(collations, alias, autoincrement, "STRICT" in tail, without_rowid)
