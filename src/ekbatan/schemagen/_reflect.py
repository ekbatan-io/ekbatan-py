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
class Exclusion:
    """A PostgreSQL ``EXCLUDE`` constraint, which reflection does not read (it sees its index)."""

    name: str
    using: str
    #: Each element as PostgreSQL writes it, with its operator: ``("r", "&&")``.
    elements: list[tuple[str, str]]
    where: str | None
    deferrable: bool
    initially_deferred: bool


@dataclass(frozen=True)
class Domain:
    """A PostgreSQL domain, all of it - reflection keeps one check and drops the base type's
    length, so ``varchar(10)`` would come back ``varchar``."""

    schema: str
    name: str
    #: The base type as PostgreSQL spells it: ``character varying(10)``.
    base: str
    not_null: bool
    default: str | None
    collation: str | None
    #: (name, expression) per check, the expression as PostgreSQL prints it.
    checks: list[tuple[str, str]]


@dataclass(frozen=True)
class SequenceSpec:
    """A sequence, with everything ``CREATE SEQUENCE`` can say."""

    schema: str | None
    name: str
    data_type: str | None
    start: int
    increment: int
    minvalue: int
    maxvalue: int
    cycle: bool
    cache: int
    #: The (table, column) whose ``SERIAL`` made it - ``None`` for one of its own.
    owner: tuple[str, str] | None


@dataclass(frozen=True)
class RelationKind:
    """What PostgreSQL says a table is beyond its columns."""

    #: ``r`` a table, ``p`` a partitioned one, ``m`` a materialized view, ...
    kind: str
    unlogged: bool
    #: ``RANGE (logdate)`` for a partitioned table.
    partition_by: str | None
    #: The table this one is a partition of, and its bounds: ``FOR VALUES FROM (...) TO (...)``.
    partition_of: str | None
    partition_bound: str | None
    #: The tables it inherits from, by name.
    inherits: list[str]


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
    column_checks: dict[tuple[str | None, str], list[tuple[str, str]]] = field(default_factory=dict)
    #: Column defaults reflection drops - MariaDB's ``nextval(...)`` - by column.
    lost_defaults: dict[ColumnKey, str] = field(default_factory=dict)
    #: Each index's key elements as the database writes them - ``a DESC NULLS LAST``,
    #: ``lower(name) COLLATE "C"`` - for the indexes reflection reads with something missing.
    index_elements: dict[IndexKey, list[str]] = field(default_factory=dict)
    #: PostgreSQL's DEFERRABLE primary and unique keys: (deferrable, initially deferred).
    deferrable: dict[IndexKey, tuple[bool, bool]] = field(default_factory=dict)
    #: PostgreSQL's EXCLUDE constraints, by (schema, table).
    exclusions: dict[tuple[str | None, str], list[Exclusion]] = field(default_factory=dict)
    #: PostgreSQL's domains, by (schema, name).
    domains: dict[tuple[str, str], Domain] = field(default_factory=dict)
    #: A domain-typed column's own NOT NULL and default, which reflection merges with the
    #: domain's: (not null, default or None).
    own_column: dict[ColumnKey, tuple[bool, str | None]] = field(default_factory=dict)
    #: The dimensions an array column was declared with, where more than one.
    array_dimensions: dict[ColumnKey, int] = field(default_factory=dict)
    #: What PostgreSQL says each table is, by (schema, table).
    relation_kinds: dict[tuple[str | None, str], RelationKind] = field(default_factory=dict)
    #: Every sequence, by (schema, name).
    sequences: dict[tuple[str | None, str], SequenceSpec] = field(default_factory=dict)
    #: Indexes reflection skips (SQLite's on expressions), by (schema, table).
    extra_indexes: dict[tuple[str | None, str], list[IndexSpec]] = field(default_factory=dict)
    #: What the database holds that no module can say, by kind - "function", "trigger" - each
    #: a list of names: said in a note.
    unwritten: dict[str, list[str]] = field(default_factory=dict)
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
        partitions: set[str] = set()
        if engine.dialect.name == "postgresql":
            _postgres(engine, schema, result)
            # A materialized view is queried like a view, and create_all must not build a
            # table by its name.
            if config.views:
                view_names |= set(inspector.get_materialized_view_names(schema=schema))
            # A partition is created by its parent's DDL (see _render), not as a table.
            partitions = {
                name
                for (where, name), kind in result.relation_kinds.items()
                if where == schema and kind.partition_of is not None
            }
        schema_name = schema if schema is not None else default
        for table in sorted(metadata.tables.values(), key=lambda t: t.name):
            if table.schema != schema:
                continue
            if table.name in BOOKKEEPING or not _included(table.name, schema_name, config):
                continue
            if table.name in partitions:
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
        if engine.dialect.name in ("mysql", "mariadb"):
            _mysql(engine, schema, result)
        elif engine.dialect.name == "sqlite":
            _sqlite_triggers(engine, result)

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
            result.column_checks.setdefault((schema, str(table)), []).append(
                (str(column), str(clause))
            )


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


