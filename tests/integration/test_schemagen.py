"""Generating table definitions from the schema the migrations produce.

Java's build starts a container, runs Flyway into it and points jOOQ at the result. This is that
pipeline, so the test runs it: a real container, real migrations, real reflection.

    uv run pytest tests/integration/test_schemagen.py

The case that matters most is `test_the_forced_types_survive_a_move_to_mysql`. Everything else
here checks the generator does what it says; that one checks it prevents the thing it exists to
prevent.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import MetaData, Table
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateTable

from ekbatan.errors import ValidationError
from ekbatan.schemagen import check_fingerprint, fingerprint, generate, render

pytest.importorskip("alembic")

ENV = """
from alembic import context
from sqlalchemy import create_engine

url = context.config.get_main_option("sqlalchemy.url")
assert url is not None
engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
"""

REVISION = '''
"""{message}"""
from alembic import op

revision = "{revision}"
down_revision = {down!r}


def upgrade():
{body}


def downgrade():
    pass
'''


def statements(*sql: str) -> str:
    return "\n".join(f'    op.execute("""{s}""")' for s in sql)


@pytest.fixture
def migrations(tmp_path: Path) -> Path:
    """An Alembic directory whose migrations create an application table and the framework's."""
    root = tmp_path / "migrations"
    (root / "versions").mkdir(parents=True)
    (root / "env.py").write_text(ENV)
    (root / "script.py.mako").write_text('"""${message}"""\n')
    (root / "versions" / "0001_app.py").write_text(
        REVISION.format(
            message="app",
            revision="0001",
            down=None,
            body=statements(
                """
                CREATE TABLE widgets (
                    id UUID NOT NULL PRIMARY KEY,
                    version BIGINT NOT NULL,
                    state VARCHAR(24) NOT NULL,
                    name VARCHAR(80) NOT NULL,
                    tags JSONB,
                    retired_at TIMESTAMP,
                    created_date TIMESTAMP NOT NULL,
                    updated_date TIMESTAMP NOT NULL,
                    CONSTRAINT uq_widgets_name UNIQUE (name)
                )
                """
            ),
        )
    )
    # The framework's own tables, as a real application's migration would create them - which
    # is what gives the generator something to *decline* to reflect.
    from sqlalchemy import MetaData as _MetaData

    from ekbatan.migrations import emit_ddl
    from ekbatan.schema import notifications_table, outbox_table, scheduled_tasks_table

    framework = _MetaData()
    outbox_table(framework)
    notifications_table(framework)
    scheduled_tasks_table(framework)
    (root / "versions" / "0002_framework.py").write_text(
        REVISION.format(
            message="framework",
            revision="0002",
            down="0001",
            body=statements(
                *[s.strip() for s in emit_ddl(framework, "postgresql").split(";") if s.strip()]
            ),
        )
    )
    return root


@pytest.fixture
def generated(migrations: Path, tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Path]:
    if request.config.getoption("--dialects") not in (None, "", "postgres", "all"):
        pytest.skip("the generator drives a Postgres container")
    out = tmp_path / "app" / "schema.py"
    generate(migrations, out)
    yield out


# -- what it produces --------------------------------------------------------------------------


def test_it_generates_the_application_tables(generated: Path) -> None:
    text = generated.read_text()

    assert "WIDGETS = Table(" in text
    assert '"widgets",' in text
    assert 'Column("name", VARCHAR(length=80), nullable=False)' in text


def test_the_framework_s_own_tables_are_built_not_reflected(generated: Path) -> None:
    """Their shape is a contract a CDC connector and other ports bind to, so there is one
    definition to be right about - and it is `ekbatan.schema`, not a reflection of one server."""
    text = generated.read_text()

    assert "EVENTS = outbox_table(metadata)" in text
    assert "EVENT_NOTIFICATIONS = notifications_table(metadata)" in text
    assert "SCHEDULED_TASKS = scheduled_tasks_table(metadata)" in text
    assert 'Column("action_params"' not in text, "reflected, when it should have been built"


def test_the_migration_bookkeeping_tables_are_left_out(generated: Path) -> None:
    text = generated.read_text()

    assert "alembic_version" not in text
    assert "ekbatan_revision_checksums" not in text


def test_a_unique_constraint_survives(generated: Path) -> None:
    """Not decoration: the saga example relies on one for its idempotency, so a generator that
    dropped constraints would silently remove a correctness guarantee."""
    assert 'UniqueConstraint("name", name="uq_widgets_name")' in generated.read_text()


def test_nullable_and_primary_key_survive(generated: Path) -> None:
    text = generated.read_text()

    assert 'Column("id", uuid_column(), primary_key=True)' in text
    assert 'Column("tags", json_column())' in text, "nullable columns carry no nullable= flag"
    assert 'Column("created_date", timestamp_column(), nullable=False)' in text


def test_what_it_writes_is_importable_and_gives_real_tables(generated: Path) -> None:
    """The output is code. If it does not import, nothing else here matters."""
    sys.path.insert(0, str(generated.parent))
    try:
        import importlib

        module = importlib.import_module(generated.stem)
        assert isinstance(module.WIDGETS, Table)
        assert {c.name for c in module.WIDGETS.columns} >= {"id", "name", "tags"}
        assert isinstance(module.EVENTS, Table)
    finally:
        sys.path.remove(str(generated.parent))
        sys.modules.pop(generated.stem, None)


# -- the reason it exists ------------------------------------------------------------------------


