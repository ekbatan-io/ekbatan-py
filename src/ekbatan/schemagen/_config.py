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

    - ``name``: one of the framework's helpers (``uuid_column``, ``json_column``,
      ``timestamp_column``) or a SQLAlchemy type, written as in Python - ``Text``,
      ``String(36)``, ``Numeric(19, 4)``, ``postgresql.INET``. jOOQ's ``withName``.
    - ``user_type``: your own type, by dotted path - ``myapp.types.Money`` or
      ``myapp.types:Money``, with arguments if it takes any (``myapp.types.Money(4)``). A
      ``TypeDecorator`` is jOOQ's ``userType`` and ``converter`` in one class.

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
    user_type: str | None = None
    include_expression: str | None = None
    include_types: str | None = None
    exclude_expression: str | None = None
    exclude_types: str | None = None
    nullability: Nullability = "ALL"
    #: Only when reading these databases; empty means every one. Used by the built-in defaults,
    #: which differ per database exactly as Java's build scripts do.
    dialects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.name is None) == (self.user_type is None):
            raise ValidationError(
                f"a forced type needs exactly one of name and user-type, got {self._what()}"
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

    def _what(self) -> str:
        return f"name={self.name!r}" if self.name is not None else f"user-type={self.user_type!r}"

    @property
    def target(self) -> str:
        """What the columns become, as written."""
        return self.name if self.name is not None else str(self.user_type)

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
#: - every zone-less timestamp to ``timestamp_column()`` - Java's ``Instant`` +
#:   ``InstantConverter`` on ``TIMESTAMP`` (PG) and ``(?i:DATETIME|TIMESTAMP)`` (MySQL, MariaDB).
#:   ``TIMESTAMPTZ`` does not match, as it does not in Java: it keeps its zone.
#: - JSON to ``json_column()`` - Java's ``ObjectNode`` on ``JSONB`` (PG) and ``JSON`` (MySQL,
#:   MariaDB). A plain PostgreSQL ``JSON`` is not forced, as in Java.
#: - a PostgreSQL ``UUID`` to ``uuid_column()``, so the same schema renders ``CHAR(36)`` - not
#:   SQLAlchemy's ``CHAR(32)`` - if it is ever used on MySQL.
#: - a ``CHAR(36)`` named ``id`` or ``*_id`` to ``uuid_column()`` when reading MySQL or MariaDB -
#:   Java's ``UuidStringConverter``, with Java's own two patterns. Nothing in those databases
#:   says a ``CHAR(36)`` is a uuid; the name is the only signal, and it is narrow on purpose.
DEFAULT_FORCED_TYPES: tuple[ForcedType, ...] = (
    ForcedType(name="timestamp_column", include_types="TIMESTAMP|DATETIME"),
    ForcedType(name="json_column", include_types="JSONB", dialects=("postgresql",)),
    ForcedType(name="json_column", include_types="JSON", dialects=("mysql", "mariadb", "sqlite")),
    ForcedType(name="uuid_column", include_types="UUID", dialects=("postgresql",)),
    ForcedType(
        name="uuid_column",
        include_types=r"CHAR\(36\)",
        include_expression=r".*\.id|.*_id",
        dialects=("mysql", "mariadb"),
    ),
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
    "user-type",
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

    table = document.get("tool", {}).get("ekbatan", {}).get("schemagen")
    if table is None:
        if required:
            raise ValidationError(f"{path} has no [tool.ekbatan.schemagen] table")
        return None
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
            raise ValidationError(f"{where}: {key} must be a string, got {value!r}")
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
    unknown = sorted(set(entry) - _FORCED_KEYS)
    if unknown:
        raise ValidationError(f"{where}: unknown key(s) {unknown}; known: {sorted(_FORCED_KEYS)}")
    for key, value in entry.items():
        if not isinstance(value, str):
            raise ValidationError(f"{where}: {key} must be a string, got {value!r}")
    try:
        return ForcedType(
            name=entry.get("name"),
            user_type=entry.get("user-type"),
            include_expression=entry.get("include-expression"),
            include_types=entry.get("include-types"),
            exclude_expression=entry.get("exclude-expression"),
            exclude_types=entry.get("exclude-types"),
            nullability=entry.get("nullability", "ALL"),
        )
    except ValidationError as error:
        raise ValidationError(f"{where}: {error}") from None
