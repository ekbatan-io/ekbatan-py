"""The complete-project-setup page shows a whole project, for every choice of its picker.

The page imports each file it shows when the site is built - its own, from
`website/src/setup-guide/`, and the migrations and SQLite's schema from the example projects -
so what it shows cannot drift from files that exist. What can still go wrong is which files it
shows: a module one of them imports that the page never shows, or a file of its own that no
choice shows at all. Following the page end to end, for every database, installer and mode, is
`scripts/check-setup-guide.py`.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "website" / "src" / "pages" / "learn" / "complete-project-setup.mdx"
GUIDE = ROOT / "website" / "src" / "setup-guide"

TEXT = PAGE.read_text()
IMPORTS = dict(re.findall(r'^import (\w+) from "([^"]+)\?raw";', TEXT, re.M))

_spec = importlib.util.spec_from_file_location(
    "check_setup_guide", ROOT / "scripts" / "check-setup-guide.py"
)
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
# Registered first: its dataclasses look their module up by name.
sys.modules[_spec.name] = check
_spec.loader.exec_module(check)

CHOICES = [(db, mode) for db in ("pg", "mysql", "mariadb", "sqlite") for mode in ("sync", "async")]
#: Written by the page's own commands rather than shown: the builders always, the schema where
#: schemagen writes it.
GENERATED = {"builders"}


def shown(db: str, mode: str) -> dict[str, str]:
    """What the page shows for one choice - each file's path and text - as the check reads it."""
    return {item.path: item.text for item in check.read_page(db, "uv", mode) if item.kind == "file"}


def test_every_file_the_page_imports_exists() -> None:
    missing = [path for path in IMPORTS.values() if not (PAGE.parent / path).resolve().is_file()]
    assert not missing


def test_every_file_of_the_guide_is_on_the_page() -> None:
    imported = {(PAGE.parent / path).resolve() for path in IMPORTS.values()}
    orphans = sorted(
        str(path.relative_to(GUIDE))
        for path in GUIDE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.resolve() not in imported
    )
    assert not orphans, f"in website/src/setup-guide but on no page: {orphans}"


@pytest.mark.parametrize(("db", "mode"), CHOICES)
def test_each_choice_shows_a_whole_project(db: str, mode: str) -> None:
    files = shown(db, mode)
    package = "src/wallet/"
    needed = {
        "model.py",
        "converters.py",
        "actions.py",
        "handlers.py",
        "jobs.py",
        "repository.py",
        "app.py",
        "ekbatan.yaml",
    }
    needed |= {"__main__.py", "schema.py"} if db == "sqlite" else {"api.py"}
    names = {path.removeprefix(package) for path in files}
    assert needed <= names, f"{db} {mode}: not shown {sorted(needed - names)}"
    if db != "sqlite":
        migrations = sorted(n for n in names if n.startswith("migrations/"))
        assert migrations == [f"migrations/{name}" for name in check.migrations_of(db)]


@pytest.mark.parametrize(("db", "mode"), CHOICES)
def test_every_module_a_shown_file_imports_is_shown(db: str, mode: str) -> None:
    files = shown(db, mode)
    modules = {Path(path).stem for path in files if path.endswith(".py")}
    generated = GENERATED | ({"schema"} if db != "sqlite" else set())
    for path, text in files.items():
        for module in re.findall(r"^from \.(\w+) import", text, re.M):
            assert module in modules | generated, f"{db} {mode}: {path} imports .{module}"


@pytest.mark.parametrize(("db", "mode"), CHOICES)
def test_a_file_is_shown_once_for_a_choice(db: str, mode: str) -> None:
    paths = [item.path for item in check.read_page(db, "uv", mode) if item.kind == "file"]
    assert len(paths) == len(set(paths)), f"{db} {mode}: shown twice"
