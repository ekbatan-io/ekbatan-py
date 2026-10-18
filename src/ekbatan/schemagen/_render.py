"""Writing the module: one ``Table`` per table, with what makes ``create_all`` build it again.

A generated table carries its columns and types, ``NOT NULL``, the primary key, unique
constraints, foreign keys, indexes, checks, server defaults, identity and computed columns,
and comments - what jOOQ generates as ``Keys``, ``Indexes`` and ``defaultValue(...)``. So
``metadata.create_all`` on an empty database, in a test, builds what the migrations built.
Ekbatan itself uses none of it at run time; it is there so the module does not misdescribe the
database.

Views go in their own ``view_metadata``: they can be queried like tables, but ``create_all``
must not create a *table* by a view's name.
"""

from __future__ import annotations

import keyword
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)
from sqlalchemy import types as sqltypes
from sqlalchemy.engine import Dialect
from sqlalchemy.sql.elements import TextClause

from ..errors import ValidationError
from ._config import ForcedType, SchemagenConfig
from ._reflect import FRAMEWORK_TABLES, Reflected, Relation
from ._types import Emitted, emit_type, lit, resolve, sqlalchemy_names, type_names

log = logging.getLogger("io.ekbatan.schemagen")

#: The column helpers' DDL, per database: a forced timestamp column whose own type is not this
#: is said to differ, because ``create_all`` from the module builds this instead.
_TIMESTAMP_DDL = {
    "postgresql": {"timestamp without time zone"},
    "mysql": {"datetime(6)"},
    "mariadb": {"datetime(6)"},
    "sqlite": {"datetime"},
}


def constant(name: str) -> str:
    """The Python name a table is exported under."""
    upper = re.sub(r"\W", "_", name.upper())
    if upper[:1].isdigit():
        upper = f"T_{upper}"
    return f"{upper}_" if keyword.iskeyword(upper.lower()) else upper


def q(value: str) -> str:
    """A string literal, double-quoted as the rest of the module is."""
    return lit(value)


