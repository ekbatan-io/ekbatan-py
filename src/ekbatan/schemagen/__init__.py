"""Generating table definitions from the database your migrations actually produce.

Java's build starts a container, runs Flyway into it, points jOOQ at the result and compiles the
generated classes. This is that, for the same reason: **the schema is the database's, not a
hand-transcription of it**, so the two cannot drift.

    uv run python -m ekbatan.schemagen        # settings from [tool.ekbatan.schemagen]
    uv run python -m ekbatan.schemagen --migrations ./migrations --out app/schema.py

What it does, in order:

1. starts a throwaway database for ``image`` - or uses ``url`` if you have one
2. runs **your** migrations into it, through :func:`ekbatan.migrations.migrate` - the same code
   path a deployment takes, so what is reflected is what a deploy produces
3. reflects the tables and views the configuration asks for
4. **forces the types** - yours first, then Ekbatan's own, as Java's ``forcedTypes`` - and maps
   every other type through the type table in ``_types``
5. writes a module, with keys, indexes, checks and defaults, so ``create_all`` builds it again

## Configuration

The build-time settings live where a Python project keeps them, in ``pyproject.toml``::

    [tool.ekbatan.schemagen]
    migrations = "src/myapp/migrations"
    out = "src/myapp/schema.py"
    image = "postgres:17-alpine"

    [[tool.ekbatan.schemagen.forced-types]]
    user-type = "myapp.types.Money"
    include-expression = ".*\\\\.amount"

A command-line flag wins over the file. See :class:`ForcedType` and :class:`SchemagenConfig`.

## Why the types have to be forced

Reflection sees one database. A column reflected from PostgreSQL as ``TIMESTAMP`` compiles on
MySQL to ``TIMESTAMP`` - which has a 2038 ceiling and shifts with the session's timezone - where
this framework requires ``DATETIME(6)``. A ``UUID`` reflected from PostgreSQL becomes
``CHAR(32)`` on MySQL, the hex with its hyphens stripped, which no Java service can join against.
And from MySQL a uuid is a ``CHAR(36)`` like any other. So the reflected types are mapped onto
the framework's own column helpers - exactly Java's ``forcedTypes``, for exactly the same reason.

## Why the framework's own tables are not reflected

``events``, ``event_notifications`` and ``scheduled_tasks`` are a cross-language contract - a
Debezium connector and another port's service bind to their column names. They are emitted as
calls to :mod:`ekbatan.schema`, so there is one definition to be right about.

## Staleness

The generated module records a fingerprint of the migrations it was built from and checks it on
import. Gradle gets this for free because you cannot compile without regenerating. Nothing in
Python runs before ``python -m app``, so the check is how the guarantee is kept.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, Table, create_engine

from ..errors import ValidationError
from ._config import (
    DEFAULT_FORCED_TYPES,
    DEFAULT_IMAGE,
    ForcedType,
    SchemagenConfig,
    load_config,
)
from ._reflect import BOOKKEEPING, FRAMEWORK_TABLES, Reflected, Relation, reflect
from ._render import render_module
from ._types import resolve

__all__ = [
    "BOOKKEEPING",
    "DEFAULT_FORCED_TYPES",
    "FRAMEWORK_TABLES",
    "ForcedType",
    "SchemagenConfig",
    "check_fingerprint",
    "fingerprint",
    "generate",
    "load_config",
    "main",
    "render",
]

log = logging.getLogger("io.ekbatan.schemagen")


def fingerprint(migrations: Path) -> str:
    """A hash of every migration file, so a stale module can say so.

    Every SQL file the migrator reads - versioned, repeatable and ``post-apply`` alike, in any
    directory under this one, hidden ones skipped - sorted by path and hashed with its path, so
    moving one counts as a change. Line endings are normalised as the migrator's own checksums
    normalise them, so a checkout that turns ``\\n`` into ``\\r\\n`` does not make every generated
    module stale. Other files - an ``__init__.py``, a ``README`` - are not schema and are not
    hashed. For a directory with no subdirectories this is the hash it always was.
    """
    from ..migrations import _files, checksum

    digest = hashlib.sha256()
    for path in _files(migrations):
        if path.suffix.lower() != ".sql":
            continue
        digest.update(path.relative_to(migrations).as_posix().encode())
        digest.update(checksum(path).encode())
    return digest.hexdigest()[:16]


def render(
    tables: Sequence[Table],
    *,
    framework: Iterable[str],
    migrations_fingerprint: str,
    module: str | None,
    dialect: str | None = None,
    config: SchemagenConfig | None = None,
) -> str:
    """The generated module for tables already in hand, as text - no database needed.

    ``dialect`` is the database the tables were read from (``postgresql``, ``mysql``,
    ``mariadb``, ``sqlite``): the built-in forced types differ per database, as Java's build
    scripts do. Without it, only the forced types that apply to every database are tried.
    """
    from sqlalchemy import MetaData

    reflected = Reflected(dialect_name=dialect or "")
    for table in tables:
        reflected.tables.append(Relation(table, None, None))
    for name in framework:
        reflected.framework.append(Relation(Table(name, MetaData()), None, None))
    text, notes = render_module(
        reflected,
        config or SchemagenConfig(),
        dialect=_dialect(dialect),
        migrations_fingerprint=migrations_fingerprint,
        module=module,
    )
    for note in notes:
        log.warning("%s", note)
    return text


def _dialect(name: str | None) -> Any:
    if not name:
        return None
    from sqlalchemy.dialects import registry

    return registry.load(name)()


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
            f"but {directory} now fingerprints {actual}. Regenerate it (`make schema`, or):\n"
            f"    uv run python -m ekbatan.schemagen --migrations {directory} "
            f"--out {Path(generated)}"
        )


def generate(
    migrations: Path | None = None,
    out: Path | None = None,
    *,
    url: str | None = None,
    image: str | None = None,
    config: SchemagenConfig | None = None,
) -> str:
    """Run the whole pipeline and write the module. Returns what was written.

    ``migrations``, ``out``, ``url`` and ``image`` win over the same settings in ``config``.
    Notes - a type that cannot be read, a zoned timestamp, a schema not read - are logged to
    ``io.ekbatan.schemagen`` as warnings; the command prints them.
    """
    from ..config import ShardingConfig, split_login
    from ..migrations import migrate

    settings = (config or SchemagenConfig()).with_overrides(
        migrations=migrations, out=out, url=url, image=image
    )
    if settings.migrations is None:
        raise ValidationError(
            "no migrations directory: set `migrations` in [tool.ekbatan.schemagen] in "
            "pyproject.toml, or pass --migrations"
        )
    if settings.out is None:
        raise ValidationError(
            "no output file: set `out` in [tool.ekbatan.schemagen] in pyproject.toml, or pass --out"
        )
    source = settings.migrations
    if not source.is_dir():
        raise ValidationError(f"no migration directory at {source}")
    # A forced type that names nothing real fails now, naming itself - not after a container
    # has started, and not as a generated module that does not import.
    for forced in settings.forced_types:
        resolve(forced)

    stop, address = (None, settings.url) if settings.url else _start(settings.image)
    try:
        # A container's url carries its login; the config wants it in username and password.
        plain, username, password = split_login(address)
        migrate(ShardingConfig.single(plain, username=username, password=password), source)
        engine: Engine = create_engine(address)
        try:
            reflected = reflect(engine, settings)
            dialect = engine.dialect
        finally:
            engine.dispose()
    finally:
        if stop is not None:
            stop()

    if not reflected.tables and not reflected.views:
        raise ValidationError(
            f"the migrations in {source} created no application tables to generate"
            + (
                f" - only the framework's ({', '.join(r.table.name for r in reflected.framework)})"
                if reflected.framework
                else ""
            )
            + ". Nothing to generate."
        )

    target = settings.out
    text, notes = render_module(
        reflected,
        settings,
        dialect=dialect,
        migrations_fingerprint=fingerprint(source),
        module=_relative(target.resolve().parent, source.resolve()),
    )
    for note in notes:
        log.warning("%s", note)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return text


#: How far a generated module will look for its migrations before giving up. A path that has to
#: climb further than this is one the two are not really related by - a temporary directory, a
#: different checkout - and a fingerprint check across it would be noise.
MAX_CLIMB = 4


def _start(image: str) -> tuple[Any, str]:
    """A throwaway database of the kind `image` names, how to stop it, and the url to reach it.

    The dialect follows the image rather than a separate flag: a MySQL migration cannot be run
    against Postgres, so the two were never independent and asking for both invites the pair
    that does not go together. ``sqlite`` needs no container: a file in a temporary directory.
    """
    family = image.split(":")[0].split("/")[-1].lower()
    if family == "sqlite":
        import shutil
        import tempfile

        directory = tempfile.mkdtemp(prefix="ekbatan-schemagen-")
        return (lambda: shutil.rmtree(directory, ignore_errors=True)), (
            f"sqlite:///{Path(directory) / 'schema.db'}"
        )
    try:
        if family in ("mysql", "mariadb"):
            from testcontainers.community.mysql import MySqlContainer

            # `dialect=` here is the *driver* only; testcontainers prefixes `mysql+` itself.
            # `username="root"` because the migrations issue DDL and the default `test` user is
            # granted rights on the `test` schema alone.
            mysql: Any = MySqlContainer(image, dialect="pymysql", username="root")
            mysql.start()
            _, _, rest = mysql.get_connection_url().partition("://")
            # There is no MariaDB container class, so the MySQL one drives it - and then hands
            # back a url saying `mysql+pymysql`. SQLAlchemy picks its DDL, version reporting and
            # `SKIP LOCKED` support off that scheme, and this framework picks its keyed-lock
            # provider off `engine.dialect.name`. Left alone, a MariaDB schema would be
            # generated against MySQL's rules and nothing would say so.
            return mysql.stop, f"{family}+pymysql://{rest}"

        from testcontainers.postgres import PostgresContainer

        postgres: Any = PostgresContainer(image, driver="psycopg")
        postgres.start()
        return postgres.stop, postgres.get_connection_url()
    except ImportError:  # pragma: no cover - the extra is not installed
        raise ValidationError(
            "generating without a url needs a throwaway database: "
            "install ekbatan[codegen], or pass --url"
        ) from None


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
        description="Generate table definitions from the schema your migrations produce. "
        "Settings come from [tool.ekbatan.schemagen] in pyproject.toml; a flag wins over it.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="the TOML file with [tool.ekbatan.schemagen]; default ./pyproject.toml, if it has one",
    )
    parser.add_argument("--migrations", type=Path, help="the migrations directory")
    parser.add_argument("--out", type=Path, help="the module to write")
    parser.add_argument(
        "--url", help="an existing database to migrate and reflect; without it, a throwaway one"
    )
    parser.add_argument(
        "--image",
        help=f"the throwaway database's image: postgres:*, mysql:*, mariadb:*, or sqlite "
        f"(default {DEFAULT_IMAGE})",
    )
    parser.add_argument(
        "--schema",
        action="append",
        dest="schemas",
        help="another schema (or MySQL database) to read, written qualified; repeatable",
    )
    parser.add_argument("--include", help="tables to generate: a regular expression")
    parser.add_argument("--exclude", help="tables to leave out: a regular expression")
    parser.add_argument("--no-views", action="store_true", help="leave the views out")
    parser.add_argument(
        "--no-default-forced-types",
        action="store_true",
        help="force only your own types, not Ekbatan's built-in ones",
    )
    args = parser.parse_args(argv)
    _show_notes()

    try:
        if args.config is not None:
            config = load_config(args.config)
        else:
            config = load_config(Path("pyproject.toml"), required=False)
        settings = (config or SchemagenConfig()).with_overrides(
            schemas=tuple(args.schemas) if args.schemas else None,
            includes=args.include,
            excludes=args.exclude,
            views=False if args.no_views else None,
            default_forced_types=False if args.no_default_forced_types else None,
        )
        generate(args.migrations, args.out, url=args.url, image=args.image, config=settings)
    except ValidationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote {args.out or settings.out}")
    return 0


def _show_notes() -> None:
    """Print the notes as ``note: ...`` on stderr, unless logging is already set up."""
    if log.handlers or logging.getLogger().handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("note: %(message)s"))
    log.addHandler(handler)
    log.propagate = False
