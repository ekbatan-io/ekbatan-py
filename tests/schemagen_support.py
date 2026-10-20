"""What the schemagen tests share: fresh databases, generated modules imported, types as spelled.

Every schemagen test that touches a database runs the real pipeline: migrations into a fresh
database on the session's container, ``generate(url=...)``, the module imported from the file it
wrote. Nothing here imitates a step.
"""

from __future__ import annotations

import ast
import builtins
import importlib.util
import itertools
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import create_engine

from backends import Backend

_COUNTER = itertools.count(1)

#: The migration each dialect's tests write in, by `Backend.dialect`.
SQLALCHEMY_NAME = {
    "sqlite": "sqlite",
    "postgres": "postgresql",
    "mysql": "mysql",
    "mariadb": "mariadb",
}


def fresh(backend: Backend, prefix: str = "sg") -> str:
    """A url to a database nobody has used, on the session's container."""
    return backend.url(f"{prefix}_{next(_COUNTER)}")


def migrations(root: Path, *files: str) -> Path:
    """A migrations directory holding these SQL texts as V0001, V0002, ..."""
    root.mkdir(parents=True, exist_ok=True)
    for number, sql in enumerate(files, start=1):
        (root / f"V{number:04d}__step.sql").write_text(sql)
    return root


def load(path: Path) -> ModuleType:
    """Import a generated module from its file, under a name of its own."""
    name = f"generated_schema_{next(_COUNTER)}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def column_sources(text: str) -> dict[tuple[str, str], str]:
    """``(TABLE_CONSTANT, column) -> type source`` from a generated module's text, normalised
    by ``ast.unparse`` so a test compares meaning and not quote style."""
    found: dict[tuple[str, str], str] = {}
    for node in ast.parse(text).body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        for argument in node.value.args:
            if (
                isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Name)
                and argument.func.id == "Column"
            ):
                name = argument.args[0]
                assert isinstance(name, ast.Constant)
                found[(target.id, str(name.value))] = ast.unparse(argument.args[1])
    return found


def unused_and_undefined(text: str) -> tuple[set[str], set[str]]:
    """The names a generated module imports and never uses, and the ones it uses and never
    defines - both empty for a module that imports exactly what it needs."""
    tree = ast.parse(text)
    imported: set[str] = set()
    # The builtins a record's types name - `int`, `str`, `bytes` - are there without an import.
    defined: set[str] = {"__file__", *dir(builtins)}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module != "__future__":
            imported |= {a.asname or a.name for a in node.names}
        elif isinstance(node, ast.Assign):
            defined |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.ClassDef):
            defined.add(node.name)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return imported - used, used - imported - defined


def normal(source: str) -> str:
    return ast.unparse(ast.parse(source, mode="eval"))


def raw_types(url: str, schema: str | None = None) -> dict[tuple[str, str], str]:
    """``(table, column) -> type`` as the database itself spells it, lower case, no spaces
    after commas - the same reading schemagen does."""
    from ekbatan.schemagen._reflect import Reflected, _raw_types

    engine = create_engine(url)
    try:
        reflected = Reflected(dialect_name=engine.dialect.name)
        _raw_types(engine, schema, reflected)
    finally:
        engine.dispose()
    return {
        (table, column): spelled.lower().replace(", ", ",")
        for (_, table, column), spelled in reflected.raw_types.items()
    }


@contextmanager
def notes() -> Iterator[list[str]]:
    """The notes schemagen logs while the block runs."""
    found: list[str] = []

    class _Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            found.append(record.getMessage())

    logger = logging.getLogger("io.ekbatan.schemagen")
    handler = _Keep()
    logger.addHandler(handler)
    try:
        yield found
    finally:
        logger.removeHandler(handler)


def engine(url: str) -> Any:
    return create_engine(url)
