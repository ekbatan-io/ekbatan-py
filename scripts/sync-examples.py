"""Keep the example matrix in step: every example, built with uv, pip and Poetry.

    uv run python scripts/sync-examples.py           # write
    uv run python scripts/sync-examples.py --check   # fail if anything differs (make lint)

Java carries each of its examples once per build tool - `*-gradle-*` and `*-maven-*` - each a
project of its own, with only the build files telling them apart. This is that, for the three
ways a Python project is built: `<app>-uv-<db>`, `<app>-pip-<db>`, `<app>-poetry-<db>`.

**The uv project is the source.** Edit its `src/` and its README; this writes the pip and Poetry
projects from it - the same `src/`, byte for byte - and every project's `pyproject.toml`,
`Makefile` and `.gitignore` from the templates below. The README's build section, between the
`<!-- build -->` markers, is written here too; the rest is copied from the uv project's.

Generated files are not copied: `schema.py` is made by each project's `make` (except SQLite's,
which is written by hand), and nothing under `__pycache__`.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "ekbatan-py-examples"
BUILDS = ("uv", "pip", "poetry")
BUILD_NAMES = {"uv": "uv", "pip": "pip and setuptools", "poetry": "Poetry"}


@dataclass(frozen=True)
class Example:
    app: str  # fastapi-wallet-rest
    db: str  # pg, mysql, mariadb, sqlite
    package: str
    description: str
    extras: tuple[str, ...]
    web: bool = True  # FastAPI and uvicorn
    variant: str = ""  # sharded
    image: str | None = "postgres:17-alpine"  # None: schema.py is hand-written, no container
    containers: str | None = "postgres"  # the testcontainers extra, if the test starts one

    def directory(self, build: str) -> Path:
        parts = [self.app, build, *([self.variant] if self.variant else []), self.db]
        return EXAMPLES / "-".join(parts)

    @property
    def project(self) -> str:
        return "-".join([self.app, *([self.variant] if self.variant else []), self.db])


MATRIX = (
    Example(
        "fastapi-wallet-rest",
        "pg",
        "fastapi_wallet_rest_pg",
        "A wallet service over HTTP on PostgreSQL",
        ("postgres",),
    ),
    Example(
        "fastapi-wallet-rest",
        "mysql",
        "fastapi_wallet_rest_mysql",
        "A wallet service over HTTP on MySQL",
        ("mysql",),
        image="mysql:8.4",
        containers="mysql",
    ),
    Example(
        "fastapi-wallet-rest",
        "mariadb",
        "fastapi_wallet_rest_mariadb",
        "A wallet service over HTTP on MariaDB",
        ("mariadb",),
        image="mariadb:11.4",
        containers="mysql",
    ),
    Example(
        "fastapi-wallet-rest",
        "pg",
        "fastapi_wallet_rest_sharded_pg",
        "A wallet service over HTTP on two PostgreSQL databases",
        ("postgres",),
        variant="sharded",
    ),
    Example(
        "fastapi-wallet-saga",
        "pg",
        "fastapi_wallet_saga_pg",
        "A transfer across two databases, as a saga",
        ("postgres",),
    ),
    Example(
        "job-worker",
        "pg",
        "job_worker_pg",
        "A worker running distributed jobs, no HTTP",
        ("postgres",),
        web=False,
    ),
    Example(
        "wallet-service",
        "sqlite",
        "wallet_service_sqlite",
        "A wallet, end to end, on SQLite",
        (),
        web=False,
        image=None,
        containers=None,
    ),
)

SOURCE_NOTE = (
    "# ekbatan is not on PyPI yet. Until it is, it - and its scheduler - come from this\n"
    "# repository; delete this once it is published.\n"
)


def dependencies(e: Example) -> list[str]:
    ekbatan = f"ekbatan[{','.join(e.extras)}]" if e.extras else "ekbatan"
    return [ekbatan, *(["fastapi>=0.115", "uvicorn[standard]>=0.32"] if e.web else [])]


def dev_dependencies(e: Example) -> list[str]:
    deps = (["ekbatan[codegen]"] if e.image else []) + ["pytest>=8.0"]
    if e.containers:
        deps.append(f"testcontainers[{e.containers}]>=4.15")
    if e.web:
        deps.append("httpx>=0.27")
    return deps


def toml_list(items: list[str], indent: str = "") -> str:
    return "[" + ", ".join(f'"{i}"' for i in items) + "]"


def pyproject(e: Example, build: str) -> str:
    head = (
        f'[project]\nname = "{e.project}"\nversion = "0.1.0"\n'
        f'description = "{e.description}, built with {BUILD_NAMES[build]}."\n'
        f'requires-python = ">=3.13"\n'
    )
    pytest = '[tool.pytest.ini_options]\naddopts = "-q"\n'
    if build == "uv":
        return (
            f"{head}dependencies = {toml_list(dependencies(e))}\n\n"
            f"[dependency-groups]\ndev = {toml_list(dev_dependencies(e))}\n\n"
            '[build-system]\nrequires = ["uv_build>=0.13,<0.14"]\nbuild-backend = "uv_build"\n\n'
            f"{pytest}\n{SOURCE_NOTE}[tool.uv.sources]\n"
            'ekbatan = { path = "../..", editable = true }\n'
            'ekbatan-py-db-scheduler = { path = "../../db-scheduler", editable = true }\n'
        )
    if build == "pip":
        data = '["ekbatan.yaml", "migrations/*.sql"]' if e.image else '["ekbatan.yaml"]'
        return (
            f"{head}dependencies = {toml_list(dependencies(e))}\n\n"
            f"[dependency-groups]\ndev = {toml_list(dev_dependencies(e))}\n\n"
            '[build-system]\nrequires = ["setuptools>=69"]\n'
            'build-backend = "setuptools.build_meta"\n\n'
            "# setuptools packs only .py files unless told. The service reads these at startup.\n"
            f"[tool.setuptools.package-data]\n{e.package} = {data}\n\n{pytest}"
        )
    deps = dependencies(e)
    runtime = [
        deps[0],
        "# ekbatan's own dependency, listed only so that the path below applies to it: Poetry",
        "# applies [tool.poetry.dependencies] only to what is listed here. Delete with that.",
        "ekbatan-py-db-scheduler",
        *deps[1:],
    ]
    listed = "\n".join(f"    {d}" if d.startswith("#") else f'    "{d}",' for d in runtime)
    include = (
        "# Poetry leaves a gitignored file out of the package, and schema.py is one. Name it.\n"
        f'[[tool.poetry.include]]\npath = "src/{e.package}/schema.py"\n'
        'format = ["sdist", "wheel"]\n\n'
        if e.image
        else ""
    )
    group = []
    if e.image:
        group.append("# The schema generator: a build step, never a runtime dependency.")
        group.append('ekbatan = { path = "../..", develop = true, extras = ["codegen"] }')
    group.append('pytest = ">=8.0"')
    if e.containers:
        group.append(f'testcontainers = {{ version = ">=4.15", extras = ["{e.containers}"] }}')
    if e.web:
        group.append('httpx = ">=0.27"')
    return (
        f"{head}dependencies = [\n{listed}\n]\n\n"
        '[build-system]\nrequires = ["poetry-core>=2.0,<3.0"]\n'
        'build-backend = "poetry.core.masonry.api"\n\n'
        f'[tool.poetry]\npackages = [{{ include = "{e.package}", from = "src" }}]\n\n'
        f"{include}{SOURCE_NOTE}[tool.poetry.dependencies]\n"
        'ekbatan = { path = "../..", develop = true }\n'
        'ekbatan-py-db-scheduler = { path = "../../db-scheduler", develop = true }\n\n'
        "[tool.poetry.group.dev.dependencies]\n" + "\n".join(group) + f"\n\n{pytest}"
    )


def makefile(e: Example, build: str) -> str:
    name = e.directory(build).name
    py = {"uv": "uv run python -m", "pip": "$(VENV)/python -m", "poetry": "poetry run python -m"}[
        build
    ]
    lines = [
        f"# {name} - the project builds with {BUILD_NAMES[build]}. Written by",
        "# scripts/sync-examples.py, from its uv twin; edit the template there.",
        "#",
        "#     make            generate what is out of date, then test the built wheel, installed",
        "#     make run        run it" + (" (needs the database in README.md)" if e.image else ""),
    ]
    if e.image:
        lines += [
            "#     make schema     regenerate schema.py from the migrations",
            "#     make builders   regenerate builders.py after a model change",
        ]
    lines += ["#     make clean      throw away what is generated and the environment"]
    if build == "pip":
        lines += [
            "#",
            "# PYTHON must be 3.13 or later: `make PYTHON=python3.13` if `python3` is older.",
        ]
    lines += ["", f"PACKAGE := {e.package}", "SRC     := src/$(PACKAGE)"]
    if e.image:
        lines += [f"IMAGE   := {e.image}"]
    if build == "pip":
        lines += ["PYTHON  ?= python3", "VENV    := .venv/bin"]
    lines += ["", ".PHONY: all env test run schema builders clean", ".DEFAULT_GOAL := all", ""]
    lines += ["all: test", ""]
    # The environment.
    if build == "uv":
        env = "env:\n\tuv sync --quiet\n"
    elif build == "pip":
        extras = ",".join([*e.extras, *(["codegen"] if e.image else [])])
        spec = f'"../..[{extras}]"' if extras else '"../.."'
        env = (
            "# ekbatan is not on PyPI yet: until it is, it - and its scheduler - come from this\n"
            "# repository. Once it is, `pip install --group dev -e .` alone does this.\n"
            "$(VENV)/python:\n\t$(PYTHON) -m venv .venv\n"
            f"\t$(VENV)/pip install -q -e ../../db-scheduler -e {spec}\n"
            "\t$(VENV)/pip install -q --group dev -e .\n\n"
            "env: $(VENV)/python\n"
        )
    else:
        env = "env:\n\tpoetry install --quiet\n"
    lines += [env]
    needs = "$(SRC)/schema.py" if e.image else "env"
    if e.image:
        lines += [
            "# A container starts when a migration changed, and not otherwise.",
            "$(SRC)/schema.py: $(wildcard $(SRC)/migrations/*.sql) | env",
            f"\t{py} ekbatan.schemagen --migrations $(SRC)/migrations --out $@ --image $(IMAGE)",
            "",
            "schema: $(SRC)/schema.py",
            "",
            "builders: env",
            f"\t{py} ekbatan.codegen $(PACKAGE).model --out $(SRC)/builders.py",
            "",
        ]
    lines += [
        "# The tests run against the project as its wheel, installed - not the source tree - so a",
        "# file the build leaves out fails here rather than on a server.",
        f"test: {needs}",
    ]
    if build == "uv":
        lines += [
            # ekbatan and the scheduler too: uv reuses a built wheel until the version changes,
            # so without these the tests run whatever ekbatan was the first time.
            f"\tuv sync --no-editable --reinstall-package {e.project} \\",
            "\t\t--reinstall-package ekbatan --reinstall-package ekbatan-py-db-scheduler --quiet",
            "\tuv run --no-sync pytest --pyargs $(PACKAGE)",
        ]
    elif build == "pip":
        lines += [
            "\trm -rf dist build && $(VENV)/pip wheel -q --no-deps . -w dist",
            "\t$(VENV)/pip install -q --no-deps --force-reinstall dist/*.whl",
            "\t$(VENV)/python -m pytest --pyargs $(PACKAGE)",
        ]
    else:
        lines += [
            "\trm -rf dist && poetry build --quiet --format wheel",
            "\tpoetry run pip install -q --no-deps --force-reinstall dist/*.whl",
            "\tpoetry run pytest --pyargs $(PACKAGE)",
        ]
    lines += ["", f"run: {needs}"]
    if build == "uv":
        lines += ["\tuv sync --quiet", "\tuv run python -m $(PACKAGE)"]
    elif build == "pip":
        lines += ["\t$(VENV)/pip install -q -e .", "\t$(VENV)/python -m $(PACKAGE)"]
    else:
        lines += ["\tpoetry install --quiet", "\tpoetry run python -m $(PACKAGE)"]
    generated = "$(SRC)/schema.py " if e.image else ""
    extra = {"uv": "", "pip": " build src/*.egg-info", "poetry": ""}[build]
    lines += ["", "clean:", f"\trm -rf {generated}dist .venv{extra}", ""]
    return "\n".join(lines)


def gitignore(e: Example, build: str) -> str:
    lines = [".venv/", "dist/"]
    lines += {"uv": ["uv.lock"], "pip": ["build/", "*.egg-info/"], "poetry": ["poetry.lock"]}[build]
    if e.image:
        lines += [
            "# Generated by `make schema`, as Java generates into build/. Never committed.",
            f"src/{e.package}/schema.py",
        ]
    return "\n".join(lines) + "\n"


def build_section(e: Example, build: str) -> str:
    others = [b for b in BUILDS if b != build]
    links = " and ".join(f"[{BUILD_NAMES[b]}](../{e.directory(b).name})" for b in others)
    commands = (
        "make            # generate what is out of date, then test the built wheel, installed\n"
        "make run        # run it" + ("; the database comes first, above" if e.image else "")
    )
    if not e.image:
        commands = "make run        # run it\nmake            # test the built wheel, installed"
    python = (
        "\n\nThe venv is made with `python3`, which must be 3.13 or later - "
        "`make PYTHON=python3.13` if yours is older."
        if build == "pip"
        else ""
    )
    poetry = "\n\nNeeds `poetry` 2 on the path." if build == "poetry" else ""
    return (
        "<!-- build: written by scripts/sync-examples.py -->\n"
        f"```\n{commands}\n```\n\n"
        f"Built with **{BUILD_NAMES[build]}**. The same project with {links} differs only in "
        f"`pyproject.toml`, `Makefile` and `.gitignore`.{python}{poetry}\n"
        "<!-- /build -->"
    )


BLOCK = re.compile(r"<!-- build: written by scripts/sync-examples.py -->.*?<!-- /build -->", re.S)


def readme(e: Example, build: str) -> str:
    source = (e.directory("uv") / "README.md").read_text()
    source = re.sub(r"\A# .*", f"# {e.directory(build).name}", source, count=1)
    return BLOCK.sub(lambda _: build_section(e, build), source, count=1)


def poetry_toml() -> str:
    return "[virtualenvs]\nin-project = true\n"


def wanted(e: Example) -> dict[Path, str | Path]:
    """Every file the matrix holds for this example: text to write, or a file to copy."""
    files: dict[Path, str | Path] = {}
    source = e.directory("uv") / "src"
    for build in BUILDS:
        target = e.directory(build)
        files[target / "pyproject.toml"] = pyproject(e, build)
        files[target / "Makefile"] = makefile(e, build)
        files[target / ".gitignore"] = gitignore(e, build)
        files[target / "README.md"] = readme(e, build)
        if build == "poetry":
            files[target / "poetry.toml"] = poetry_toml()
        if build != "uv":
            for path in sorted(source.rglob("*")):
                if (
                    path.is_file()
                    and "__pycache__" not in path.parts
                    and not (e.image and path.name == "schema.py")
                ):
                    files[target / "src" / path.relative_to(source)] = path
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="report differences, write nothing")
    args = parser.parse_args()
    differ: list[str] = []
    for e in MATRIX:
        files = wanted(e)
        for path, content in files.items():
            text = content.read_bytes() if isinstance(content, Path) else content.encode()
            if not path.exists() or path.read_bytes() != text:
                differ.append(str(path.relative_to(ROOT)))
                if not args.check:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(text)
        # A file in a pip or Poetry project's src/ that the uv project no longer has.
        for build in ("pip", "poetry"):
            src = e.directory(build) / "src"
            for path in src.rglob("*") if src.exists() else []:
                if (
                    path.is_file()
                    and "__pycache__" not in path.parts
                    # what a setuptools build leaves beside the package, gitignored
                    and not any(part.endswith(".egg-info") for part in path.parts)
                    and path not in files
                    and not (e.image and path.name == "schema.py")
                ):
                    differ.append(f"{path.relative_to(ROOT)} (not in the uv project)")
                    if not args.check:
                        path.unlink()
    for line in differ:
        print(("differs: " if args.check else "wrote: ") + line)
    if args.check and differ:
        print(f"{len(differ)} file(s) out of step; run scripts/sync-examples.py")
        return 1
    print(f"{len(MATRIX)} examples x {len(BUILDS)} builds, {'in step' if not differ else 'synced'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