@dataclass
class _Writer:
    reflected: Reflected
    config: SchemagenConfig
    dialect: Dialect | None
    sqlalchemy: set[str] = field(default_factory=lambda: {"Column", "MetaData", "Table"})
    helpers: set[str] = field(default_factory=set)
    aliases: dict[tuple[str, str], str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    # -- columns ---------------------------------------------------------------------------

    def column_type(self, relation: Relation, column: Column[Any]) -> str:
        table = relation.table.name
        key = (relation.schema, table, column.name)
        raw = self.reflected.raw_types.get(key)
        is_json = key in self.reflected.json_columns
        extra = ["JSON"] if is_json else []
        if isinstance(column.type, sqltypes.Enum):
            extra.append("ENUM")
        names = [column.name, f"{table}.{column.name}"]
        if relation.schema_name:
            names.append(f"{relation.schema_name}.{table}.{column.name}")
        types = type_names(column.type, raw, self.dialect, extra)

        matching = [
            candidate
            for candidate in self.config.all_forced_types
            if candidate.matches(
                dialect=self.reflected.dialect_name,
                names=names,
                types=types,
                nullable=bool(column.nullable),
            )
        ]
        forced = matching[0] if matching else None
        where = f"{relation.key}.{column.name}"
        if forced is None or forced.converter is None:
            return self._plain_type(where, column, forced, raw, is_json)
        # A converter keeps the column's own type and wraps itself around it: the type the
        # column would have without this forced type - the next one that matches and is not a
        # converter, else the type table's.
        inner = next((c for c in matching if c.converter is None), None)
        impl = self._plain_type(where, column, inner, raw, is_json)
        self.helpers.add("converted")
        return f"converted({impl}, {self.use(resolve(forced))})"

    def _plain_type(
        self,
        where: str,
        column: Column[Any],
        forced: ForcedType | None,
        raw: str | None,
        is_json: bool,
    ) -> str:
        """The column's type with no converter: a forced name or SQLAlchemy type, or the type
        table's, or - for a type SQLAlchemy cannot read - the database's own spelling."""
        emitted: Emitted | None
        if forced is not None:
            emitted = resolve(forced)
            self._timestamp_notes(where, forced, raw)
        elif is_json:
            emitted = Emitted("JSON(none_as_null=True)")
        else:
            emitted = emit_type(column.type, self.reflected.dialect_name)
            if emitted is None and raw:
                # A type SQLAlchemy has no class for: written as the database spells it, so
                # create_all builds the same column.
                emitted = Emitted(
                    f"sql_type({lit(raw)})", frozenset({("ekbatan.schema", "sql_type")})
                )
            elif emitted is None:
                # Only when the database's spelling is not known - render() on tables built by
                # hand, never a real run.
                self.notes.append(
                    f"{where} is {type(column.type).__name__}, a type with no known spelling: "
                    "written as NullType(), which create_all cannot build"
                )
                emitted = Emitted("NullType()")
            elif (
                isinstance(column.type, sqltypes.DateTime)
                and column.type.timezone
                and self.reflected.dialect_name == "postgresql"
            ):
                self.notes.append(
                    f"{where} is TIMESTAMPTZ: generated as a zoned timestamp, as Java generates "
                    "OffsetDateTime. Ekbatan recommends zone-less TIMESTAMP holding UTC - see "
                    "docs/database/multi-database.md#always-utc"
                )
        return self.use(emitted)

    def _timestamp_notes(self, where: str, forced: ForcedType, raw: str | None) -> None:
        """Say where a column forced to ``timestamp_column()`` is not the column it builds."""
        if forced.name != "timestamp_column" or raw is None:
            return
        spelled = raw.lower()
        dialect = self.reflected.dialect_name
        helper = _TIMESTAMP_DDL.get(dialect)
        if helper is None or spelled in helper:
            return
        if dialect in ("mysql", "mariadb") and spelled.startswith("timestamp"):
            why = (
                "the server converts it by the session's time zone and it ends in 2038; "
                "Ekbatan recommends DATETIME(6)"
            )
        elif dialect in ("mysql", "mariadb"):
            why = "it keeps fewer than 6 digits of a second, and cuts the rest; use DATETIME(6)"
        elif dialect == "postgresql":
            why = "it keeps fewer than 6 digits of a second, and rounds the rest"
        else:
            why = "a declared type SQLite keeps but does not enforce"
        self.notes.append(
            f"{where} is {raw.upper()}: {why}. timestamp_column() builds "
            f"{'/'.join(sorted(helper)).upper()} - see docs/database/multi-database.md#always-utc"
        )

    def use(self, emitted: Emitted) -> str:
        """Record what a piece of source needs imported, and hand it back.

        A user type whose name is taken - by SQLAlchemy, a helper, another user type - is
        imported under an alias, ``myapp_types_JSON``, and its source says so.
        """
        source = emitted.source
        for module, name in emitted.imports:
            if module == "ekbatan.schema":
                self.helpers.add(name)
                continue
            alias = self.aliases.get((module, name))
            if alias is None:
                clash = name in _reserved() or any(n == name for _, n in self.aliases)
                alias = f"{module.replace('.', '_')}_{name}" if clash else name
                self.aliases[(module, name)] = alias
            if alias != name and source.startswith(name):
                source = alias + source[len(name) :]
        self.sqlalchemy |= sqlalchemy_names(source) - set(self.aliases.values())
        return source

    def column(self, relation: Relation, column: Column[Any], single_pk: bool) -> str:
        positional = [q(column.name), self.column_type(relation, column)]
        keywords: list[str] = []
        if column.computed is not None:
            self.sqlalchemy.add("Computed")
            persisted = column.computed.persisted
            positional.append(
                f"Computed({q(str(column.computed.sqltext))}"
                + (f", persisted={persisted!r}" if persisted is not None else "")
                + ")"
            )
        if column.identity is not None:
            self.sqlalchemy.add("Identity")
            identity = column.identity
            args = []
            if identity.always:
                args.append("always=True")
            if identity.start not in (None, 1):
                args.append(f"start={identity.start}")
            if identity.increment not in (None, 1):
                args.append(f"increment={identity.increment}")
            if identity.cycle:
                args.append("cycle=True")
            positional.append(f"Identity({', '.join(args)})")

        if single_pk and column.primary_key:
            keywords.append("primary_key=True")
        elif not column.nullable:
            keywords.append("nullable=False")

        integer = isinstance(column.type, sqltypes.Integer)
        if (
            column.autoincrement is True
            and not (single_pk and column.primary_key)
            and self.reflected.dialect_name in ("mysql", "mariadb")
        ):
            self.notes.append(
                f"{relation.key}.{column.name} is AUTO_INCREMENT outside the primary key, which "
                "SQLAlchemy cannot declare: create_all builds it without"
            )
        # SQLAlchemy makes a lone integer key auto-increment unless told otherwise; the
        # database is the authority on whether it is. Lone, whether the key is written on the
        # column or apart under a name of its own (`PrimaryKeyConstraint("id", name=...)`).
        if (
            column.primary_key
            and len(relation.table.primary_key.columns) == 1
            and integer
            and column.identity is None
            and column.autoincrement is not True
        ):
            keywords.append("autoincrement=False")

        sequence, default = self.server_default(relation, column, single_pk)
        if sequence is not None:
            positional.append(sequence)
        if default is not None:
            keywords.append(default)
        if column.comment:
            keywords.append(f"comment={q(column.comment)}")
        return f"Column({', '.join(positional + keywords)})"

    def server_default(
        self, relation: Relation, column: Column[Any], single_pk: bool
    ) -> tuple[str | None, str | None]:
        """The column's ``Sequence``, if it has one, and its ``server_default=...``."""
        none = (None, None)
        if (
            column.server_default is None
            or column.computed is not None
            or column.identity is not None
        ):
            return none
        arg = getattr(column.server_default, "arg", None)
        sql = arg.text if isinstance(arg, TextClause) else (arg if isinstance(arg, str) else None)
        if sql is None:
            return none
        if sql.strip().upper() == "NULL":
            return none  # MariaDB reports this for every nullable column; it is no default
        if sql.startswith("nextval("):
            # A serial. As the key, SQLAlchemy makes it SERIAL again on its own; elsewhere the
            # sequence is declared, so create_all creates it before the table.
            if single_pk and column.primary_key:
                return none
            sequence = re.match(r"nextval\('([^']+)'", sql)
            if sequence is not None:
                self.sqlalchemy |= {"Sequence", "text"}
                # `audit.t_seq_seq` for a table in another schema: the sequence lives there too,
                # and the default names it there, so create_all must create it there.
                where, _, name = sequence.group(1).rpartition(".")
                schema = f", schema={q(where.strip(chr(34)))}" if where else ""
                return (
                    f"Sequence({q(name.strip(chr(34)))}{schema})",
                    f"server_default=text({q(sql)})",
                )
        self.sqlalchemy.add("text")
        return None, f"server_default=text({q(sql)})"

    # -- tables ----------------------------------------------------------------------------

    def table(self, relation: Relation, generated: set[str]) -> list[str]:
        table = relation.table
        pk = table.primary_key
        pk_columns = [c.name for c in pk.columns] if pk is not None else []
        single_pk = len(pk_columns) == 1 and not self._custom_pk_name(relation)

        lines = [f"{self.name_of(relation)} = Table(", f"    {q(table.name)},"]
        lines.append("    view_metadata," if relation.is_view else "    metadata,")
        for column in table.columns:
            lines.append(f"    {self.column(relation, column, single_pk)},")
        if pk_columns and not single_pk:
            self.sqlalchemy.add("PrimaryKeyConstraint")
            name = f", name={q(str(pk.name))}" if self._custom_pk_name(relation) else ""
            lines.append(f"    PrimaryKeyConstraint({', '.join(q(c) for c in pk_columns)}{name}),")
        for constraint in sorted(table.constraints, key=_by_name):
            line = self.constraint(relation, constraint, generated)
            if line is not None:
                lines.append(f"    {line},")
        for check in self.reflected.column_checks.get((relation.schema, table.name), []):
            self.sqlalchemy.add("CheckConstraint")
            lines.append(f"    CheckConstraint({q(check)}),")
        for index in sorted(table.indexes, key=_by_name):
            lines.append(f"    {self.index(index, inline=True)},")
        if table.comment:
            lines.append(f"    comment={q(table.comment)},")
        if relation.schema is not None:
            lines.append(f"    schema={q(relation.schema)},")
        lines.append(")")
        return ["\n".join(lines)]

    def _custom_pk_name(self, relation: Relation) -> bool:
        pk = relation.table.primary_key
        if pk is None or not pk.name:
            return False
        return pk.name not in (f"{relation.table.name}_pkey", "PRIMARY")

    def constraint(self, relation: Relation, constraint: Any, generated: set[str]) -> str | None:
        named = f", name={q(constraint.name)}" if isinstance(constraint.name, str) else ""
        if isinstance(constraint, UniqueConstraint) and constraint.columns:
            self.sqlalchemy.add("UniqueConstraint")
            return f"UniqueConstraint({', '.join(q(c.name) for c in constraint.columns)}{named})"
        if isinstance(constraint, CheckConstraint):
            self.sqlalchemy.add("CheckConstraint")
            return f"CheckConstraint({q(str(constraint.sqltext))}{named})"
        if isinstance(constraint, ForeignKeyConstraint):
            targets = [e.target_fullname for e in constraint.elements]
            referred = targets[0].rsplit(".", 1)[0]
            if referred not in generated:
                self.notes.append(
                    f"{relation.key}: foreign key {constraint.name or constraint.column_keys} "
                    f"refers to {referred}, which is not generated; left out"
                )
                return None
            self.sqlalchemy.add("ForeignKeyConstraint")
            options = "".join(
                f", {option}={lit(value)}"
                for option in ("ondelete", "onupdate", "deferrable", "initially")
                if (value := getattr(constraint, option)) is not None
            )
            local = ", ".join(q(c) for c in constraint.column_keys)
            remote = ", ".join(q(t) for t in targets)
            return f"ForeignKeyConstraint([{local}], [{remote}]{named}{options})"
        return None

    def index(self, index: Index, *, inline: bool, table: str | None = None) -> str:
        self.sqlalchemy.add("Index")
        parts = [q(str(index.name))]
        for expression in index.expressions:
            if isinstance(expression, Column):
                parts.append(q(expression.name) if inline else f"{table}.c[{q(expression.name)}]")
            else:
                self.sqlalchemy.add("text")
                parts.append(f"text({q(str(expression))})")
        if index.unique:
            parts.append("unique=True")
        for option, value in sorted(index.dialect_kwargs.items()):
            if value in (None, [], {}, ()):
                continue
            if option.endswith("_where"):
                self.sqlalchemy.add("text")
                parts.append(f"{option}=text({q(str(value))})")
            else:
                parts.append(f"{option}={lit(value)}")
        return f"Index({', '.join(parts)})"

    def name_of(self, relation: Relation) -> str:
        base = (
            relation.table.name
            if relation.schema is None
            else f"{relation.schema}_{relation.table.name}"
        )
        return constant(base)

    # -- the framework's tables ---------------------------------------------------------------

    def framework(self, relation: Relation) -> list[str]:
        helper = FRAMEWORK_TABLES[relation.table.name]
        self.helpers.add(helper)
        name = self.name_of(relation)
        schema = f", schema={q(relation.schema)}" if relation.schema is not None else ""
        lines = [f"{name} = {helper}(metadata{schema})"]

        from sqlalchemy import MetaData

        from .. import schema as framework_schema

        built = getattr(framework_schema, helper)(MetaData())
        expected = {c.name for c in built.columns}
        found = {c.name for c in relation.table.columns}
        if found and expected != found:
            missing, extra = sorted(expected - found), sorted(found - expected)
            self.notes.append(
                f"{relation.key} differs from {helper}(): "
                + "; ".join(
                    x
                    for x in (
                        f"the migrations lack {missing}" if missing else "",
                        f"they add {extra}" if extra else "",
                    )
                    if x
                )
                + f". The module uses {helper}(), the framework's contract"
            )
        taken = {c.name for c in built.constraints if c.name} | {i.name for i in built.indexes}
        for index in sorted(relation.table.indexes, key=_by_name):
            if index.name in taken:
                continue
            if not all(isinstance(e, Column) and e.name in expected for e in index.expressions):
                self.notes.append(
                    f"{relation.key}: index {index.name} is on an expression; left out"
                )
                continue
            lines.append(self.index(index, inline=False, table=name))
        return lines


def _reserved() -> set[str]:
    """Names a generated module already has: SQLAlchemy's, the helpers', its own."""
    from ._types import HELPERS, _namespace

    return (
        set(_namespace())
        | set(HELPERS)
        | set(FRAMEWORK_TABLES.values())
        | {
            "converted",
            "sql_type",
            "metadata",
            "view_metadata",
            "check_fingerprint",
            "MIGRATIONS_FINGERPRINT",
        }
    )


def _by_name(item: Any) -> str:
    return str(getattr(item, "name", None) or "")


def render_module(
    reflected: Reflected,
    config: SchemagenConfig,
    *,
    dialect: Dialect | None,
    migrations_fingerprint: str,
    module: str | None,
) -> tuple[str, list[str]]:
    """The generated module, as text, and the notes that came up writing it."""
    writer = _Writer(reflected, config, dialect)
    relations = [*reflected.tables, *reflected.framework, *reflected.views]

    names: dict[str, Relation] = {}
    for relation in relations:
        name = writer.name_of(relation)
        if name in names or name in ("metadata", "view_metadata"):
            other = names.get(name)
            raise ValidationError(
                f"{relation.key} and {other.key if other else name} would both be exported as "
                f"{name}; exclude one with `excludes`"
            )
        names[name] = relation

    generated = {r.key for r in reflected.tables} | {r.key for r in reflected.framework}
    body: list[str] = []
    for relation in reflected.tables:
        body += writer.table(relation, generated)
    for relation in reflected.framework:
        body.append("\n".join(writer.framework(relation)))
    views: list[str] = []
    for relation in reflected.views:
        views += writer.table(relation, generated)

    exported = sorted(names) + ["metadata"] + (["view_metadata"] if views else [])
    text = _module_text(writer, body, views, exported, migrations_fingerprint, module)
    return text, reflected.notes + writer.notes


def _module_text(
    writer: _Writer,
    body: Sequence[str],
    views: Sequence[str],
    exported: Sequence[str],
    migrations_fingerprint: str,
    module: str | None,
) -> str:
    dialects = sorted(writer.sqlalchemy & {"mysql", "postgresql", "sqlite"})
    plain = sorted(writer.sqlalchemy - {"mysql", "postgresql", "sqlite", "NullType"})

    imports = [f"from sqlalchemy import {', '.join(plain)}"]
    if dialects:
        imports.append(f"from sqlalchemy.dialects import {', '.join(dialects)}")
    if "NullType" in writer.sqlalchemy:
        imports.append("from sqlalchemy.types import NullType")
    if writer.helpers:
        imports.append(f"from ekbatan.schema import {', '.join(sorted(writer.helpers))}")
    for (source, name), alias in sorted(writer.aliases.items()):
        imports.append(f"from {source} import {name}" + (f" as {alias}" if alias != name else ""))
    imports.append("from ekbatan.schemagen import check_fingerprint")

    def joined(blocks: Iterable[str]) -> str:
        return "".join(f"{b}\n\n" for b in blocks)

    view_part = ""
    if views:
        view_part = (
            "#: The views. Queried like tables; kept out of `metadata`, so that `create_all` does\n"
            "#: not create a table by a view's name. Views come from the migrations.\n"
            f"view_metadata = MetaData()\n\n{joined(views)}"
        )
    text = f'''"""The tables, generated from the schema the migrations produce.

Generated by `python -m ekbatan.schemagen`. **Do not edit.** Change a migration and regenerate;
the fingerprint below makes a stale copy of this file fail loudly rather than disagree quietly
with the database it claims to describe.

The framework's own tables are built from `ekbatan.schema` rather than reflected, because their
shape is a contract a CDC connector and other ports bind to - there is one definition to be
right about, and it is not this file.
"""

from __future__ import annotations

{chr(10).join(imports)}

#: A hash of the migrations this file was generated from.
MIGRATIONS_FINGERPRINT = "{migrations_fingerprint}"

metadata = MetaData()

{joined(body)}{view_part}__all__ = [{", ".join(q(e) for e in exported)}]

# Nothing in Python runs before `python -m yourapp`, so this is where a stale schema is caught.
# Hashing the migration files costs microseconds and needs no database.
check_fingerprint(__file__, MIGRATIONS_FINGERPRINT, {lit(module)})
'''
    return text
