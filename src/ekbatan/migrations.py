"""Getting the schema onto every database, not just the first one.

A shard added to configuration and forgotten by the migrator is a database that accepts writes
right up until the first one touches a column that is not there. Migrating from the same
configuration the framework routes from is what keeps the two from drifting apart.

Java's counterpart is ``FlywayMigrator``, which runs Flyway with its defaults. This is that, over
`yoyo <https://ollycope.com/software/yoyo/>`_, used as a library so an application migrates itself
at startup without any external binary::

    from ekbatan.migrations import migrate

    migrate(config, "migrations")   # every shard, in order, one at a time, fail-fast

**The files** are plain SQL, named as Flyway names them, in a directory and any directory under
it:

- ``V<version>__<description>.sql`` - a **versioned** migration, applied once, in the order of
  the version *numbers*, so ``V10`` runs after ``V2``.
- ``R__<description>.sql`` - a **repeatable** migration, applied after every versioned one, and
  again whenever the file changes, in the order of the descriptions. For what is replaced whole
  each time - a view, a function, a procedure.
- ``post-apply.sql`` (any name starting ``post-apply``) - yoyo's hook, Flyway's ``afterMigrate``:
  run at the end of every migrate, whether or not anything was pending.

A file whose first line is ``-- transactional: false`` runs outside a transaction, which is what
PostgreSQL's ``CREATE INDEX CONCURRENTLY`` needs.

**One migrate** does what Flyway's does, in the same order: take the lock; refuse a schema with
tables in it but no history; compare the files with the history and refuse every disagreement
before anything runs; apply the pending versioned migrations, then the pending repeatable ones,
each recorded as it is applied; run the ``post-apply`` hooks; let go of the lock. A migration that
fails part-way where it cannot be rolled back - on MySQL and MariaDB, or outside a transaction -
is recorded as failed, and the next run refuses to go on until someone has looked and called
:func:`repair`, as Flyway refuses.

yoyo reads the files and runs their statements. The rest is here, because yoyo on its own gets it
wrong for a service that runs in more than one process - each measured, not assumed:

- **The lock.** yoyo's is a row in a table: a waiting process gives up after ten seconds, and one
  killed while holding it leaves every later deploy failing until someone clears it by hand.
  Here it is a database session lock - ``pg_try_advisory_lock``, ``GET_LOCK`` - which a process
  waits on for as long as it takes, and which the database drops when the process dies.
- **One connection.** yoyo applies each migration on a second connection it opens for the
  purpose, apart from the one that holds its lock. When the lock's connection dies - a network
  blip, an idle timeout, a proxy - the lock goes and the migration carries on, and a second
  process runs it again at the same time. Here the lock, the migration and the row recording it
  share one connection, and die together.
- **Recording.** yoyo records a migration after applying it, in a separate step; a crash between
  the two runs it again next time. Here, where the database can roll back a schema change -
  PostgreSQL, SQLite - the migration and its row in ``ekbatan_schema_history`` commit together.
- **The checks.** yoyo accepts two files with one version, and an applied file edited, renamed
  or deleted, without a word. Here each is refused before anything runs, as Flyway refuses it.
- **A replica.** Each database is checked to be a writable primary before anything else, so a
  url that reaches a replica, or a proxy that routes to one, is refused rather than migrated.

The history lives in ``ekbatan_schema_history``, with Flyway's columns: one row per migration
applied, per repeatable migration each time it is applied, and per failure that could not be
rolled back.
"""

from __future__ import annotations

import enum
import hashlib
import io
import logging
import re
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager, redirect_stdout
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Self, cast

from sqlalchemy import Boolean, Column, Index, Integer, MetaData, String, Table
from sqlalchemy.schema import CreateIndex, CreateTable

from ._guards import refuse_on_an_event_loop
from .config import DataSourceConfig, ShardingConfig
from .errors import EkbatanError, ValidationError
from .persistence import DatabaseRegistry
from .sharding import DEFAULT_SHARD, ShardIdentifier

if TYPE_CHECKING:
    from ._yoyo import _Common

__all__ = [
    "BASELINE_DESCRIPTION",
    "HISTORY_TABLE",
    "MDC_SHARD",
    "MDC_TARGET",
    "BaselineResult",
    "Hook",
    "InfoResult",
    "MigrateResult",
    "MigrationError",
    "MigrationInfo",
    "MigrationScript",
    "MigrationState",
    "RepairResult",
    "Result",
    "Target",
    "YoyoMigrator",
    "YoyoMigratorBuilder",
    "baseline",
    "emit_ddl",
    "history_table",
    "info",
    "migrate",
    "migrate_registry",
    "migrate_targets",
    "repair",
    "target",
    "targets",
    "validate_scripts",
]

LOG = logging.getLogger("io.ekbatan.flyway.FlywayMigrator")

#: Java's `FlywayMigrator.MDC_SHARD`: while a target migrates, each record this module logs
#: carries its shard as ``group:member`` - ``1:0`` - under this attribute name, so a formatter
#: written for Java's ``%X{ekbatanShard}`` has ``%(ekbatanShard)s`` here.
MDC_SHARD = "ekbatanShard"

#: Java's `FlywayMigrator.MDC_TARGET`: the target's name - ``mexico/member-0``.
MDC_TARGET = "ekbatanTarget"

# Python has no MDC. Its standard counterpart is this: the values live in a context variable,
# which is per thread and per task as the MDC is per thread, and a filter on this module's
# logger copies them onto each record it sees. Outside a migration a record has neither
# attribute, as Java's MDC has neither key - `Formatter(defaults=...)` covers a format that
# names them.
_log_context: ContextVar[dict[str, str] | None] = ContextVar("ekbatan_migration_log", default=None)


