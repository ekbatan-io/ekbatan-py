"""`python -m ekbatan.schemagen`, as a user runs it: the file, every flag over it, the exit codes,
what goes to stdout and stderr - and, for each database, a real throwaway one.

`main()` is called in-process for the flags (so each one's effect can be checked precisely) and
the command is run in a subprocess for what only a real process shows: the exit code, the streams,
the notes, and a container started and removed again.

    uv run pytest tests/integration/test_schemagen_cli.py --dialects=all

The container tests start their own database from `--image`, one per database the run asks for,
and need Docker. The ledger is `docs/SCHEMAGEN-TEST-LEDGER.md`, section (d).
"""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

import ekbatan.schemagen as schemagen
from backends import IMAGES, docker_is_available
from ekbatan.schemagen._records import record_name
from schemagen_support import load, migrations

ROOT = Path(__file__).resolve().parents[2]

TABLES = (
    "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name VARCHAR(20) NOT NULL, at TIMESTAMP);\n"
    "CREATE TABLE ledger (id INTEGER PRIMARY KEY, amount NUMERIC(10, 2));\n"
    "CREATE VIEW widget_names AS SELECT id, name FROM widgets;\n"
)


def _project(root: Path, table: str = "") -> Path:
    """A project: migrations under src/app, and a pyproject.toml with `table` as its settings."""
    migrations(root / "src" / "app" / "migrations", TABLES)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "app"\n\n[tool.ekbatan.schemagen]\n'
        'migrations = "src/app/migrations"\nout = "src/app/schema.py"\nimage = "sqlite"\n' + table
    )
    return root


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The images `main` really starts a database for - still started, as sqlite."""
    images: list[str] = []
    real = schemagen._start

    def recording(image: str) -> tuple[Any, str]:
        images.append(image)
        return real("sqlite")

    monkeypatch.setattr(schemagen, "_start", recording)
    return images


def _main(
    cwd: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str
) -> tuple[int, str, str]:
    monkeypatch.chdir(cwd)
    code = schemagen.main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# -- main(), in-process: every flag over the file (ledger D-5, D-6, D-7) ---------------------


def test_the_file_alone_is_enough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)

    code, out, err = _main(project, monkeypatch, capsys)

    assert (code, err) == (0, "")
    assert out == f"wrote {Path('src/app/schema.py')}\n"
    module = load(project / "src" / "app" / "schema.py")
    assert module.__all__ == [
        "LEDGER",
        "WIDGETS",
        "WIDGET_NAMES",
        "LedgerRecord",
        "WidgetNamesRecord",
        "WidgetsRecord",
        "metadata",
        "view_metadata",
    ]


def test_migrations_and_out_flags_win_over_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    other = migrations(tmp_path / "other", "CREATE TABLE gadgets (id INTEGER PRIMARY KEY);\n")
    out = tmp_path / "elsewhere" / "schema.py"

    code, printed, _ = _main(
        project, monkeypatch, capsys, "--migrations", str(other), "--out", str(out)
    )

    assert (code, printed) == (0, f"wrote {out}\n")
    assert load(out).__all__ == ["GADGETS", "GadgetsRecord", "metadata"]
    assert not (project / "src" / "app" / "schema.py").exists()


def test_the_image_flag_wins_over_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    started: list[str],
) -> None:
    project = _project(tmp_path)

    assert _main(project, monkeypatch, capsys, "--image", "mysql:8.4")[0] == 0
    assert _main(project, monkeypatch, capsys)[0] == 0

    assert started == ["mysql:8.4", "sqlite"]


def test_the_url_flag_wins_over_the_file_s_and_the_file_s_is_used_without_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    started: list[str],
) -> None:
    """A url means no throwaway database at all - from the file or the flag."""
    project = _project(tmp_path, f'url = "sqlite:///{tmp_path / "from_file.db"}"\n')

    assert _main(project, monkeypatch, capsys)[0] == 0
    assert (tmp_path / "from_file.db").exists()
    assert (
        _main(project, monkeypatch, capsys, "--url", f"sqlite:///{tmp_path / 'from_flag.db'}")[0]
        == 0
    )
    assert (tmp_path / "from_flag.db").exists()

    assert started == []


@pytest.mark.parametrize(
    ("flags", "exported"),
    [
        ((), ["LEDGER", "WIDGETS", "WIDGET_NAMES", "metadata", "view_metadata"]),
        (("--no-views",), ["LEDGER", "WIDGETS", "metadata"]),
        (("--include", "widgets"), ["WIDGETS", "metadata"]),
        (("--exclude", "ledger"), ["WIDGETS", "WIDGET_NAMES", "metadata", "view_metadata"]),
        (("--include", "ledger|widgets", "--exclude", "w.*"), ["LEDGER", "metadata"]),
        (("--include", "main\\.ledger"), ["LEDGER", "metadata"]),
    ],
    ids=["none", "no views", "include", "exclude", "both", "schema-qualified include"],
)
def test_table_flags_win_over_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: tuple[str, ...],
    exported: list[str],
) -> None:
    """The file says views and every table; each flag narrows it. A table is matched by its
    name and by `schema.table`."""
    project = _project(tmp_path, 'includes = ".*"\nviews = true\n')

    assert _main(project, monkeypatch, capsys, *flags)[0] == 0

    # The tables the flags chose, each with its record.
    exported_now = load(project / "src" / "app" / "schema.py").__all__
    assert [n for n in exported_now if not n.endswith("Record")] == exported
    assert [n for n in exported_now if n.endswith("Record")] == sorted(
        record_name(n.lower()) for n in exported if n.isupper()
    )


def test_no_default_forced_types_wins_over_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path, "default-forced-types = true\n")
    schema = project / "src" / "app" / "schema.py"

    _main(project, monkeypatch, capsys)
    assert 'Column("at", timestamp_column())' in schema.read_text()
    _main(project, monkeypatch, capsys, "--no-default-forced-types")

    assert 'Column("at", TIMESTAMP())' in schema.read_text()


def test_a_schema_flag_replaces_the_file_s_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Replaces, not adds to: the file's `nowhere` would fail to read. SQLite's own schema,
    `main`, is the connection's own and changes nothing."""
    project = _project(tmp_path, 'schemas = ["nowhere"]\n')

    code, _, err = _main(project, monkeypatch, capsys, "--schema", "main", "--schema", "main")

    assert (code, err) == (0, "")


