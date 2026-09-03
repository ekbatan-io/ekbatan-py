"""Getting the schema onto every database, not just the first one.

A shard added to configuration and forgotten by the migrator is a database that accepts writes
right up until the first one touches a column that is not there. Migrating from the same
configuration the framework routes from is what keeps the two from drifting apart.

Migrations are plain per-dialect SQL files, run by `yoyo <https://ollycope.com/software/yoyo/>`_.
Plain SQL because the outbox schema differs between dialects in ways worth reading - a partial
index Postgres has and MySQL does not - and a migration tool that generates DDL from models
hides exactly the differences an operator needs to see.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.schema import CreateIndex, CreateTable

from .config import ShardingConfig
from .errors import ValidationError
from .persistence import DatabaseRegistry
from .sharding import ShardIdentifier

__all__ = ["emit_ddl", "migrate", "migrate_registry"]

LOG = logging.getLogger("ekbatan.migrations")


def migrate(config: ShardingConfig, directory: str | Path) -> Mapping[ShardIdentifier, int]:
    """Apply every pending migration to every configured shard.

    Returns how many were applied per shard. Runs them one shard at a time and stops at the
    first failure: half a cluster migrated is a state worth noticing immediately, and carrying
    on would turn one broken database into several.
    """
    try:
        from yoyo import get_backend, read_migrations  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - the extra is not installed
        raise ValidationError("migrations need yoyo: install ekbatan[migrations]") from None

    path = Path(directory)
    if not path.is_dir():
        raise ValidationError(f"no migration directory at {path}")

    migrations = read_migrations(str(path))
    if not migrations:
        LOG.warning("no migrations found in %s", path)

    applied: dict[ShardIdentifier, int] = {}
    for shard in config.shards:
        backend = get_backend(shard.primary.url)
        with backend.lock():
            pending = backend.to_apply(migrations)
            backend.apply_migrations(pending)
            applied[shard.shard] = len(pending)
        LOG.info("applied %d migration(s) to %s", applied[shard.shard], shard.label)
    return applied


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