class _InLogContext(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in (_log_context.get() or {}).items():
            setattr(record, key, value)
        return True


LOG.addFilter(_InLogContext())


@contextmanager
def _in_log_context(each: Target) -> Iterator[None]:
    """Java's `inLogContext`: the target's shard and name on every record while it migrates,
    and whatever a caller had there put back afterwards, failure or not."""
    token = _log_context.set(
        {MDC_SHARD: f"{each.shard.group}:{each.shard.member}", MDC_TARGET: each.name}
    )
    try:
        yield
    finally:
        _log_context.reset(token)


#: One row per migration applied, per repeatable migration each time it is applied, and per
#: failure that could not be rolled back - Flyway's ``flyway_schema_history``, under this name.
HISTORY_TABLE = "ekbatan_schema_history"

#: What a baseline row says, where Flyway's says ``<< Flyway Baseline >>``.
BASELINE_DESCRIPTION = "<< Baseline >>"

#: Flyway's names. The prefix and the separator are matched as written and the suffix in any
#: case - Flyway runs ``V2__x.SQL`` as it runs ``V2__x.sql``. The version is digits separated by
#: ``.`` or ``_``, so ``V1_1`` and ``V1.1`` are one version, and the description may be empty.
VERSIONED_NAME = re.compile(r"V(?P<version>\d+(?:[._]\d+)*)__(?P<description>.*)(?i:\.sql)")
REPEATABLE_NAME = re.compile(r"R__(?P<description>.*)(?i:\.sql)")
#: yoyo's hook: a SQL file whose name starts with ``post-apply``.
HOOK_NAME = re.compile(r"post-apply.*(?i:\.sql)")
#: Something that looks like one of the three but is not SQL - ``V3__backfill.py``.
LOOKS_LIKE_A_MIGRATION = re.compile(r"(V\d+(?:[._]\d+)*__|R__|post-apply).*")
#: A repeatable migration given a version - ``R1__x.sql``.
REPEATABLE_WITH_A_VERSION = re.compile(r"R\d[\d._]*__.*(?i:\.sql)")
#: A Flyway callback - ``afterMigrate.sql``, ``beforeEachMigrate__grants.sql``.
FLYWAY_CALLBACK = re.compile(r"(?:(?:before|after)[A-Z]\w*?|createSchema)(?:__.*)?(?i:\.sql)")
#: ``DELIMITER //`` - a command of the mysql client, not SQL.
DELIMITER = re.compile(r"^\s*DELIMITER\s+\S+\s*$", re.IGNORECASE | re.MULTILINE)

#: The widths of the history's columns, which are Flyway's.
DESCRIPTION_LIMIT = 200
SCRIPT_LIMIT = 1000

#: Rows the history keeps for itself rather than for a migration.
SYNTHETIC = frozenset({"BASELINE", "DELETE"})


class MigrationError(EkbatanError):
    """Migrations could not be applied, or the files and the database disagree."""


# -- the files -------------------------------------------------------------------------------


def version_key(version: str) -> tuple[int, ...]:
    """A version as numbers, so ``10`` comes after ``2`` and ``1`` equals ``01`` and ``1.0``.

    Trailing zero parts are dropped, as Flyway compares them: ``1.0`` and ``1`` are one version,
    and two files carrying it are a duplicate.
    """
    parts = [int(part) for part in re.split(r"[._]", version)]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def description_order(description: str) -> bytes:
    """A description as Java's ``String.compareTo`` orders it - by UTF-16 unit, so capitals come
    before lower case and ``a b`` before ``a-b`` before ``ab`` - which is the order Flyway applies
    repeatable migrations in."""
    return description.encode("utf-16-be", "surrogatepass")


def checksum(path: Path) -> str:
    """What the file contains, as a SHA-256 over its text.

    Line endings and a byte-order mark are normalised first: a checkout that turns ``\\n`` into
    ``\\r\\n`` has not edited the migration, and saying it had would teach people to ignore the
    one check that matters.
    """
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise MigrationError(f"{path} is not UTF-8 text: {error}") from None
    text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load(path: Path) -> Any:
    from ._yoyo import load_sql

    try:
        return load_sql(str(path))
    except KeyError:
        # yoyo reads `-- transactional:` as a key into {"true": ..., "false": ...}.
        raise MigrationError(f"{path}: `-- transactional:` must be `true` or `false`") from None


@dataclass(frozen=True, slots=True)
class MigrationScript:
    """One migration file, read and checked: versioned, or repeatable when it has no version."""

    #: The version as written in the file name - ``0001`` for ``V0001__x.sql`` - or ``None`` for a
    #: repeatable migration.
    version: str | None
    #: The description, with underscores as spaces, as Flyway shows it.
    description: str
    path: Path
    checksum: str
    #: Where the file is inside its location, as the history records it - ``sub/V2__x.sql``.
    script: str = ""

    def __post_init__(self) -> None:
        if not self.script:
            object.__setattr__(self, "script", self.path.name)

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

    def load(self) -> Any:
        """The file as a yoyo migration, its statements split and its directives read."""
        return _load(self.path)

    def __str__(self) -> str:
        if self.version is None:
            return f"repeatable migration {self.description!r} ({self.script})"
        return f"version {self.version} ({self.script})"


@dataclass(frozen=True, slots=True)
class Hook:
    """A ``post-apply`` file - yoyo's hook, and Flyway's ``afterMigrate`` callback.

    Run at the end of every migrate, under the lock, in its own transaction unless it says
    otherwise, and recorded nowhere: so it runs again next time, whatever happened this time, and
    has to be written so that it can.
    """

    path: Path
    script: str

    def load(self) -> Any:
        return _load(self.path)


class Scripts(NamedTuple):
    """Everything read from the locations, in the order it runs."""

    #: By version.
    versioned: tuple[MigrationScript, ...]
    #: By description, as Flyway orders them.
    repeatable: tuple[MigrationScript, ...]
    #: In location order, then by path.
    hooks: tuple[Hook, ...]

    @property
    def migrations(self) -> tuple[MigrationScript, ...]:
        return self.versioned + self.repeatable


def validate_scripts(*locations: str | Path) -> tuple[MigrationScript, ...]:
    """Read every migration in ``locations``, refuse what is wrong, and return them in the order
    they run: versioned by version, then repeatable by description.

    Needs no database, so a test can call it and fail in CI:

    - a ``.sql`` file that is not ``V<version>__<description>.sql``, ``R__<description>.sql`` or
      a ``post-apply`` hook - Flyway skips it, and it never runs
    - two files with the same version, or two repeatable ones with the same description, in one
      location or across several
    - a ``.rollback.sql`` file, a Flyway callback or ``.conf`` file, a migration that is not SQL
    - ``DELIMITER``, and ``-- depends:``
    - a description too long for the history

    Directories are read at any depth, as Flyway reads them, skipping hidden ones. Other files -
    an ``__init__.py`` that makes a directory a package, a ``README`` - are left alone.
    """
    return _scan(*locations).migrations


def _scan(*locations: str | Path) -> Scripts:
    if not locations:
        raise ValidationError("at least one migrations location is required")

    migrations: list[MigrationScript] = []
    hooks: list[Hook] = []
    for location in locations:
        directory = Path(location)
        if not directory.is_dir():
            raise ValidationError(f"no migrations directory at {directory}")
        for path in _files(directory):
            found = _read(path, directory)
            if isinstance(found, Hook):
                hooks.append(found)
            elif found is not None:
                migrations.append(found)

    versioned = [m for m in migrations if not m.repeatable]
    repeatable = [m for m in migrations if m.repeatable]
    _refuse_duplicates(versioned, repeatable)
    for path in [*(m.path for m in migrations), *(h.path for h in hooks)]:
        _refuse_content(path)
        _load(path)
    return Scripts(
        tuple(sorted(versioned, key=lambda m: m.key)),
        tuple(sorted(repeatable, key=lambda m: description_order(m.description))),
        tuple(hooks),
    )


def _files(directory: Path) -> list[Path]:
    """Every file under ``directory``, at any depth, in a stable order - skipping anything hidden,
    as Flyway skips ``.hidden/``."""
    found = []
    for path in sorted(directory.rglob("*")):
        if any(part.startswith(".") for part in path.relative_to(directory).parts):
            continue
        if path.is_file():
            found.append(path)
    return found


def _read(path: Path, directory: Path) -> MigrationScript | Hook | None:
    name = path.name
    script = path.relative_to(directory).as_posix()
    lowered = name.lower()
    if lowered.endswith(".rollback.sql"):
        raise MigrationError(
            f"{path}: rollback files are not supported. To undo a migration that has run, add a "
            f"new one that reverses it - an applied migration is history."
        )
    if lowered.endswith(".sql.conf"):
        raise MigrationError(
            f"{path}: Flyway's script configuration files are not read here. To run a migration "
            f"outside a transaction, make `-- transactional: false` its first line."
        )
    if lowered.endswith(".sql"):
        if len(script) > SCRIPT_LIMIT:
            raise MigrationError(
                f"{path}: the path is longer than the {SCRIPT_LIMIT} characters the history keeps"
            )
        versioned = VERSIONED_NAME.fullmatch(name)
        repeatable = None if versioned else REPEATABLE_NAME.fullmatch(name)
        matched = versioned or repeatable
        if matched is not None:
            description = matched["description"].replace("_", " ")
            if len(description) > DESCRIPTION_LIMIT:
                # Flyway cuts it to fit, and then cannot find a repeatable migration's row by it:
                # the next migrate fails, saying the migration is not there. Measured.
                raise MigrationError(
                    f"{path}: the description is {len(description)} characters, and the history "
                    f"keeps {DESCRIPTION_LIMIT}. Shorten the file name."
                )
            return MigrationScript(
                version=versioned["version"] if versioned else None,
                description=description,
                path=path,
                checksum=checksum(path),
                script=script,
            )
        if HOOK_NAME.fullmatch(name):
            return Hook(path, script)
        if FLYWAY_CALLBACK.fullmatch(name):
            raise MigrationError(
                f"{path}: Flyway callbacks are not run here. The one this has is yoyo's post-apply "
                f"hook, which is Flyway's afterMigrate: a file named post-apply.sql runs at the "
                f"end of every migrate."
            )
        if REPEATABLE_WITH_A_VERSION.fullmatch(name):
            raise MigrationError(
                f"{path}: a repeatable migration has no version - name it R__<description>.sql. "
                f"Flyway would skip this file, and it would never run."
            )
        raise MigrationError(
            f"{path}: a migration's file name must be V<version>__<description>.sql, like "
            f"V0001__create_wallets.sql, or R__<description>.sql for a repeatable one, or start "
            f"with post-apply for a hook. A file that does not match would otherwise be skipped "
            f"without a word, and never run."
        )
    if LOOKS_LIKE_A_MIGRATION.fullmatch(path.stem):
        raise MigrationError(
            f"{path}: only SQL migrations are supported. A file named like a migration that is "
            f"not one - a script, an editor's backup - does not belong beside them."
        )
    return None


def _refuse_duplicates(
    versioned: Sequence[MigrationScript], repeatable: Sequence[MigrationScript]
) -> None:
    lines: list[str] = []
    by_version: dict[tuple[int, ...], list[MigrationScript]] = {}
    for script in versioned:
        by_version.setdefault(script.key, []).append(script)
    for found in by_version.values():
        if len(found) > 1:
            lines.append(f"version {found[0].version}: " + ", ".join(str(s.path) for s in found))
    by_description: dict[str, list[MigrationScript]] = {}
    for script in repeatable:
        by_description.setdefault(script.description, []).append(script)
    for found in by_description.values():
        if len(found) > 1:
            lines.append(
                f"repeatable {found[0].description!r}: " + ", ".join(str(s.path) for s in found)
            )
    if lines:
        raise MigrationError(
            "two migrations are one migration - each version, and each repeatable migration's "
            "description, belongs to one file. Give one of them the next free version, or "
            "another description:\n  " + "\n  ".join(lines)
        )


def _refuse_content(path: Path) -> None:
    from yoyo.migrations import read_sql_migration

    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise MigrationError(f"{path} is not UTF-8 text: {error}") from None
    if DELIMITER.search(text):
        raise MigrationError(
            f"{path}: `DELIMITER` is a command of the mysql client, not SQL, and is not "
            f"understood here. Leave it out: a procedure's or a trigger's BEGIN ... END body is "
            f"kept whole without it, and every other statement ends at its `;`."
        )
    directives, _, _ = read_sql_migration(str(path))
    if directives.get("depends", "").strip():
        raise MigrationError(
            f"{path}: `-- depends:` is not supported. Migrations run in the order of their "
            f"version numbers and nothing else; give this one a version after the migrations "
            f"it depends on."
        )


# -- the history -----------------------------------------------------------------------------


def history_table(metadata: MetaData, *, name: str = HISTORY_TABLE) -> Table:
    """Flyway's history table: its columns, its index on ``success``, under this name.

    Two things differ, both on purpose. ``checksum`` is a SHA-256, which sees an edit that joins
    two lines where Flyway's CRC32 does not. ``installed_on`` is text - an ISO-8601 instant in
    UTC - rather than a timestamp column, so the row reads the same on every dialect and no
    driver's idea of a date gets in the way.
    """
    return Table(
        name,
        metadata,
        Column("installed_rank", Integer, primary_key=True, autoincrement=False),
        Column("version", String(50), nullable=True),
        Column("description", String(DESCRIPTION_LIMIT), nullable=False),
        Column("type", String(20), nullable=False),
        Column("script", String(SCRIPT_LIMIT), nullable=False),
        Column("checksum", String(64), nullable=True),
        Column("installed_by", String(100), nullable=False),
        Column("installed_on", String(32), nullable=False),
        Column("execution_time", Integer, nullable=False),
        Column("success", Boolean, nullable=False),
        Index(f"{name}_s_idx", "success"),
        extend_existing=True,
    )


class _Row(NamedTuple):
    installed_rank: int
    version: str | None
    description: str
    type: str
    script: str
    checksum: str | None
    installed_by: str
    installed_on: str
    execution_time: int
    success: bool

    def __str__(self) -> str:
        if self.version is None:
            return f"repeatable migration {self.description!r} ({self.script})"
        return f"version {self.version} ({self.script})"


@dataclass(slots=True)
class _Session:
    """One database, locked, and what everything done to it needs."""

    backend: _Common
    label: str
    output_query_results: bool = True
    installed_by: str = ""


def _prepare_history(session: _Session) -> None:
    """Make sure the history table is there - and refuse the two databases Flyway refuses.

    A schema with anything in it and no history was built by something else - by hand, by
    ``create_all``, by another tool. Running every migration from the first against it is either
    an error on the first ``CREATE TABLE`` or, worse, a partial success. Flyway refuses it and
    points at ``baseline``; so does this.
    """
    backend = session.backend
    if backend.table_exists(HISTORY_TABLE):
        _refuse_an_old_shape(session)
        return
    found = backend.schema_objects()
    if found:
        raise MigrationError(
            f"{session.label} already has {', '.join(found)}"
            f"{' and more' if len(found) == 5 else ''} but no {HISTORY_TABLE}, so nothing says "
            f"which migrations built it. If it is what your migrations up to some version make, "
            f"record that with baseline(version=...) and run again - the migrations after it "
            f"then run. If it should be empty, this is the wrong database."
        )
    _create_history(backend)


def _create_history(backend: _Common) -> None:
    with backend.transaction():
        _create_history_table(backend)


def _create_history_table(backend: _Common) -> None:
    table = history_table(MetaData())
    dialect = _dialect(backend.dialect)
    backend.execute(str(CreateTable(table).compile(dialect=dialect)))
    for index in table.indexes:
        backend.execute(str(CreateIndex(index).compile(dialect=dialect)))


def _refuse_an_old_shape(session: _Session) -> None:
    if "installed_rank" not in session.backend.columns(HISTORY_TABLE):
        raise MigrationError(
            f"{session.label}: {HISTORY_TABLE} was made by a development build of this migrator "
            f"from before repeatable migrations, and has a different shape. No release ever made "
            f"it, so there is nothing to upgrade: drop it, and either drop what its migrations "
            f"made or record it with baseline(version=...), then run again."
        )


def _read_history(backend: _Common) -> list[_Row]:
    rows = backend.execute(
        "SELECT installed_rank, version, description, type, script, checksum, installed_by, "
        f"installed_on, execution_time, success FROM {HISTORY_TABLE} ORDER BY installed_rank"
    ).fetchall()
    return [
        _Row(
            int(row[0]),
            row[1],
            row[2] or "",
            row[3],
            row[4],
            row[5],
            row[6] or "",
            row[7],
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
    checksum: str | None,
    execution_time: int,
    success: bool,
) -> None:
    """One row, ranked after the last. Called under the lock, so nothing can take the rank."""
    backend = session.backend
    rank = backend.execute(
        f"SELECT COALESCE(MAX(installed_rank), 0) + 1 FROM {HISTORY_TABLE}"
    ).fetchone()[0]
    backend.execute(
        f"INSERT INTO {HISTORY_TABLE} (installed_rank, version, description, type, script, "
        "checksum, installed_by, installed_on, execution_time, success) VALUES (:installed_rank, "
        ":version, :description, :type, :script, :checksum, :installed_by, :installed_on, "
        ":execution_time, :success)",
        {
            "installed_rank": int(rank),
            "version": version,
            "description": description,
            "type": type,
            "script": script,
            "checksum": checksum,
            "installed_by": session.installed_by,
            "installed_on": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "execution_time": execution_time,
            "success": success,
        },
    )


def _record(
    session: _Session, script: MigrationScript, started: float, *, success: bool = True
) -> None:
    _insert(
        session,
        version=script.version,
        description=script.description,
        type="SQL",
        script=script.script,
        checksum=script.checksum,
        execution_time=int((time.monotonic() - started) * 1000),
        success=success,
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

    #: ``Versioned``, ``Repeatable``, or empty for a baseline row.
    category: str
    version: str | None
    description: str
    #: ``SQL`` or ``BASELINE``.
    type: str
    script: str
    checksum: str | None
    state: MigrationState
    installed_rank: int | None = None
    installed_on: str | None = None
    installed_by: str | None = None
    execution_time: int | None = None


@dataclass(slots=True)
class _Model:
    """The files against the history, worked out once and used by migrate, info and repair
    alike, so the three cannot disagree."""

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
    "keep every schema change on their own, and a file marked `-- transactional: false` runs "
    "outside a transaction - so part of it may be in the database. Put the database right by "
    "hand, finishing what it started or undoing it, then run repair(): that removes the failed "
    "row, and the next run applies the migration again."
)


def _failed_and_gone(row: _Row) -> str:
    return (
        f"{row} failed on an earlier run, and its file is gone. Part of it may be in the "
        f"database: put that right by hand, then run repair(), which removes the failed row."
    )


def _info(row: _Row, state: MigrationState) -> MigrationInfo:
    if row.type == "BASELINE":
        category = ""
    else:
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
    _versioned(model, scripts.versioned, rows)
    _repeatable(model, scripts.repeatable, rows)
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
            if script.checksum != row.checksum:
                model.misaligned.append((row, script))
                model.problems.append(
                    f"{script.script} has changed since it was applied "
                    f"({(row.checksum or '')[:12]} then, {script.checksum[:12]} now). An applied "
                    f"migration is history: put the file back as it was, and make the change in "
                    f"a new migration."
                )
            elif script.description != row.description:
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
    files = {script.description: script for script in scripts}
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
        elif row.checksum != script.checksum:
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
            and newest_row.checksum == script.checksum
        )
        if not up_to_date:
            pending.append(script)
    pending.sort(key=lambda s: description_order(s.description))
    model.pending += pending
    model.infos += [_unapplied(script, MigrationState.PENDING) for script in pending]


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
    #: The highest version applied after it.
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
    """A target per member of the layout, on its ``primaryConfig`` - Java's ``targets``.

    The primary, as Java's Flyway module migrates ``primaryConfig``: the replica follows it, and
    any other named config on the member is a pool onto the same schema.
    """
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
    scripts = _scan(*locations)
    return _run(_choose(config, shards), scripts, output_query_results)


def migrate_targets(
    migration_targets: Iterable[Target],
    *locations: str | Path,
    output_query_results: bool = True,
) -> list[Result]:
    """Java's ``FlywayMigrator.migrate(Target...)``."""
    chosen = list(migration_targets)
    if not chosen:
        raise ValidationError("at least one migration target is required")
    return _run(chosen, _scan(*locations), output_query_results)


def _each[T](
    chosen: Sequence[Target],
    verb: str,
    action: Callable[[Target], T],
    *,
    log_the_stop: bool = False,
) -> list[T]:
    """``action`` on each target in turn, stopping at the first that fails - and saying which
    targets were already done, since the database's own error names none of them.

    ``log_the_stop`` writes it to the log as well, as Java's builder ``migrate()`` does:
    ``Stopped at <target>; already migrated: [<names>]`` at ERROR, outside the target's log
    context.

    The one way in to every database a migration run touches, so the sync-on-a-loop refusal is
    here: refused before the first target, raised as itself rather than as a ``MigrationError``.
    """
    refuse_on_an_event_loop("ekbatan.migrations (migrate, info, repair, baseline)")
    results: list[T] = []
    done: list[str] = []
    for each in chosen:
        try:
            results.append(action(each))
        except Exception as error:
            if log_the_stop:
                LOG.error("Stopped at %s; already migrated: [%s]", each.label, ", ".join(done))
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
    chosen: Sequence[Target],
    scripts: Scripts,
    output_query_results: bool,
    *,
    log_the_stop: bool = True,
) -> list[Result]:
    """Migrate ``chosen`` in turn. ``log_the_stop`` is off only for Java's single-database
    ``migrate(DataSourceConfig, ...)``, which has no run of targets to stop."""

    def one(each: Target) -> Result:
        with _in_log_context(each):
            try:
                outcome = _migrate(each, scripts, output_query_results)
            except Exception:
                # Java's `inLogContext`: an ERROR line naming the target, inside its log context.
                # The failure itself is in the exception, which goes on to the caller.
                LOG.error("Migrating %s failed", each.label)
                raise
            LOG.info(
                "%s is at version %s (%d applied)",
                each.name,
                outcome.target_schema_version,
                outcome.migrations_executed,
            )
        return Result(each, outcome)

    return _each(chosen, "migrated", one, log_the_stop=log_the_stop)


