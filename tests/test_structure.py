"""The suite and the examples do not meet.

The framework's tests have their own domain, in `tests/fixtures/`, which exists to exercise
every shape worth exercising - including a few no application would want. The examples have the
opposite job: showing what good code with this framework looks like.

Left coupled, every change made for coverage lands in the documentation and every change made
for clarity breaks the suite. So this is a rule rather than a habit, and it is checked.
"""

from __future__ import annotations

import ast
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
EXAMPLES = ROOT / "ekbatan-py-examples"

#: What a build leaves inside an example project - its environment, its wheel - is not its code.
_NOT_SOURCE = {".venv", "dist", "build", ".pytest_cache", "__pycache__"}


def example_sources() -> list[pathlib.Path]:
    """Every Python file the example projects carry, and nothing an install put beside them."""
    return [
        path
        for path in EXAMPLES.rglob("*.py")
        if not any(
            part in _NOT_SOURCE or part.endswith(".egg-info")
            for part in path.relative_to(EXAMPLES).parts
        )
    ]


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
        for path in example_sources()
    }
    offenders = {path: names for path, names in offenders.items() if names}
    assert offenders == {}, f"an example is reading from the suite: {offenders}"


#: What an example may import besides the framework and the standard library.
#:
#: Every one of these is declared in the example projects' own `pyproject.toml` (written by
#: `scripts/sync-examples.py`), and that is the point of the list: adding a dependency to an
#: example is an edit in two places and therefore a decision, rather than something that happens
#: because an import was convenient. Nothing in `src/` may import any of them.
EXAMPLE_DEPENDENCIES = {
    # The framework itself, which is the point.
    "ekbatan",
    # db-scheduler is part of the framework's surface, not a third-party import an example
    # sneaked in: a `DistributedJob` returns a db-scheduler `Schedule`, exactly as the Java
    # `DistributedJob` returns one. A user writing a job imports it in either language.
    "db_scheduler",
    "sqlalchemy",
    # The `fastapi-wallet-*` projects are REST services, which is the whole thing they
    # exist to show - so they need a web framework, the way Java's grid examples need Spring,
    # Quarkus or Micronaut.
    "fastapi",
    "pydantic",
    "starlette",
    "uvicorn",
    "httpx",
    # The example suites, which run against real servers for the same reason the framework's
    # do - and `testcontainers` is also the framework's own `codegen` extra, because
    # `schemagen` starts a throwaway database exactly as Java's build does.
    "pytest",
    "testcontainers",
}


def test_an_example_depends_on_nothing_but_the_framework_and_its_declared_extras():
    """An example that needs another example is not standalone, and neither is one that reaches
    into a sibling package.

    The standard library is read from the interpreter rather than listed by hand: a hand-list
    turns "this example used `os`" into a test failure, which trains people to extend the list
    without reading it - and the list is the part that is supposed to be read.
    """
    allowed = EXAMPLE_DEPENDENCIES | set(sys.stdlib_module_names)
    for path in example_sources():
        package = path.relative_to(EXAMPLES).parts[0]
        # Its own package, by name: the generated schema.py imports the example's converters
        # by the dotted path pyproject.toml gives schemagen.
        src = EXAMPLES / package / "src"
        own = {p.name for p in src.iterdir() if p.is_dir()} if src.is_dir() else set()
        for module in imported_modules(path):
            root = module.split(".")[0]
            assert root in allowed | own or module.startswith(f"examples.{package}"), (
                f"{path.relative_to(ROOT)} imports {module!r}; an example may use the framework, "
                f"the standard library, and what the example projects declare - see "
                f"EXAMPLE_DEPENDENCIES in this file"
            )


def test_the_framework_itself_imports_none_of_an_example_s_extras():
    """The other half, and the one that matters: `src/` must not grow a web framework because
    an example has one."""
    # Minus the ones the framework legitimately has: `sqlalchemy` and `db_scheduler` are hard
    # dependencies. What is left is the web stack, which `src/` must never acquire. (yoyo, the
    # `migrations` extra, is imported only by `ekbatan._yoyo`, which is imported only once
    # something migrates - so it stays optional without being listed here.)
    forbidden = EXAMPLE_DEPENDENCIES - {
        "ekbatan",
        "sqlalchemy",
        "db_scheduler",
        # A framework extra, imported inside a function so it stays optional: `testcontainers`
        # for `schemagen`, which starts a throwaway database exactly as Java's build does.
        "testcontainers",
        "pytest",
    }
    offenders: dict[str, list[str]] = {}
    for path in (ROOT / "src").rglob("*.py"):
        reached = sorted(m for m in imported_modules(path) if m.split(".")[0] in forbidden)
        if reached:
            offenders[str(path.relative_to(ROOT))] = reached
    assert offenders == {}, f"the framework reached for an example's dependency: {offenders}"


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
        "ekbatan-py-examples/ is a folder of separate applications, not a package to import from"
    )
    assert list((TESTS / "unit").glob("test_*.py")), "the unit suite is not empty"
    assert list((TESTS / "integration").glob("test_*.py")), "the integration suite is not empty"


def test_importing_the_framework_does_not_load_yoyo():
    """yoyo is the optional `migrations` extra. `ekbatan._yoyo` holds everything that touches
    it and is imported only once something migrates - so an application that never migrates
    from files, or migrates with another tool, never needs it installed."""
    import subprocess

    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, ekbatan, ekbatan.migrations, ekbatan.schemagen; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] == 'yoyo'))",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]", f"yoyo was loaded by a plain import: {done.stdout}"
