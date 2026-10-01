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

APP = """
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
);
"""


@pytest.fixture
def migrations(tmp_path: Path) -> Path:
    """A migrations directory that creates an application table and the framework's."""
    root = tmp_path / "migrations"
    root.mkdir()
    (root / "V0001__app.sql").write_text(APP)
    # The framework's own tables, as a real application's migration would create them - which
    # is what gives the generator something to *decline* to reflect.
    from sqlalchemy import MetaData as _MetaData

    from ekbatan.migrations import emit_ddl
    from ekbatan.schema import notifications_table, outbox_table, scheduled_tasks_table

    framework = _MetaData()
    outbox_table(framework)
    notifications_table(framework)
    scheduled_tasks_table(framework)
    (root / "V0002__framework.sql").write_text(emit_ddl(framework, "postgresql"))
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

    assert "ekbatan_schema_history" not in text


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
    (migrations / "V0003__more.sql").write_text("CREATE TABLE gadgets (id UUID PRIMARY KEY);")

    assert fingerprint(migrations) != before


@pytest.mark.parametrize(
    "name",
    ["2026/V0003__more.sql", "R__totals.sql", "post-apply.sql", "V0004__caps.SQL"],
    ids=["in a subdirectory", "repeatable", "post-apply hook", "in capitals"],
)
def test_the_fingerprint_covers_every_file_the_migrator_reads(migrations: Path, name: str) -> None:
    """The migrator reads subdirectories, repeatable files, hooks and `.SQL` - a change to any of
    them changes the schema, so it has to make the module stale."""
    before = fingerprint(migrations)
    path = migrations / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("CREATE TABLE gadgets (id UUID PRIMARY KEY);")

    assert fingerprint(migrations) != before


def test_the_fingerprint_of_a_flat_directory_is_what_it_always_was(migrations: Path) -> None:
    """Every module generated before subdirectories were read stays current."""
    import hashlib

    from ekbatan.migrations import checksum

    digest = hashlib.sha256()
    for path in sorted(migrations.glob("*.sql")):
        digest.update(path.name.encode())
        digest.update(checksum(path).encode())

    assert fingerprint(migrations) == digest.hexdigest()[:16]


def test_the_fingerprint_ignores_what_is_not_a_migration(migrations: Path) -> None:
    """An `__init__.py` that makes the directory a package, a `README`, a cache: none is
    schema, and a hash that moved with them would call a current module stale."""
    before = fingerprint(migrations)
    (migrations / "__init__.py").write_text("")
    (migrations / "README.md").write_text("notes")
    (migrations / "__pycache__").mkdir()
    (migrations / "__pycache__" / "x.pyc").write_bytes(b"junk")

    assert fingerprint(migrations) == before


def test_the_fingerprint_ignores_line_endings(migrations: Path) -> None:
    """As the migrator's checksums do: a checkout that turns \\n into \\r\\n changed nothing."""
    before = fingerprint(migrations)
    path = migrations / "V0001__app.sql"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    assert fingerprint(migrations) == before


def test_importing_a_stale_module_fails_loudly(generated: Path, migrations: Path) -> None:
    """The guarantee Gradle gets for free. Nothing in Python runs before `python -m app`, so a
    stale schema would otherwise just disagree with the database in front of it."""
    (migrations / "V0003__more.sql").write_text("CREATE TABLE gadgets (id UUID PRIMARY KEY);")

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
    root.mkdir()

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


# -- what reflection gets wrong, per dialect ------------------------------------------------------
#
# Both cases below are bugs this generator had. Reflection is asymmetric: PostgreSQL names a uuid
# and MySQL does not, and MySQL decorates its integers with a display width that means nothing
# anywhere else. A generator that transcribed either would emit a module that is wrong somewhere.


def test_a_uuid_stored_as_text_is_recognised_by_name() -> None:
    """From MySQL a uuid reflects as `CHAR(36)`, indistinguishable from any other char column.

    Left alone it hard-codes `CHAR(36)` into a schema that then creates a text column on
    PostgreSQL rather than a native `uuid` - and two ports that disagree about the column type
    cannot join on it. Java forces the same way, with `withIncludeExpression` on the name.
    """
    from sqlalchemy import CHAR, Column, MetaData

    table = Table(
        "t",
        MetaData(),
        Column("id", CHAR(36), primary_key=True),
        Column("owner_id", CHAR(36), nullable=False),
        Column("currency", CHAR(3), nullable=False),
        Column("label", CHAR(36), nullable=False),
    )
    text = render([table], framework=[], migrations_fingerprint="x", module=None)

    assert 'Column("id", uuid_column(), primary_key=True)' in text
    assert 'Column("owner_id", uuid_column(), nullable=False)' in text
    assert 'Column("currency", CHAR(length=3), nullable=False)' in text, "too short to be one"
    assert 'Column("label", CHAR(length=36), nullable=False)' in text, "not named like an id"


def test_a_dialect_type_is_emitted_as_its_portable_equivalent() -> None:
    """MariaDB reflects a bigint as `mysql.BIGINT(display_width=20)`.

    Emitting that verbatim against the generic `BIGINT` gives a module that does not import -
    `TypeError: BIGINT() takes no arguments` - which is exactly what this generator did before
    the normalisation. `display_width` is a MySQL display artifact and belongs in no other
    dialect's schema.
    """
    from sqlalchemy import Column, MetaData
    from sqlalchemy.dialects import mysql

    table = Table(
        "t",
        MetaData(),
        Column("n", mysql.BIGINT(display_width=20), nullable=False),
        Column("s", mysql.VARCHAR(length=24), nullable=False),
    )
    text = render([table], framework=[], migrations_fingerprint="x", module=None)

    assert "display_width" not in text
    assert 'Column("n", BIGINT(), nullable=False)' in text
    assert 'Column("s", VARCHAR(length=24), nullable=False)' in text


def test_the_generated_module_imports_whatever_it_used() -> None:
    """A module that names a type it never imported does not run, and every other assertion
    here reads the text rather than executing it."""
    from sqlalchemy import Column, MetaData, Numeric
    from sqlalchemy.dialects import mysql

    table = Table(
        "t",
        MetaData(),
        Column("n", mysql.BIGINT(display_width=20)),
        Column("m", Numeric(19, 4)),
    )
    text = render([table], framework=["events"], migrations_fingerprint="x", module=None)

    imported = text.split("from sqlalchemy import ")[1].split("\n")[0]
    assert "BIGINT" in imported and "NUMERIC" in imported
    assert "outbox_table" in text.split("from ekbatan.schema import ")[1].split("\n")[0]