class _Failed(Exception):
    """A migration failed; ``recorded`` says whether its failure was written down."""

    def __init__(self, error: BaseException, recorded: bool | BaseException) -> None:
        super().__init__(str(error))
        self.error = error
        self.recorded = recorded


class _HookFailed(Exception):
    def __init__(self, hook: Hook, error: BaseException) -> None:
        super().__init__(str(error))
        self.hook = hook
        self.error = error


def _open(each: Target) -> _Common:
    from ._yoyo import open_backend

    return open_backend(
        each.label, each.data_source.sqlalchemy_url(), each.data_source.data_source_properties
    )


def _migrate(each: Target, scripts: Scripts, output_query_results: bool) -> MigrateResult:
    from ._yoyo import MigrationBackendError

    label = each.label
    backend: _Common | None = None
    current: MigrationScript | None = None
    applied: list[MigrationScript] = []
    warnings: list[str] = []
    before: str | None = None
    if not scripts.migrations:
        warnings.append("no migrations found - are the locations right?")
    try:
        backend = _open(each)
        backend.refuse_if_read_only()
        backend.acquire()
        try:
            session = _Session(backend, label, output_query_results, backend.current_user())
            _prepare_history(session)
            model = _model(scripts, _read_history(backend))
            model.refuse(label)
            warnings += model.warnings
            for warning in warnings:
                LOG.warning("%s: %s", label, warning)
            before = model.schema_version
            LOG.info("%s: current version %s", label, before or "<< empty >>")
            for current in model.pending:
                _apply(session, current)
                applied.append(current)
            current = None
            _run_hooks(session, scripts.hooks)
        finally:
            backend.release()
    except MigrationError:
        raise
    except MigrationBackendError as error:
        raise MigrationError(str(error)) from error
    except _Failed as failed:
        raise MigrationError(
            _failure(label, backend, current, applied, failed.error, failed.recorded)
        ) from failed.error
    except _HookFailed as failed:
        ran = f" ({', '.join(s.script for s in applied)})" if applied else ""
        raise MigrationError(
            f"migrating {label}: the post-apply hook {failed.hook.script} failed: "
            f"{type(failed.error).__name__}: {failed.error}. Every migration this run applied is "
            f"recorded{ran}; the hook runs again at the end of the next run, as Flyway's "
            f"afterMigrate does."
        ) from failed.error
    except Exception as error:
        raise MigrationError(_failure(label, backend, current, applied, error, False)) from error
    finally:
        if backend is not None:
            backend.close()

    target_version = before
    for script in applied:
        if script.version is not None and (
            target_version is None or script.key > version_key(target_version)
        ):
            target_version = script.version
    if applied:
        LOG.info(
            "%s: applied %d migration%s%s",
            label,
            len(applied),
            "" if len(applied) == 1 else "s",
            f", now at version {target_version}" if target_version else "",
        )
    else:
        LOG.info("%s: up to date, nothing to apply", label)
    return MigrateResult(
        initial_schema_version=before,
        target_schema_version=target_version,
        migrations=tuple(s.script for s in applied),
        warnings=tuple(warnings),
    )


