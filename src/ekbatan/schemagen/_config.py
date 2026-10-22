"""What a project can set: where things are, which tables, and which types are forced.

Java keeps these in the build script - the `generateJooqClasses` block, `database.withForcedTypes`.
A Python project keeps its build-time settings in `pyproject.toml`, under `[tool.<name>]`, so
schemagen reads `[tool.ekbatan.schemagen]` there; a command-line flag wins over the file.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from ..errors import ValidationError

__all__ = ["DEFAULT_FORCED_TYPES", "ForcedType", "SchemagenConfig", "load_config"]

Nullability = Literal["ALL", "NULL", "NOT_NULL"]


@dataclass(frozen=True)
class ForcedType:
    """One forced type, as jOOQ's ``ForcedType``: which columns, and what they become.

    What they become - exactly one of:

    - ``name``: a time kind (``Instant``, ``LocalDateTime``, ``OffsetDateTime`` - the label the
      column's record gets, see :mod:`ekbatan.time`), one of the framework's helpers
      (``uuid_column``, ``json_column``, ``timestamp_column``) or a SQLAlchemy type, written as
      in Python - ``Text``, ``String(36)``, ``Numeric(19, 4)``, ``postgresql.INET``. jOOQ's
      ``withName``.
    - ``converter``: your own converter, by dotted path - ``myapp.converters.MoneyConverter``
      or ``myapp.converters:MoneyConverter``, with arguments if it takes any. Any object with
      ``from_db`` and ``to_db``. The column keeps its own database type and the converter is
      wrapped around it: ``converted(sqlalchemy.NUMERIC(precision=19, scale=4),
      MoneyConverter())``.
      jOOQ's ``userType`` + ``converter``.
    - ``sqlalchemy_type``: a ready-made SQLAlchemy type, by dotted path -
      ``pgvector.sqlalchemy.Vector(3)``. A ``TypeEngine``, a ``TypeDecorator``, or a function
      returning one.

    Which columns - regular expressions that must match the **whole** text, as jOOQ's do:

    - ``include_expression`` against the column's name, as ``column``, ``table.column`` and,
      for a schema named in the configuration, ``schema.table.column``. Case-sensitive.
    - ``include_types`` against the column's SQL type, as the database spells it and as
      SQLAlchemy does - ``CHAR(36)`` and ``CHAR``, ``TIMESTAMP(3)`` and ``TIMESTAMP``. Not
      case-sensitive: a type has no case.
    - ``exclude_expression`` and ``exclude_types`` take matches back out; ``nullability``
      limits it to ``NULL`` or ``NOT_NULL`` columns.

    At least one include is required. A forced type with neither would force every column, which
    is never what anybody meant.
    """

    name: str | None = None
    converter: str | None = None
    sqlalchemy_type: str | None = None
    include_expression: str | None = None
    include_types: str | None = None
    exclude_expression: str | None = None
    exclude_types: str | None = None
    nullability: Nullability = "ALL"
    #: Only when reading these databases; empty means every one. Used by the built-in defaults,
    #: which differ per database exactly as Java's build scripts do.
    dialects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for label in (
            "name",
            "converter",
            "sqlalchemy_type",
            "include_expression",
            "include_types",
            "exclude_expression",
            "exclude_types",
        ):
            value = getattr(self, label)
            if isinstance(value, str) and not value.strip():
                # `include-expression = ""` matches no column at all, silently; an empty target
                # is no target. Leave the key out instead.
                raise ValidationError(
                    f"a forced type has an empty {label.replace('_', '-')}; leave the key out, "
                    "or give it a value"
                )
        given = [t for t in (self.name, self.converter, self.sqlalchemy_type) if t is not None]
        if len(given) != 1:
            raise ValidationError(
                "a forced type needs exactly one of name, converter and sqlalchemy-type, got "
                + (", ".join(self._targets()) or "none")
            )
        if self.include_expression is None and self.include_types is None:
            raise ValidationError(
                f"the forced type {self._what()} needs include-expression or include-types: "
                "with neither it would force every column"
            )
        for label in ("include_expression", "include_types", "exclude_expression", "exclude_types"):
            pattern = getattr(self, label)
            if pattern is None:
                continue
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValidationError(
                    f"the forced type {self._what()} has a bad {label.replace('_', '-')} "
                    f"{pattern!r}: {error}"
                ) from None
        if self.nullability not in ("ALL", "NULL", "NOT_NULL"):
            raise ValidationError(
                f"the forced type {self._what()} has nullability {self.nullability!r}; "
                "use ALL, NULL or NOT_NULL"
            )

    def _targets(self) -> list[str]:
        return [
            f"{key}={value!r}"
            for key, value in (
                ("name", self.name),
                ("converter", self.converter),
                ("sqlalchemy-type", self.sqlalchemy_type),
            )
            if value is not None
        ]

    def _what(self) -> str:
        return ", ".join(self._targets())

    @property
    def target(self) -> str:
        """What the columns become, as written."""
        return str(self.name or self.converter or self.sqlalchemy_type)

    def matches(
        self,
        *,
        dialect: str | None,
        names: Sequence[str],
        types: Sequence[str],
        nullable: bool,
    ) -> bool:
        """Whether a column of these names and SQL types is one this forced type takes."""
        if self.dialects and dialect not in self.dialects:
            return False
        if self.nullability == "NULL" and not nullable:
            return False
        if self.nullability == "NOT_NULL" and nullable:
            return False
        if self.include_expression is not None and not _any(self.include_expression, names):
            return False
        if self.include_types is not None and not _any(self.include_types, types, re.I):
            return False
        if self.exclude_expression is not None and _any(self.exclude_expression, names):
            return False
        return not (self.exclude_types is not None and _any(self.exclude_types, types, re.I))


def _any(pattern: str, candidates: Sequence[str], flags: int = 0) -> bool:
    return any(re.fullmatch(pattern, c, flags) is not None for c in candidates)


#: What Ekbatan forces when you force nothing - the forced types every Java example's build
#: script carries, with the converters' work done by the framework's column helpers.
#:
#: - every zone-less timestamp to ``Instant`` - a ``timestamp_column()`` whose record says
#:   :data:`ekbatan.time.Instant` - Java's ``Instant`` + ``InstantConverter`` on ``TIMESTAMP``
#:   (PG) and ``(?i:DATETIME|TIMESTAMP)`` (MySQL, MariaDB). A wall-clock column is forced to
#:   ``LocalDateTime`` by a rule of the project's own, which is tried first.
#: - PostgreSQL's ``TIMESTAMPTZ`` to ``OffsetDateTime``, as jOOQ generates it: the column keeps
#:   its own type and precision, and its record says :data:`ekbatan.time.OffsetDateTime`.
#: - JSON to ``json_column()`` - Java's ``ObjectNode`` on ``JSONB`` (PG) and ``JSON`` (MySQL,
#:   MariaDB). A plain PostgreSQL ``JSON`` is not forced, as in Java.
#: - a PostgreSQL ``UUID`` to ``uuid_column()``, so the same schema renders ``CHAR(36)`` - not
#:   SQLAlchemy's ``CHAR(32)`` - if it is ever used on MySQL.
#:
#: Not here: a ``CHAR(36)`` uuid on MySQL or MariaDB. Nothing in those databases says a
#: ``CHAR(36)`` holds a uuid, so it stays text unless a project says so with its own forced
#: type - as each Java MySQL build adds ``UuidStringConverter`` itself.
DEFAULT_FORCED_TYPES: tuple[ForcedType, ...] = (
    ForcedType(name="Instant", include_types="TIMESTAMP|DATETIME"),
    ForcedType(name="OffsetDateTime", include_types="TIMESTAMPTZ", dialects=("postgresql",)),
    ForcedType(name="json_column", include_types="JSONB", dialects=("postgresql",)),
    ForcedType(name="json_column", include_types="JSON", dialects=("mysql", "mariadb", "sqlite")),
    ForcedType(name="uuid_column", include_types="UUID", dialects=("postgresql",)),
)

DEFAULT_IMAGE = "postgres:17-alpine"


@dataclass(frozen=True)
class SchemagenConfig:
    """Everything one run of schemagen is told, from the file, the flags, or a caller.

    ``schemas`` names the schemas to read - PostgreSQL schemas, or MySQL and MariaDB databases.
    The first is written unqualified, as jOOQ's ``outputSchemaToDefault``, so the same module
    works whatever the database is called; the others are qualified. Empty means the
    connection's own schema only.

    ``includes`` and ``excludes`` are regular expressions over table names (and
    ``schema.table``), whole-text, as jOOQ's are. The migration history tables are always left
    out, as ``includeFlywayTable(false)`` leaves Flyway's.
    """

    migrations: Path | None = None
    out: Path | None = None
    url: str | None = field(default=None, repr=False)
    image: str = DEFAULT_IMAGE
    schemas: tuple[str, ...] = ()
    includes: str = ".*"
    excludes: str | None = None
    views: bool = True
    forced_types: tuple[ForcedType, ...] = ()
    default_forced_types: bool = True

    def __post_init__(self) -> None:
        # Empty is never "the default", however it is given - the file, a flag, a caller: it
        # used to be read as no setting at all, or as a pattern matching nothing (`--include=`).
        for label in ("includes", "excludes", "image", "url"):
            value = getattr(self, label)
            if isinstance(value, str) and not value.strip():
                raise ValidationError(
                    f"{label} is empty; leave it out to use the default, or give it a value"
                )
        if any(not isinstance(s, str) or not s.strip() for s in self.schemas):
            raise ValidationError(
                "schemas is empty or holds an empty name; leave it out to read the "
                "connection's own schema only, or name each schema"
            )
        for label in ("includes", "excludes"):
            pattern = getattr(self, label)
            if pattern is None:
                continue
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValidationError(
                    f"{label} {pattern!r} is not a regular expression: {error}"
                ) from None

    @property
    def all_forced_types(self) -> tuple[ForcedType, ...]:
        """In the order they are tried: yours, then Ekbatan's. The first that matches wins."""
        return self.forced_types + (DEFAULT_FORCED_TYPES if self.default_forced_types else ())

    def with_overrides(self, **overrides: Any) -> SchemagenConfig:
        """This config with every override that is not ``None`` applied - the command line's."""
        return replace(self, **{k: v for k, v in overrides.items() if v is not None})


#: The keys `[tool.ekbatan.schemagen]` accepts. Anything else is refused, so a typo is an error
#: rather than a setting that silently does nothing.
_KEYS = {
    "migrations",
    "out",
    "url",
    "image",
    "schemas",
    "includes",
    "excludes",
    "views",
    "forced-types",
    "default-forced-types",
}
_FORCED_KEYS = {
    "name",
    "converter",
    "sqlalchemy-type",
    "include-expression",
    "include-types",
    "exclude-expression",
    "exclude-types",
    "nullability",
}


def load_config(path: Path, *, required: bool = True) -> SchemagenConfig | None:
    """`[tool.ekbatan.schemagen]` from a TOML file - a ``pyproject.toml``, usually.

    Paths in it are relative to the file, so the command works from any directory. ``None``
    when the file has no such table and ``required`` is false.
    """
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise ValidationError(f"no configuration file at {path}") from None
        return None
    except tomllib.TOMLDecodeError as error:
        raise ValidationError(f"{path} is not valid TOML: {error}") from None
    except UnicodeDecodeError:
        raise ValidationError(f"{path} is not UTF-8, which TOML must be") from None
    except OSError as error:  # a directory, no permission
        raise ValidationError(f"cannot read {path}: {error.strerror or error}") from None

    table: Any = document
    for level, name in enumerate(("tool", "ekbatan", "schemagen")):
        table = table.get(name)
        if table is None:
            if required:
                raise ValidationError(f"{path} has no [tool.ekbatan.schemagen] table")
            return None
        if not isinstance(table, dict):
            dotted = ".".join(("tool", "ekbatan", "schemagen")[: level + 1])
            # The kind, not the value: a misplaced url would carry its password into the error.
            raise ValidationError(f"{path}: {dotted} must be a table, got a {type(table).__name__}")
    return config_from_table(table, base=path.parent, source=str(path))


def config_from_table(table: Mapping[str, Any], *, base: Path, source: str) -> SchemagenConfig:
    """A config from the contents of a `[tool.ekbatan.schemagen]` table."""
    where = f"[tool.ekbatan.schemagen] in {source}"
    unknown = sorted(set(table) - _KEYS)
    if unknown:
        raise ValidationError(f"{where}: unknown key(s) {unknown}; known: {sorted(_KEYS)}")

    def text(key: str) -> str | None:
        value = table.get(key)
        if value is not None and not isinstance(value, str):
            # A url can carry a password, so a wrong one is described, never shown.
            got = f"a {type(value).__name__}" if key == "url" else repr(value)
            raise ValidationError(f"{where}: {key} must be a string, got {got}")
        if value is not None and not value.strip():
            # Empty is never "the default": it used to be read as no setting at all -
            # `includes = ""` included every table, `excludes = ""` excluded none, `image = ""`
            # started the default image - which is not what anybody writing it means.
            raise ValidationError(
                f"{where}: {key} is empty; remove the line to use the default, or give it a value"
            )
        return value

    def flag(key: str, default: bool) -> bool:
        value = table.get(key, default)
        if not isinstance(value, bool):
            raise ValidationError(f"{where}: {key} must be true or false, got {value!r}")
        return value

    def path(key: str) -> Path | None:
        value = text(key)
        return None if value is None else (base / value)

    schemas = table.get("schemas", [])
    if isinstance(schemas, str):
        schemas = [schemas]
    if not isinstance(schemas, list) or not all(isinstance(s, str) for s in schemas):
        raise ValidationError(f"{where}: schemas must be a list of names, got {schemas!r}")

    forced = table.get("forced-types", [])
    if not isinstance(forced, list):
        raise ValidationError(f"{where}: forced-types must be an array of tables")
    forced_types = tuple(
        _forced_type(entry, f"{where}, forced-types[{i}]") for i, entry in enumerate(forced)
    )

    if any(not s.strip() for s in schemas):
        raise ValidationError(
            f"{where}: schemas holds an empty name; remove it, or name the schema"
        )
    return SchemagenConfig(
        migrations=path("migrations"),
        out=path("out"),
        url=text("url"),
        image=text("image") or DEFAULT_IMAGE,
        schemas=tuple(schemas),
        includes=text("includes") or ".*",
        excludes=text("excludes"),
        views=flag("views", True),
        forced_types=forced_types,
        default_forced_types=flag("default-forced-types", True),
    )


def _forced_type(entry: object, where: str) -> ForcedType:
    if not isinstance(entry, dict):
        raise ValidationError(f"{where}: must be a table, got {entry!r}")
    if "user-type" in entry:
        raise ValidationError(
            f"{where}: user-type is now two keys. Use converter for your own class with "
            "from_db and to_db, or sqlalchemy-type for a ready-made SQLAlchemy type"
        )
    unknown = sorted(set(entry) - _FORCED_KEYS)
    if unknown:
        raise ValidationError(f"{where}: unknown key(s) {unknown}; known: {sorted(_FORCED_KEYS)}")
    for key, value in entry.items():
        if not isinstance(value, str):
            raise ValidationError(f"{where}: {key} must be a string, got {value!r}")
    try:
        return ForcedType(
            name=entry.get("name"),
            converter=entry.get("converter"),
            sqlalchemy_type=entry.get("sqlalchemy-type"),
            include_expression=entry.get("include-expression"),
            include_types=entry.get("include-types"),
            exclude_expression=entry.get("exclude-expression"),
            exclude_types=entry.get("exclude-types"),
            nullability=entry.get("nullability", "ALL"),
        )
    except ValidationError as error:
        raise ValidationError(f"{where}: {error}") from None