def test_the_forced_types_survive_a_move_to_mysql(generated: Path) -> None:
    """The whole point, and the thing reflection alone gets wrong.

    A schema reflected from PostgreSQL and used against MySQL renders `id` as `CHAR(32)` - the
    hex with its hyphens stripped, which no Java service can join against - and `created_date`
    as `TIMESTAMP`, which has a 2038 ceiling and shifts with the session's timezone. Both are
    silent. Forcing the types back onto the framework's helpers is what prevents it.
    """
    sys.path.insert(0, str(generated.parent))
    try:
        import importlib

        module = importlib.import_module(generated.stem)
        for dialect, uuid_type, stamp in (
            (postgresql.dialect(), "UUID", "TIMESTAMP WITHOUT TIME ZONE"),
            (mysql.dialect(), "CHAR(36)", "DATETIME(6)"),
        ):
            ddl = " ".join(str(CreateTable(module.WIDGETS).compile(dialect=dialect)).split())
            assert f"id {uuid_type}" in ddl, ddl
            assert f"created_date {stamp}" in ddl, ddl
    finally:
        sys.path.remove(str(generated.parent))
        sys.modules.pop(generated.stem, None)


def test_a_naive_reflection_would_have_got_that_wrong(generated: Path) -> None:
    """Evidence rather than assertion: the same column, reflected and left alone, is the bug."""
    from sqlalchemy import Column, DateTime, Table, Uuid

    naive = Table("n", MetaData(), Column("id", Uuid), Column("at", DateTime))
    ddl = " ".join(str(CreateTable(naive).compile(dialect=mysql.dialect())).split())

    assert "id CHAR(32)" in ddl, "the join-breaking form"
    assert "at DATETIME," in ddl or ddl.endswith("at DATETIME )"), "the second-precision form"


# -- staleness -----------------------------------------------------------------------------------


def test_the_fingerprint_changes_when_a_migration_changes(migrations: Path) -> None:
    before = fingerprint(migrations)
    (migrations / "versions" / "0002_more.py").write_text(
        REVISION.format(
            message="more",
            revision="0002",
            down="0001",
            body=statements("CREATE TABLE gadgets (id UUID PRIMARY KEY)"),
        )
    )

    assert fingerprint(migrations) != before


def test_the_fingerprint_ignores_env_and_caches(migrations: Path) -> None:
    """`env.py` is machinery, not schema, and `__pycache__` would make the hash depend on
    whether anything had been run."""
    before = fingerprint(migrations)
    (migrations / "env.py").write_text(ENV + "\n# a comment\n")
    (migrations / "versions" / "__pycache__").mkdir()
    (migrations / "versions" / "__pycache__" / "x.py").write_text("junk")

    assert fingerprint(migrations) == before


def test_importing_a_stale_module_fails_loudly(generated: Path, migrations: Path) -> None:
    """The guarantee Gradle gets for free. Nothing in Python runs before `python -m app`, so a
    stale schema would otherwise just disagree with the database in front of it."""
    (migrations / "versions" / "0002_more.py").write_text(
        REVISION.format(
            message="more",
            revision="0002",
            down="0001",
            body=statements("CREATE TABLE gadgets (id UUID PRIMARY KEY)"),
        )
    )

    sys.path.insert(0, str(generated.parent))
    try:
        import importlib

        with pytest.raises(ValidationError) as raised:
            importlib.import_module(generated.stem)
        assert "Regenerate it" in str(raised.value)
        assert "schemagen" in str(raised.value)
    finally:
        sys.path.remove(str(generated.parent))
        sys.modules.pop(generated.stem, None)


def test_the_check_is_quiet_when_the_migrations_are_not_shipped(tmp_path: Path) -> None:
    """A wheel carries the generated module and not the migration files. An installed
    application must not fail to import because of that."""
    check_fingerprint(str(tmp_path / "schema.py"), "whatever", "migrations")


def test_the_check_is_skipped_rather_than_recording_a_nonsense_path(tmp_path: Path) -> None:
    """`module=None` is what the generator records when the two are not sensibly related - a
    temporary directory, a different checkout. Better than `../../../../../Users/someone/...`,
    which is true on one machine and meaningless everywhere else."""
    check_fingerprint(str(tmp_path / "schema.py"), "whatever", None)


# -- the command line ------------------------------------------------------------------------------


def test_the_module_runs_as_a_command(migrations: Path, tmp_path: Path) -> None:
    out = tmp_path / "cli" / "schema.py"
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "ekbatan.schemagen",
            "--migrations",
            str(migrations),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )

    assert done.returncode == 0, done.stderr
    assert out.exists()
    assert "WIDGETS" in out.read_text()


def test_a_missing_migration_directory_says_so(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="no migration directory"):
        generate(tmp_path / "nope", tmp_path / "schema.py")


def test_migrations_that_create_no_application_tables_are_refused(tmp_path: Path) -> None:
    """Silence would be worse: an empty generated module imports fine and every query then
    fails somewhere else."""
    root = tmp_path / "empty"
    (root / "versions").mkdir(parents=True)
    (root / "env.py").write_text(ENV)
    (root / "script.py.mako").write_text('"""${message}"""\n')

    with pytest.raises(ValidationError, match="no application tables"):
        generate(root, tmp_path / "schema.py")


def test_render_needs_no_database(tmp_path: Path) -> None:
    """The emitter is separable from the pipeline, so most of it is testable in milliseconds."""
    from sqlalchemy import BigInteger, Column

    table = Table("things", MetaData(), Column("n", BigInteger, nullable=False))
    text = render(
        [table], framework=["events"], migrations_fingerprint="abc123", module="migrations"
    )

    assert "THINGS = Table(" in text
    assert "EVENTS = outbox_table(metadata)" in text
    assert 'MIGRATIONS_FINGERPRINT = "abc123"' in text