def test_the_file_s_forced_types_are_used_written_as_module_colon_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A converter by `module:Class` with arguments, from the file, into the written module."""
    project = _project(
        tmp_path,
        "\n[[tool.ekbatan.schemagen.forced-types]]\n"
        'converter = "cli_conv:Cents(2)"\ninclude-expression = "ledger\\\\.amount"\n',
    )
    (tmp_path / "cli_conv.py").write_text(
        "class Cents:\n"
        "    def __init__(self, places):\n"
        "        self.places = places\n"
        "    def from_db(self, value) -> object:\n"
        "        return value\n"
        "    def to_db(self, value):\n"
        "        return value\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    try:
        assert _main(project, monkeypatch, capsys)[0] == 0
        written = (project / "src" / "app" / "schema.py").read_text()
        assert 'Column("amount", converted(NUMERIC(precision=10, scale=2), Cents(2)))' in written
        assert "from cli_conv import Cents" in written
    finally:
        sys.modules.pop("cli_conv", None)


def test_a_config_file_elsewhere_has_its_paths_read_from_where_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path / "project")
    (project / "pyproject.toml").rename(project / "schemagen.toml")
    elsewhere = tmp_path / "somewhere"
    elsewhere.mkdir()

    code, _, err = _main(
        elsewhere, monkeypatch, capsys, "--config", str(project / "schemagen.toml")
    )

    assert (code, err) == (0, "")
    assert (project / "src" / "app" / "schema.py").exists()
    assert not (elsewhere / "src").exists()


# -- main(), in-process: every refusal is `error: ...` and exit 1 (ledger D-3) ----------------


@pytest.mark.parametrize(
    ("setup", "args", "message"),
    [
        ("nothing", (), "error: no migrations directory: set `migrations` in"),
        ("nothing", ("--config", "missing.toml"), "error: no configuration file at missing.toml"),
        ("nothing", ("--config", "."), "error: cannot read ."),
        ("bad toml", (), "error: pyproject.toml is not valid TOML: "),
        ("typo", (), "error: [tool.ekbatan.schemagen] in pyproject.toml: unknown key(s)"),
        ("bad forced type", (), "error: the forced type name 'Txet' is not a SQLAlchemy type"),
        ("no out", (), "error: no output file: set `out`"),
        ("nothing", ("--migrations", "nope", "--out", "x.py"), "error: no migration directory at"),
        ("empty", (), "created no application tables to generate"),
        ("project", ("--include", "("), "error: includes '(' is not a regular expression"),
    ],
    ids=[
        "no settings",
        "missing config",
        "config is a directory",
        "bad toml",
        "typo",
        "bad forced type",
        "no out",
        "no migration directory",
        "no tables",
        "bad include flag",
    ],
)
def test_a_refusal_is_one_error_line_and_exit_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    setup: str,
    args: tuple[str, ...],
    message: str,
) -> None:
    body = {
        "bad toml": "[tool.ekbatan.schemagen\n",
        "typo": '[tool.ekbatan.schemagen]\nmigratons = "m"\n',
        "bad forced type": '[tool.ekbatan.schemagen]\nmigrations = "m"\nout = "s.py"\n\n'
        '[[tool.ekbatan.schemagen.forced-types]]\nname = "Txet"\ninclude-types = "X"\n',
        "no out": '[tool.ekbatan.schemagen]\nmigrations = "m"\n',
        "empty": '[tool.ekbatan.schemagen]\nmigrations = "m"\nout = "s.py"\nimage = "sqlite"\n',
    }.get(setup)
    if body is not None:
        (tmp_path / "pyproject.toml").write_text(body)
        (tmp_path / "m").mkdir()
    if setup == "project":
        _project(tmp_path)

    code, out, err = _main(tmp_path, monkeypatch, capsys, *args)

    assert code == 1
    assert out == ""
    assert err.startswith("error: ") and message in err, err
    assert err.count("\n") == 1 or "\n    uv run" in err, "one line"


def test_help_lists_every_flag_and_exits_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exited:
        schemagen.main(["--help"])

    shown = capsys.readouterr().out
    assert exited.value.code == 0
    for flag in (
        "--config",
        "--migrations",
        "--out",
        "--url",
        "--image",
        "--schema",
        "--include",
        "--exclude",
        "--no-views",
        "--no-default-forced-types",
    ):
        assert flag in shown, flag


def test_an_unknown_flag_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        schemagen.main(["--views"])

    assert exited.value.code == 2
    assert "unrecognized arguments: --views" in capsys.readouterr().err


def test_python_m_runs_main_and_exits_with_its_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["schemagen", "--config", "missing.toml"])
    monkeypatch.delitem(sys.modules, "ekbatan.schemagen.__main__", raising=False)

    with pytest.raises(SystemExit) as exited:
        runpy.run_module("ekbatan.schemagen", run_name="__main__")

    assert exited.value.code == 1
    assert "error: no configuration file at missing.toml" in capsys.readouterr().err


# -- the command in a process of its own (ledger D-1..D-4) -----------------------------------


def _run(
    cwd: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ, **(env or {}))
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), environment.get("PYTHONPATH", "")]
    )
    return subprocess.run(
        [sys.executable, "-m", "ekbatan.schemagen", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=environment,
        timeout=600,
        check=False,
    )


def test_the_command_with_a_sqlite_image_writes_says_and_tidies_up(tmp_path: Path) -> None:
    """Exit 0, `wrote <path>` on stdout, the notes as `note: ...` on stderr, the temporary
    database removed, and a module a fresh interpreter imports."""
    project = _project(tmp_path / "project")
    scratch = tmp_path / "tmp"
    scratch.mkdir()

    ran = _run(project, env={"TMPDIR": str(scratch)})

    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == f"wrote {Path('src/app/schema.py')}\n"
    assert ran.stderr.splitlines() == [
        "note: widgets.at is TIMESTAMP: a declared type SQLite keeps but does not enforce. "
        "timestamp_column() builds DATETIME - see docs/database/multi-database.md#always-utc"
    ]
    assert list(scratch.iterdir()) == [], "the throwaway database is gone"
    imported = subprocess.run(
        [sys.executable, "-c", "import schema; print(schema.__all__)"],
        cwd=project / "src" / "app",
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        check=False,
    )
    assert imported.returncode == 0, imported.stderr
    assert "WIDGETS" in imported.stdout


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("", "error: no migrations directory"),
        ("[tool.ekbatan.schemagen\n", "error: pyproject.toml is not valid TOML"),
        (
            '[tool.ekbatan.schemagen]\nmigrations = "m"\nout = "s.py"\n\n'
            '[[tool.ekbatan.schemagen.forced-types]]\nconverter = "nowhere.X"\n'
            'include-types = "X"\n',
            "error: the forced converter 'nowhere.X': cannot import nowhere",
        ),
        (
            '[tool.ekbatan.schemagen]\nurl = ["postgresql://u:S3cret-cli-77@h/db"]\n',
            "error: [tool.ekbatan.schemagen] in pyproject.toml: url must be a string, got a list",
        ),
    ],
    ids=["no settings", "bad toml", "converter does not import", "url of the wrong kind"],
)
def test_the_command_refuses_with_one_line_and_no_traceback(
    tmp_path: Path, body: str, message: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(body)
    (tmp_path / "m").mkdir()

    ran = _run(tmp_path)

    assert ran.returncode == 1
    assert ran.stdout == ""
    assert ran.stderr.startswith(message), ran.stderr
    assert "Traceback" not in ran.stderr
    assert "S3cret-cli-77" not in ran.stderr


# -- a real throwaway database for each image (ledger D-2) -----------------------------------

#: Per database: the migration, and what only that database's own reading writes. MariaDB's
#: JSON is a LONGTEXT with a check: read as MySQL, it would come out `mysql.LONGTEXT()`.
CONTAINER_CASES = {
    "postgres": (
        "CREATE TABLE things (id UUID PRIMARY KEY, doc JSONB, at TIMESTAMP);\n",
        ['Column("id", uuid_column(), primary_key=True)', 'Column("doc", json_column())'],
    ),
    "mysql": (
        "CREATE TABLE things (id INTEGER PRIMARY KEY, doc JSON, at DATETIME(6));\n",
        ['Column("doc", json_column())', 'Column("at", timestamp_column())'],
    ),
    "mariadb": (
        "CREATE TABLE things (id INTEGER PRIMARY KEY, doc JSON, address INET6, at DATETIME(6));\n",
        ['Column("doc", json_column())', 'Column("address", sql_type("inet6"))'],
    ),
}


#: Run first by any Python started with its directory on the path: it gives the containers the
#: command starts a session label of the test's choosing, so the test can look for exactly
#: those - another test run on the same Docker starts containers of the same image.
SITECUSTOMIZE = """import os

