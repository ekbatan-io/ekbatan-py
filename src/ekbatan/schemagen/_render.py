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
    Computed,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)
from sqlalchemy import types as sqltypes
from sqlalchemy.engine import Dialect
from sqlalchemy.sql.elements import TextClause

from ..errors import ValidationError
from ._config import ForcedType, SchemagenConfig
from ._records import (
    Field,
    Unknowable,
    converter_user_type,
    python_type,
    record_name,
    spelled_type,
)
from ._records import render as render_record
from ._reflect import FRAMEWORK_TABLES, Reflected, Relation
from ._types import MODULES, Emitted, emit_type, lit, modules_used, resolve, type_names

log = logging.getLogger("io.ekbatan.schemagen")

#: The shape of the module this schemagen writes. Raised whenever that shape changes - typed
#: records arrived with 2, every SQLAlchemy name written through ``import sqlalchemy`` with 3,
#: the records' timestamps typed by time kind (``Instant``, ``LocalDateTime``,
#: ``OffsetDateTime``) with 4, sequences as constants, partitions created after their parent
#: and each table's own options (ENGINE, ``postgresql_partition_by``...) with 5, MySQL's and
#: MariaDB's partitioning applied after the table is created and sequence defaults written as the
#: catalog says them with 6 - so a module written by an older schemagen fails on import instead
#: of quietly lacking what the code beside it expects. A module from before the number has none.
SCHEMA_FORMAT = 6

#: The module's own constants, in capitals as a table's are: a table that would take one of
#: these names is exported with ``_`` after it, as a keyword is.
_OWN_CONSTANTS = frozenset({"MIGRATIONS_FINGERPRINT", "SCHEMA_FORMAT"})

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
    taken = keyword.iskeyword(upper.lower()) or upper in _OWN_CONSTANTS
    return f"{upper}_" if taken else upper


def q(value: str) -> str:
    """A string literal, double-quoted as the rest of the module is."""
    return lit(value)


