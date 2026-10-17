"""Getting the schema onto every database, not just the first one - the way Java's Flyway does.

A shard added to configuration and forgotten by the migrator is a database that accepts writes
right up until the first one touches a column that is not there. Migrating from the same
configuration the framework routes from is what keeps the two from drifting apart.

Java's counterpart is ``FlywayMigrator``, which runs Flyway 12 with its defaults. This is that
migrator, written in Python and compatible with Flyway on disk and on the wire: a Java service
and a Python service can migrate the same database, in either order, taking turns, at the same
time. No Java and no Flyway are needed to run it::

    from ekbatan.migrations import migrate

    migrate(config, "migrations")   # every shard, in order, one at a time, fail-fast

**The files** are Flyway's, read by Flyway's rules - the same names, the same statements, the
same checksum:

- ``V<version>__<description>.sql`` - a **versioned** migration, applied once, in the order of
  the version *numbers*, so ``V10`` runs after ``V2``. ``V1_5`` is version ``1.5``.
- ``R__<description>.sql`` - a **repeatable** migration, applied after every versioned one, and
  again whenever the file changes, in the order of the descriptions.
- ``<migration>.sql.conf`` beside a migration - Flyway's script configuration:
  ``executeInTransaction``, ``encoding``, ``placeholderReplacement``.
- Any other ``.sql`` file is skipped with one line in the log, as Flyway skips it.

A migration runs in a transaction unless it holds a statement PostgreSQL refuses inside one -
``CREATE INDEX CONCURRENTLY``, ``VACUUM`` and the rest of Flyway's list - which Flyway's parser
finds and so does this; ``executeInTransaction=false`` in the ``.conf`` file says it outright.

**The history** is Flyway's ``flyway_schema_history`` table, with Flyway's columns, types, rows
and ranks, so either port reads what the other wrote.

**The lock** is Flyway's - the same PostgreSQL advisory-lock key, the same MySQL ``GET_LOCK``
name - so a Java and a Python migrator started at the same moment wait for each other.

**One migrate** does what Flyway's does: take the lock; compare the files with the history and
refuse every disagreement before anything runs; refuse a schema with tables in it but no history;
apply the pending versioned migrations, then the pending repeatable ones, each recorded as it is
applied; let go of the lock. A migration that fails part-way where it cannot be rolled back - on
MySQL and MariaDB, or outside a transaction - is recorded as failed, and the next run refuses to
go on until someone has looked and called :func:`repair`, as Flyway refuses.

Where this goes past Flyway, on purpose:

- **One connection** holds the lock, runs each migration and writes its row, so a migration and
  its row commit together where the database can roll back a schema change, and a lost
  connection takes the lock with it rather than leaving a migration running unlocked.
- **A replica** is refused before anything else.
- **Every pending file is parsed before the first one runs**, so an unknown placeholder or an
  unfinished statement fails the run with nothing applied.
"""

from __future__ import annotations

import enum
import logging
import re
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Self

from sqlalchemy import MetaData
from sqlalchemy.schema import CreateIndex, CreateTable

from ._flyway_sql import SqlParseError, Statement, checksum, parser_for
from .config import DataSourceConfig, ShardingConfig
from .errors import EkbatanError, ValidationError
from .persistence import DatabaseRegistry
from .sharding import DEFAULT_SHARD, ShardIdentifier

if TYPE_CHECKING:
    from ._migration_backend import Backend, ResultSet

__all__ = [
    "BASELINE_DESCRIPTION",
    "DEFAULT_LOCATION",
    "HISTORY_TABLE",
    "MDC_SHARD",
    "MDC_TARGET",
    "BaselineResult",
    "FlywayConfiguration",
    "FlywayMigrator",
    "FlywayMigratorBuilder",
    "InfoResult",
    "MigrateResult",
    "MigrationError",
    "MigrationInfo",
    "MigrationScript",
    "MigrationState",
    "RepairResult",
    "Result",
    "ScriptConfig",
    "Target",
    "baseline",
    "emit_ddl",
    "info",
    "migrate",
    "migrate_registry",
    "migrate_targets",
    "repair",
    "target",
    "targets",
    "validate",
    "validate_scripts",
]

LOG = logging.getLogger("io.ekbatan.flyway.FlywayMigrator")

# Flyway's own log lines come from its classes' loggers, so a Java and a Python service can be
# quietened or turned up by the same names - `org.flywaydb` at WARN quietens both.
_MIGRATE_LOG = logging.getLogger("org.flywaydb.core.internal.command.DbMigrate")
_VALIDATE_LOG = logging.getLogger("org.flywaydb.core.internal.command.DbValidate")
_HISTORY_LOG = logging.getLogger("org.flywaydb.core.internal.schemahistory.JdbcTableSchemaHistory")
_SCRIPT_LOG = logging.getLogger("org.flywaydb.core.internal.sqlscript.DefaultSqlScriptExecutor")
_NAMING_LOG = logging.getLogger("org.flywaydb.core.internal.resource.ResourceNameValidator")
_BASELINE_LOG = logging.getLogger("org.flywaydb.core.internal.command.DbBaseline")
_REPAIR_LOG = logging.getLogger("org.flywaydb.core.internal.command.DbRepair")

#: Java's `FlywayMigrator.MDC_SHARD`: while a target migrates, each record the migrator logs
#: carries its shard as ``group:member`` - ``1:0`` - under this attribute name, so a formatter
#: written for Java's ``%X{ekbatanShard}`` has ``%(ekbatanShard)s`` here.
MDC_SHARD = "ekbatanShard"

#: Java's `FlywayMigrator.MDC_TARGET`: the target's name - ``mexico/member-0``.
MDC_TARGET = "ekbatanTarget"

#: Java's `FlywayMigrator.DEFAULT_LOCATION`, used when no location is given.
DEFAULT_LOCATION = "classpath:db/migration"

# Python has no MDC. Its standard counterpart is this: the values live in a context variable,
# which is per thread and per task as the MDC is per thread, and a filter on each of the
# migrator's loggers copies them onto each record. Outside a migration a record has neither
# attribute, as Java's MDC has neither key - `Formatter(defaults=...)` covers a format that
# names them.
_log_context: ContextVar[dict[str, str] | None] = ContextVar("ekbatan_migration_log", default=None)


