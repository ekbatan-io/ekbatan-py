"""Generating table definitions from the database your migrations actually produce.

Java's build starts a container, runs Flyway into it, points jOOQ at the result and compiles the
generated classes. This is that, for the same reason: **the schema is the database's, not a
hand-transcription of it**, so the two cannot drift.

    uv run python -m ekbatan.schemagen --migrations ./migrations --out app/schema.py

What it does, in order:

1. starts a throwaway container, or uses ``--url`` if you have a database already
2. runs **your** migrations into it, through :func:`ekbatan.migrations.migrate` - the same code
   path a deployment takes, so what is reflected is what a deploy produces
3. reflects the result
4. **forces the types**, which is the step that cannot be skipped - see below
5. writes a module you commit

## Why the types have to be forced

Reflection sees one database. A column reflected from PostgreSQL as ``TIMESTAMP`` compiles on
MySQL to ``TIMESTAMP`` - which has a 2038 ceiling and shifts with the session's timezone - where
this framework requires ``DATETIME(6)``. A ``UUID`` reflected from PostgreSQL becomes
``CHAR(32)`` on MySQL, the hex with its hyphens stripped, which no Java service can join against.

Both are silent. Both corrupt data across ports rather than failing.

So reflected types are mapped back onto the framework's own column helpers, which carry the
per-dialect variants that make those two problems go away. This is exactly Java's ``forcedTypes``
block, and it exists for exactly the same reason.

## Why the framework's own tables are not reflected

``events``, ``event_notifications`` and ``scheduled_tasks`` are a cross-language contract - a
Debezium connector and another port's service bind to their column names. They are emitted as
calls to :mod:`ekbatan.schema`, so there is one definition to be right about. Java does the same
thing with ``includeFlywayTable(false)`` and its schema mapping.

## Staleness

The generated module records a fingerprint of the migrations it was built from and checks it on
import. Hashing a handful of files costs nothing and needs no database, and it turns "somebody
edited a migration and forgot to regenerate" into a loud failure rather than a schema that
quietly disagrees with the rows in front of it.

Gradle gets this for free because you cannot compile without regenerating. Nothing in Python
runs before ``python -m app``, so the check is how the guarantee is kept.
"""

from __future__ import annotations

import argparse
import hashlib
import keyword
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, MetaData, Table, UniqueConstraint, create_engine

from .errors import ValidationError

__all__ = ["FRAMEWORK_TABLES", "fingerprint", "generate", "render"]

#: Tables this framework owns. Emitted as calls to `ekbatan.schema` rather than reflected,
#: because their shape is a contract other languages bind to.
FRAMEWORK_TABLES = {
    "events": "outbox_table",
    "event_notifications": "notifications_table",
    "scheduled_tasks": "scheduled_tasks_table",
}

#: Tables that belong to the migration machinery and to nobody else.
BOOKKEEPING = frozenset({"alembic_version", "ekbatan_revision_checksums", "flyway_schema_history"})

#: The `forcedTypes` equivalent: a reflected type, and the helper that carries its per-dialect
#: variants. Everything not named here is emitted as SQLAlchemy reflected it.
FORCED = {
    "UUID": "uuid_column()",
    "Uuid": "uuid_column()",
    "JSON": "json_column()",
    "JSONB": "json_column()",
    "TIMESTAMP": "timestamp_column()",
    "DATETIME": "timestamp_column()",
    "DateTime": "timestamp_column()",
}


def fingerprint(migrations: Path) -> str:
    """A hash of every migration file, so a stale module can say so.

    Sorted by name and hashed whole - a rename counts as a change, which is right: Alembic
    keys a revision by the id inside the file, so a rename that changed nothing else would
    still be worth regenerating for.
    """
    digest = hashlib.sha256()
    for path in sorted(migrations.rglob("*.py")):
        if path.name == "env.py" or "__pycache__" in path.parts:
            continue
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _constant(table: str) -> str:
    name = table.upper().replace(" ", "_").replace("-", "_")
    return f"{name}_" if keyword.iskeyword(name.lower()) else name


def _column_type(column: Any) -> tuple[str, set[str]]:
    """The source for one column's type, and what that source needs imported."""
    forced = FORCED.get(type(column.type).__name__)
    if forced is not None:
        return forced, set()

    # Not forced: emit what SQLAlchemy reflected, with its arguments. `repr` on a type gives
    # `VARCHAR(length=24)`; the constructor name plus its own repr is what round-trips.
    rendered = repr(column.type)
    return rendered, {rendered.split("(")[0]}


