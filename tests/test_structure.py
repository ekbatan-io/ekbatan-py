"""The suite and the examples do not meet.

The framework's tests have their own domain, in `tests/fixtures/`, which exists to exercise
every shape worth exercising - including a few no application would want. The examples have the
opposite job: showing what good code with this framework looks like.

Left coupled, every change made for coverage lands in the documentation and every change made
for clarity breaks the suite. So this is a rule rather than a habit, and it is checked.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
EXAMPLES = ROOT / "examples"

DATABASE_FIXTURES = {
    "registry",
    "sharded_registry",
    "executor",
    "wallets",
    "notifications",
    "outbox",
    "pipeline",
    "sharded",
    "notifications_in",
    "sql",
    "backend",
    "dialect",
    "purses",
    "sharded_purses",
    "sharded_wallets",
}


def imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_no_test_imports_an_example():
    offenders = {
        str(path.relative_to(ROOT)): sorted(
            m for m in imported_modules(path) if m.startswith("examples")
        )
        for path in TESTS.rglob("*.py")
    }
    offenders = {path: names for path, names in offenders.items() if names}
    assert offenders == {}, (
        f"the suite is reading from the examples: {offenders}. The tests own "
        f"tests/fixtures/; the examples are documentation and answer to a different master."
    )


def test_no_example_imports_a_test():
    offenders = {
        str(path.relative_to(ROOT)): sorted(
            m
            for m in imported_modules(path)
            if m.split(".")[0] in {"tests", "fixtures", "backends"}
        )
        for path in EXAMPLES.rglob("*.py")
    }
    offenders = {path: names for path, names in offenders.items() if names}
    assert offenders == {}, f"an example is reading from the suite: {offenders}"


def test_an_example_depends_on_nothing_but_the_framework():
    """An example that needs another example is not standalone, and neither is one that reaches
    into a sibling package."""
    allowed = {
        "ekbatan",
        "sqlalchemy",
        "pytest",
        "dataclasses",
        "decimal",
        "enum",
        "uuid",
        "datetime",
        "typing",
        "pathlib",
        "collections",
        "tempfile",
        "__future__",
        # db-scheduler is part of the framework's surface, not a third-party import an example
        # sneaked in: a `DistributedJob` returns a db-scheduler `Schedule`, exactly as the Java
        # `DistributedJob` returns one. A user writing a job imports it in either language.
        "db_scheduler",
    }
    for path in EXAMPLES.rglob("*.py"):
        package = path.relative_to(EXAMPLES).parts[0]
        for module in imported_modules(path):
            root = module.split(".")[0]
            assert root in allowed or module.startswith(f"examples.{package}"), (
                f"{path.relative_to(ROOT)} imports {module!r}; an example may use the framework "
                f"and the standard library, and nothing else"
            )


def test_the_unit_suite_never_asks_for_a_database():
    """`tests/unit` is database-free by rule, not by luck - so it stays runnable in a second
    with nothing installed and nothing running."""
    offenders: dict[str, list[str]] = {}
    for path in (TESTS / "unit").rglob("test_*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                asked = {a.arg for a in node.args.args} & DATABASE_FIXTURES
                if asked:
                    offenders.setdefault(str(path.relative_to(ROOT)), []).append(
                        f"{node.name} wants {sorted(asked)}"
                    )
    assert offenders == {}, f"these belong in tests/integration/: {offenders}"


def test_the_layout_is_what_it_claims_to_be():
    assert (TESTS / "fixtures").is_dir(), "the tests own their domain"
    assert (TESTS / "unit").is_dir() and (TESTS / "integration").is_dir()
    assert not (EXAMPLES / "__init__.py").exists(), (
        "examples/ is a folder of separate applications, not a package to import from"
    )
    assert list((TESTS / "unit").glob("test_*.py")), "the unit suite is not empty"
    assert list((TESTS / "integration").glob("test_*.py")), "the integration suite is not empty"