def _apply(session: _Session, script: MigrationScript) -> None:
    """Run one migration and record it - together, where the database can roll back a schema
    change, so a crash cannot leave it applied and unrecorded. A failure that could not be rolled
    back is recorded as one, as Flyway records it, so the next run stops rather than running it
    again over what it half did."""
    backend, label = session.backend, session.label
    migration = script.load()
    printed = io.StringIO()
    started = time.monotonic()
    if script.repeatable:
        LOG.info("%s: applying repeatable migration %r", label, script.description)
    else:
        LOG.info("%s: migrating to version %s - %s", label, script.version, script.description)
    try:
        # yoyo prints what a SELECT in a migration returns, straight to stdout. Captured here and
        # handed to the log instead, as Flyway logs it - on by default, as Flyway has it.
        with redirect_stdout(printed):
            if migration.use_transactions:
                # One transaction for the migration and its row. On MySQL and MariaDB a schema
                # change still commits on its own inside it - nothing can stop that - but a
                # migration that only changes data is recorded atomically there too.
                with backend.transaction():
                    migration.process_steps(backend, "apply")
                    _record(session, script, started)
            else:
                migration.process_steps(backend, "apply")
                with backend.transaction():
                    _record(session, script, started)
    except Exception as error:
        recorded: bool | BaseException = False
        if not (backend.has_transactional_ddl and migration.use_transactions):
            try:
                with backend.transaction():
                    _record(session, script, started, success=False)
                recorded = True
            except Exception as unrecorded:
                recorded = unrecorded
        raise _Failed(error, recorded) from error
    finally:
        output = printed.getvalue().rstrip()
        if output and session.output_query_results:
            LOG.info("%s: %s returned:\n%s", label, script.script, output)