# -- PostgreSQL's catalog -------------------------------------------------------------------------


def _postgres(engine: Engine, schema: str | None, result: Reflected) -> None:
    """What PostgreSQL says that reflection does not read: deferrable keys, EXCLUDE
    constraints, domains whole, a domain column's own NOT NULL and default, array dimensions,
    partitioning, inheritance, UNLOGGED, every sequence with its options, and what no module can
    say (functions, procedures, triggers, composite types, extensions)."""
    own = "COALESCE(:schema, current_schema())"
    joined = (
        "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
        f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = {own} "
    )
    params = {"schema": schema}
    with engine.connect() as connection:

        def rows(sql: str) -> list[Any]:
            return list(connection.execute(text(sql), params).all())

        for table, name, deferred in rows(
            f"SELECT c.relname, con.conname, con.condeferred {joined}"
            "AND con.contype IN ('p', 'u') AND con.condeferrable"
        ):
            result.deferrable[(schema, str(table), str(name))] = (True, bool(deferred))

        for table, name, definition, deferrable, deferred in rows(
            "SELECT c.relname, con.conname, pg_get_constraintdef(con.oid), con.condeferrable, "
            f"con.condeferred {joined}AND con.contype = 'x'"
        ):
            found = _exclusion(str(name), str(definition), bool(deferrable), bool(deferred))
            if found is None:
                result.notes.append(
                    f"{table}: EXCLUDE constraint {name} could not be read ({definition}); left out"
                )
                continue
            result.exclusions.setdefault((schema, str(table)), []).append(found)

        for row in rows(
            "SELECT n.nspname, t.typname, format_type(t.typbasetype, t.typtypmod), "
            "t.typnotnull, t.typdefault, "
            "CASE WHEN t.typcollation <> b.typcollation THEN co.collname END, "
            "ARRAY(SELECT k.conname || chr(1) || pg_get_constraintdef(k.oid) "
            "      FROM pg_constraint k WHERE k.contypid = t.oid AND k.contype = 'c' "
            "      ORDER BY k.conname) "
            "FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
            "JOIN pg_type b ON b.oid = t.typbasetype "
            "LEFT JOIN pg_collation co ON co.oid = t.typcollation "
            "WHERE t.typtype = 'd' AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
        ):
            nspname, name, base, not_null, default, collation, checks = row
            parsed: list[tuple[str, str]] = []
            for check in checks or []:
                check_name, _, definition = str(check).partition(chr(1))
                body = re.sub(r"^CHECK\s*", "", definition.strip())
                body = re.sub(r"\s+NOT VALID$", "", body)
                parsed.append((check_name, body))
            result.domains[(str(nspname), str(name))] = Domain(
                str(nspname),
                str(name),
                str(base),
                bool(not_null),
                None if default is None else str(default),
                None if collation is None else str(collation),
                parsed,
            )

        for table, column, not_null, default, kind, dimensions in rows(
            "SELECT c.relname, a.attname, a.attnotnull, pg_get_expr(d.adbin, d.adrelid), "
            "t.typtype, a.attndims FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_type t ON t.oid = a.atttypid "
            "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
            f"WHERE n.nspname = {own} AND a.attnum > 0 AND NOT a.attisdropped "
            "AND (t.typtype = 'd' OR a.attndims > 1)"
        ):
            key = (schema, str(table), str(column))
            if kind == "d":
                result.own_column[key] = (bool(not_null), None if default is None else str(default))
            if dimensions and int(dimensions) > 1:
                result.array_dimensions[key] = int(dimensions)

        for row in rows(
            "SELECT c.relname, c.relkind, c.relpersistence, "
            "CASE WHEN c.relkind = 'p' THEN pg_get_partkeydef(c.oid) END, "
            "(SELECT p.relname FROM pg_inherits i JOIN pg_class p ON p.oid = i.inhparent "
            " WHERE i.inhrelid = c.oid AND c.relispartition), "
            "CASE WHEN c.relispartition THEN pg_get_expr(c.relpartbound, c.oid) END, "
            "ARRAY(SELECT p.relname FROM pg_inherits i JOIN pg_class p ON p.oid = i.inhparent "
            "      WHERE i.inhrelid = c.oid AND NOT c.relispartition ORDER BY i.inhseqno) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = {own} AND c.relkind IN ('r', 'p', 'm', 'f')"
        ):
            table, kind, persistence, partition_by, parent, bound, inherits = row
            result.relation_kinds[(schema, str(table))] = RelationKind(
                str(kind),
                persistence == "u",
                None if partition_by is None else str(partition_by),
                None if parent is None else str(parent),
                None if bound is None else str(bound),
                [str(i) for i in inherits or []],
            )

        for row in rows(
            "SELECT s.sequencename, s.data_type::text, s.start_value, s.increment_by, "
            "s.min_value, s.max_value, s.cycle, s.cache_size, "
            "(SELECT d.deptype::text || chr(1) || c2.relname || chr(1) || a.attname "
            " FROM pg_depend d "
            " JOIN pg_class c2 ON c2.oid = d.refobjid "
            " JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid "
            " WHERE d.classid = 'pg_class'::regclass AND d.objid = sc.oid "
            " AND d.deptype IN ('a', 'i') LIMIT 1) "
            "FROM pg_sequences s JOIN pg_namespace n ON n.nspname = s.schemaname "
            "JOIN pg_class sc ON sc.relname = s.sequencename AND sc.relnamespace = n.oid "
            f"WHERE s.schemaname = {own}"
        ):
            name, data_type, start, increment, minimum, maximum, cycle, cache, owner = row
            owned = None
            if owner is not None:
                kind, table, column = str(owner).split(chr(1))
                if kind == "i":
                    continue  # an identity column's: Identity(...) says it
                owned = (table, column)
            result.sequences[(schema, str(name))] = SequenceSpec(
                schema,
                str(name),
                None if data_type is None else str(data_type),
                int(start),
                int(increment),
                int(minimum),
                int(maximum),
                bool(cycle),
                int(cache),
                owned,
            )

        extension_owned = (
            "NOT EXISTS (SELECT 1 FROM pg_depend e WHERE e.objid = {oid} AND e.deptype = 'e')"
        )
        for name, kind in rows(
            "SELECT p.proname, p.prokind FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            f"WHERE n.nspname = {own} AND " + extension_owned.format(oid="p.oid")
        ):
            what = {"p": "procedure", "a": "aggregate", "w": "window function"}.get(
                str(kind), "function"
            )
            result.unwritten.setdefault(what, []).append(str(name))
        for table, name in rows(
            "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = {own} "
            "AND NOT t.tgisinternal"
        ):
            result.unwritten.setdefault("trigger", []).append(f"{name} (on {table})")
        for (name,) in rows(
            "SELECT t.typname FROM pg_type t JOIN pg_class c ON c.oid = t.typrelid "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            f"WHERE n.nspname = {own} AND c.relkind = 'c' AND "
            + extension_owned.format(oid="t.oid")
        ):
            result.unwritten.setdefault("composite type", []).append(str(name))
        if schema is None:
            for (name,) in rows("SELECT extname FROM pg_extension WHERE extname <> 'plpgsql'"):
                result.unwritten.setdefault("extension", []).append(str(name))


