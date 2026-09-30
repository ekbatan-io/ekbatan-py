"""Getting the schema onto every database, not just the first one.

A shard added to configuration and forgotten by the migrator is a database that accepts writes
right up until the first one touches a column that is not there. Migrating from the same
configuration the framework routes from is what keeps the two from drifting apart.

Java's counterpart is ``FlywayMigrator``. This is that, over `yoyo
<https://ollycope.com/software/yoyo/>`_, used as a library so an application migrates itself at
startup without any external binary::

    from ekbatan.migrations import migrate

    migrate(config, "migrations")   # every shard, in order, one at a time, fail-fast

**The files** are plain SQL, named as Flyway names them: ``V<version>__<description>.sql`` -
``V0001__create_wallets.sql``, ``V2__add_index.sql``, ``V1.1__backfill.sql``. They run in the
order of their version *numbers*, so ``V10`` runs after ``V2``. A file that marks itself
``-- transactional: false`` on its first line runs outside a transaction, which is what
PostgreSQL's ``CREATE INDEX CONCURRENTLY`` needs.

yoyo reads the files and runs their statements. Everything else is here, because yoyo on its own
gets each of these wrong - each measured, not assumed:

- **Two files with the same version** both run in yoyo, because its name for a migration is the
  whole file name. Here they are refused before any database is touched, so a test that calls
  :func:`validate_scripts` fails in CI.
- **An applied migration that was edited, renamed or deleted, or a new one older than one already
  applied** is accepted silently by yoyo. Here each is refused before anything is applied, as
  Flyway refuses it. A migration applied by a *newer* release - newer than every file here - is
  left alone with a warning, as Flyway leaves it, so an older release can still start.
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
  MySQL and MariaDB commit every schema change on their own, so there it cannot be one step, in
  this or in any tool.
- **A replica.** Each database is checked to be a writable primary before anything else, so a
  url that reaches a replica, or a proxy that routes to one, is refused rather than migrated.

The history lives in ``ekbatan_schema_history``: one row per applied migration, with a checksum of
what the file contained.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import time
from collections.abc import Iterable, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Self

from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.schema import CreateIndex, CreateTable

from .config import DataSourceConfig, ShardingConfig
from .errors import EkbatanError, ValidationError
from .persistence import DatabaseRegistry
from .sharding import DEFAULT_SHARD, ShardIdentifier

if TYPE_CHECKING:
    from ._yoyo import _Common

__all__ = [
    "HISTORY_TABLE",
    "MigrateResult",
    "MigrationError",
    "MigrationScript",
    "Result",
    "Target",
    "YoyoMigrator",
    "YoyoMigratorBuilder",
    "emit_ddl",
    "history_table",
    "migrate",
    "migrate_registry",
    "migrate_targets",
    "target",
    "targets",
    "validate_scripts",
]

LOG = logging.getLogger("ekbatan.migrations")

#: One row per applied migration: which version, which file, and what the file contained.
HISTORY_TABLE = "ekbatan_schema_history"

#: Flyway's versioned-migration name. The version is digits separated by `.` or `_`, so `V1_1`
#: and `V1.1` are the same version, as they are in Flyway.
SCRIPT_NAME = re.compile(r"V(?P<version>\d+(?:[._]\d+)*)__(?P<description>.+)\.sql")
#: Something that looks like a migration but is not SQL - `V3__backfill.py`.
LOOKS_LIKE_A_MIGRATION = re.compile(r"V\d+(?:[._]\d+)*__.+")


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
    text = text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MigrationScript:
    """One migration file, read and checked."""

    #: The version as written in the file name - ``0001`` for ``V0001__x.sql``.
    version: str
    #: The description, with underscores as spaces, as Flyway shows it.
    description: str
    path: Path
    checksum: str

    @property
    def script(self) -> str:
        """The file name, which is what the history records."""
        return self.path.name

    @property
    def key(self) -> tuple[int, ...]:
        return version_key(self.version)

    def load(self) -> Any:
        """The file as a yoyo migration, its statements split and its directives read."""
        from yoyo.migrations import Migration

        migration = Migration(self.path.stem, str(self.path), str(self.path.parent))
        try:
            migration.load()
        except KeyError:
            # yoyo reads `-- transactional:` as a key into {"true": ..., "false": ...}.
            raise MigrationError(
                f"{self.path}: `-- transactional:` must be `true` or `false`"
            ) from None
        return migration


def validate_scripts(*locations: str | Path) -> tuple[MigrationScript, ...]:
    """Read every migration in ``locations``, refuse what is wrong, and return them in order.

    Needs no database, so a test can call it and fail in CI:

    - a ``.sql`` file not named ``V<version>__<description>.sql`` - it would otherwise be skipped
      without a word and never run
    - two files with the same version, in one location or across several
    - a ``.rollback.sql`` file, or a migration that is not SQL
    - a ``-- depends:`` directive: the order is the version order, and nothing else

    Other files - an ``__init__.py`` that makes the directory a package, a ``README`` - are left
    alone. Subdirectories are not read, so a directory per dialect beside another does not
    merge into it.
    """
    if not locations:
        raise ValidationError("at least one migrations location is required")

    scripts: list[MigrationScript] = []
    for location in locations:
        directory = Path(location)
        if not directory.is_dir():
            raise ValidationError(f"no migrations directory at {directory}")
        for path in sorted(directory.iterdir()):
            script = _read(path)
            if script is not None:
                scripts.append(script)

    by_version: dict[tuple[int, ...], list[MigrationScript]] = {}
    for script in scripts:
        by_version.setdefault(script.key, []).append(script)
    duplicated = {key: found for key, found in by_version.items() if len(found) > 1}
    if duplicated:
        lines = [
            f"version {found[0].version}: " + ", ".join(str(s.path) for s in found)
            for found in duplicated.values()
        ]
        raise MigrationError(
            "two migrations have the same version - each version is one migration, so give "
            "one of them the next free number:\n  " + "\n  ".join(lines)
        )

    for script in scripts:
        _refuse_directives(script)
        script.load()
    return tuple(sorted(scripts, key=lambda s: s.key))


def _read(path: Path) -> MigrationScript | None:
    name = path.name
    if not path.is_file() or name.startswith("."):
        return None
    if name.endswith(".rollback.sql"):
        raise MigrationError(
            f"{path}: rollback files are not supported. To undo a migration that has run, add a "
            f"new one that reverses it - an applied migration is history."
        )
    if path.suffix == ".sql":
        matched = SCRIPT_NAME.fullmatch(name)
        if matched is None:
            raise MigrationError(
                f"{path}: a migration's file name must be V<version>__<description>.sql, like "
                f"V0001__create_wallets.sql. A file that does not match would otherwise be "
                f"skipped without a word, and never run."
            )
        return MigrationScript(
            version=matched["version"],
            description=matched["description"].replace("_", " "),
            path=path,
            checksum=checksum(path),
        )
    if LOOKS_LIKE_A_MIGRATION.fullmatch(path.stem):
        raise MigrationError(f"{path}: only SQL migrations are supported - V<version>__<desc>.sql")
    return None


def _refuse_directives(script: MigrationScript) -> None:
    from yoyo.migrations import read_sql_migration

    directives, _, _ = read_sql_migration(str(script.path))
    if directives.get("depends", "").strip():
        raise MigrationError(
            f"{script.path}: `-- depends:` is not supported. Migrations run in the order of "
            f"their version numbers and nothing else; give this one a version after the "
            f"migrations it depends on."
        )


# -- the history -----------------------------------------------------------------------------


def history_table(metadata: MetaData, *, name: str = HISTORY_TABLE) -> Table:
    """One row per applied migration.

    ``installed_on`` is text - an ISO-8601 instant in UTC - rather than a timestamp column, so the
    row reads the same on every dialect and no driver's idea of a date gets in the way.
    """
    return Table(
        name,
        metadata,
        Column("version", String(50), primary_key=True),
        Column("description", String(200), nullable=False),
        Column("script", String(1000), nullable=False),
        Column("checksum", String(64), nullable=False),
        Column("installed_on", String(32), nullable=False),
        Column("execution_time", Integer, nullable=False),
        extend_existing=True,
    )


class _Applied(NamedTuple):
    version: str
    script: str
    checksum: str


def _ensure_history(backend: _Common) -> None:
    ddl = CreateTable(history_table(MetaData()), if_not_exists=True)
    with backend.transaction():
        backend.execute(str(ddl.compile(dialect=_dialect(backend.dialect))))


def _read_history(backend: _Common) -> list[_Applied]:
    rows = backend.execute(f"SELECT version, script, checksum FROM {HISTORY_TABLE}").fetchall()
    return [_Applied(*row) for row in rows]


def _record(backend: _Common, script: MigrationScript, started: float) -> None:
    backend.execute(
        f"INSERT INTO {HISTORY_TABLE} "
        "(version, description, script, checksum, installed_on, execution_time) "
        "VALUES (:version, :description, :script, :checksum, :installed_on, :execution_time)",
        {
            "version": script.version,
            "description": script.description[:200],
            "script": script.script,
            "checksum": script.checksum,
            "installed_on": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "execution_time": int((time.monotonic() - started) * 1000),
        },
    )


def _plan(
    label: str, scripts: Sequence[MigrationScript], applied: Sequence[_Applied]
) -> tuple[list[MigrationScript], list[str]]:
    """What is still to run, after refusing every way the files and the database disagree.

    All of it is checked before anything runs, so a refusal never leaves a database half-way.
    """
    by_key = {script.key: script for script in scripts}
    latest_file = max(by_key, default=None)
    applied_keys = {version_key(row.version): row for row in applied}
    latest_applied = max(applied_keys, default=None)

    problems: list[str] = []
    warnings: list[str] = []
    for key, row in sorted(applied_keys.items()):
        script = by_key.get(key)
        if script is None:
            if latest_file is None or key > latest_file:
                warnings.append(
                    f"{row.script} (version {row.version}) has been applied to this database "
                    f"but is newer than every migration here - a newer release migrated it. "
                    f"Left alone, as Flyway leaves it."
                )
            else:
                problems.append(
                    f"{row.script} (version {row.version}) has been applied to this database but "
                    f"its file is gone. An applied migration is history: restore the file."
                )
        elif script.checksum != row.checksum:
            problems.append(
                f"{script.script} has changed since it was applied ({row.checksum[:12]} then, "
                f"{script.checksum[:12]} now). An applied migration is history: put the file "
                f"back as it was, and make the change in a new migration."
            )
        elif script.script != row.script:
            problems.append(
                f"version {row.version} was applied as {row.script}, and its file is now named "
                f"{script.script}. An applied migration is history: put the old name back."
            )

    pending = [script for script in scripts if script.key not in applied_keys]
    if latest_applied is not None:
        late = [script for script in pending if script.key < latest_applied]
        if late:
            ahead = applied_keys[latest_applied]
            problems += [
                f"{script.script} has not been applied, but version {ahead.version} already has. "
                f"Migrations run in version order, so this one would never run in the place its "
                f"number says: give it a version after {ahead.version}."
                for script in late
            ]

    if problems:
        raise MigrationError(
            f"{label}: the migrations here do not match what this database has applied, so "
            f"nothing was run:\n  - " + "\n  - ".join(problems)
        )
    return pending, warnings


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


@dataclass(frozen=True, slots=True)
class MigrateResult:
    """What one database's migration did - the parts of Flyway's ``MigrateResult`` that apply."""

    #: The highest version applied before this run, or ``None`` for a fresh database.
    initial_schema_version: str | None
    #: The highest version applied after it.
    target_schema_version: str | None
    #: The files this run applied, in the order it applied them.
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
    scripts = validate_scripts(*locations)
    chosen = targets(config)
    if shards is not None:
        wanted = set(shards)
        known = {t.shard for t in chosen}
        unknown = sorted(wanted - known, key=str)
        if unknown:
            raise ValidationError(
                f"cannot migrate {', '.join(str(s) for s in unknown)}: not in this layout, "
                f"which has {', '.join(str(t.shard) for t in chosen)}"
            )
        # Layout order, not argument order: a run that stops half-way leaves a state the
        # operator can reason about.
        chosen = [t for t in chosen if t.shard in wanted]
    return _run(chosen, scripts, output_query_results)


def migrate_targets(
    migration_targets: Iterable[Target],
    *locations: str | Path,
    output_query_results: bool = True,
) -> list[Result]:
    """Java's ``FlywayMigrator.migrate(Target...)``."""
    chosen = list(migration_targets)
    if not chosen:
        raise ValidationError("at least one migration target is required")
    return _run(chosen, validate_scripts(*locations), output_query_results)