class _InLogContext(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in (_log_context.get() or {}).items():
            setattr(record, key, value)
        return True


_FILTER = _InLogContext()
for _logger in (
    LOG,
    _MIGRATE_LOG,
    _VALIDATE_LOG,
    _HISTORY_LOG,
    _SCRIPT_LOG,
    _NAMING_LOG,
    _BASELINE_LOG,
    _REPAIR_LOG,
    logging.getLogger("org.flywaydb.core.internal.sqlscript.DefaultSqlScriptExecutor"),
):
    _logger.addFilter(_FILTER)


@contextmanager
def _in_log_context(each: Target) -> Iterator[None]:
    """Java's `inLogContext`: the target's shard and name on every record while it migrates."""
    token = _log_context.set(
        {MDC_SHARD: f"{each.shard.group}:{each.shard.member}", MDC_TARGET: each.name}
    )
    try:
        yield
    finally:
        _log_context.reset(token)


#: Flyway's history table, and its default name.
HISTORY_TABLE = "flyway_schema_history"

#: What Flyway's baseline row says.
BASELINE_DESCRIPTION = "<< Flyway Baseline >>"
#: What Flyway's row for the schemas it created says.
SCHEMA_CREATION_DESCRIPTION = "<< Flyway Schema Creation >>"

#: The widths of the history's columns. Longer values are cut to fit, as Flyway cuts them.
DESCRIPTION_LIMIT = 200
SCRIPT_LIMIT = 1000

#: Rows the history keeps for itself rather than for a migration.
SYNTHETIC = frozenset({"BASELINE", "DELETE", "SCHEMA"})

#: Flyway's callback names, which are also file prefixes: ``afterMigrate.sql``.
CALLBACK_EVENTS = (
    "beforeClean",
    "afterClean",
    "afterCleanError",
    "beforeMigrate",
    "beforeEachMigrate",
    "beforeEachMigrateStatement",
    "afterEachMigrateStatement",
    "afterEachMigrateStatementError",
    "afterEachMigrate",
    "afterEachMigrateError",
    "beforeRepeatables",
    "afterVersioned",
    "afterMigrateApplied",
    "afterMigrate",
    "afterMigrateError",
    "beforeUndo",
    "beforeEachUndo",
    "beforeEachUndoStatement",
    "afterEachUndoStatement",
    "afterEachUndoStatementError",
    "afterEachUndo",
    "afterEachUndoError",
    "afterUndo",
    "afterUndoError",
    "beforeValidate",
    "afterValidate",
    "afterValidateError",
    "beforeBaseline",
    "afterBaseline",
    "afterBaselineError",
    "beforeRepair",
    "afterRepair",
    "afterRepairError",
    "beforeInfo",
    "afterInfo",
    "afterInfoError",
    "afterMigrateOperationFinish",
    "afterInfoOperationFinish",
    "createSchema",
    "beforeCreateSchema",
    "beforeConnect",
    "afterConnect",
)

#: Script migrations - files Flyway runs with an interpreter. Refused here; see `_scan`.
SCRIPT_SUFFIXES = (".py", ".sh", ".bash", ".ps1", ".bat", ".cmd", ".js")


class MigrationError(EkbatanError):
    """Migrations could not be applied, or the files and the database disagree."""


# -- the files -------------------------------------------------------------------------------


def version_key(version: str) -> tuple[int, ...]:
    """A version as numbers, as Flyway's ``MigrationVersion`` compares it: ``10`` after ``2``,
    and ``1``, ``01`` and ``1.0`` one version - trailing zero parts are dropped."""
    parts = [int(part) for part in version.replace("_", ".").split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def normalise_version(version: str) -> str:
    """The version as Flyway writes it in the history: ``1_5`` is ``1.5``, ``0001`` stays."""
    return version.replace("_", ".")


_VERSION = re.compile(r"\d+(?:\.\d+)*")


def description_order(description: str) -> bytes:
    """A description as Java's ``String.compareTo`` orders it - by UTF-16 unit, so capitals come
    before lower case - which is the order Flyway applies repeatable migrations in."""
    return description.encode("utf-16-be", "surrogatepass")


def abbreviate_description(description: str) -> str:
    """Flyway's ``AbbreviationUtils.abbreviateDescription``: what fits in the column."""
    if len(description) <= DESCRIPTION_LIMIT:
        return description
    return description[: DESCRIPTION_LIMIT - 3] + "..."


def abbreviate_script(script: str) -> str:
    """Flyway's ``AbbreviationUtils.abbreviateScript``."""
    if len(script) <= SCRIPT_LIMIT:
        return script
    return "..." + script[3:SCRIPT_LIMIT]


class ResourceName(NamedTuple):
    """Flyway's ``ResourceName``: a file name taken apart by Flyway's rules."""

    prefix: str
    version: str
    description: str
    valid: bool
    message: str = ""


_PREFIXES = sorted(["V", "R", "B", *CALLBACK_EVENTS], key=len, reverse=True)


def parse_name(filename: str) -> ResourceName:
    """Flyway's ``ResourceNameParser``, for the ``.sql`` suffix: the longest matching prefix -
    ``V``, ``R``, ``B`` or a callback's name - then everything up to the first ``__`` is the
    version, the rest the description with ``_`` read as a space. ``V1__x.sql`` is valid,
    ``V__x.sql``, ``Vx__y.sql`` and ``R1__x.sql`` are not, and neither is a name with no known
    prefix: ``v1__x.sql``, ``U1__x.sql``, ``create_tables.sql``."""
    stem = filename[:-4] if filename.lower().endswith(".sql") else filename
    prefix = next((p for p in _PREFIXES if stem.startswith(p)), None)
    if prefix is None:
        return ResourceName("", "", "", False, f"Unrecognised migration name format: {filename}")
    left, separator, right = stem[len(prefix) :].partition("__")
    if not separator:
        left, right = stem[len(prefix) :], ""
    description = right.replace("_", " ")
    if prefix in ("V", "B"):
        if not left:
            return ResourceName(
                prefix,
                left,
                description,
                False,
                f"Invalid versioned migration name format: {filename} (It must contain a version "
                f"and should look like this: {prefix}1.2__{right or 'description'}.sql)",
            )
        if not _VERSION.fullmatch(left.replace("_", ".")):
            return ResourceName(
                prefix,
                left,
                description,
                False,
                f"Invalid versioned migration name format: {filename} (could not recognise "
                f"version number {left})",
            )
    elif left:
        return ResourceName(
            prefix,
            left,
            description,
            False,
            f"Invalid repeatable migration / callback name format: {filename} (It cannot "
            f"contain a version and should look like this: {prefix}__{right or 'description'}.sql)",
        )
    return ResourceName(prefix, left, description, True)


@dataclass(frozen=True, slots=True)
class ScriptConfig:
    """A migration's ``.sql.conf`` file - Flyway's ``SqlScriptMetadata``."""

    #: ``executeInTransaction``: ``None`` lets the statements decide.
    execute_in_transaction: bool | None = None
    #: ``encoding``: what the file is written in, when not the configured encoding.
    encoding: str | None = None
    #: ``placeholderReplacement``: ``None`` follows the configuration.
    placeholder_replacement: bool | None = None


def _java_properties(text: str) -> dict[str, str]:
    """``key=value`` lines, as Java's ``Properties`` reads the simple cases: ``#`` and ``!``
    comments, ``=`` or ``:`` or a space between key and value, both trimmed."""
    found: dict[str, str] = {}
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line[0] in "#!":
            continue
        match = re.match(r"([^=:\s]+)\s*(?:[=:]\s*|\s+)?(.*)", line)
        if match:
            found[match.group(1)] = match.group(2).strip()
    return found


def read_script_config(text: str, where: str) -> ScriptConfig:
    """A ``.sql.conf`` file, refused where Flyway refuses it: an unknown key, a boolean that is
    not ``true`` or ``false``, or ``shouldExecute``, which needs a paid Flyway edition."""
    values = _java_properties(text)

    def boolean(key: str) -> bool | None:
        value = values.pop(key, None)
        if value is None:
            return None
        if value.lower() not in ("true", "false"):
            raise MigrationError(
                f"{where}: Invalid value for {key} (should be either true or false): {value}"
            )
        return value.lower() == "true"

    config = ScriptConfig(
        execute_in_transaction=boolean("executeInTransaction"),
        encoding=values.pop("encoding", None),
        placeholder_replacement=boolean("placeholderReplacement"),
    )
    if "shouldExecute" in values:
        raise MigrationError(
            f"{where}: shouldExecute is a feature of Flyway's paid editions, and Flyway's free "
            f"edition - which Java's migrator runs - refuses it, so this does too."
        )
    if values:
        noun = "property" if len(values) == 1 else "properties"
        raise MigrationError(
            f"{where}: Unknown configuration {noun}: {', '.join(values)}. Please check your "
            f"script config files"
        )
    return config


@dataclass(frozen=True, slots=True)
class MigrationScript:
    """One migration file, read and checked: versioned, or repeatable when it has no version."""

    #: The version as Flyway writes it - ``1.5`` for ``V1_5__x.sql`` - or ``None`` for a
    #: repeatable migration.
    version: str | None
    #: The description, with underscores as spaces, as Flyway shows it.
    description: str
    #: Where the file is inside its location, as the history records it - ``sub/V2__x.sql``.
    script: str
    #: Where the file is, for a person to find it.
    path: str
    #: The file's text, decoded.
    text: str = field(repr=False)
    #: Flyway's CRC32 checksum, as the history stores it.
    checksum: int = 0
    #: For a repeatable migration whose placeholders were replaced: the checksum of the file as
    #: written, which Flyway also accepts as a match.
    equivalent_checksum: int | None = None
    config: ScriptConfig = ScriptConfig()

    @property
    def repeatable(self) -> bool:
        return self.version is None

    @property
    def category(self) -> str:
        """``Versioned`` or ``Repeatable``, as Flyway's ``info`` says it."""
        return "Repeatable" if self.repeatable else "Versioned"

    @property
    def key(self) -> tuple[int, ...]:
        if self.version is None:
            raise ValueError(f"{self.script} is a repeatable migration and has no version")
        return version_key(self.version)

    @property
    def filename(self) -> str:
        return self.script.rsplit("/", 1)[-1]

    def checksum_matches(self, applied: int | None) -> bool:
        """Flyway's ``checksumMatches``: the checksum, or the one before placeholders."""
        return applied == self.checksum or (
            self.equivalent_checksum is not None and applied == self.equivalent_checksum
        )

    def __str__(self) -> str:
        if self.version is None:
            return f"repeatable migration {self.description!r} ({self.script})"
        return f"version {self.version} ({self.script})"


@dataclass(frozen=True, slots=True)
class Callback:
    """A Flyway SQL callback file: ``afterMigrate.sql``, ``beforeEachMigrate__grants.sql``."""

    event: str
    description: str
    script: str
    path: str
    text: str = field(repr=False)
    config: ScriptConfig = ScriptConfig()


class Scripts(NamedTuple):
    """Everything read from the locations, in the order it runs."""

    #: By version.
    versioned: tuple[MigrationScript, ...]
    #: By description, as Flyway orders them.
    repeatable: tuple[MigrationScript, ...]
    #: Flyway's SQL callbacks, by description.
    callbacks: tuple[Callback, ...] = ()

    @property
    def migrations(self) -> tuple[MigrationScript, ...]:
        return self.versioned + self.repeatable


@dataclass(frozen=True, slots=True)
class _Resource:
    """One file in a location."""

    #: Its path inside the location, ``/``-separated.
    relative: str
    #: Where it is, for a person.
    shown: str
    read: Callable[[], bytes] = field(repr=False)


def _location_resources(location: str) -> list[_Resource]:
    directory = Path(location)
    if not directory.is_dir():
        raise ValidationError(f"no migrations directory at {directory}")
    found = []
    for path in sorted(_files(directory)):
        found.append(_Resource(path.relative_to(directory).as_posix(), str(path), path.read_bytes))
    return found


def _files(directory: Path) -> list[Path]:
    """Every file under ``directory``, at any depth - skipping hidden directories, as Flyway
    skips ``.hidden/``."""
    found = []
    for path in directory.iterdir():
        if path.is_dir():
            if not path.name.startswith("."):
                found += _files(path)
        elif path.is_file():
            found.append(path)
    return found


def _decode(data: bytes, encoding: str, where: str) -> str:
    try:
        return data.decode(encoding)
    except LookupError:
        raise MigrationError(f"{where}: unknown encoding {encoding!r}") from None
    except UnicodeDecodeError as error:
        raise MigrationError(
            f"Unable to calculate checksum of {where.rsplit('/', 1)[-1]}: it is not {encoding} "
            f"text ({error}). Set its encoding in a .sql.conf file beside it."
        ) from None


def validate_scripts(*locations: str | Path) -> tuple[MigrationScript, ...]:
    """Read every migration in ``locations``, refuse what Flyway would refuse, and return them in
    the order they run: versioned by version, then repeatable by description.

    Needs no database, so a test can call it and fail in CI: two files with one version, two
    repeatable ones with one description, a file that is not text, a ``.conf`` file Flyway would
    refuse, a script migration. A file Flyway skips is skipped here too, with Flyway's log line.
    """
    return _scan(locations, FlywayConfiguration()).migrations


def _scan(locations: Sequence[str | Path], configuration: FlywayConfiguration) -> Scripts:
    if not locations:
        raise ValidationError("at least one migrations location is required")

    resources: list[_Resource] = []
    for location in locations:
        if not str(location).strip():
            raise ValidationError("locations cannot contain blank values")
        resources += _location_resources(str(location))

    conf = {r.relative[: -len(".conf")]: r for r in resources if r.relative.endswith(".sql.conf")}
    versioned: list[MigrationScript] = []
    repeatable: list[MigrationScript] = []
    callbacks: list[Callback] = []
    invalid: list[str] = []
    for resource in resources:
        filename = resource.relative.rsplit("/", 1)[-1]
        if (
            filename.lower().endswith(SCRIPT_SUFFIXES)
            and parse_name(filename.rsplit(".", 1)[0] + ".sql").valid
            and filename[0] in "VR"
        ):
            raise MigrationError(
                f"{resource.shown}: only SQL migrations are supported. Flyway would run this file "
                f"with an interpreter; this migrator does not run programs, so it refuses the file "
                f"rather than skip a migration a Java service would apply."
            )
        if not filename.lower().endswith(".sql"):
            continue
        name = parse_name(filename)
        if not name.valid:
            invalid.append(name.message)
            continue
        config = ScriptConfig()
        conf_file = conf.get(resource.relative)
        if conf_file is not None:
            config = read_script_config(
                _decode(conf_file.read(), "utf-8", conf_file.shown), conf_file.shown
            )
        text = _decode(resource.read(), config.encoding or configuration._encoding, resource.shown)
        if name.prefix == "B":
            raise MigrationError(
                f"{resource.shown}: baseline migrations (B<version>__) are not supported. Use "
                f"versioned migrations, and baseline() for a database that was built another way."
            )
        if name.prefix in ("V", "R"):
            script = MigrationScript(
                version=normalise_version(name.version) if name.prefix == "V" else None,
                description=name.description,
                script=resource.relative,
                path=resource.shown,
                text=text,
                checksum=checksum(text),
                equivalent_checksum=checksum(text) if name.prefix == "R" else None,
                config=config,
            )
            (versioned if name.prefix == "V" else repeatable).append(script)
        else:
            callbacks.append(
                Callback(
                    name.prefix, name.description, resource.relative, resource.shown, text, config
                )
            )

    if invalid:
        if configuration._validate_migration_naming:
            raise MigrationError("Invalid SQL filenames found:\n" + "\n".join(invalid))
        _NAMING_LOG.info(
            "%d SQL migrations were detected but not run because they did not follow the "
            "filename convention.",
            len(invalid),
        )
        _NAMING_LOG.info(
            "Set 'validateMigrationNaming' to true to fail fast and see a list of the invalid "
            "file names."
        )
    _refuse_duplicates(versioned, repeatable, callbacks)
    return Scripts(
        tuple(sorted(versioned, key=lambda m: m.key)),
        tuple(sorted(repeatable, key=lambda m: description_order(m.description))),
        tuple(sorted(callbacks, key=lambda c: description_order(c.description))),
    )


def _refuse_duplicates(
    versioned: Sequence[MigrationScript],
    repeatable: Sequence[MigrationScript],
    callbacks: Sequence[Callback],
) -> None:
    lines: list[str] = []
    by_version: dict[tuple[int, ...], list[MigrationScript]] = {}
    for script in versioned:
        by_version.setdefault(script.key, []).append(script)
    for found in by_version.values():
        if len(found) > 1:
            lines.append(f"version {found[0].version}: " + ", ".join(s.path for s in found))
    by_description: dict[str, list[MigrationScript]] = {}
    for script in repeatable:
        by_description.setdefault(script.description, []).append(script)
    for found in by_description.values():
        if len(found) > 1:
            lines.append(
                f"repeatable {found[0].description!r}: " + ", ".join(s.path for s in found)
            )
    by_name: dict[str, list[Callback]] = {}
    for callback in callbacks:
        by_name.setdefault(callback.script.rsplit("/", 1)[-1][:-4], []).append(callback)
    for name, same in by_name.items():
        if len(same) > 1:
            lines.append(f"callback {name}: " + ", ".join(c.path for c in same))
    if lines:
        raise MigrationError(
            "two migrations are one migration - each version, each repeatable migration's "
            "description and each callback's name belongs to one file. Give one of them the next "
            "free version, or another name:\n  " + "\n  ".join(lines)
        )


# -- the history -----------------------------------------------------------------------------


class _Row(NamedTuple):
    installed_rank: int
    version: str | None
    description: str
    type: str
    script: str
    checksum: int | None
    installed_by: str
    installed_on: datetime | None
    execution_time: int
    success: bool

    def __str__(self) -> str:
        if self.version is None:
            return f"repeatable migration {self.description!r} ({self.script})"
        return f"version {self.version} ({self.script})"


@dataclass(slots=True)
class _Session:
    """One database, locked, and what everything done to it needs."""

    backend: Backend
    label: str
    configuration: FlywayConfiguration
    schema: str = ""
    table: str = ""
    installed_by: str = ""

    @property
    def schema_shown(self) -> str:
        return self.backend.quote(self.schema)


def _open_session(each: Target, configuration: FlywayConfiguration) -> _Session:
    backend = _open(each)
    try:
        schema = backend.current_schema()
    except Exception:
        backend.close()
        raise
    return _Session(
        backend,
        each.label,
        configuration,
        schema=schema,
        table=backend.quote(schema, configuration._table),
    )


def _history_exists(session: _Session) -> bool:
    return session.backend.table_exists(session.schema, session.configuration._table)


def _create_history(session: _Session) -> None:
    """Flyway's table, with Flyway's statements for this dialect."""
    _HISTORY_LOG.info("Creating Schema History table %s ...", session.table)
    backend = session.backend
    with backend.transaction():
        for statement in backend.create_history_script(
            session.table, session.schema, session.configuration._table
        ):
            backend.execute(statement)


def _refuse_another_shape(session: _Session) -> None:
    expected = {
        "installed_rank",
        "version",
        "description",
        "type",
        "script",
        "checksum",
        "installed_by",
        "installed_on",
        "execution_time",
        "success",
    }
    found = session.backend.columns(session.table)
    if not expected <= found:
        raise MigrationError(
            f"{session.label}: {session.table} does not have Flyway's columns (it lacks "
            f"{', '.join(sorted(expected - found))}), so it is not a Flyway schema history table "
            f"and cannot be read as one."
        )


def _installed_on(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _read_history(session: _Session) -> list[_Row]:
    rows = session.backend.execute(
        "SELECT installed_rank, version, description, type, script, checksum, installed_by, "
        f"installed_on, execution_time, success FROM {session.table} ORDER BY installed_rank"
    ).fetchall()
    return [
        _Row(
            int(row[0]),
            None if row[1] is None else str(row[1]),
            row[2] or "",
            str(row[3]),
            row[4] or "",
            None if row[5] is None else int(row[5]),
            row[6] or "",
            _installed_on(row[7]),
            int(row[8]),
            bool(row[9]),
        )
        for row in rows
    ]


def _insert(
    session: _Session,
    *,
    version: str | None,
    description: str,
    type: str,
    script: str,
    checksum: int | None,
    execution_time: int,
    success: bool,
) -> None:
    """One row, as Flyway's ``addAppliedMigration`` writes it: ranked after the last - or 0 for
    the schema-creation row of an empty history - with the description and script cut to fit,
    and ``installed_on`` left to the database's clock. Called under the lock."""
    backend = session.backend
    last = backend.scalar(f"SELECT MAX(installed_rank) FROM {session.table}")
    rank = (0 if type == "SCHEMA" else 1) if last is None else int(last) + 1
    backend.execute(
        f"INSERT INTO {session.table} (installed_rank, version, description, type, script, "
        "checksum, installed_by, execution_time, success) VALUES (%s, %s, %s, %s, %s, %s, %s, "
        "%s, %s)",
        (
            rank,
            version,
            abbreviate_description(description),
            type,
            abbreviate_script(script),
            checksum,
            session.installed_by,
            execution_time,
            success,
        ),
    )


# -- where each migration stands ---------------------------------------------------------------


class MigrationState(enum.Enum):
    """Where a migration stands, in Flyway's words, as ``info`` shows it."""

    PENDING = "Pending"
    SUCCESS = "Success"
    FAILED = "Failed"
    #: A repeatable migration whose file has changed since it was last applied: it runs again.
    OUTDATED = "Outdated"
    #: An earlier application of a repeatable migration that has been applied again since.
    SUPERSEDED = "Superseded"
    MISSING = "Missing"
    MISSING_FAILED = "Failed (Missing)"
    #: Applied by a newer release: newer than every file here.
    FUTURE = "Future"
    FUTURE_FAILED = "Failed (Future)"
    #: Not applied, and older than one that was: it would never run where its number says.
    IGNORED = "Ignored"
    #: Marked deleted by ``repair`` after its file went.
    DELETED = "Deleted"
    BASELINE = "Baseline"
    BELOW_BASELINE = "Below Baseline"
    BASELINE_IGNORED = "Ignored (Baseline)"


@dataclass(frozen=True, slots=True)
class MigrationInfo:
    """One migration, or one row of the history, and where it stands - a line of Flyway's
    ``info``."""

    #: ``Versioned``, ``Repeatable``, or empty for a row the history keeps for itself.
    category: str
    version: str | None
    description: str
    #: ``SQL``, ``BASELINE``, ``SCHEMA`` or ``DELETE``.
    type: str
    script: str
    checksum: int | None
    state: MigrationState
    installed_rank: int | None = None
    installed_on: datetime | None = None
    installed_by: str | None = None
    execution_time: int | None = None


@dataclass(slots=True)
class _Model:
    """The files against the history, worked out once and used by migrate, info, validate and
    repair alike, so they cannot disagree."""

    infos: list[MigrationInfo] = field(default_factory=list)
    pending: list[MigrationScript] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: The highest version applied - Flyway's "current version of schema".
    schema_version: str | None = None
    #: Applied, and their files gone: what repair marks deleted.
    missing: list[_Row] = field(default_factory=list)
    #: Applied, and their files now say something else: what repair realigns.
    misaligned: list[tuple[_Row, MigrationScript]] = field(default_factory=list)

    def refuse(self, label: str) -> None:
        if self.problems:
            raise MigrationError(
                f"{label}: the migrations here do not match what this database has applied, so "
                f"nothing was run:\n  - " + "\n  - ".join(self.problems)
            )


_COULD_NOT_ROLL_BACK = (
    "is recorded as failed, because what it did could not be rolled back - MySQL and MariaDB "
    "keep every schema change on their own, and a migration run outside a transaction keeps "
    "what it did - so part of it may be in the database. Put the database right by hand, "
    "finishing what it started or undoing it, then run repair(): that removes the failed row, "
    "and the next run applies the migration again."
)


def _failed_and_gone(row: _Row) -> str:
    return (
        f"{row} failed on an earlier run, and its file is gone. Part of it may be in the "
        f"database: put that right by hand, then run repair(), which removes the failed row."
    )


def _info(row: _Row, state: MigrationState) -> MigrationInfo:
    category = ""
    if row.type not in SYNTHETIC:
        category = "Repeatable" if row.version is None else "Versioned"
    return MigrationInfo(
        category=category,
        version=row.version,
        description=row.description,
        type=row.type,
        script=row.script,
        checksum=row.checksum,
        state=state,
        installed_rank=row.installed_rank,
        installed_on=row.installed_on,
        installed_by=row.installed_by,
        execution_time=row.execution_time,
    )


def _unapplied(script: MigrationScript, state: MigrationState) -> MigrationInfo:
    return MigrationInfo(
        category=script.category,
        version=script.version,
        description=script.description,
        type="SQL",
        script=script.script,
        checksum=script.checksum,
        state=state,
    )


def _model(scripts: Scripts, rows: Sequence[_Row]) -> _Model:
    """Where every file and every row stands - Flyway's ``MigrationInfoService``, and its
    ``validate`` as ``migrate`` runs it: pending files are not a problem, and neither is anything
    applied by a newer release."""
    model = _Model()
    for row in rows:
        if row.type == "SCHEMA":
            model.infos.append(_info(row, MigrationState.SUCCESS))
    _versioned(model, scripts.versioned, rows)
    _repeatable(model, scripts.repeatable, [r for r in rows if r.type != "SCHEMA"])
    model.infos.sort(key=_info_order)
    return model


def _info_order(info: MigrationInfo) -> tuple[int, tuple[int, ...], bytes]:
    """Flyway's order: what a baseline skipped, then everything applied in the order it was
    applied, then what is pending - versioned by version, then repeatable by description."""
    if info.state in (MigrationState.BELOW_BASELINE, MigrationState.BASELINE_IGNORED):
        return (0, version_key(info.version or "0"), b"")
    if info.installed_rank is not None:
        return (1, (info.installed_rank,), b"")
    if info.version is not None:
        return (2, version_key(info.version), b"")
    return (3, (), description_order(info.description))


def _versioned(model: _Model, scripts: Sequence[MigrationScript], rows: Sequence[_Row]) -> None:
    files = {script.key: script for script in scripts}
    newest_file = max(files, default=None)

    # A DELETE row, written by repair, marks the last row of its version deleted, and is not a
    # migration itself.
    applied: list[_Row] = []
    deleted: set[int] = set()
    baseline: tuple[int, ...] | None = None
    for row in rows:
        if row.version is None:
            continue
        key = version_key(row.version)
        if row.type == "BASELINE" and (baseline is None or key > baseline):
            baseline = key
        if row.type == "DELETE":
            if row.success:
                for earlier in reversed(applied):
                    if earlier.type not in SYNTHETIC and version_key(earlier.version or "") == key:
                        deleted.add(earlier.installed_rank)
                        break
            continue
        applied.append(row)

    # The current version counts a failed row, as Flyway's does: the database has been touched.
    current: tuple[int, ...] | None = None
    for row in applied:
        key = version_key(row.version or "")
        if row.installed_rank not in deleted and (current is None or key > current):
            current, model.schema_version = key, row.version

    matched: set[tuple[int, ...]] = set()
    for row in applied:
        key = version_key(row.version or "")
        script = None
        if row.type not in SYNTHETIC and row.installed_rank not in deleted:
            script = files.get(key)
        if row.type == "BASELINE":
            state = MigrationState.BASELINE
        elif row.installed_rank in deleted:
            state = MigrationState.DELETED
        elif script is None:
            future = newest_file is None or key > newest_file
            if future:
                state = MigrationState.FUTURE if row.success else MigrationState.FUTURE_FAILED
            else:
                state = MigrationState.MISSING if row.success else MigrationState.MISSING_FAILED
        else:
            matched.add(key)
            state = MigrationState.SUCCESS if row.success else MigrationState.FAILED
        model.infos.append(_info(row, state))

        if state is MigrationState.FAILED:
            model.problems.append(f"{row} failed on an earlier run and {_COULD_NOT_ROLL_BACK}")
        elif state is MigrationState.MISSING_FAILED:
            model.problems.append(_failed_and_gone(row))
        elif state is MigrationState.FUTURE_FAILED:
            model.warnings.append(
                f"{row} failed when a newer release applied it, and is newer than every "
                f"migration here. Left alone, as Flyway leaves it; the newer release will not "
                f"run until it has been repaired."
            )
        elif state is MigrationState.FUTURE:
            model.warnings.append(
                f"{row.script} (version {row.version}) has been applied to this database but is "
                f"newer than every migration here - a newer release migrated it. Left alone, as "
                f"Flyway leaves it."
            )
        elif state is MigrationState.MISSING:
            model.missing.append(row)
            model.problems.append(
                f"{row.script} (version {row.version}) has been applied to this database but its "
                f"file is gone. An applied migration is history: restore the file - or, if it "
                f"was removed on purpose, run repair() to record it as deleted."
            )
        elif state is MigrationState.SUCCESS and script is not None:
            if not script.checksum_matches(row.checksum):
                model.misaligned.append((row, script))
                model.problems.append(
                    f"{script.script} has changed since it was applied (checksum {row.checksum} "
                    f"then, {script.checksum} now). An applied migration is history: put the file "
                    f"back as it was, and make the change in a new migration."
                )
            elif abbreviate_description(script.description) != row.description:
                model.misaligned.append((row, script))
                model.problems.append(
                    f"version {row.version} was applied as {row.description!r}, and its file "
                    f"{script.script} now says {script.description!r}. An applied migration is "
                    f"history: put the old name back - or, if only the name changed, run "
                    f"repair() to record the new one."
                )

    for key, script in sorted(files.items()):
        if key in matched:
            continue
        if baseline is not None and key <= baseline:
            state = (
                MigrationState.BASELINE_IGNORED
                if key == baseline
                else MigrationState.BELOW_BASELINE
            )
            model.infos.append(_unapplied(script, state))
            continue
        if current is not None and key < current:
            state = MigrationState.IGNORED
            model.problems.append(
                f"{script.script} has not been applied, but version {model.schema_version} "
                f"already has. Migrations run in version order, so this one would never run in "
                f"the place its number says: give it a version after {model.schema_version}."
            )
        else:
            state = MigrationState.PENDING
            model.pending.append(script)
        model.infos.append(_unapplied(script, state))


def _repeatable(model: _Model, scripts: Sequence[MigrationScript], rows: Sequence[_Row]) -> None:
    # Matched by the description as the history holds it - cut to fit - so a long description
    # finds its own row again. Flyway matches the uncut one, and loses the row; see
    # DECISIONS-TO-REVIEW.
    files = {abbreviate_description(script.description): script for script in scripts}
    applied = [row for row in rows if row.version is None]

    deleted: set[int] = set()
    for i, row in enumerate(applied):
        if row.type == "DELETE" and row.success:
            for earlier in reversed(applied[:i]):
                if earlier.type not in SYNTHETIC and earlier.description == row.description:
                    deleted.add(earlier.installed_rank)
                    break
    # Only the newest row of each description counts; a DELETE row counts as the newest too.
    latest: dict[str, _Row] = {}
    for row in applied:
        latest[row.description] = row

    for row in applied:
        if row.type == "DELETE":
            continue
        script = files.get(row.description)
        newest = latest[row.description] is row
        if row.installed_rank in deleted:
            state = MigrationState.DELETED
        elif not newest:
            state = MigrationState.SUPERSEDED
        elif script is None:
            state = MigrationState.MISSING if row.success else MigrationState.MISSING_FAILED
        elif not row.success:
            state = MigrationState.FAILED
        elif not script.checksum_matches(row.checksum):
            state = MigrationState.OUTDATED
        else:
            state = MigrationState.SUCCESS
        model.infos.append(_info(row, state))

        if state is MigrationState.FAILED:
            model.problems.append(f"{row} failed on an earlier run and {_COULD_NOT_ROLL_BACK}")
        elif state is MigrationState.MISSING_FAILED:
            model.problems.append(_failed_and_gone(row))
        elif state is MigrationState.MISSING:
            model.missing.append(row)
            model.problems.append(
                f"{row} has been applied to this database but its file is gone. Restore it - or, "
                f"if it was removed on purpose, run repair() to record it as deleted. An older "
                f"release started against a database a newer one migrated stops here too, as it "
                f"does with Flyway, when the newer release added a repeatable migration."
            )

    pending = []
    for description, script in files.items():
        newest_row = latest.get(description)
        up_to_date = (
            newest_row is not None
            and newest_row.type != "DELETE"
            and newest_row.installed_rank not in deleted
            and script.checksum_matches(newest_row.checksum)
        )
        if not up_to_date:
            pending.append(script)
    pending.sort(key=lambda s: description_order(s.description))
    model.pending += pending
    model.infos += [_unapplied(script, MigrationState.PENDING) for script in pending]


# -- configuration ---------------------------------------------------------------------------


class FlywayConfiguration:
    """The Flyway settings this migrator honours, set as Java's ``customize`` sets them on
    Flyway's ``FluentConfiguration`` - the same names, in snake case, each returning the
    configuration so the calls chain::

        FlywayMigrator.builder().with_data_source(ds).customize(
            lambda c: c.lock_retry_count(-1).table("schema_history")
        ).migrate()

    Every default is Flyway's.
    """

    __slots__ = (
        "_baseline_description",
        "_baseline_on_migrate",
        "_baseline_version",
        "_encoding",
        "_execute_in_transaction",
        "_installed_by",
        "_lock_retry_count",
        "_mixed",
        "_output_query_results",
        "_table",
        "_validate_migration_naming",
    )

    def __init__(self) -> None:
        self._table = HISTORY_TABLE
        self._lock_retry_count = 50
        self._output_query_results = True
        self._baseline_on_migrate = False
        self._baseline_version = "1"
        self._baseline_description = BASELINE_DESCRIPTION
        self._validate_migration_naming = False
        self._mixed = False
        self._execute_in_transaction = True
        self._installed_by: str | None = None
        self._encoding = "utf-8"

    def copy(self) -> FlywayConfiguration:
        other = FlywayConfiguration()
        for name in self.__slots__:
            setattr(other, name, getattr(self, name))
        return other

    def table(self, name: str) -> Self:
        """``table``: the history table's name - ``flyway_schema_history``. A Java service and a
        Python one sharing a database must use the same name, or neither sees the other."""
        if not name or not name.strip():
            raise ValidationError("table cannot be blank")
        self._table = name
        return self

    def lock_retry_count(self, count: int) -> Self:
        """``lockRetryCount``: how many times to retry PostgreSQL's lock, a second apart, before
        giving up - 50. ``-1`` waits for as long as it takes. MySQL's lock always waits."""
        self._lock_retry_count = int(count)
        return self

    def output_query_results(self, enabled: bool) -> Self:
        """``outputQueryResults``: log what a ``SELECT`` in a migration returns - on."""
        self._output_query_results = bool(enabled)
        return self

    def baseline_on_migrate(self, enabled: bool) -> Self:
        """``baselineOnMigrate``: baseline a schema with things in it but no history, rather than
        refuse it - off."""
        self._baseline_on_migrate = bool(enabled)
        return self

    def baseline_version(self, version: str) -> Self:
        """``baselineVersion`` - ``1``."""
        if not _VERSION.fullmatch(normalise_version(str(version or ""))):
            raise ValidationError(
                f"a baseline version is digits separated by . or _, not {version!r}"
            )
        self._baseline_version = normalise_version(str(version))
        return self

    def baseline_description(self, description: str) -> Self:
        """``baselineDescription`` - ``<< Flyway Baseline >>``."""
        if not description:
            raise ValidationError("a baseline needs a description")
        self._baseline_description = description
        return self

    def validate_migration_naming(self, enabled: bool) -> Self:
        """``validateMigrationNaming``: fail on a ``.sql`` file whose name Flyway cannot read,
        rather than skip it with a line in the log - off."""
        self._validate_migration_naming = bool(enabled)
        return self

    def mixed(self, enabled: bool) -> Self:
        """``mixed``: allow one migration to hold statements that must run outside a
        transaction beside ones that can run in one - off."""
        self._mixed = bool(enabled)
        return self

    def execute_in_transaction(self, enabled: bool) -> Self:
        """``executeInTransaction``: run each migration in a transaction where it can - on."""
        self._execute_in_transaction = bool(enabled)
        return self

    def installed_by(self, user: str | None) -> Self:
        """``installedBy``: what the history's ``installed_by`` says - the database user."""
        self._installed_by = user
        return self

    def encoding(self, encoding: str) -> Self:
        """``encoding``: what the files are written in - UTF-8."""
        self._encoding = encoding
        return self


# -- running ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Target:
    """One database to migrate - Java's ``FlywayMigrator.Target``."""

    shard: ShardIdentifier
    name: str
    data_source: DataSourceConfig

    def __post_init__(self) -> None:
        if self.shard is None:
            raise ValidationError("shard is required")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValidationError("name is required")
        if self.data_source is None:
            raise ValidationError("data_source is required")

    @property
    def label(self) -> str:
        return f"{self.name} ({self.shard})"


@dataclass(frozen=True, slots=True)
class MigrateResult:
    """What one database's migration did - the parts of Flyway's ``MigrateResult`` that apply."""

    #: The highest version applied before this run, or ``None`` for a fresh database.
    initial_schema_version: str | None
    #: The version of the last versioned migration this run applied, or ``None`` when it applied
    #: none - Flyway's ``targetSchemaVersion``.
    target_schema_version: str | None
    #: The files this run applied, in the order it applied them - versioned, then repeatable.
    migrations: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def migrations_executed(self) -> int:
        return len(self.migrations)


@dataclass(frozen=True, slots=True)
class Result:
    """One target and what migrating it did - Java's ``FlywayMigrator.Result``."""

    target: Target
    migrate_result: MigrateResult


@dataclass(frozen=True, slots=True)
class InfoResult:
    """One target and where each of its migrations stands - Flyway's ``info``."""

    target: Target
    schema_version: str | None
    migrations: tuple[MigrationInfo, ...]
    #: What ``migrate`` would refuse, and what it would warn about.
    problems: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def pending(self) -> tuple[MigrationInfo, ...]:
        return tuple(m for m in self.migrations if m.state is MigrationState.PENDING)


@dataclass(frozen=True, slots=True)
class RepairResult:
    """One target and what ``repair`` changed in its history - Flyway's ``RepairResult``."""

    target: Target
    #: Failed rows removed.
    removed: tuple[str, ...] = ()
    #: Applied migrations whose files are gone, now marked deleted.
    deleted: tuple[str, ...] = ()
    #: Applied migrations whose checksum or description now matches their file.
    aligned: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BaselineResult:
    """One target and the baseline it now has - Flyway's ``BaselineResult``."""

    target: Target
    version: str
    description: str
    #: ``False`` when the same baseline was already there, and nothing was written.
    created: bool = True


def targets(config: ShardingConfig) -> list[Target]:
    """A target per member of the layout, on its ``primaryConfig`` - Java's ``targets``."""
    if config is None:
        raise ValidationError("config is required")
    found: list[Target] = []
    for group in config.groups:
        for member in group.members:
            name = f"{group.name}/{member.name or f'member-{member.member}'}"
            found.append(
                Target(ShardIdentifier(group.group, member.member), name, member.primary_config())
            )
    return found


def target(
    data_source: DataSourceConfig,
    *,
    shard: ShardIdentifier | None = None,
    name: str | None = None,
) -> Target:
    """Java's three ``target(...)`` overloads in one: ``(DEFAULT, "default")`` with no shard,
    ``shard-<group>-<member>`` with a shard and no name."""
    if shard is None:
        return Target(DEFAULT_SHARD, name or "default", data_source)
    return Target(shard, name or f"shard-{shard.group}-{shard.member}", data_source)


def _choose(config: ShardingConfig, shards: Iterable[ShardIdentifier] | None) -> list[Target]:
    chosen = targets(config)
    if shards is None:
        return chosen
    wanted = set(shards)
    known = {t.shard for t in chosen}
    unknown = sorted(wanted - known, key=str)
    if unknown:
        raise ValidationError(
            f"cannot migrate {', '.join(str(s) for s in unknown)}: not in this layout, "
            f"which has {', '.join(str(t.shard) for t in chosen)}"
        )
    # Layout order, not argument order: a run that stops half-way leaves a state the operator
    # can reason about.
    return [t for t in chosen if t.shard in wanted]


def _locations(locations: Sequence[str | Path]) -> tuple[str | Path, ...]:
    return tuple(locations) if locations else (DEFAULT_LOCATION,)


def migrate(
    config: ShardingConfig,
    *locations: str | Path,
    shards: Iterable[ShardIdentifier] | None = None,
    output_query_results: bool = True,
) -> list[Result]:
    """Migrate every member of the layout, or only the ones named in ``shards``.

    Java's ``FlywayMigrator.migrate(ShardingConfig, locations...)``. One target at a time, in the
    layout's order, fail-fast: carrying on past a failure turns one broken database into several.

    ``shards`` picks a subset - what Java does by passing some of ``targets(config)`` - for rolling
    a migration out one group at a time. A shard the layout does not have is refused rather than
    skipped: a typo there would otherwise read as "nothing to do" and migrate nothing at all.
    """
    configuration = FlywayConfiguration().output_query_results(output_query_results)
    scripts = _scan(_locations(locations), configuration)
    return _run(_choose(config, shards), scripts, configuration)


def migrate_targets(
    migration_targets: Iterable[Target],
    *locations: str | Path,
    output_query_results: bool = True,
) -> list[Result]:
    """Java's ``FlywayMigrator.migrate(Target...)``."""
    chosen = list(migration_targets)
    if not chosen:
        raise ValidationError("at least one migration target is required")
    configuration = FlywayConfiguration().output_query_results(output_query_results)
    return _run(chosen, _scan(_locations(locations), configuration), configuration)


def _each[T](chosen: Sequence[Target], verb: str, action: Callable[[Target], T]) -> list[T]:
    """``action`` on each target in turn, stopping at the first that fails - and saying which
    targets were already done, since the database's own error names none of them."""
    results: list[T] = []
    done: list[str] = []
    for each in chosen:
        try:
            results.append(action(each))
        except Exception as error:
            message = (
                str(error)
                if isinstance(error, MigrationError)
                else (f"{each.label}: {type(error).__name__}: {error}")
            )
            raise MigrationError(
                f"{message}\nAlready {verb}: {', '.join(done) or 'none'}. Targets are {verb} one "
                f"at a time, so the rest are untouched - fix this one and run again; what was "
                f"{'applied' if verb == 'migrated' else 'done'} is skipped."
            ) from error.__cause__ or error
        done.append(each.name)
    return results


def _run(
    chosen: Sequence[Target], scripts: Scripts, configuration: FlywayConfiguration
) -> list[Result]:
    def one(each: Target) -> Result:
        with _in_log_context(each):
            outcome = _migrate(each, scripts, configuration)
            LOG.info(
                "%s is at version %s (%d applied)",
                each.name,
                outcome.target_schema_version or outcome.initial_schema_version or "<< empty >>",
                outcome.migrations_executed,
            )
        return Result(each, outcome)

    return _each(chosen, "migrated", one)


class _Failed(Exception):
    """A migration failed; ``recorded`` says whether its failure was written down."""

    def __init__(
        self, error: BaseException, recorded: bool | BaseException, rolled_back: bool
    ) -> None:
        super().__init__(str(error))
        self.error = error
        self.recorded = recorded
        self.rolled_back = rolled_back


def _open(each: Target) -> Backend:
    from ._migration_backend import open_backend

    return open_backend(
        each.label, each.data_source.sqlalchemy_url(), each.data_source.data_source_properties
    )


def _format_time(millis: int) -> str:
    """Flyway's ``TimeFormat``: ``00:00.012s``."""
    return f"{millis // 60000:02d}:{(millis % 60000) // 1000:02d}.{millis % 1000:03d}s"


@dataclass(slots=True)
class _Prepared:
    """A migration's statements, parsed before anything runs, and how it runs."""

    script: MigrationScript
    statements: list[Statement]
    in_transaction: bool


def _parse(
    session: _Session, text: str, where: str, config: ScriptConfig
) -> tuple[list[Statement], bool]:
    """A file's statements, as Flyway's ``ParserSqlScript`` reads them, and whether it runs in
    a transaction: its ``.conf`` file says, or else every statement must be able to."""
    backend = session.backend
    parser = parser_for(backend.parser_dialect(), backend.server_version())
    try:
        statements = parser.parse(text)
    except SqlParseError as error:
        raise MigrationError(f"{where}: {error}") from None
    configuration = session.configuration
    transactional = [s.can_execute_in_transaction for s in statements]
    check = (
        not configuration._mixed
        and config.execute_in_transaction is None
        and configuration._execute_in_transaction
    )
    seen: set[bool] = set()
    for offending in statements if check else ():
        seen.add(offending.can_execute_in_transaction)
        if len(seen) < 2:
            continue
        raise MigrationError(
            f"{where}: Detected both transactional and non-transactional statements within the "
            f"same migration (even though mixed is false). Offending statement found at line "
            f"{offending.line}: {offending.sql}"
            f"{'' if offending.can_execute_in_transaction else ' [non-transactional]'}"
        )
    if config.execute_in_transaction is not None:
        in_transaction = config.execute_in_transaction
    else:
        in_transaction = all(transactional) and configuration._execute_in_transaction
    return statements, in_transaction


def _migrate(each: Target, scripts: Scripts, configuration: FlywayConfiguration) -> MigrateResult:
    from ._migration_backend import MigrationBackendError

    session: _Session | None = None
    current: _Prepared | None = None
    applied: list[MigrationScript] = []
    warnings: list[str] = []
    before: str | None = None
    try:
        session = _open_session(each, configuration)
        backend = session.backend
        backend.refuse_if_read_only()
        backend.acquire(session.table, configuration._lock_retry_count)
        try:
            session.installed_by = configuration._installed_by or backend.current_user()
            model = _validated(session, scripts, warnings)
            if not _history_exists(session):
                model = _prepare_history(session, scripts, warnings) or model
            before = model.schema_version
            prepared = [
                _Prepared(script, *_parse(session, script.text, script.path, script.config))
                for script in model.pending
            ]
            _MIGRATE_LOG.info(
                "Current version of schema %s: %s",
                session.schema_shown,
                before or "<< Empty Schema >>",
            )
            started = time.monotonic()
            for current in prepared:
                _apply(session, current)
                applied.append(current.script)
            current = None
            millis = int((time.monotonic() - started) * 1000)
            _log_summary(session, applied, millis)
        finally:
            backend.release()
    except MigrationError:
        raise
    except MigrationBackendError as error:
        raise MigrationError(str(error)) from error
    except _Failed as failed:
        raise MigrationError(
            _failure(each.label, session, current, applied, failed)
        ) from failed.error
    except Exception as error:
        raise MigrationError(
            _failure(each.label, session, current, applied, _Failed(error, False, False))
        ) from error
    finally:
        if session is not None:
            session.backend.close()

    target_version = next((s.version for s in reversed(applied) if s.version is not None), None)
    return MigrateResult(
        initial_schema_version=before,
        target_schema_version=target_version,
        migrations=tuple(s.script for s in applied),
        warnings=tuple(warnings),
    )


def _validated(session: _Session, scripts: Scripts, warnings: list[str]) -> _Model:
    """Flyway's ``validate``, as ``migrate`` runs it first: the files against the history,
    every disagreement refused before anything runs."""
    started = time.monotonic()
    rows: list[_Row] = []
    if _history_exists(session):
        _refuse_another_shape(session)
        rows = _read_history(session)
    else:
        _HISTORY_LOG.info("Schema history table %s does not exist yet", session.table)
    model = _model(scripts, rows)
    model.refuse(session.label)
    millis = int((time.monotonic() - started) * 1000)
    count = len(model.infos)
    if count == 1:
        _VALIDATE_LOG.info(
            "Successfully validated 1 migration (execution time %s)", _format_time(millis)
        )
    else:
        _VALIDATE_LOG.info(
            "Successfully validated %d migrations (execution time %s)", count, _format_time(millis)
        )
    if count == 0:
        warning = "No migrations found. Are your locations set up correctly?"
        _VALIDATE_LOG.warning(warning)
        warnings.append(warning)
    for warning in model.warnings:
        _MIGRATE_LOG.warning("%s: %s", session.label, warning)
        warnings.append(warning)
    return model


def _prepare_history(session: _Session, scripts: Scripts, warnings: list[str]) -> _Model | None:
    """The history table, created - and the two databases Flyway refuses, refused.

    A schema with anything in it and no history was built by something else - by hand, by
    ``create_all``, by another tool. Running every migration from the first against it is either
    an error on the first ``CREATE TABLE`` or, worse, a partial success. Flyway refuses it and
    points at ``baseline``; so does this, unless ``baseline_on_migrate`` says to baseline it.
    Returns the model again when a baseline changed it.
    """
    backend = session.backend
    found = backend.schema_objects(session.schema)
    if found:
        if session.configuration._baseline_on_migrate:
            _write_baseline(session)
            return _model(scripts, _read_history(session))
        raise MigrationError(
            f"{session.label} already has {', '.join(found)}"
            f"{' and more' if len(found) == 5 else ''} but no {session.configuration._table}, so "
            f"nothing says "
            f'which migrations built it - Flyway refuses it too ("Found non-empty schema(s) '
            f'{session.schema_shown} but no schema history table"). If it is what your '
            f"migrations up to some version make, record that with baseline(version=...) and run "
            f"again - the migrations after it then run. If it should be empty, this is the wrong "
            f"database."
        )
    _create_history(session)
    return None


def _write_baseline(session: _Session) -> None:
    configuration = session.configuration
    _create_history(session)
    with session.backend.transaction():
        _insert(
            session,
            version=configuration._baseline_version,
            description=configuration._baseline_description,
            type="BASELINE",
            script=configuration._baseline_description,
            checksum=None,
            execution_time=0,
            success=True,
        )
    _BASELINE_LOG.info(
        "Successfully baselined schema with version: %s", configuration._baseline_version
    )


def _log_summary(session: _Session, applied: Sequence[MigrationScript], millis: int) -> None:
    if not applied:
        _MIGRATE_LOG.info("Schema %s is up to date. No migration necessary.", session.schema_shown)
        return
    version = next((s.version for s in reversed(applied) if s.version is not None), None)
    _MIGRATE_LOG.info(
        "Successfully applied %d migration%s to schema %s%s (execution time %s)",
        len(applied),
        "" if len(applied) == 1 else "s",
        session.schema_shown,
        f", now at version v{version}" if version else "",
        _format_time(millis),
    )


def _migration_text(session: _Session, prepared: _Prepared) -> str:
    """Flyway's ``toMigrationText``."""
    script = prepared.script
    marker = "" if prepared.in_transaction else " [non-transactional]"
    if script.version is not None:
        described = f" - {script.description}" if script.description else ""
        return f'schema {session.schema_shown} to version "{script.version}{described}"{marker}'
    return f'schema {session.schema_shown} with repeatable migration "{script.description}"{marker}'


def _execute(session: _Session, statements: Sequence[Statement], where: str) -> None:
    """Each statement in turn, its results logged as Flyway logs them."""
    backend = session.backend
    for statement in statements:
        try:
            if statement.copy_data is not None:
                backend.copy(statement.sql, statement.copy_data)
                results: list[ResultSet] = []
            else:
                results = backend.run(statement.sql)
        except Exception as error:
            raise _StatementFailed(where, statement, error) from error
        if session.configuration._output_query_results:
            for result in results:
                _SCRIPT_LOG.info("%s", _ascii_table(result))


class _StatementFailed(Exception):
    def __init__(self, where: str, statement: Statement, error: BaseException) -> None:
        super().__init__(
            f"{type(error).__name__}: {error}".rstrip()
            + f" (in {where} at line {statement.line}: {statement.sql})"
        )
        self.where = where
        self.statement = statement
        self.error = error


def _ascii_table(result: ResultSet) -> str:
    """Flyway's ``AsciiTable``, as ``outputQueryResults`` prints a result."""
    columns = list(result.columns)
    rows = [["" if v is None else str(v) for v in row] for row in result.rows]
    widths = [len(c) for c in columns]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))
    ruler = "+" + "".join("-" + "-" * w + "-+" for w in widths) + "\n"
    header = "|" + "".join(f" {c.ljust(w)} |" for c, w in zip(columns, widths, strict=True)) + "\n"
    body = "".join(
        "|" + "".join(f" {v.ljust(w)} |" for v, w in zip(row, widths, strict=True)) + "\n"
        for row in rows
    )
    if not rows:
        body = "| " + "No rows returned".ljust(len(ruler) - min(len(ruler), 5)) + " |\n"
    return ruler + header + ruler + body + ruler


def _apply(session: _Session, prepared: _Prepared) -> None:
    """Run one migration and record it - together, where the database can roll back a schema
    change, so a crash cannot leave it applied and unrecorded. A failure that could not be rolled
    back is recorded as one, as Flyway records it, so the next run stops rather than running it
    again over what it half did."""
    backend = session.backend
    script = prepared.script
    text = _migration_text(session, prepared)
    started = time.monotonic()
    _MIGRATE_LOG.info("Migrating %s", text)

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    def record(success: bool) -> None:
        _insert(
            session,
            version=script.version,
            description=script.description,
            type="SQL",
            script=script.script,
            checksum=script.checksum,
            execution_time=elapsed(),
            success=success,
        )

    try:
        if prepared.in_transaction:
            # One transaction for the migration and its row. On MySQL and MariaDB a schema
            # change still commits on its own inside it - nothing can stop that - but a
            # migration that only changes data is recorded atomically there too.
            with backend.transaction():
                _execute(session, prepared.statements, script.script)
                record(True)
        else:
            _execute(session, prepared.statements, script.script)
            with backend.transaction():
                record(True)
    except Exception as error:
        rolled_back = backend.transactional_ddl and prepared.in_transaction
        failed = f"Migration of {text} failed!"
        recorded: bool | BaseException = False
        if rolled_back:
            _MIGRATE_LOG.error("%s Changes successfully rolled back.", failed)
        else:
            _MIGRATE_LOG.error("%s Please restore backups and roll back database and code!", failed)
            try:
                with backend.transaction():
                    record(False)
                recorded = True
            except Exception as unrecorded:
                recorded = unrecorded
        raise _Failed(error, recorded, rolled_back) from error


def _failure(
    label: str,
    session: _Session | None,
    current: _Prepared | None,
    applied: Sequence[MigrationScript],
    failed: _Failed,
) -> str:
    """What happened, where, and what state it left - the underlying error only names a url."""
    error = failed.error
    backend = session.backend if session is not None else None
    where = f" in {current.script.script}" if current is not None else ""
    parts = [f"migrating {label} failed{where}: {type(error).__name__}: {error}".rstrip()]
    if isinstance(error, _StatementFailed):
        parts = [f"migrating {label} failed{where}: {error}"]
    if backend is not None and backend.connection_lost():
        parts.append("The connection to the database was lost.")
    if applied:
        parts.append(f"Applied by this run before that: {', '.join(s.script for s in applied)}.")
    if current is not None and backend is not None:
        name = current.script.script
        if failed.rolled_back:
            parts.append(f"Nothing of {name} remains: it was rolled back as a whole.")
        else:
            why = (
                "MySQL and MariaDB commit each one on its own"
                if not backend.transactional_ddl
                else "it ran outside a transaction"
            )
            parts.append(f"Any change {name} had already made stays - {why}.")
            if failed.recorded is True:
                parts.append(
                    "It is recorded as failed, so the next run stops here until the database has "
                    "been checked and put right, and repair() has been run."
                )
            else:
                reason = "" if failed.recorded is False else f" ({failed.recorded})"
                parts.append(
                    f"Recording it as failed did not work either{reason}, so the next run would "
                    f"apply it again: check the database before running it."
                )
    return " ".join(parts)


# -- info, validate, repair, baseline --------------------------------------------------------


def info(
    config: ShardingConfig,
    *locations: str | Path,
    shards: Iterable[ShardIdentifier] | None = None,
) -> list[InfoResult]:
    """Where each migration stands on each database - Flyway's ``info``.

    Changes nothing and takes no lock, as Flyway's takes none; ``problems`` is what ``migrate``
    would refuse.
    """
    configuration = FlywayConfiguration()
    scripts = _scan(_locations(locations), configuration)
    return _each(
        _choose(config, shards), "read", lambda each: _info_of(each, scripts, configuration)
    )


def _info_of(each: Target, scripts: Scripts, configuration: FlywayConfiguration) -> InfoResult:
    session = _open_session(each, configuration)
    try:
        rows: list[_Row] = []
        if _history_exists(session):
            _refuse_another_shape(session)
            rows = _read_history(session)
        model = _model(scripts, rows)
        return InfoResult(
            target=each,
            schema_version=model.schema_version,
            migrations=tuple(model.infos),
            problems=tuple(model.problems),
            warnings=tuple(model.warnings),
        )
    finally:
        session.backend.close()


def validate(
    config: ShardingConfig,
    *locations: str | Path,
    shards: Iterable[ShardIdentifier] | None = None,
) -> list[InfoResult]:
    """Flyway's ``validate``: refuse a database whose history disagrees with the files - and,
    unlike ``migrate``'s own check, one with migrations still pending, as Flyway's ``validate``
    refuses those too. Changes nothing."""
    configuration = FlywayConfiguration()
    scripts = _scan(_locations(locations), configuration)
    return _each(
        _choose(config, shards), "validated", lambda each: _validate(each, scripts, configuration)
    )


def _validate(each: Target, scripts: Scripts, configuration: FlywayConfiguration) -> InfoResult:
    result = _info_of(each, scripts, configuration)
    problems = list(result.problems) + [
        f"Detected resolved migration not applied to database: "
        f"{m.version if m.version is not None else m.description} ({m.script})"
        for m in result.pending
    ]
    if problems:
        raise MigrationError(
            f"{each.label}: Validate failed: Migrations have failed validation\n  - "
            + "\n  - ".join(problems)
        )
    count = len(result.migrations)
    _VALIDATE_LOG.info("Successfully validated %d migration%s", count, "" if count == 1 else "s")
    return result


def repair(
    config: ShardingConfig,
    *locations: str | Path,
    shards: Iterable[ShardIdentifier] | None = None,
) -> list[RepairResult]:
    """Put each database's history right after a person has put the database right - Flyway's
    ``repair``, step for step:

    1. remove the rows of failed migrations, so the next run applies them again;
    2. mark an applied migration whose file is gone as deleted - a migration a newer release
       applied is left alone;
    3. record the checksum and description a versioned migration's file has now, where they
       changed.

    Under the lock, in one transaction. It changes the history only: whatever a failed migration
    half did to the schema is for a person to finish or undo first.
    """
    configuration = FlywayConfiguration()
    scripts = _scan(_locations(locations), configuration)
    return _each(
        _choose(config, shards), "repaired", lambda each: _repair(each, scripts, configuration)
    )


def _repair(each: Target, scripts: Scripts, configuration: FlywayConfiguration) -> RepairResult:
    from ._migration_backend import MigrationBackendError

    label = each.label
    session = _open_session(each, configuration)
    backend = session.backend
    try:
        backend.refuse_if_read_only()
        backend.acquire(session.table, configuration._lock_retry_count)
        try:
            result = _repair_locked(each, scripts, session)
        finally:
            backend.release()
    except MigrationBackendError as error:
        raise MigrationError(str(error)) from error
    finally:
        backend.close()

    for kind, found in (
        ("removed", result.removed),
        ("marked deleted", result.deleted),
        ("realigned", result.aligned),
    ):
        for one in found:
            _REPAIR_LOG.info("%s: repair %s %s", label, kind, one)
    if result.removed:
        _REPAIR_LOG.warning(
            "%s: failed migrations were removed from the history; whatever they did to the "
            "schema before failing is still there, and is for a person to finish or undo",
            label,
        )
    if not (result.removed or result.deleted or result.aligned):
        _REPAIR_LOG.info("%s: nothing to repair", label)
    return result


def _repair_locked(each: Target, scripts: Scripts, session: _Session) -> RepairResult:
    backend = session.backend
    session.installed_by = session.configuration._installed_by or backend.current_user()
    if not _history_exists(session):
        return RepairResult(each)
    _refuse_another_shape(session)
    with backend.transaction():
        failed = [row for row in _read_history(session) if not row.success]
        backend.execute(f"DELETE FROM {session.table} WHERE success = %s", (False,))
        model = _model(scripts, _read_history(session))
        for row in model.missing:
            _insert(
                session,
                version=row.version,
                description=row.description,
                type="DELETE",
                script=row.script,
                checksum=row.checksum,
                execution_time=0,
                success=True,
            )
        for row, script in model.misaligned:
            backend.execute(
                f"UPDATE {session.table} SET description = %s, type = %s, checksum = %s "
                "WHERE installed_rank = %s",
                (
                    abbreviate_description(script.description),
                    "SQL",
                    script.checksum,
                    row.installed_rank,
                ),
            )
    return RepairResult(
        each,
        removed=tuple(str(row) for row in failed),
        deleted=tuple(str(row) for row in model.missing),
        aligned=tuple(str(script) for _, script in model.misaligned),
    )


def baseline(
    config: ShardingConfig,
    *,
    version: str = "1",
    description: str = BASELINE_DESCRIPTION,
    shards: Iterable[ShardIdentifier] | None = None,
) -> list[BaselineResult]:
    """Record that each database already is what the migrations up to ``version`` make - Flyway's
    ``baseline``. Migrations at or below it never run there; the ones after it do.

    For a database that existed before its migrations did, which ``migrate`` refuses, as Flyway
    does: it has tables but no history. Refused where there is already a history with anything
    in it, and - unlike Flyway - where the database is empty, since baselining an empty database
    skips the migrations that would have built it.
    """
    configuration = (
        FlywayConfiguration().baseline_version(version).baseline_description(description)
    )
    return _each(_choose(config, shards), "baselined", lambda each: _baseline(each, configuration))


def _baseline(each: Target, configuration: FlywayConfiguration) -> BaselineResult:
    from ._migration_backend import MigrationBackendError

    label = each.label
    version = configuration._baseline_version
    description = configuration._baseline_description
    session = _open_session(each, configuration)
    backend = session.backend
    try:
        backend.refuse_if_read_only()
        backend.acquire(session.table, configuration._lock_retry_count)
        try:
            session.installed_by = configuration._installed_by or backend.current_user()
            if _history_exists(session):
                _refuse_another_shape(session)
                rows = _read_history(session)
                marker = next((row for row in rows[:2] if row.type == "BASELINE"), None)
                if marker is not None:
                    same = version_key(marker.version or "") == version_key(version)
                    if same and marker.description == description:
                        _BASELINE_LOG.info(
                            "Schema history table %s already initialized with (%s,%s). Skipping.",
                            session.table,
                            version,
                            description,
                        )
                        return BaselineResult(each, marker.version or version, description, False)
                    raise MigrationError(
                        f"{label}: Unable to baseline schema history table {session.table} with "
                        f"({version},{description}) as it has already been baselined with "
                        f"({marker.version},{marker.description})"
                    )
                if rows:
                    raise MigrationError(
                        f"{label}: Unable to baseline schema history table {session.table} as it "
                        f"already contains migrations - the history already says what built it."
                    )
                raise MigrationError(
                    f"{label}: Unable to baseline schema history table {session.table} as it "
                    f"already exists, and is empty. Delete the schema history table, and run "
                    f"baseline again."
                )
            if not backend.schema_objects(session.schema):
                raise MigrationError(
                    f"{label} is empty, so there is nothing to baseline - baselining it would "
                    f"skip the migrations that build it. Run migrate instead."
                )
            _write_baseline(session)
        finally:
            backend.release()
    except MigrationBackendError as error:
        raise MigrationError(str(error)) from error
    finally:
        backend.close()
    return BaselineResult(each, version, description)


class FlywayMigrator:
    """Java's ``FlywayMigrator``: its static ``migrate`` overloads, ``targets``, ``target`` and
    ``builder`` - and Flyway's ``info``, ``validate``, ``repair`` and ``baseline``, which Java
    reaches through Flyway itself.

    The name is Java's. This is not Flyway and needs no Java: it is a migrator written in
    Python that reads and writes what Flyway reads and writes."""

    # Java nests these in `FlywayMigrator`; so does this, as well as the module having them.
    Target = Target
    Result = Result
    DEFAULT_LOCATION = DEFAULT_LOCATION
    MDC_SHARD = MDC_SHARD
    MDC_TARGET = MDC_TARGET

    migrate = staticmethod(migrate)
    migrate_targets = staticmethod(migrate_targets)
    targets = staticmethod(targets)
    target = staticmethod(target)
    info = staticmethod(info)
    validate = staticmethod(validate)
    repair = staticmethod(repair)
    baseline = staticmethod(baseline)

    @staticmethod
    def migrate_data_source(
        data_source: DataSourceConfig,
        *locations: str | Path,
        output_query_results: bool = True,
    ) -> MigrateResult:
        """Java's ``migrate(DataSourceConfig, locations...)``: one database, outside a layout."""
        (result,) = migrate_targets(
            [target(data_source)], *locations, output_query_results=output_query_results
        )
        return result.migrate_result

    @staticmethod
    def builder() -> FlywayMigratorBuilder:
        return FlywayMigratorBuilder()


class FlywayMigratorBuilder:
    """Fluent builder that runs the migration - Java's ``FlywayMigrator.Builder``. Obtain via
    :meth:`FlywayMigrator.builder`.

    Java's ``withDataSource``, ``withShardingConfig``, ``withTarget``, ``withTargets``,
    ``locations``, ``customize`` and ``migrate()``. ``customize`` is handed a
    :class:`FlywayConfiguration`, as Java's is handed Flyway's ``FluentConfiguration``.
    ``info()``, ``validate()``, ``repair()`` and ``baseline()`` run Flyway's commands of those
    names against the same targets.
    """

    __slots__ = ("_configuration", "_locations", "_targets")

    def __init__(self) -> None:
        self._targets: list[Target] = []
        self._locations: tuple[str | Path, ...] = (DEFAULT_LOCATION,)
        self._configuration = FlywayConfiguration()

    def with_data_source(self, data_source: DataSourceConfig) -> Self:
        return self.with_target(target(data_source))

    def with_sharding_config(self, config: ShardingConfig) -> Self:
        return self.with_targets(targets(config))

    def with_target(self, migration_target: Target) -> Self:
        if migration_target is None:
            raise ValidationError("target is required")
        self._targets.append(migration_target)
        return self

    def with_targets(self, migration_targets: Iterable[Target]) -> Self:
        if migration_targets is None:
            raise ValidationError("targets is required")
        for each in migration_targets:
            self.with_target(each)
        return self

    def locations(self, *locations: str | Path) -> Self:
        """Java's ``locations``: replaces them; none at all means ``DEFAULT_LOCATION``."""
        for location in locations:
            if location is None or not str(location).strip():
                raise ValidationError("locations cannot contain blank values")
        self._locations = _locations(locations)
        return self

    def customize(self, customizer: Callable[[FlywayConfiguration], object]) -> Self:
        """Java's ``customize``: change the Flyway settings before the run. Called in the order
        given, each on the same configuration."""
        if customizer is None:
            raise ValidationError("customizer is required")
        customizer(self._configuration)
        return self

    def output_query_results(self, enabled: bool) -> Self:
        """Shorthand for ``customize(lambda c: c.output_query_results(enabled))``."""
        self._configuration.output_query_results(enabled)
        return self

    def _chosen(self) -> list[Target]:
        if not self._targets:
            raise ValidationError("at least one migration target is required")
        return list(self._targets)

    def _scripts(self) -> Scripts:
        return _scan(self._locations, self._configuration)

    def migrate(self) -> list[Result]:
        chosen = self._chosen()
        return _run(chosen, self._scripts(), self._configuration.copy())

    def info(self) -> list[InfoResult]:
        scripts, configuration = self._scripts(), self._configuration.copy()
        return _each(self._chosen(), "read", lambda each: _info_of(each, scripts, configuration))

    def validate(self) -> list[InfoResult]:
        scripts, configuration = self._scripts(), self._configuration.copy()
        return _each(
            self._chosen(), "validated", lambda each: _validate(each, scripts, configuration)
        )

    def repair(self) -> list[RepairResult]:
        scripts, configuration = self._scripts(), self._configuration.copy()
        return _each(self._chosen(), "repaired", lambda each: _repair(each, scripts, configuration))

    def baseline(
        self, version: str | None = None, description: str | None = None
    ) -> list[BaselineResult]:
        configuration = self._configuration.copy()
        if version is not None:
            configuration.baseline_version(version)
        if description is not None:
            configuration.baseline_description(description)
        chosen = self._chosen()
        return _each(chosen, "baselined", lambda each: _baseline(each, configuration))


# -- development helpers ---------------------------------------------------------------------


def migrate_registry(registry: DatabaseRegistry, metadata: MetaData) -> None:
    """Create anything in ``metadata`` that is missing, on every shard.

    For development and tests. Real deployments migrate from files, because ``create_all`` has
    no answer for a column that changed.
    """
    for shard in registry.shards:
        metadata.create_all(registry.manager(shard).primary)


def emit_ddl(metadata: MetaData, dialect: str) -> str:
    """The ``CREATE`` statements for these tables, as text, for a given dialect.

    A starting point for the first migration - run it, read it, commit it as a file. Generated
    once and then owned by you, rather than regenerated behind your back on every deploy.
    """
    target_dialect = _dialect(dialect)
    statements: list[str] = []
    for table in metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=target_dialect)).strip() + ";")
        # By name: `table.indexes` is a set, so its order changes from one process to the next,
        # and output that is committed as a file has to be the same every time it is generated.
        statements += [
            str(CreateIndex(index).compile(dialect=target_dialect)).strip() + ";"
            for index in sorted(table.indexes, key=lambda index: str(index.name))
        ]
    return "\n\n".join(statements) + "\n"


def _dialect(name: str) -> Any:
    """The dialect itself, not an engine - so this works without the driver installed."""
    from sqlalchemy.dialects import mysql, postgresql, sqlite

    dialects = {
        "postgres": postgresql.dialect,
        "postgresql": postgresql.dialect,
        "mysql": mysql.dialect,
        "mariadb": mysql.dialect,
        "sqlite": sqlite.dialect,
    }
    try:
        return dialects[name]()
    except KeyError:
        raise ValidationError(
            f"unknown dialect {name!r}; expected one of {sorted(dialects)}"
        ) from None