if os.environ.get("SCHEMAGEN_TEST_SESSION"):
    import testcontainers.core.labels as labels

    labels.SESSION_ID = os.environ["SCHEMAGEN_TEST_SESSION"]
"""


def _containers(session: str) -> list[str]:
    listed = subprocess.run(
        ["docker", "ps", "-a", "-q", "--filter", f"label=org.testcontainers.session-id={session}"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return listed.stdout.split()


def test_the_command_starts_migrates_and_removes_a_throwaway_database(
    tmp_path: Path, dialect: str
) -> None:
    """`--image` for each database: a container started, the migrations run into it, the
    module written from it, and the container removed - with the database's own reading
    (MariaDB generated as MariaDB, not as the MySQL its container class says)."""
    if dialect == "sqlite":
        pytest.skip("covered without a container by the sqlite image test")
    if not docker_is_available():
        pytest.skip("needs Docker")
    image = IMAGES[dialect]
    sql, expected = CONTAINER_CASES[dialect]
    source = migrations(tmp_path / "migrations", sql)
    out = tmp_path / "app" / "schema.py"
    hook = tmp_path / "hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(SITECUSTOMIZE)
    session = f"schemagen-cli-{uuid.uuid4()}"
    env = {
        "SCHEMAGEN_TEST_SESSION": session,
        "PYTHONPATH": os.pathsep.join([str(hook), os.environ.get("PYTHONPATH", "")]),
    }
    labelled = subprocess.run(
        [sys.executable, "-c", "import testcontainers.core.labels as l; print(l.SESSION_ID)"],
        capture_output=True,
        text=True,
        env=dict(os.environ, **env),
        check=True,
    )
    assert labelled.stdout.strip() == session, "the label is set, so the check below means it"

    ran = _run(tmp_path, "--migrations", str(source), "--out", str(out), "--image", image, env=env)

    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == f"wrote {out}\n"
    written = out.read_text()
    for line in expected:
        assert line in written, written
    assert load(out).__all__ == ["THINGS", "ThingsRecord", "metadata"]
    assert _containers(session) == [], "the throwaway container is removed"