def _run(
    chosen: Sequence[Target], scripts: Sequence[MigrationScript], output_query_results: bool
) -> list[Result]:
    results: list[Result] = []
    for each in chosen:
        try:
            outcome = _migrate(each, scripts, output_query_results)
        except MigrationError as error:
            done = ", ".join(r.target.name for r in results) or "none"
            raise MigrationError(
                f"{error}\nAlready migrated: {done}. Targets are migrated one at a time, so the "
                f"rest are untouched - fix this one and run again; what was applied is skipped."
            ) from error.__cause__ or error
        results.append(Result(each, outcome))
        LOG.info(
            "%s is at version %s (%d applied)",
            each.name,
            outcome.target_schema_version,
            outcome.migrations_executed,
        )
    return results


def _migrate(
    each: Target, scripts: Sequence[MigrationScript], output_query_results: bool
) -> MigrateResult:
    from ._yoyo import MigrationBackendError, open_backend

    label = f"{each.name} ({each.shard})"
    backend: _Common | None = None
    current: MigrationScript | None = None
    applied: list[MigrationScript] = []
    try:
        backend = open_backend(label, each.data_source.url, each.data_source.connect_args)
        backend.refuse_if_read_only()
        backend.acquire()
        try:
            _ensure_history(backend)
            history = _read_history(backend)
            pending, warnings = _plan(label, scripts, history)
            for warning in warnings:
                LOG.warning("%s: %s", label, warning)
            before = max((version_key(row.version) for row in history), default=None)
            for current in pending:
                _apply(backend, current, label, output_query_results)
                applied.append(current)
            current = None
        finally:
            backend.release()
    except MigrationError:
        raise
    except MigrationBackendError as error:
        raise MigrationError(str(error)) from error
    except Exception as error:
        raise MigrationError(_failure(label, backend, current, applied, error)) from error
    finally:
        if backend is not None:
            backend.close()

    versions = {version_key(row.version): row.version for row in history}
    versions.update({s.key: s.version for s in applied})
    return MigrateResult(
        initial_schema_version=None if before is None else versions[before],
        target_schema_version=versions[max(versions)] if versions else None,
        migrations=tuple(s.script for s in applied),
        warnings=tuple(warnings),
    )