def render(
    tables: Sequence[Table],
    *,
    framework: Iterable[str],
    migrations_fingerprint: str,
    module: str | None,
) -> str:
    """The generated module, as text."""
    sqlalchemy_imports: set[str] = {"Column", "MetaData", "Table"}
    helper_imports: set[str] = set()
    body: list[str] = []

    for table in tables:
        lines = [f"{_constant(table.name)} = Table(", f'    "{table.name}",', "    metadata,"]
        for column in table.columns:
            source, needed = _column_type(column)
            if source in set(FORCED.values()):
                helper_imports.add(source.removesuffix("()"))
            sqlalchemy_imports |= needed
            parts = [f'"{column.name}"', source]
            if column.primary_key:
                parts.append("primary_key=True")
            elif not column.nullable:
                parts.append("nullable=False")
            lines.append(f"    Column({', '.join(parts)}),")
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint) and constraint.columns:
                cols = ", ".join(f'"{c.name}"' for c in constraint.columns)
                named = f', name="{constraint.name}"' if constraint.name else ""
                lines.append(f"    UniqueConstraint({cols}{named}),")
                sqlalchemy_imports.add("UniqueConstraint")
        lines.append(")")
        body.append("\n".join(lines))

    for name in sorted(framework):
        helper = FRAMEWORK_TABLES[name]
        helper_imports.add(helper)
        body.append(f"{_constant(name)} = {helper}(metadata)")

    exported = sorted(
        [_constant(t.name) for t in tables] + [_constant(n) for n in framework] + ["metadata"]
    )
    helpers = ", ".join(sorted(helper_imports))
    imports = ", ".join(sorted(sqlalchemy_imports))

    return f'''"""The tables, generated from the schema the migrations produce.

Generated by `python -m ekbatan.schemagen`. **Do not edit.** Change a migration and regenerate;
the fingerprint below makes a stale copy of this file fail loudly rather than disagree quietly
with the database it claims to describe.

The framework's own tables are built from `ekbatan.schema` rather than reflected, because their
shape is a contract a CDC connector and other ports bind to - there is one definition to be
right about, and it is not this file.
"""

from __future__ import annotations

from sqlalchemy import {imports}

from ekbatan.schema import {helpers}
from ekbatan.schemagen import check_fingerprint

#: A hash of the migrations this file was generated from.
MIGRATIONS_FINGERPRINT = "{migrations_fingerprint}"

metadata = MetaData()

{chr(10).join(chr(10).join([b, ""]) for b in body)}
__all__ = {exported!r}

# Nothing in Python runs before `python -m yourapp`, so this is where a stale schema is caught.
# Hashing the migration files costs microseconds and needs no database.
check_fingerprint(__file__, MIGRATIONS_FINGERPRINT, {module!r})
'''


def check_fingerprint(generated: str, expected: str, migrations: str | None) -> None:
    """Refuse to import a module generated from different migrations than the ones on disk.

    Quiet when the migrations cannot be found - a wheel ships the generated module and not the
    migrations, and an installed application must not fail to import because of that.
    """
    if migrations is None:
        return
    directory = (Path(generated).parent / migrations).resolve()
    if not directory.is_dir():
        return
    actual = fingerprint(directory)
    if actual != expected:
        raise ValidationError(
            f"{Path(generated).name} was generated from migrations fingerprinted {expected}, "
            f"but {directory} now fingerprints {actual}. Regenerate it:\n"
            f"    uv run python -m ekbatan.schemagen --migrations {directory} "
            f"--out {Path(generated)}"
        )


def generate(
    migrations: Path,
    out: Path,
    *,
    url: str | None = None,
    image: str = "postgres:17-alpine",
) -> str:
    """Run the whole pipeline and write the module. Returns what was written."""
    from .config import DataSourceConfig, ShardConfig, ShardingConfig
    from .migrations import migrate
    from .persistence import DatabaseRegistry  # noqa: F401  (import cycle guard)
    from .sharding import DEFAULT_SHARD

    if not migrations.is_dir():
        raise ValidationError(f"no migration directory at {migrations}")

    container = None
    if url is None:
        try:
            from testcontainers.postgres import PostgresContainer
        except ImportError:  # pragma: no cover - the extra is not installed
            raise ValidationError(
                "generating without --url needs a throwaway database: "
                "install ekbatan[codegen], or pass --url"
            ) from None
        container = PostgresContainer(image, driver="psycopg")
        container.start()
        url = container.get_connection_url()

    try:
        migrate(
            ShardingConfig(
                shards=(ShardConfig(shard=DEFAULT_SHARD, primary=DataSourceConfig(url=url)),),
                default_shard=DEFAULT_SHARD,
            ),
            migrations,
        )
        engine: Engine = create_engine(url)
        try:
            reflected = MetaData()
            reflected.reflect(bind=engine)
        finally:
            engine.dispose()
    finally:
        if container is not None:
            container.stop()

    application = [
        reflected.tables[name]
        for name in sorted(reflected.tables)
        if name not in FRAMEWORK_TABLES and name not in BOOKKEEPING
    ]
    framework = [n for n in sorted(reflected.tables) if n in FRAMEWORK_TABLES]
    if not application:
        raise ValidationError(
            f"the migrations in {migrations} created no application tables - only "
            f"{sorted(reflected.tables)}. Nothing to generate."
        )

    text = render(
        application,
        framework=framework,
        migrations_fingerprint=fingerprint(migrations),
        module=_relative(out.resolve().parent, migrations.resolve()),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    return text


#: How far a generated module will look for its migrations before giving up. A path that has to
#: climb further than this is one the two are not really related by - a temporary directory, a
#: different checkout - and a fingerprint check across it would be noise.
MAX_CLIMB = 4


def _relative(base: Path, target: Path) -> str | None:
    """How the generated module finds its migrations, from wherever it lives.

    ``None`` when there is no sensible relative path - the module then skips its staleness
    check rather than recording something like ``../../../../../../Users/someone/...``, which
    is true on one machine and meaningless everywhere else.
    """
    import os

    try:
        relative = os.path.relpath(target, base)
    except ValueError:  # different drives on Windows
        return None
    return None if relative.split(os.sep).count("..") > MAX_CLIMB else relative


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ekbatan.schemagen",
        description="Generate table definitions from the schema your migrations produce.",
    )
    parser.add_argument("--migrations", type=Path, required=True, help="the Alembic directory")
    parser.add_argument("--out", type=Path, required=True, help="the module to write")
    parser.add_argument(
        "--url",
        help="an existing database to migrate and reflect; without it, a throwaway container",
    )
    parser.add_argument("--image", default="postgres:17-alpine", help="the container image")
    args = parser.parse_args(argv)

    try:
        generate(args.migrations, args.out, url=args.url, image=args.image)
    except ValidationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