def _run_hooks(session: _Session, hooks: Sequence[Hook]) -> None:
    backend, label = session.backend, session.label
    for hook in hooks:
        migration = hook.load()
        printed = io.StringIO()
        try:
            with redirect_stdout(printed):
                if migration.use_transactions:
                    with backend.transaction():
                        migration.process_steps(backend, "apply")
                else:
                    migration.process_steps(backend, "apply")
        except Exception as error:
            raise _HookFailed(hook, error) from error
        finally:
            output = printed.getvalue().rstrip()
            if output and session.output_query_results:
                LOG.info("%s: %s returned:\n%s", label, hook.script, output)
        LOG.info("%s: ran %s", label, hook.script)


def _failure(
    label: str,
    backend: _Common | None,
    current: MigrationScript | None,
    applied: Sequence[MigrationScript],
    error: BaseException,
    recorded: bool | BaseException,
) -> str:
    """What happened, where, and what state it left - the underlying error only names a url."""
    where = f" in {current.script}" if current is not None else ""
    parts = [f"migrating {label} failed{where}: {type(error).__name__}: {error}".rstrip()]
    if backend is not None and backend.connection_lost():
        parts.append("The connection to the database was lost.")
    if applied:
        parts.append(f"Applied by this run before that: {', '.join(s.script for s in applied)}.")
    if current is not None and backend is not None:
        if backend.has_transactional_ddl and current.load().use_transactions:
            parts.append(f"Nothing of {current.script} remains: it was rolled back as a whole.")
        else:
            why = (
                "MySQL and MariaDB commit each one on its own"
                if not backend.has_transactional_ddl
                else "it ran outside a transaction"
            )
            parts.append(f"Any change {current.script} had already made stays - {why}.")
            if recorded is True:
                parts.append(
                    "It is recorded as failed, so the next run stops here until the database has "
                    "been checked and put right, and repair() has been run."
                )
            else:
                reason = "" if recorded is False else f" ({recorded})"
                parts.append(
                    f"Recording it as failed did not work either{reason}, so the next run would "
                    f"apply it again: check the database before running it."
                )
    return " ".join(parts)