@dataclass
class _Writer:
    reflected: Reflected
    config: SchemagenConfig
    dialect: Dialect | None
    #: The modules the module writes SQLAlchemy's names through: ``sqlalchemy``, and each
    #: dialect module a type needs.
    modules: set[str] = field(default_factory=lambda: {"sqlalchemy"})
    helpers: set[str] = field(default_factory=set)
    aliases: dict[tuple[str, str], str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    #: Each relation's record: its name and its keys, by the relation's key.
    records: dict[str, tuple[str, list[Field]]] = field(default_factory=dict)
    #: The record names, known before any table is written so a user type cannot take one.
    record_names: set[str] = field(default_factory=set)
    #: The names the module exports its tables under.
    constants: set[str] = field(default_factory=set)
    #: What the records' types import: (module, name) -> the name in the module.
    type_aliases: dict[tuple[str, str], str] = field(default_factory=dict)
    #: Each column's Python type, by (relation key, column name), set as its type is written.
    py_types: dict[tuple[str, str], object] = field(default_factory=dict)
    #: The keys of the tables the module builds.
    generated: set[str] = field(default_factory=set)
    #: A record's type where the column is written as the database spells it but its values
    #: are known all the same, by (relation key, column name).
    spelled_hints: dict[tuple[str, str], object] = field(default_factory=dict)
    #: (table, table it must come after) that no foreign key says: a default drawing on a
    #: sequence another table's SERIAL owns.
    after: list[tuple[Relation, str]] = field(default_factory=list)

    # -- columns ---------------------------------------------------------------------------

    def column_type(self, relation: Relation, column: Column[Any]) -> str:
        table = relation.table.name
        key = (relation.schema, table, column.name)
        dimensions = self.reflected.array_dimensions.get(key)
        if dimensions and isinstance(column.type, sqltypes.ARRAY) and not column.type.dimensions:
            # PostgreSQL keeps how many dimensions an array was declared with; reflection does
            # not read it, and a record of `list[int]` for an `INTEGER[][]` would be wrong.
            from sqlalchemy.dialects import postgresql

            column.type = postgresql.ARRAY(column.type.item_type, dimensions=dimensions)
        sqlite_table = self.reflected.sqlite_tables.get((relation.schema, table))
        collation = sqlite_table.collations.get(column.name) if sqlite_table else None
        if collation is not None:
            # SQLite's reflection does not read a column's COLLATE; its table's text says it.
            if isinstance(column.type, sqltypes.String) and not column.type.collation:
                column.type = column.type.copy()
                column.type.collation = collation
            elif not isinstance(column.type, sqltypes.String):
                self.notes.append(
                    f"{relation.key}.{column.name} is {column.type} COLLATE {collation}: a "
                    "collation on a column that is not text, which SQLAlchemy cannot declare; "
                    "create_all builds it without"
                )
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
        record_key = (relation.key, column.name)
        if forced is None or forced.converter is None:
            source = self._plain_type(where, column, forced, raw, is_json, relation)
            built = self.built(source)
            spelled = getattr(built, "spelling", None)
            known = (
                spelled_type(self.reflected.dialect_name, spelled)
                if isinstance(spelled, str)
                else None
            )
            known = self.spelled_hints.get(record_key, known)
            self.py_types[record_key] = (
                known if known is not None else self.python_type_of(where, built)
            )
            if forced is not None and forced.name == "LocalDateTime":
                # The same zone-less column as an Instant's; only what the record says differs.
                from ..time import LocalDateTime

                self.py_types[record_key] = LocalDateTime
            from sqlalchemy.dialects import postgresql

            if (
                forced is None
                and isinstance(column.type, sqltypes.ARRAY)
                and isinstance(column.type.item_type, postgresql.DOMAIN)
            ):
                self.notes.append(
                    f"{where} is an array of the domain {column.type.item_type.name}: asyncpg "
                    "reads it as a list, but psycopg does not know a domain's array type and "
                    "hands back its text, which SQLAlchemy then splits into characters - so its "
                    "record types it Any. Read it cast to the base type's array "
                    "(sqlalchemy.cast(column, postgresql.ARRAY(...))) for a list on both"
                )
                self.py_types[record_key] = Any
            return source
        # A converter keeps the column's own type and wraps itself around it: the type the
        # column would have without this forced type - the next one that matches and is not a
        # converter, else the type table's.
        inner = next((c for c in matching if c.converter is None), None)
        impl = self._plain_type(where, column, inner, raw, is_json, relation)
        self.helpers.add("converted")
        converter = self.use(resolve(forced))
        # The record holds what the converter gives, read off the converter the module builds.
        self.py_types[record_key] = converter_user_type(self.built(converter), forced.converter)
        if self.py_types[record_key] is Any:
            self.notes.append(
                f"{where}: the converter {forced.converter} says its from_db() returns Any, and "
                "has no Converter[DbT, UserT] base saying more, so its record types it Any"
            )
        return f"converted({impl}, {converter})"

    def built(self, source: str) -> Any:
        """What a piece of the module's source builds, with the names the module imports."""
        import importlib

        from .. import schema as framework_schema
        from ._types import _namespace

        names = dict(_namespace())
        names.update(
            {
                n: getattr(framework_schema, n)
                for n in ("uuid_column", "json_column", "timestamp_column", "converted", "sql_type")
            }
        )
        for (module, attribute), alias in self.aliases.items():
            names[alias] = getattr(importlib.import_module(module), attribute)
        return eval(source, {"__builtins__": {}}, names)

    def python_type_of(self, where: str, column_type: Any) -> object:
        """The column's Python type for its record; ``Any``, with a note, where nothing says."""
        try:
            return python_type(column_type)
        except Unknowable as why:
            self.notes.append(
                f"{where}: {why}, so its record types it Any. A converter gives it a type of "
                "your own"
            )
            return Any

    def type_name(self, module: str, attribute: str) -> str:
        """The name a record's type is written under, importing it - aliased when the name is
        already the module's: a SQLAlchemy type, a helper, a table, a record, a user type."""
        key = (module, attribute)
        if key in self.aliases:
            return self.aliases[key]
        if module == "ekbatan.schema" and (
            attribute in self.helpers or attribute not in self.taken_names(exclude=key)
        ):
            self.helpers.add(attribute)
            return attribute
        alias = self.type_aliases.get(key)
        if alias is None:
            clash = attribute in self.taken_names(exclude=key)
            alias = f"{module.replace('.', '_')}_{attribute}" if clash else attribute
            self.type_aliases[key] = alias
        return alias

    def taken_names(self, *, exclude: tuple[str, str]) -> set[str]:
        return (
            self.modules
            | self.helpers
            | set(self.aliases.values())
            | {a for k, a in self.type_aliases.items() if k != exclude}
            | self.constants
            | self.record_names
            | {
                "metadata",
                "view_metadata",
                "check_fingerprint",
                "MIGRATIONS_FINGERPRINT",
                "SCHEMA_FORMAT",
            }
        )

    def _plain_type(
        self,
        where: str,
        column: Column[Any],
        forced: ForcedType | None,
        raw: str | None,
        is_json: bool,
        relation: Relation | None = None,
    ) -> str:
        """The column's type with no converter: a forced name or SQLAlchemy type, or the type
        table's, or - for a type SQLAlchemy cannot read - the database's own spelling."""
        emitted: Emitted | None
        zoned = isinstance(column.type, sqltypes.DateTime) and bool(column.type.timezone)
        if forced is not None and forced.name == "OffsetDateTime" and zoned:
            # Already a zoned timestamp: written as the type table writes it, so a TIMESTAMPTZ(3)
            # keeps its precision. Only the record's kind comes from the forced type.
            emitted = emit_type(column.type, self.reflected.dialect_name) or resolve(forced)
            self._offset_note(where)
        elif forced is not None:
            emitted = resolve(forced)
            self._timestamp_notes(where, forced, raw)
        elif is_json:
            emitted = Emitted("sqlalchemy.JSON(none_as_null=True)")
        else:
            from sqlalchemy.dialects import postgresql

            emitted = None
            if isinstance(column.type, postgresql.DOMAIN) and relation is not None:
                emitted = self.domain(where, column.type, relation)
            elif (
                isinstance(column.type, sqltypes.ARRAY)
                and isinstance(column.type.item_type, postgresql.DOMAIN)
                and relation is not None
            ):
                # An array of a domain: the domain whole, as a column of it is - else its
                # check is lost and create_all builds a domain that allows anything.
                item = self.domain(where, column.type.item_type, relation)
                if item is not None:
                    dimensions = column.type.dimensions
                    more = f", dimensions={dimensions}" if dimensions else ""
                    emitted = Emitted(f"postgresql.ARRAY({item.source}{more})", item.imports)
            if emitted is None:
                emitted = emit_type(column.type, self.reflected.dialect_name)
            emitted = self._backslashed(where, column, raw, relation, emitted)
            if (
                self.reflected.dialect_name == "postgresql"
                and type(column.type) is sqltypes.String
                and raw in ('"char"', "name")
            ):
                # PostgreSQL's one-byte "char" and its `name`: reflection reads them as a plain
                # String, which create_all builds VARCHAR. Written as PostgreSQL spells them.
                emitted = None
                if raw == '"char"' and relation is not None:
                    self.notes.append(
                        f'{where} is "char": it reads as str on psycopg and as bytes on '
                        "asyncpg, so its record types it Any. A converter gives it a type of "
                        "your own"
                    )
                    self.spelled_hints[(relation.key, column.name)] = Any
            if (
                self.reflected.dialect_name == "sqlite"
                and raw is not None
                and not _sqlite_named(raw)
            ):
                # A type name SQLite keeps but SQLAlchemy reads only by its affinity - `ANY`,
                # `UNSIGNED BIG INT`, none at all: written as SQLite spells it, as a STRICT
                # table requires and as `create_all` builds the same column.
                emitted = None
            if emitted is None and (raw or (raw == "" and self.reflected.dialect_name == "sqlite")):
                # A type SQLAlchemy has no class for: written as the database spells it, so
                # create_all builds the same column - with MySQL's spatial reference system,
                # which its spelling of the type leaves out.
                srid = (
                    self.reflected.srids.get((relation.schema, relation.table.name, column.name))
                    if relation is not None
                    else None
                )
                spelled = raw if srid is None else f"{raw} SRID {srid}"
                emitted = Emitted(
                    f"sql_type({lit(spelled)})", frozenset({("ekbatan.schema", "sql_type")})
                )
            elif emitted is None:
                # Only when the database's spelling is not known - render() on tables built by
                # hand, never a real run.
                self.notes.append(
                    f"{where} is {type(column.type).__name__}, a type with no known spelling: "
                    "written as NullType(), which create_all cannot build"
                )
                emitted = Emitted("sqlalchemy.types.NullType()")
            elif zoned:
                self._offset_note(where)
        return self.use(emitted)

    def _backslashed(
        self,
        where: str,
        column: Column[Any],
        raw: str | None,
        relation: Relation | None,
        emitted: Emitted | None,
    ) -> Emitted | None:
        """A MySQL ENUM or SET with a backslash in a value, written as the database spells it.

        SQLAlchemy's MySQL ENUM and SET put their values into CREATE TABLE with only ``'``
        escaped, and MySQL reads a backslash there as an escape: ``x\\y`` would be built ``xy``.
        The record still names the values; a value is a plain string either way.
        """
        from sqlalchemy.dialects import mysql

        from ._types import _mysql_value

        dialect = self.reflected.dialect_name
        if (
            dialect not in ("mysql", "mariadb")
            or raw is None
            or relation is None
            or not isinstance(column.type, (sqltypes.Enum, mysql.SET))
        ):
            return emitted
        values = column.type.enums if isinstance(column.type, sqltypes.Enum) else column.type.values
        held = [_mysql_value(v, dialect) for v in values]
        if not any("\\" in v for v in held):
            return emitted
        self.notes.append(
            f"{where}: a value holds a backslash, which SQLAlchemy's {type(column.type).__name__} "
            "writes unescaped in CREATE TABLE - so the column is written as the database spells "
            f"its type, sql_type(...), and its values are plain strings"
        )
        from typing import Literal

        self.spelled_hints[(relation.key, column.name)] = (
            Literal[tuple(held)] if isinstance(column.type, sqltypes.Enum) else set[str]
        )
        return Emitted(f"sql_type({lit(raw)})", frozenset({("ekbatan.schema", "sql_type")}))

    def _offset_note(self, where: str) -> None:
        """Say what a zoned timestamp gives back: the moment, with an offset not its own."""
        if self.reflected.dialect_name != "postgresql":
            return
        self.notes.append(
            f"{where} is TIMESTAMPTZ: its record says OffsetDateTime, as Java generates "
            "OffsetDateTime. PostgreSQL keeps only the moment, so the offset read back is the "
            "session's (psycopg) or UTC (asyncpg), not the one written. Ekbatan recommends "
            "zone-less TIMESTAMP holding UTC - see docs/database/multi-database.md#always-utc"
        )

    def _timestamp_notes(self, where: str, forced: ForcedType, raw: str | None) -> None:
        """Say where a column forced to ``timestamp_column()`` is not the column it builds."""
        if forced.name not in ("timestamp_column", "Instant", "LocalDateTime") or raw is None:
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
        elif "with time zone" in spelled:
            why = "it is zoned, and the column the forced type builds has no zone"
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

        A user type whose name is taken - by SQLAlchemy, a helper, a table, a record, another
        user type - is imported under an alias, ``myapp_types_JSON``, and its source says so.
        """
        source = emitted.source
        for module, name in emitted.imports:
            if module == "ekbatan.schema":
                self.helpers.add(name)
                continue
            alias = self.aliases.get((module, name))
            if alias is None:
                clash = (
                    name in _reserved()
                    or name in self.constants
                    or name in self.record_names
                    or any(n == name for _, n in self.aliases)
                )
                alias = f"{module.replace('.', '_')}_{name}" if clash else name
                self.aliases[(module, name)] = alias
            if alias != name and source.startswith(name):
                source = alias + source[len(name) :]
        self.modules |= modules_used(source) - set(self.aliases.values())
        return source

    def column(self, relation: Relation, column: Column[Any], single_pk: bool) -> str:
        column_type = self.column_type(relation, column)
        positional = [q(column.name), column_type]
        keywords: list[str] = []
        sqlite_table = self.reflected.sqlite_tables.get((relation.schema, relation.table.name))
        if sqlite_table is not None and column.name in sqlite_table.generated:
            # SQLite's own text of the column: SQLAlchemy's reading runs one generated column
            # into the next.
            expression, stored = sqlite_table.generated[column.name]
            column.computed = Computed(expression, persisted=stored)
        if column.computed is not None:
            persisted = column.computed.persisted
            positional.append(
                f"sqlalchemy.Computed({q(str(column.computed.sqltext))}"
                + (f", persisted={persisted!r}" if persisted is not None else "")
                + ")"
            )
        if column.identity is not None:
            identity = column.identity
            args = ["always=True"] if identity.always else []
            args += sequence_options(
                _integer_kind(column.type),
                start=identity.start,
                increment=identity.increment,
                minvalue=identity.minvalue,
                maxvalue=identity.maxvalue,
                cycle=identity.cycle,
                cache=identity.cache,
            )
            positional.append(f"sqlalchemy.Identity({', '.join(args)})")

        own = self.reflected.own_column.get((relation.schema, relation.table.name, column.name))
        # A domain column: reflection gives it the domain's NOT NULL; the column's own is this.
        nullable = (not own[0]) if own is not None else column.nullable
        if single_pk and column.primary_key:
            keywords.append("primary_key=True")
        elif not nullable:
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
            and not self._rowid_alias(relation, column)
        ):
            keywords.append("autoincrement=False")

        sequence, default = self.server_default(relation, column, single_pk)
        if sequence is not None:
            positional.append(sequence)
        if default is not None:
            if "timestamp_column()" in column_type:
                default = self._at_six_digits(relation, column, default)
            keywords.append(default)
        if column.comment:
            keywords.append(f"comment={q(column.comment)}")
        return f"sqlalchemy.Column({', '.join(positional + keywords)})"

    def server_default(
        self, relation: Relation, column: Column[Any], single_pk: bool
    ) -> tuple[str | None, str | None]:
        """The column's ``Sequence``, if it has one, and its ``server_default=...``."""
        none = (None, None)
        if column.computed is not None or column.identity is not None:
            return none
        own = self.reflected.own_column.get((relation.schema, relation.table.name, column.name))
        if own is not None:
            # A domain column: reflection gives it the domain's default; its own is this.
            sql: str | None = own[1]
        elif (relation.schema, relation.table.name, column.name) in self.reflected.lost_defaults:
            sql = self.reflected.lost_defaults[(relation.schema, relation.table.name, column.name)]
            return None, f"server_default=sqlalchemy.text({q(sql)})"
        elif column.server_default is None:
            return none
        else:
            arg = getattr(column.server_default, "arg", None)
            sql = (
                arg.text if isinstance(arg, TextClause) else (arg if isinstance(arg, str) else None)
            )
        if sql is None:
            return none
        if sql.strip().upper() == "NULL":
            return none  # MariaDB reports this for every nullable column; it is no default
        drawn = self.reflected.default_sequences.get(
            (relation.schema, relation.table.name, column.name)
        )
        if drawn is not None:
            return self._sequence_default(relation, column, single_pk, *drawn)
        if sql.startswith("nextval("):
            # A serial. As the key, SQLAlchemy makes it SERIAL again on its own; elsewhere the
            # sequence is declared, so create_all creates it before the table.
            if single_pk and column.primary_key:
                return none
            sequence = re.match(r"nextval\('([^']+)'", sql)
            if sequence is not None:
                # `audit.t_seq_seq` for a table in another schema: the sequence lives there too,
                # and the default names it there, so create_all must create it there.
                where, _, name = sequence.group(1).rpartition(".")
                bare = name.strip(chr(34))
                schema = f", schema={q(where.strip(chr(34)))}" if where else ""
                spec = self.reflected.sequences.get((relation.schema, bare))
                if spec is not None and spec.owner is None:
                    # A sequence of its own, written once at the module's top as a constant
                    # (`sequences()`); create_all creates it before any table.
                    return None, f"server_default=sqlalchemy.text({q(sql)})"
                options = "".join(f", {o}" for o in self.sequence_args(spec)) if spec else ""
                return (
                    f"sqlalchemy.Sequence({q(bare)}{schema}{options})",
                    f"server_default=sqlalchemy.text({q(sql)})",
                )
        return None, f"server_default=sqlalchemy.text({q(sql)})"

    def _sequence_default(
        self,
        relation: Relation,
        column: Column[Any],
        single_pk: bool,
        schema: str | None,
        name: str,
        sql: str,
    ) -> tuple[str | None, str | None]:
        """A PostgreSQL default drawing on a sequence, as the catalog says it.

        Only a ``SERIAL`` key - its sequence owned by this very column - is left for SQLAlchemy
        to make SERIAL again. Any other sequence is the default's own: a sequence of its own is
        a module constant already and the default names it; one owned by another column, or in
        a schema not read, is declared on the column, in its real schema, so create_all
        creates it. The default is written as the server prints it.
        """
        spec = self.reflected.sequences.get((schema, name))
        default = f"server_default=sqlalchemy.text({q(sql)})"
        if spec is None:
            drawn = self.reflected.drawn_sequences.get((schema, name))
            if drawn is not None:
                options = "".join(f", {o}" for o in self.sequence_args(drawn))
                where = f", schema={q(schema)}" if schema is not None else ""
                return f"sqlalchemy.Sequence({q(name)}{where}{options})", default
        owned_here = (
            spec is not None
            and spec.owner == (relation.table.name, column.name)
            and schema == relation.schema
        )
        if owned_here and single_pk and column.primary_key:
            return None, None
        if spec is not None and spec.owner is None:
            return None, default
        if spec is not None and spec.owner is not None and not owned_here:
            owner = spec.owner[0] if schema is None else f"{schema}.{spec.owner[0]}"
            if owner in self.generated:
                # Another table's SERIAL makes it: that table is built first, and the default
                # names what it made.
                self.after.append((relation, owner))
                return None, default
        where = f", schema={q(schema)}" if schema is not None else ""
        options = "".join(f", {o}" for o in self.sequence_args(spec)) if spec else ""
        return f"sqlalchemy.Sequence({q(name)}{where}{options})", default

    def _at_six_digits(self, relation: Relation, column: Column[Any], default: str) -> str:
        """A MySQL default of the current time, at the column's own precision, made the
        precision of the column ``timestamp_column()`` builds.

        MySQL refuses ``DATETIME(6) DEFAULT CURRENT_TIMESTAMP``: the default's fractional
        digits must be the column's. A ``TIMESTAMP DEFAULT CURRENT_TIMESTAMP`` - the most usual
        MySQL timestamp there is - forced to ``timestamp_column()``, which builds
        ``DATETIME(6)``, would make a module whose ``create_all`` fails. MariaDB makes the same
        change itself, silently.
        """
        if self.reflected.dialect_name not in ("mysql", "mariadb"):
            return default
        changed = _NOW.sub(
            lambda m: (
                m.group() if re.search(r"\(\s*6\s*\)$", m.group()) else "CURRENT_TIMESTAMP(6)"
            ),
            default,
        )
        if changed != default:
            self.notes.append(
                f"{relation.key}.{column.name}: its default of the current time is written at "
                "six digits, CURRENT_TIMESTAMP(6), as the DATETIME(6) timestamp_column() builds "
                "requires"
            )
        return changed

    # -- tables ----------------------------------------------------------------------------

    def table(self, relation: Relation, generated: set[str]) -> list[str]:
        table = relation.table
        pk = table.primary_key
        pk_columns = [c.name for c in pk.columns] if pk is not None else []
        single_pk = len(pk_columns) == 1 and not self._custom_pk_name(relation)

        lines = [f"{self.name_of(relation)} = sqlalchemy.Table(", f"    {q(table.name)},"]
        lines.append("    view_metadata," if relation.is_view else "    metadata,")
        for column in table.columns:
            lines.append(f"    {self.column(relation, column, single_pk)},")
        if pk_columns and not single_pk:
            name = f", name={q(str(pk.name))}" if self._custom_pk_name(relation) else ""
            keys = ", ".join(q(c) for c in pk_columns)
            later = self._deferrable(relation, str(pk.name))
            lines.append(f"    sqlalchemy.PrimaryKeyConstraint({keys}{name}{later}),")
        for constraint in sorted(table.constraints, key=_by_name):
            line = self.constraint(relation, constraint, generated)
            if line is not None:
                lines.append(f"    {line},")
        for exclusion in self.reflected.exclusions.get((relation.schema, table.name), []):
            lines.append(f"    {self.exclusion(relation, exclusion)},")
        for name, check in self.reflected.column_checks.get((relation.schema, table.name), []):
            # MariaDB names a column's own check after the column.
            lines.append(f"    sqlalchemy.CheckConstraint({q(check)}, name={q(name)}),")
        for index in sorted(table.indexes, key=_by_name):
            lines.append(f"    {self.index(index, inline=True, relation=relation)},")
        known = {i.name for i in table.indexes}
        for spec in self.reflected.extra_indexes.get((relation.schema, table.name), []):
            if spec.name in known:
                continue
            # An index reflection skipped (SQLite's on an expression): written from the
            # database's own text of it.
            parts = [q(spec.name), *(f"sqlalchemy.text({q(e)})" for e in spec.elements)]
            if spec.unique:
                parts.append("unique=True")
            if spec.where is not None:
                parts.append(
                    f"{self.reflected.dialect_name}_where=sqlalchemy.text({q(spec.where)})"
                )
            lines.append(f"    sqlalchemy.Index({', '.join(parts)}),")
        for option in self.table_options(relation):
            lines.append(f"    {option},")
        if table.comment:
            lines.append(f"    comment={q(table.comment)},")
        if relation.schema is not None:
            lines.append(f"    schema={q(relation.schema)},")
        lines.append(")")
        lines += self.partitions(relation)
        for sql in self.reflected.after_create.get((relation.schema, table.name), []):
            # What `Table` cannot say, said by the statement that says it, after the table.
            escaped = q(sql.replace("%", "%%").replace("%%(fullname)s", "%(fullname)s"))
            lines.append(
                f'sqlalchemy.event.listen({self.name_of(relation)}, "after_create", '
                f"sqlalchemy.DDL({escaped}))"
            )
        self.records[relation.key] = (
            self.record_of(relation),
            [
                Field(
                    c.name,
                    self.py_types[(relation.key, c.name)],
                    # A key column is NOT NULL as the module declares it - SQLite alone lets one
                    # hold NULL unless it says otherwise, and the module's key does not.
                    nullable=bool(c.nullable) and not c.primary_key,
                    optional=self._filled_by_database(c, relation),
                )
                for c in table.columns
            ],
        )
        return ["\n".join(lines)]

    def partitions(self, relation: Relation) -> list[str]:
        """``create_all`` creating a partitioned table's partitions after it, as PostgreSQL
        prints them - SQLAlchemy has no partition of its own to declare."""
        mysql_table = self.reflected.mysql_tables.get((relation.schema, relation.table.name))
        if (
            mysql_table is not None
            and mysql_table.partitioning is not None
            and not relation.is_view
        ):
            # MySQL and MariaDB partition a table by `ALTER TABLE ... PARTITION BY ...` as well
            # as by its CREATE: the clause as the server prints it, applied right after.
            clause = mysql_table.partitioning
            escaped = q(f"ALTER TABLE %(fullname)s {clause.replace('%', '%%')}")
            self.notes.append(
                f"{relation.key} is partitioned: SQLAlchemy cannot declare its partitions, so "
                "create_all partitions it right after creating it (ALTER TABLE ... "
                f"{clause.split(chr(10))[0]} ...)"
            )
            return [
                f'sqlalchemy.event.listen({self.name_of(relation)}, "after_create", '
                f"sqlalchemy.DDL({escaped}))"
            ]
        children: list[tuple[str, Any]] = []

        def collect(parent: str) -> None:
            for (schema, name), kind in sorted(
                self.reflected.relation_kinds.items(), key=lambda kv: kv[0][1]
            ):
                if schema == relation.schema and kind.partition_of == parent:
                    children.append((name, kind))
                    collect(name)

        collect(relation.table.name)
        if not children:
            return []

        def quote(name: str) -> str:
            return '"' + name.replace('"', '""') + '"'

        def qualified(name: str) -> str:
            return (
                quote(name)
                if relation.schema is None
                else f"{quote(relation.schema)}.{quote(name)}"
            )

        constant = self.name_of(relation)
        lines = []
        for name, kind in children:
            parent = qualified(str(kind.partition_of))
            ddl = f"CREATE TABLE {qualified(name)} PARTITION OF {parent} {kind.partition_bound}"
            if kind.partition_by is not None:
                ddl += f" PARTITION BY {kind.partition_by}"
            # DDL() formats its text with % (`%(table)s`): a % of the SQL's own is said twice.
            escaped = q(ddl.replace("%", "%%"))
            lines.append(
                f'sqlalchemy.event.listen({constant}, "after_create", sqlalchemy.DDL({escaped}))'
            )
        self.notes.append(
            f"{relation.key} is partitioned ({', '.join(n for n, _ in children)}): the "
            "partitions are not written as tables - query them through it - and create_all "
            "creates them after it, with the indexes and keys they take from it"
        )
        return lines

    def table_options(self, relation: Relation) -> list[str]:
        """What the table says about itself beyond its columns and keys, as ``Table`` keywords."""
        options: list[str] = []
        kind = self.reflected.relation_kinds.get((relation.schema, relation.table.name))
        if kind is not None and not relation.is_view:
            if kind.partition_by is not None:
                options.append(f"postgresql_partition_by={q(kind.partition_by)}")
            if kind.inherits:
                parents = [q(p) for p in kind.inherits]
                options.append(
                    f"postgresql_inherits={parents[0]}"
                    if len(parents) == 1
                    else f"postgresql_inherits=({', '.join(parents)},)"
                )
            if kind.unlogged:
                options.append('prefixes=["UNLOGGED"]')
        dialect = self.reflected.dialect_name
        if dialect in ("mysql", "mariadb") and not relation.is_view:
            # ENGINE, DEFAULT CHARSET, COLLATE, ROW_FORMAT...: a table's character set is its
            # columns' unless they say their own, so without it a latin1 table's text would be
            # built utf8mb4. The comment is written as `comment=`; AUTO_INCREMENT's counter is
            # data, not schema. Partitioning is applied after the table is created (see
            # `partitions`): reflection reads it in pieces, and SQLAlchemy writes them wrong.
            read: set[str] = set()
            for option, value in sorted(relation.table.dialect_kwargs.items()):
                name = option.removeprefix(f"{dialect}_")
                read.add(name.replace(" ", "_").lower())
                if value is None or name in ("comment", "auto_increment") or "partition" in name:
                    continue
                if (
                    name == "checksum"
                    and str(relation.table.dialect_kwargs.get(f"{dialect}_engine")).upper()
                    == "ARIA"
                ):
                    continue  # Aria's PAGE_CHECKSUM, which reflection reads as CHECKSUM
                if not option.startswith(f"{dialect}_"):
                    continue
                options.append(f"{dialect}_{name.replace(' ', '_')}={lit(str(value))}")
            text_of = self.reflected.mysql_tables.get((relation.schema, relation.table.name))
            if text_of is not None:
                # What the table's text says that reflection does not read - STATS_PERSISTENT,
                # STATS_AUTO_RECALC, PAGE_CHECKSUM... - written as the server prints it.
                for option, value in text_of.options:
                    name = option.replace(" ", "_").lower()
                    if name in read or name in ("comment", "auto_increment"):
                        continue
                    options.append(f"{dialect}_{name}={lit(value)}")
                if text_of.unread:
                    self.notes.append(
                        f"{relation.key}: table options schemagen cannot read, not written - "
                        f"create_all builds the table without them: {text_of.unread}"
                    )
        found = self.reflected.sqlite_tables.get((relation.schema, relation.table.name))
        if found is not None:
            if found.autoincrement:
                options.append("sqlite_autoincrement=True")
            if found.without_rowid:
                options.append("sqlite_with_rowid=False")
            if found.strict:
                options.append("sqlite_strict=True")
        return options

    def record_of(self, relation: Relation) -> str:
        base = (
            relation.table.name
            if relation.schema is None
            else f"{relation.schema}_{relation.table.name}"
        )
        return record_name(base)

    def _rowid_alias(self, relation: Relation, column: Column[Any]) -> bool:
        """Whether the column is SQLite's ``INTEGER PRIMARY KEY``: the rowid under another name,
        which SQLite fills when an insert leaves it out."""
        found = self.reflected.sqlite_tables.get((relation.schema, relation.table.name))
        return found is not None and found.rowid_alias == column.name

    def _filled_by_database(self, column: Column[Any], relation: Relation | None = None) -> bool:
        """Whether an insert may leave the column out: the database fills it."""
        if column.computed is not None or column.identity is not None:
            return True
        if relation is not None and self._rowid_alias(relation, column):
            return True
        if column.autoincrement is True:
            return True
        if column.server_default is None:
            return False
        arg = getattr(column.server_default, "arg", None)
        sql = arg.text if isinstance(arg, TextClause) else arg
        # MariaDB reports a default of NULL for every nullable column; it is no default.
        return not (isinstance(sql, str) and sql.strip().upper() == "NULL")

    def _custom_pk_name(self, relation: Relation) -> bool:
        """Whether the key must be written apart: named other than by default, or DEFERRABLE."""
        pk = relation.table.primary_key
        if pk is None or not pk.name:
            return False
        if self._deferrable(relation, str(pk.name)):
            return True
        return pk.name not in (f"{relation.table.name}_pkey", "PRIMARY")

    def _deferrable(self, relation: Relation, name: str) -> str:
        """``, deferrable=True, initially="DEFERRED"`` for a DEFERRABLE key, else nothing."""
        found = self.reflected.deferrable.get((relation.schema, relation.table.name, name))
        if found is None:
            return ""
        return ", deferrable=True" + (', initially="DEFERRED"' if found[1] else "")

    def constraint(self, relation: Relation, constraint: Any, generated: set[str]) -> str | None:
        named = f", name={q(constraint.name)}" if isinstance(constraint.name, str) else ""
        if isinstance(constraint, UniqueConstraint) and constraint.columns:
            keys = ", ".join(q(c.name) for c in constraint.columns)
            options = "".join(
                f", {option}={lit(value)}"
                for option, value in sorted(constraint.dialect_kwargs.items())
                if value not in (None, False, [], {}, ())
            )
            later = self._deferrable(relation, str(constraint.name))
            return f"sqlalchemy.UniqueConstraint({keys}{named}{later}{options})"
        if isinstance(constraint, CheckConstraint):
            return f"sqlalchemy.CheckConstraint({q(str(constraint.sqltext))}{named})"
        if isinstance(constraint, ForeignKeyConstraint):
            targets = [e.target_fullname for e in constraint.elements]
            referred = targets[0].rsplit(".", 1)[0]
            if referred not in generated:
                key = constraint.name or constraint.column_keys
                bare = referred.rsplit(".", 1)[-1]
                if "." in referred and bare in generated:
                    # MySQL and MariaDB name the connection's own database; PostgreSQL does not.
                    # Unqualified, the key would mean the listed database's table; qualified,
                    # the module would carry the database's name. Neither is right.
                    self.notes.append(
                        f"{relation.key}: foreign key {key} refers to {referred}, a table in the "
                        f"connection's own database, which this module writes without that "
                        f"database's name; a key across databases cannot, so it is left out"
                    )
                else:
                    self.notes.append(
                        f"{relation.key}: foreign key {key} refers to {referred}, which is not "
                        f"generated; left out"
                    )
                return None
            said = {
                option: getattr(constraint, option)
                for option in ("ondelete", "onupdate", "deferrable", "initially", "match")
            }
            sqlite_table = self.reflected.sqlite_tables.get((relation.schema, relation.table.name))
            if sqlite_table is not None:
                # SQLite's reflection misses a column's own REFERENCES actions and a DEFERRABLE
                # on a line of its own: the actions are PRAGMA foreign_key_list's, DEFERRABLE
                # the table's text.
                columns = tuple(constraint.column_keys)
                text_of = next((k for k in sqlite_table.foreign_keys if k.columns == columns), None)
                if text_of is not None:
                    said["ondelete"] = text_of.ondelete
                    said["onupdate"] = text_of.onupdate
                    said["deferrable"] = True if text_of.deferrable else None
                    said["initially"] = "DEFERRED" if text_of.initially_deferred else None
            options = "".join(
                f", {option}={lit(value)}" for option, value in said.items() if value is not None
            )
            local = ", ".join(q(c) for c in constraint.column_keys)
            remote = ", ".join(q(t) for t in targets)
            return f"sqlalchemy.ForeignKeyConstraint([{local}], [{remote}]{named}{options})"
        return None

    def sequence_args(self, spec: Any) -> list[str]:
        """A sequence's options as ``Sequence`` keywords: all but the ones PostgreSQL (or
        MariaDB) would choose by itself."""
        kind = spec.data_type or "bigint"
        found = sequence_options(
            kind,
            start=spec.start,
            increment=spec.increment,
            minvalue=spec.minvalue,
            maxvalue=spec.maxvalue,
            cycle=spec.cycle,
            cache=spec.cache,
        )
        if kind in ("smallint", "integer"):
            found.append(f"data_type=sqlalchemy.{kind.upper()}()")
        return found

    def domain(self, where: str, domain_type: Any, relation: Relation) -> Emitted | None:
        """A PostgreSQL domain, all of it: its base type with its length, NOT NULL, default,
        collation and checks - so create_all on an empty database builds the same domain."""
        name, schema = domain_type.name, getattr(domain_type, "schema", None)
        candidates = sorted(
            (d for (s, n), d in self.reflected.domains.items() if n == name),
            key=lambda d: (d.schema != (schema or relation.schema_name), d.schema),
        )
        if not candidates:
            return None
        spec = candidates[0]
        base: Any = domain_type.data_type
        if self.dialect is not None and hasattr(self.dialect, "_reflect_type"):
            try:
                found = self.dialect._reflect_type(spec.base, {}, {}, f"DOMAIN {name}", None)
            except Exception:  # a domain over another domain or an enum: reflection's own
                found = None
            if found is not None and not isinstance(found, sqltypes.NullType):
                base = found
        inner = emit_type(base, self.reflected.dialect_name)
        if inner is None:
            inner = Emitted(
                f"sql_type({lit(spec.base)})", frozenset({("ekbatan.schema", "sql_type")})
            )
        args = [q(name), self.use(inner)]
        if spec.collation is not None:
            args.append(f"collation={q(spec.collation)}")
        if spec.default is not None:
            args.append(f"default=sqlalchemy.text({q(spec.default)})")
        if len(spec.checks) == 1 and spec.not_null:
            # SQLAlchemy writes `CONSTRAINT name` before `NOT NULL`, which names the NOT NULL
            # and not the check: the check keeps its meaning and loses its name.
            args.append(f"check=sqlalchemy.text({q(spec.checks[0][1])})")
            self.notes.append(
                f"{where}: domain {name} is NOT NULL with check {spec.checks[0][0]}; SQLAlchemy "
                "cannot name a check beside NOT NULL, so create_all names it itself"
            )
        elif len(spec.checks) == 1:
            args.append(f"constraint_name={q(spec.checks[0][0])}")
            args.append(f"check=sqlalchemy.text({q(spec.checks[0][1])})")
        elif spec.checks:
            args.append(
                f"check=sqlalchemy.text({q(' AND '.join(body for _, body in spec.checks))})"
            )
            self.notes.append(
                f"{where}: domain {name} has {len(spec.checks)} checks "
                f"({', '.join(n for n, _ in spec.checks)}); SQLAlchemy's DOMAIN declares one, "
                "so they are written as one, joined by AND, without their names"
            )
        if spec.not_null:
            args.append("not_null=True")
        if schema is not None:
            args.append(f"schema={q(schema)}")
        return Emitted(f"postgresql.DOMAIN({', '.join(args)})")

    def exclusion(self, relation: Relation, exclusion: Any) -> str:
        """A PostgreSQL ``EXCLUDE`` constraint, as ``postgresql.ExcludeConstraint``."""
        self.modules.add("postgresql")
        columns = {c.name for c in relation.table.columns}
        elements = []
        for expression, operator in exclusion.elements:
            name = _identifier(expression)
            element = q(name) if name in columns else f"sqlalchemy.literal_column({q(expression)})"
            elements.append(f"({element}, {q(operator)})")
        options = [f"name={q(exclusion.name)}", f"using={q(exclusion.using)}"]
        if exclusion.where is not None:
            options.append(f"where=sqlalchemy.text({q(exclusion.where)})")
        if exclusion.deferrable:
            options.append("deferrable=True")
            if exclusion.initially_deferred:
                options.append('initially="DEFERRED"')
        return f"postgresql.ExcludeConstraint({', '.join(elements + options)})"

    def index(
        self,
        index: Index,
        *,
        inline: bool,
        table: str | None = None,
        relation: Relation | None = None,
    ) -> str:
        parts = [q(str(index.name))]
        options = {k: v for k, v in index.dialect_kwargs.items() if v not in (None, [], {}, ())}
        elements = self.index_elements(relation, index) if relation is not None else None
        if elements is None:
            for expression in index.expressions:
                if isinstance(expression, Column):
                    parts.append(
                        q(expression.name) if inline else f"{table}.c[{q(expression.name)}]"
                    )
                else:
                    parts.append(f"sqlalchemy.text({q(str(expression))})")
        else:
            plain: set[str] = set()
            for column, element in elements:
                if column is not None:
                    plain.add(column)
                    parts.append(q(column))
                else:
                    parts.append(f"sqlalchemy.text({q(element)})")
            # What reflection said per column - an op class, a prefix length - is in the text
            # of an element written as text; said again beside it, it would be said twice.
            for option in ("postgresql_ops", "mysql_length", "mariadb_length"):
                if isinstance(options.get(option), dict):
                    kept = {k: v for k, v in options[option].items() if k in plain}
                    if kept:
                        options[option] = kept
                    else:
                        del options[option]
        if index.unique:
            parts.append("unique=True")
        for option, value in sorted(options.items()):
            if option.endswith("_where"):
                parts.append(f"{option}=sqlalchemy.text({q(str(value))})")
            else:
                parts.append(f"{option}={lit(value)}")
        return f"sqlalchemy.Index({', '.join(parts)})"

    def index_elements(
        self, relation: Relation, index: Index
    ) -> list[tuple[str | None, str]] | None:
        """The index's key elements, each a column's name where a plain name says all the
        database does, else the element's text as the database writes it. ``None`` when the
        database's text is not known or says nothing reflection did not."""
        key = (relation.schema, relation.table.name, str(index.name))
        elements = self.reflected.index_elements.get(key)
        if elements is None:
            return None
        found = [(self.plain_column(relation, index, e), e) for e in elements]
        reflected = [e.name if isinstance(e, Column) else None for e in index.expressions]
        if None not in [c for c, _ in found] and [c for c, _ in found] == reflected:
            return None  # every element a plain column, as reflection already says
        return found

    def plain_column(self, relation: Relation, index: Index, element: str) -> str | None:
        """The column an index element names and says nothing more about - beyond an op class
        or a prefix length reflection already reads - else ``None``."""
        found = re.fullmatch(
            r'(?:"((?:[^"]|"")+)"|`((?:[^`]|``)+)`|\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_$]*))'
            r"(?:\((\d+)\))?(?:\s+(.*))?",
            element.strip(),
            re.S,
        )
        if found is None:
            return None
        quoted, ticked, bracketed, bare, length, rest = found.groups()
        name = (
            quoted.replace('""', '"')
            if quoted is not None
            else ticked.replace("``", "`")
            if ticked is not None
            else bracketed
            if bracketed is not None
            else bare
        )
        columns = {c.name: c.name for c in relation.table.columns}
        if name not in columns and self.reflected.dialect_name in ("sqlite", "mysql", "mariadb"):
            # Neither SQLite nor MySQL tells case apart in a column's name.
            columns = {c.name.lower(): c.name for c in relation.table.columns}
            name = name.lower()
        column = columns.get(name)
        if column is None:
            return None
        options = index.dialect_kwargs
        if length is not None:
            lengths = options.get(f"{self.reflected.dialect_name}_length") or {}
            if not isinstance(lengths, dict) or str(lengths.get(column)) != length:
                return None
        rest = (rest or "").strip()
        if not rest or rest.upper() == "ASC":
            return column
        ops = options.get("postgresql_ops") or {}
        if isinstance(ops, dict) and ops.get(column) == rest:
            return column
        return None

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
        self.records[relation.key] = (
            self.record_of(relation),
            [
                Field(
                    c.name,
                    self.python_type_of(f"{relation.key}.{c.name}", c.type),
                    nullable=bool(c.nullable),
                    optional=self._filled_by_database(c),
                )
                for c in built.columns
            ],
        )
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
            if self.index_elements(relation, index):
                self.notes.append(
                    f"{relation.key}: index {index.name} orders or collates a column, which an "
                    "index beside a framework table is not written with; left out"
                )
                continue
            lines.append(self.index(index, inline=False, table=name))
        known = {i.name for i in relation.table.indexes}
        for spec in self.reflected.extra_indexes.get((relation.schema, relation.table.name), []):
            if spec.name not in known and spec.name not in taken:
                self.notes.append(
                    f"{relation.key}: index {spec.name} is on an expression; left out"
                )
        return lines


#: MySQL's ways of saying the current time in a default or an ``ON UPDATE``, at any precision.
_NOW = re.compile(
    r"\b(?:current_timestamp|now|localtimestamp|localtime)\b(?:\s*\(\s*\d*\s*\))?", re.I
)


#: The range of each PostgreSQL integer type: what a sequence or identity of it defaults to.
_INTEGER_RANGE = {
    "smallint": (-32768, 32767),
    "integer": (-2147483648, 2147483647),
    "bigint": (-9223372036854775808, 9223372036854775807),
}


def _integer_kind(column_type: Any) -> str:
    if isinstance(column_type, sqltypes.SmallInteger):
        return "smallint"
    if isinstance(column_type, sqltypes.BigInteger):
        return "bigint"
    return "integer"


def sequence_options(
    kind: str,
    *,
    start: int | None,
    increment: int | None,
    minvalue: int | None,
    maxvalue: int | None,
    cycle: bool | None,
    cache: int | None,
) -> list[str]:
    """A sequence's or identity's options, as keywords - those PostgreSQL would not choose by
    itself for a ``kind`` (``integer``...) sequence, so ``Identity()`` stays ``Identity()``."""
    low, high = _INTEGER_RANGE.get(kind, _INTEGER_RANGE["bigint"])
    step = increment if increment is not None else 1
    least = 1 if step > 0 else low
    most = high if step > 0 else -1
    first = minvalue if step > 0 else maxvalue
    found: list[str] = []
    for name, value, default in (
        ("start", start, first if first is not None else (least if step > 0 else most)),
        ("increment", increment, 1),
        ("minvalue", minvalue, least),
        ("maxvalue", maxvalue, most),
        ("cycle", cycle, False),
        ("cache", cache, 1),
    ):
        if value is not None and value != default:
            found.append(f"{name}={value!r}")
    return found


def _identifier(text_: str) -> str | None:
    """The name a piece of SQL is, when it is no more than a name."""
    found = re.fullmatch(r'"((?:[^"]|"")+)"|([A-Za-z_][A-Za-z0-9_$]*)', text_.strip())
    if found is None:
        return None
    return found.group(1).replace('""', '"') if found.group(1) is not None else found.group(2)


def _sqlite_named(raw: str) -> bool:
    """Whether SQLAlchemy reads a SQLite type by its name, not only by its affinity."""
    from sqlalchemy.dialects.sqlite.base import ischema_names

    found = re.match(r"\s*([A-Za-z_ ]+?)\s*(\(|$)", raw)
    return found is not None and found.group(1).upper() in ischema_names


def _reserved() -> set[str]:
    """Names a user type is not imported under: SQLAlchemy's - which the module writes through
    ``sqlalchemy.``, but a reader would take a bare ``JSON`` for it - the modules, the helpers',
    and the module's own."""
    from ._types import HELPERS, sqlalchemy_exports

    return (
        set(sqlalchemy_exports())
        | set(MODULES)
        | set(HELPERS)
        | set(FRAMEWORK_TABLES.values())
        | {
            "converted",
            "sql_type",
            "metadata",
            "view_metadata",
            "check_fingerprint",
            "MIGRATIONS_FINGERPRINT",
            "SCHEMA_FORMAT",
        }
    )


#: What to say of what the database holds and no module can write, by its kind.
_UNWRITTEN = {
    "function": "call one with sqlalchemy.func.<name>(...); create_all does not create it",
    "procedure": "call one with a CALL statement; create_all does not create it",
    "aggregate": "create_all does not create it",
    "window function": "create_all does not create it",
    "trigger": "create_all does not create it - a test that needs one runs the migrations",
    "event": "create_all does not create it - a test that needs one runs the migrations",
    "composite type": (
        "a column of one is written as the database spells its type, and create_all cannot "
        "create the type - make it first (CREATE TYPE ...), or run the migrations"
    ),
    "extension": "create_all does not create one - run CREATE EXTENSION before it",
    "collation": (
        "a column using one is written COLLATE it, and create_all fails until it exists - "
        "create it first (CREATE COLLATION ...), or run the migrations"
    ),
    "table with row level security": (
        "create_all builds them without it: a test that needs it runs the migrations"
    ),
    "policy": "create_all builds the table without it - a test that needs one runs the migrations",
    "rule": "create_all builds the table without it - a test that needs one runs the migrations",
    "extended statistics": "create_all does not create them; they only guide the planner",
    "index comment": "create_all builds the index without it",
    "table in a tablespace of its own": "create_all builds it in the default tablespace",
    "publication": "create_all does not create one: replication is set up apart from the schema",
}

#: The plural of a kind where adding "s" is wrong.
_PLURALS = {
    "table with row level security": "tables with row level security",
    "extended statistics": "extended statistics",
    "table in a tablespace of its own": "tables in a tablespace of their own",
}


def _plural(kind: str) -> str:
    if kind in _PLURALS:
        return _PLURALS[kind]
    return kind[:-1] + "ies" if kind.endswith("y") else kind + ("es" if kind.endswith("x") else "s")


def _unwritten(reflected: Reflected) -> list[str]:
    return [
        f"the database has {_plural(kind)} schemagen does not write: "
        f"{', '.join(sorted(names))}. "
        f"{_UNWRITTEN.get(kind, 'create_all does not create them')}"
        for kind, names in sorted(reflected.unwritten.items())
    ]


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

    # Each table's record, `WalletsRecord` - named before any table is written, so that a user
    # type or converter that happens to share the name is imported under an alias instead.
    records: dict[str, Relation] = {}
    for relation in relations:
        record = writer.record_of(relation)
        if record in records:
            raise ValidationError(
                f"{relation.key} and {records[record].key} would both have the record "
                f"{record}; exclude one with `excludes`"
            )
        records[record] = relation
    writer.record_names = set(records)
    writer.constants = set(names)
    exported_sequences: list[str] = []

    sequences: list[str] = []
    for (schema, name), spec in sorted(
        reflected.sequences.items(), key=lambda kv: (kv[0][0] or "", kv[0][1])
    ):
        if spec.owner is not None:
            continue  # a SERIAL's: written on its column
        constant_name = constant(name if schema is None else f"{schema}_{name}")
        if constant_name in names or constant_name in ("metadata", "view_metadata"):
            raise ValidationError(
                f"the sequence {name} and "
                f"{names[constant_name].key if constant_name in names else constant_name} "
                f"would both be exported as {constant_name}; exclude the table with `excludes`"
            )
        writer.constants.add(constant_name)
        args = [q(name), *writer.sequence_args(spec)]
        if schema is not None:
            args.append(f"schema={q(schema)}")
        args.append("metadata=metadata")
        sequences.append(f"{constant_name} = sqlalchemy.Sequence({', '.join(args)})")
        exported_sequences.append(constant_name)

    generated = {r.key for r in reflected.tables} | {r.key for r in reflected.framework}
    writer.generated = generated
    tables: list[tuple[Relation, str]] = []
    for relation in reflected.tables:
        tables += [(relation, t) for t in writer.table(relation, generated)]
    for relation in reflected.framework:
        tables.append((relation, "\n".join(writer.framework(relation))))
    view_tables: list[tuple[Relation, str]] = []
    for relation in reflected.views:
        view_tables += [(relation, t) for t in writer.table(relation, generated)]

    # The records last, once every name the tables import is known.
    def with_record(relation: Relation, text: str) -> str:
        record, fields = writer.records[relation.key]
        return f"{text}\n\n\n{render_record(record, relation.key, fields, writer.type_name)}"

    body = [with_record(r, t) for r, t in tables]
    # Inheritance is not a foreign key, so create_all would not know to make the parent first.
    dependencies = []
    for relation in reflected.tables:
        kind = reflected.relation_kinds.get((relation.schema, relation.table.name))
        for parent in kind.inherits if kind is not None else []:
            key = parent if relation.schema is None else f"{relation.schema}.{parent}"
            found = next((r for r in reflected.tables if r.key == key), None)
            if found is None:
                writer.notes.append(
                    f"{relation.key} inherits from {parent}, which is not generated: create_all "
                    "cannot build it without"
                )
                continue
            dependencies.append(
                f"{writer.name_of(relation)}.add_is_dependent_on({writer.name_of(found)})"
            )
    for relation, owner in writer.after:
        found = next((r for r in reflected.tables if r.key == owner), None)
        if found is not None and found is not relation:
            dependencies.append(
                f"{writer.name_of(relation)}.add_is_dependent_on({writer.name_of(found)})"
            )
    if dependencies:
        body.append("\n".join(dependencies))
    if sequences:
        body.insert(0, "\n".join(sequences))
    writer.notes += _unwritten(reflected)
    views = [with_record(r, t) for r, t in view_tables]

    exported = [*sorted([*names, *exported_sequences]), *sorted(records), "metadata"] + (
        ["view_metadata"] if views else []
    )
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
    dialects = sorted(writer.modules - {"sqlalchemy"})

    # Every SQLAlchemy name is written through its module: a table constant is the table's name
    # in capitals, and would replace a bare `TEXT` imported for a table named `text`.
    imports = ["import sqlalchemy"]
    if dialects:
        imports.append(f"from sqlalchemy.dialects import {', '.join(dialects)}")
    if writer.helpers:
        imports.append(f"from ekbatan.schema import {', '.join(sorted(writer.helpers))}")
    for (source, name), alias in sorted(writer.aliases.items()):
        imports.append(f"from {source} import {name}" + (f" as {alias}" if alias != name else ""))
    by_module: dict[str, list[str]] = {}
    for (source, name), alias in sorted(writer.type_aliases.items()):
        by_module.setdefault(source, []).append(name + (f" as {alias}" if alias != name else ""))
    for source, names in sorted(by_module.items()):
        imports.append(f"from {source} import {', '.join(names)}")
    imports.append("from ekbatan.schemagen import check_fingerprint")

    def joined(blocks: Iterable[str]) -> str:
        return "".join(f"{b}\n\n\n" for b in blocks)

    view_part = ""
    if views:
        view_part = (
            "#: The views. Queried like tables; kept out of `metadata`, so that `create_all` does\n"
            "#: not create a table by a view's name. Views come from the migrations.\n"
            f"view_metadata = sqlalchemy.MetaData()\n\n{joined(views)}"
        )
    text = f'''"""The tables, generated from the schema the migrations produce.

Generated by `python -m ekbatan.schemagen`. **Do not edit.** Change a migration and regenerate;
the fingerprint below makes a stale copy of this file fail loudly rather than disagree quietly
with the database it claims to describe.

Each table has its record beside it, `WALLETS` and `WalletsRecord`: a `TypedDict` with a key
per column, typed as the column reads - a converter's column as the converter's own type. It
is what a repository's `to_record` returns and `from_record` takes, and to SQLAlchemy it is a
plain `dict`.

The framework's own tables are built from `ekbatan.schema` rather than reflected, because their
shape is a contract a CDC connector and other ports bind to - there is one definition to be
right about, and it is not this file.
"""

from __future__ import annotations

{chr(10).join(imports)}

#: A hash of the migrations this file was generated from.
MIGRATIONS_FINGERPRINT = "{migrations_fingerprint}"
#: The shape of this file, as the schemagen that wrote it numbers it.
SCHEMA_FORMAT = {SCHEMA_FORMAT}

metadata = sqlalchemy.MetaData()

{joined(body)}{view_part}__all__ = [{", ".join(q(e) for e in exported)}]

# Nothing in Python runs before `python -m yourapp`, so this is where a stale schema is caught.
# Hashing the migration files costs microseconds and needs no database.
check_fingerprint(__file__, MIGRATIONS_FINGERPRINT, {lit(module)}, SCHEMA_FORMAT)
'''
    return text
