"""Getting the schema onto every database, not just the first one.

A shard added to configuration and forgotten by the migrator is a database that accepts writes
right up until the first one touches a column that is not there. Migrating from the same
configuration the framework routes from is what keeps the two from drifting apart.

The migrations themselves are `Alembic <https://alembic.sqlalchemy.org>`_'s - SQLAlchemy's own
migration tool, by the same author. Nothing here reimplements ordering, the revision graph,
transactional DDL, autogenerate or the CLI; Alembic is used as a library, with a ``Config`` built
in memory rather than an ``alembic.ini``, so an application migrates itself at startup without
any external binary::

    from ekbatan.migrations import migrate

    migrate(config, "migrations")   # every shard, in order, fail-fast

Two things are added on top, both because a sharded deployment needs them and Alembic does not
have them:

**Every shard, one at a time.** Alembic upgrades one url per ``Config``. This drives one per
shard sequentially and stops at the first failure, naming the shard and what had already
succeeded - half a cluster migrated is a state an operator has to reason about, and the
underlying error only names a connection url.

**Checksums, which Alembic does not do.** Alembic's ``alembic_version`` holds a single row: the
current head. It has no per-migration history and no record of what a revision *contained*, so
editing an already-applied revision is silently accepted and the database and the file then
disagree with nothing to say so - measured, not assumed. Flyway refuses that, and so does this:
:func:`migrate` records a hash of each applied revision in ``ekbatan_revision_checksums`` and
verifies it before every upgrade.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Engine, MetaData, String, Table, delete, insert, select
from sqlalchemy.schema import CreateIndex, CreateTable

from .clock import to_storage
from .config import ShardingConfig
from .errors import EkbatanError, ValidationError
from .persistence import DatabaseRegistry
from .schema import timestamp_column
from .sharding import ShardIdentifier

__all__ = [
    "CHECKSUM_TABLE",
    "MigrationError",
    "checksum_table",
    "emit_ddl",
    "migrate",
    "migrate_registry",
]

LOG = logging.getLogger("ekbatan.migrations")

#: Beside Alembic's `alembic_version`, never instead of it. Alembic owns which revision is
#: current; this owns what each applied revision contained.
CHECKSUM_TABLE = "ekbatan_revision_checksums"


class MigrationError(EkbatanError):
    """Migrations could not be applied, or the database has drifted from the files."""


def checksum_table(metadata: MetaData, *, name: str = CHECKSUM_TABLE) -> Table:
    """One row per applied revision: which file, and what was in it."""
    return Table(
        name,
        metadata,
        Column("revision", String(64), primary_key=True),
        Column("script", String(255), nullable=False),
        Column("checksum", String(64), nullable=False),
        Column("applied_on", timestamp_column(), nullable=False),
        extend_existing=True,
    )


def migrate(
    config: ShardingConfig,
    script_location: str | Path,
    *,
    revision: str = "head",
) -> Mapping[ShardIdentifier, str]:
    """Upgrade every configured shard to ``revision``.

    Returns the revision each shard is on afterwards. One shard at a time, fail-fast: carrying
    on past a failure turns one broken database into several.
    """
    from sqlalchemy import create_engine

    location = Path(script_location)
    if not location.is_dir():
        raise ValidationError(f"no Alembic script directory at {location}")

    done: dict[ShardIdentifier, str] = {}
    for shard in config.shards:
        engine = create_engine(shard.primary.url)
        try:
            checksum_table(MetaData()).create(engine, checkfirst=True)
            _verify(engine, location)
            _upgrade(shard.primary.url, location, revision)
            _record(engine, location)
            done[shard.shard] = _current(engine) or "base"
        except MigrationError:
            raise
        except Exception as error:
            already = ", ".join(f"{s} ({r})" for s, r in done.items()) or "none"
            raise MigrationError(
                f"migrating shard {shard.label} failed: {type(error).__name__}: {error}. "
                f"Already migrated: {already}. Migrations are applied one shard at a time, so "
                f"the rest are untouched - fix this one and run again; the applied ones are "
                f"skipped."
            ) from error
        finally:
            engine.dispose()
        LOG.info("shard %s is at revision %s", shard.label, done[shard.shard])
    return done


def _config(url: str, location: Path) -> Any:
    """An Alembic ``Config`` with no ``alembic.ini`` behind it.

    Built in memory on purpose: the connection settings an application already has are the ones
    the migration should use, and a second copy in a file is a second thing to keep right.
    """
    try:
        from alembic.config import Config
    except ImportError:  # pragma: no cover - the extra is not installed
        raise ValidationError("migrations need Alembic: install ekbatan[migrations]") from None

    config = Config()
    config.set_main_option("script_location", str(location))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _upgrade(url: str, location: Path, revision: str) -> None:
    from alembic import command

    command.upgrade(_config(url, location), revision)


def _scripts(location: Path) -> dict[str, tuple[str, str]]:
    """Every revision Alembic can see, as ``revision -> (script name, checksum)``."""
    from alembic.script import ScriptDirectory

    directory = ScriptDirectory(str(location))
    found: dict[str, tuple[str, str]] = {}
    for script in directory.walk_revisions():
        path = Path(script.path)
        found[script.revision] = (
            path.name,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return found


def _verify(engine: Engine, location: Path) -> None:
    """Refuse when an applied revision's file is not the one that was applied.

    The database was built by a different version of this file, and nothing here can know which
    of the two its rows actually match. Alembic will not notice - it compares revision ids, and
    the id did not change.
    """
    table = checksum_table(MetaData())
    with engine.connect() as conn:
        recorded = {r.revision: (r.script, r.checksum) for r in conn.execute(select(table))}
    if not recorded:
        return

    current = _scripts(location)
    for revision, (script, checksum) in recorded.items():
        now = current.get(revision)
        if now is None:
            raise MigrationError(
                f"revision {revision} ({script}) is applied to this database but is not in "
                f"{location}. A revision that has run is history; restore the file rather than "
                f"deleting it."
            )
        if now[1] != checksum:
            raise MigrationError(
                f"{now[0]} has changed since it was applied ({checksum[:12]} then, "
                f"{now[1][:12]} now). An applied revision is history; add a new one rather "
                f"than editing this."
            )


def _record(engine: Engine, location: Path) -> None:
    """Note what each applied revision contained, for the next :func:`_verify`."""
    from datetime import UTC, datetime

    table = checksum_table(MetaData())
    applied = _applied(engine, location)
    current = _scripts(location)
    with engine.begin() as conn:
        for revision in applied:
            script = current.get(revision)
            if script is None:
                continue
            conn.execute(delete(table).where(table.c.revision == revision))
            conn.execute(
                insert(table).values(
                    revision=revision,
                    script=script[0],
                    checksum=script[1],
                    applied_on=to_storage(datetime.now(UTC)),
                )
            )


def _applied(engine: Engine, location: Path) -> list[str]:
    """Every revision at or below the current head, which is what has actually run."""
    from alembic.script import ScriptDirectory

    head = _current(engine)
    if head is None:
        return []
    directory = ScriptDirectory(str(location))
    return [script.revision for script in directory.iterate_revisions(head, "base")]


def _current(engine: Engine) -> str | None:
    """The revision Alembic thinks this database is on."""
    from alembic.migration import MigrationContext

    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


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
    target = _dialect(dialect)
    statements: list[str] = []
    for table in metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=target)).strip() + ";")
        statements += [
            str(CreateIndex(index).compile(dialect=target)).strip() + ";" for index in table.indexes
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