# -- info, repair, baseline ------------------------------------------------------------------


def info(
    config: ShardingConfig,
    *locations: str | Path,
    shards: Iterable[ShardIdentifier] | None = None,
) -> list[InfoResult]:
    """Where each migration stands on each database - Flyway's ``info``.

    Changes nothing and takes no lock, as Flyway's takes none; ``problems`` is what ``migrate``
    would refuse.
    """
    scripts = _scan(*locations)
    return _each(_choose(config, shards), "read", lambda each: _info_of(each, scripts))


def _info_of(each: Target, scripts: Scripts) -> InfoResult:
    backend = _open(each)
    try:
        session = _Session(backend, each.label)
        rows: list[_Row] = []
        if backend.table_exists(HISTORY_TABLE):
            _refuse_an_old_shape(session)
            rows = _read_history(backend)
        model = _model(scripts, rows)
        return InfoResult(
            target=each,
            schema_version=model.schema_version,
            migrations=tuple(model.infos),
            problems=tuple(model.problems),
            warnings=tuple(model.warnings),
        )
    finally:
        backend.close()


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
    scripts = _scan(*locations)
    return _each(_choose(config, shards), "repaired", lambda each: _repair(each, scripts))


def _repair(each: Target, scripts: Scripts) -> RepairResult:
    from ._yoyo import MigrationBackendError

    label = each.label
    backend = _open(each)
    try:
        backend.refuse_if_read_only()
        backend.acquire()
        try:
            result = _repair_locked(each, scripts, backend)
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
            LOG.info("%s: repair %s %s", label, kind, one)
    if result.removed:
        LOG.warning(
            "%s: failed migrations were removed from the history; whatever they did to the "
            "schema before failing is still there, and is for a person to finish or undo",
            label,
        )
    if not (result.removed or result.deleted or result.aligned):
        LOG.info("%s: nothing to repair", label)
    return result