def _exclusion(name: str, definition: str, deferrable: bool, deferred: bool) -> Exclusion | None:
    """``EXCLUDE USING gist (r WITH &&) WHERE ((x > 0))``, read."""
    found = re.match(r"EXCLUDE USING (\w+) ", definition)
    if found is None:
        return None
    parts = index_parts(f"x ON t {definition[found.end() :]}")
    if parts is None:
        return None
    elements: list[tuple[str, str]] = []
    for element in parts[0]:
        expression, with_, operator = element.rpartition(" WITH ")
        if not with_:
            return None
        elements.append((expression.strip(), operator.strip()))
    tail = re.sub(r"\s*(NOT\s+)?DEFERRABLE(\s+INITIALLY\s+(DEFERRED|IMMEDIATE))?\s*$", "", parts[1])
    where = None
    if tail:
        matched = re.fullmatch(r"WHERE\s+(.*)", tail, re.S)
        if matched is None:
            return None  # INCLUDE, WITH (...): not said by ExcludeConstraint
        where = matched.group(1).strip()
    return Exclusion(name, found.group(1), elements, where, deferrable, deferred)


# -- MySQL's and MariaDB's catalog ----------------------------------------------------------------


def _mysql(engine: Engine, schema: str | None, result: Reflected) -> None:
    """What MySQL and MariaDB say that reflection does not read: MariaDB's sequences and the
    ``nextval`` defaults reflection drops, and - for the notes - invisible columns, unenforced
    checks, index comments and invisible indexes, system-versioned tables, routines, triggers
    and events."""
    mariadb = engine.dialect.name == "mariadb"
    own = "COALESCE(:schema, DATABASE())"
    params = {"schema": schema}
    with engine.connect() as connection:
        database = connection.execute(text(f"SELECT {own}"), params).scalar_one()

        def rows(sql: str) -> list[Any]:
            return list(connection.execute(text(sql), params).all())

        def note(kind: str, name: str) -> None:
            result.unwritten.setdefault(kind, []).append(name)

        for table, column, default, extra in rows(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_DEFAULT, EXTRA FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = {own}"
        ):
            if "INVISIBLE" in str(extra).upper():
                note("invisible column", f"{table}.{column}")
            if default is not None and re.match(r"(?i)nextval\(", str(default)):
                # MariaDB's `DEFAULT NEXT VALUE FOR s`, which reflection drops. The database
                # names the sequence's database; the module's tables are written without it.
                found = re.sub(
                    rf"(?i)nextval\(`{re.escape(str(database))}`\.", "nextval(", str(default)
                )
                result.lost_defaults[(schema, str(table), str(column))] = found

        if not mariadb:
            for table, name in rows(
                "SELECT TABLE_NAME, CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
                f"WHERE TABLE_SCHEMA = {own} AND CONSTRAINT_TYPE = 'CHECK' AND ENFORCED = 'NO'"
            ):
                note("unenforced check", f"{name} (on {table})")
        hidden = "IS_VISIBLE = 'NO'" if not mariadb else "IGNORED = 'YES'"
        for table, name, comment, invisible in rows(
            f"SELECT TABLE_NAME, INDEX_NAME, MAX(INDEX_COMMENT), MAX({hidden}) "
            f"FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = {own} "
            "GROUP BY TABLE_NAME, INDEX_NAME"
        ):
            if comment:
                note("index comment", f"{name} (on {table})")
            if invisible:
                note("invisible index" if not mariadb else "ignored index", f"{name} (on {table})")
        for table, kind in rows(
            "SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = {own} AND TABLE_TYPE IN ('SEQUENCE', 'SYSTEM VERSIONED')"
        ):
            if kind == "SYSTEM VERSIONED":
                note("system-versioned table", str(table))
                continue
            quoted = engine.dialect.identifier_preparer.quote
            where = "" if schema is None else f"{quoted(schema)}."
            values = connection.execute(
                text(
                    "SELECT start_value, increment, minimum_value, maximum_value, cycle_option, "
                    f"cache_size FROM {where}{quoted(str(table))}"
                )
            ).one()
            start, increment, minimum, maximum, cycle, cache = values
            result.sequences[(schema, str(table))] = SequenceSpec(
                schema,
                str(table),
                None,
                int(start),
                int(increment),
                int(minimum),
                int(maximum),
                bool(cycle),
                int(cache),
                None,
            )
        for name, kind in rows(
            "SELECT ROUTINE_NAME, ROUTINE_TYPE FROM information_schema.ROUTINES "
            f"WHERE ROUTINE_SCHEMA = {own}"
        ):
            note(str(kind).lower(), str(name))
        for name, table in rows(
            "SELECT TRIGGER_NAME, EVENT_OBJECT_TABLE FROM information_schema.TRIGGERS "
            f"WHERE TRIGGER_SCHEMA = {own}"
        ):
            note("trigger", f"{name} (on {table})")
        for (name,) in rows(
            f"SELECT EVENT_NAME FROM information_schema.EVENTS WHERE EVENT_SCHEMA = {own}"
        ):
            note("event", str(name))


def _sqlite_triggers(engine: Engine, result: Reflected) -> None:
    with engine.connect() as connection:
        for name, table in connection.exec_driver_sql(
            "SELECT name, tbl_name FROM sqlite_master WHERE type = 'trigger'"
        ).all():
            result.unwritten.setdefault("trigger", []).append(f"{name} (on {table})")