def _apply(
    backend: _Common, script: MigrationScript, label: str, output_query_results: bool
) -> None:
    """Run one migration and record it - together, where the database can roll back a schema
    change, so a crash cannot leave it applied and unrecorded."""
    migration = script.load()
    printed = io.StringIO()
    started = time.monotonic()
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
                    _record(backend, script, started)
            else:
                migration.process_steps(backend, "apply")
                with backend.transaction():
                    _record(backend, script, started)
    finally:
        output = printed.getvalue().rstrip()
        if output and output_query_results:
            LOG.info("%s: %s returned:\n%s", label, script.script, output)
    LOG.info("%s: applied %s", label, script.script)


def _failure(
    label: str,
    backend: _Common | None,
    current: MigrationScript | None,
    applied: Sequence[MigrationScript],
    error: BaseException,
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
            parts.append(
                f"Any schema change {current.script} had already made stays - "
                + (
                    "MySQL and MariaDB commit each one on its own"
                    if not backend.has_transactional_ddl
                    else "it ran outside a transaction"
                )
                + " - so check the database before running it again."
            )
    return " ".join(parts)


class YoyoMigrator:
    """Java's ``FlywayMigrator``, over yoyo: its static ``migrate`` overloads, ``targets``,
    ``target`` and ``builder``."""

    # Java nests these two in `FlywayMigrator`; so does this, as well as the module having them.
    Target = Target
    Result = Result

    migrate = staticmethod(migrate)
    migrate_targets = staticmethod(migrate_targets)
    targets = staticmethod(targets)
    target = staticmethod(target)

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
    def builder() -> YoyoMigratorBuilder:
        return YoyoMigratorBuilder()


class YoyoMigratorBuilder:
    """Fluent builder that runs the migration. Obtain via :meth:`YoyoMigrator.builder`.

    Java's ``withDataSource``, ``withShardingConfig``, ``withTarget``, ``withTargets``,
    ``locations`` and ``migrate()``. Java's ``customize`` hands over Flyway's own configuration;
    the one setting that means something here is ``output_query_results``, which is Flyway's
    ``outputQueryResults``.
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

    def migrate(self) -> list[Result]:
        return migrate_targets(
            self._targets, *self._locations, output_query_results=self._output_query_results
        )


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