def _repair_locked(each: Target, scripts: Scripts, backend: _Common) -> RepairResult:
    session = _Session(backend, each.label, installed_by=backend.current_user())
    if not backend.table_exists(HISTORY_TABLE):
        return RepairResult(each)
    _refuse_an_old_shape(session)
    with backend.transaction():
        failed = [row for row in _read_history(backend) if not row.success]
        backend.execute(f"DELETE FROM {HISTORY_TABLE} WHERE success = :success", {"success": False})
        model = _model(scripts, _read_history(backend))
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
                f"UPDATE {HISTORY_TABLE} SET description = :description, checksum = :checksum "
                "WHERE installed_rank = :installed_rank",
                {
                    "description": script.description,
                    "checksum": script.checksum,
                    "installed_rank": row.installed_rank,
                },
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
    if not re.fullmatch(r"\d+(?:[._]\d+)*", version or ""):
        raise ValidationError(f"a baseline version is digits separated by . or _, not {version!r}")
    if not description or len(description) > DESCRIPTION_LIMIT:
        raise ValidationError(f"a baseline needs a description of at most {DESCRIPTION_LIMIT}")
    return _each(
        _choose(config, shards), "baselined", lambda each: _baseline(each, version, description)
    )


def _baseline(each: Target, version: str, description: str) -> BaselineResult:
    from ._yoyo import MigrationBackendError

    label = each.label
    backend = _open(each)
    try:
        backend.refuse_if_read_only()
        backend.acquire()
        try:
            session = _Session(backend, label, installed_by=backend.current_user())
            if backend.table_exists(HISTORY_TABLE):
                _refuse_an_old_shape(session)
                rows = _read_history(backend)
                marker = next((row for row in rows if row.type == "BASELINE"), None)
                if marker is not None:
                    same = version_key(marker.version or "") == version_key(version)
                    if same and marker.description == description:
                        LOG.info("%s: already baselined at version %s", label, marker.version)
                        return BaselineResult(each, marker.version or version, description, False)
                    raise MigrationError(
                        f"{label} has already been baselined at version {marker.version} "
                        f"({marker.description!r}), not {version} ({description!r})."
                    )
                if rows:
                    raise MigrationError(
                        f"{label} already has migrations in its history, so there is nothing to "
                        f"baseline: the history already says what built it."
                    )
                raise MigrationError(
                    f"{label} has an empty {HISTORY_TABLE}. Drop the table, and baseline again."
                )
            if not backend.schema_objects():
                raise MigrationError(
                    f"{label} is empty, so there is nothing to baseline - baselining it would "
                    f"skip the migrations that build it. Run migrate instead."
                )
            # One transaction for the table and its baseline row, so a failure leaves neither -
            # where DDL is transactional. MySQL commits the CREATE TABLE on its own.
            with backend.transaction():
                _create_history_table(backend)
                _insert(
                    session,
                    version=version,
                    description=description,
                    type="BASELINE",
                    script=description,
                    checksum=None,
                    execution_time=0,
                    success=True,
                )
        finally:
            backend.release()
    except MigrationBackendError as error:
        raise MigrationError(str(error)) from error
    finally:
        backend.close()
    LOG.info("%s: baselined at version %s", label, version)
    return BaselineResult(each, version, description)


class YoyoMigrator:
    """Java's ``FlywayMigrator``, over yoyo: its static ``migrate`` overloads, ``targets``,
    ``target`` and ``builder`` - and Flyway's ``info``, ``repair`` and ``baseline``, which Java
    reaches through Flyway itself."""

    # Java nests these two in `FlywayMigrator`; so does this, as well as the module having them.
    Target = Target
    Result = Result

    migrate = staticmethod(migrate)
    migrate_targets = staticmethod(migrate_targets)
    targets = staticmethod(targets)
    target = staticmethod(target)
    info = staticmethod(info)
    repair = staticmethod(repair)
    baseline = staticmethod(baseline)

    @staticmethod
    def migrate_data_source(
        data_source: DataSourceConfig,
        *locations: str | Path,
        output_query_results: bool = True,
    ) -> MigrateResult:
        """Java's ``migrate(DataSourceConfig, locations...)``: one database, outside a layout."""
        (result,) = _run(
            [target(data_source)],
            _scan(*locations),
            output_query_results,
            log_the_stop=False,
        )
        return result.migrate_result

    @staticmethod
    def builder() -> YoyoMigratorBuilder:
        return YoyoMigratorBuilder()


class YoyoMigratorBuilder:
    """Fluent builder that runs the migration. Obtain via :meth:`YoyoMigrator.builder`.

    Java's ``withDataSource``, ``withShardingConfig``, ``withTarget``, ``withTargets``,
    ``locations`` and ``migrate()``. Java's ``customize`` hands over Flyway's own configuration;
    the one setting that means something here is ``output_query_results``, which is Flyway's
    ``outputQueryResults``. ``info()``, ``repair()`` and ``baseline()`` run Flyway's commands of
    those names against the same targets.
    """

    __slots__ = ("_locations", "_output_query_results", "_targets")

    def __init__(self) -> None:
        self._targets: list[Target] = []
        self._locations: tuple[str | Path, ...] = ()
        self._output_query_results = True

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
        self._locations = locations
        return self

    def output_query_results(self, enabled: bool) -> Self:
        self._output_query_results = enabled
        return self

    def _chosen(self) -> list[Target]:
        if not self._targets:
            raise ValidationError("at least one migration target is required")
        return list(self._targets)

    def migrate(self) -> list[Result]:
        return migrate_targets(
            self._targets, *self._locations, output_query_results=self._output_query_results
        )

    def info(self) -> list[InfoResult]:
        scripts = _scan(*self._locations)
        return _each(self._chosen(), "read", lambda each: _info_of(each, scripts))

    def repair(self) -> list[RepairResult]:
        scripts = _scan(*self._locations)
        return _each(self._chosen(), "repaired", lambda each: _repair(each, scripts))

    def baseline(
        self, version: str = "1", description: str = BASELINE_DESCRIPTION
    ) -> list[BaselineResult]:
        if not re.fullmatch(r"\d+(?:[._]\d+)*", version or ""):
            raise ValidationError(
                f"a baseline version is digits separated by . or _, not {version!r}"
            )
        return _each(
            self._chosen(), "baselined", lambda each: _baseline(each, version, description)
        )


# -- development helpers ---------------------------------------------------------------------


def migrate_registry(registry: DatabaseRegistry, metadata: MetaData) -> None:
    """Create anything in ``metadata`` that is missing, on every shard.

    For development and tests. Real deployments migrate from files, because ``create_all`` has
    no answer for a column that changed.

    The event tables land where the framework reads them, in ``eventlog``, created here first: a
    schema on PostgreSQL, a database of its own on MySQL and MariaDB. On SQLite they are in the
    file itself (:func:`~ekbatan.schema.eventlog_translation`).
    """
    from sqlalchemy import text

    from .schema import _EVENTLOG_CREATES, EVENTLOG_SCHEMA, eventlog_translation

    in_eventlog = any(table.schema == EVENTLOG_SCHEMA for table in metadata.tables.values())
    for shard in registry.shards:
        engine = registry.manager(shard)._engine()
        translation = eventlog_translation(engine.dialect.name)
        if translation is not None:
            engine = engine.execution_options(schema_translate_map=translation)
        elif in_eventlog:
            with engine.begin() as conn:
                conn.execute(text(_EVENTLOG_CREATES[engine.dialect.name]))
        metadata.create_all(engine)


def emit_ddl(metadata: MetaData, dialect: str) -> str:
    """The ``CREATE`` statements for these tables, as text, for a given dialect.

    A starting point for the first migration - run it, read it, commit it as a file. Generated
    once and then owned by you, rather than regenerated behind your back on every deploy.

    The event tables are written where the framework reads them, in ``eventlog``, created
    first: a schema on PostgreSQL, a database of its own on MySQL and MariaDB - Java's
    ``V0000__create_eventlog_database.sql``. On SQLite they are in the file itself
    (:func:`~ekbatan.schema.eventlog_translation`).
    """
    from .schema import _EVENTLOG_CREATES, EVENTLOG_SCHEMA, eventlog_translation

    target_dialect = _dialect(dialect)
    translation = eventlog_translation(target_dialect.name) or {}
    statements: list[str] = []
    schema_created = False
    for original in metadata.sorted_tables:
        table = original
        if original.schema in translation:
            # Copied rather than altered: the caller's metadata keeps the table as declared.
            # `None` is SQLAlchemy's documented "the target metadata's schema", which its type
            # stub leaves out.
            table = original.to_metadata(MetaData(), schema=cast(Any, translation[original.schema]))
        elif original.schema == EVENTLOG_SCHEMA and not schema_created:
            statements.append(f"{_EVENTLOG_CREATES[target_dialect.name]};")
            schema_created = True
        statements.append(str(CreateTable(table).compile(dialect=target_dialect)).strip() + ";")
        # By name: `table.indexes` is a set, so its order changes from one process to the next,
        # and output that is committed as a file has to be the same every time it is generated.
        statements += [
            str(CreateIndex(index).compile(dialect=target_dialect)).strip() + ";"
            for index in sorted(table.indexes, key=lambda index: str(index.name))
        ]
    return "\n\n".join(statements) + "\n"


def _dialect(name: str) -> Any:
    """The dialect itself, not an engine - so this works without the driver installed.

    Generating Postgres DDL on a machine that only talks to MySQL is a reasonable thing to
    want, and needing a driver for it would be a surprise with no upside.
    """
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
