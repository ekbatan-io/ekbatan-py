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
import typing
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Table, create_engine

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
            if isinstance(argument, ast.Call) and ast.unparse(argument.func) == "sqlalchemy.Column":
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
        elif isinstance(node, ast.Import):
            imported |= {a.asname or a.name.partition(".")[0] for a in node.names}
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


def emittable_names() -> dict[str, str]:
    """Every SQLAlchemy name schemagen can write into a module, by name -> the module it is
    written through (``sqlalchemy``, ``sqlalchemy.types``, ``postgresql``, ``mysql``, ``sqlite``).

    Read off schemagen's own source rather than listed by hand, so a name added to the type
    table or the writer is covered without anyone remembering to: every ``sqlalchemy.X`` (and
    ``postgresql.X`` ...) in its strings, every ``_sa("X")`` and ``_of("d", "X")``, every type
    it names by text or by class for a ``type(t).__name__`` to come from. And every type
    SQLAlchemy and its dialect modules export, because a type the table has no rule for is
    written as its ``repr``.
    """
    import re

    import sqlalchemy
    from sqlalchemy import types
    from sqlalchemy.dialects import mysql, postgresql, sqlite
    from sqlalchemy.types import TypeEngine

    import ekbatan.schemagen as package

    modules: dict[str, Any] = {
        "sqlalchemy": sqlalchemy,
        "sqlalchemy.types": types,
        "postgresql": postgresql,
        "mysql": mysql,
        "sqlite": sqlite,
    }
    DIALECTS = ("postgresql", "mysql", "sqlite")
    written = re.compile(
        r"\b(sqlalchemy\.types|sqlalchemy|postgresql|mysql|sqlite)\.([A-Za-z_]\w*)"
    )

    def is_type(module: str, name: str) -> bool:
        found = getattr(modules[module], name, None)
        return isinstance(found, type) and issubclass(found, TypeEngine)

    def real(module: str, name: str) -> bool:
        found = getattr(modules[module], name, None)
        return found is not None and not isinstance(found, type(sqlalchemy))

    names: dict[str, str] = {}
    root = Path(package.__file__).parent
    for path in (root / "_types.py", root / "_render.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for module, name in written.findall(node.value):
                    if real(module, name):
                        names.setdefault(name, module)
                for module in ("sqlalchemy", *DIALECTS):
                    if is_type(module, node.value):
                        names.setdefault(node.value, module)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                args = [a.value for a in node.args if isinstance(a, ast.Constant)]
                if node.func.id == "_sa" and args:
                    names.setdefault(args[0], "sqlalchemy")
                elif node.func.id == "_of" and len(args) >= 2:
                    names.setdefault(args[1], args[0])
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                owner = {"sqltypes": "sqlalchemy"}.get(node.value.id, node.value.id)
                if owner in modules and is_type(owner, node.attr):
                    names.setdefault(node.attr, owner)
    for module in ("sqlalchemy", *DIALECTS):
        for name in dir(modules[module]):
            if not name.startswith("_") and is_type(module, name):
                names.setdefault(name, module)
    return dict(sorted(names.items()))


def describe(table: Table) -> list[Any]:
    """What a table is, as plain values two modules' tables can be compared by."""
    columns = [
        (
            c.name,
            type(c.type),
            repr(c.type),
            c.nullable,
            c.primary_key,
            c.autoincrement,
            c.comment,
            str(getattr(c.server_default, "arg", None)),
            str(c.computed.sqltext) if c.computed is not None else None,
            (c.identity.start, c.identity.increment) if c.identity is not None else None,
            repr(c.default),
        )
        for c in table.columns
    ]
    constraints = sorted(
        (
            type(k).__name__,
            str(k.name),
            sorted(c.name for c in k.columns),
            str(getattr(k, "sqltext", "")),
            sorted(e.target_fullname for e in getattr(k, "elements", [])),
        )
        for k in table.constraints
    )
    indexes = sorted(
        (
            str(i.name),
            i.unique,
            [str(e) for e in i.expressions],
            sorted((k, str(v)) for k, v in i.dialect_kwargs.items() if v is not None),
        )
        for i in table.indexes
    )
    return [table.name, table.comment, columns, constraints, indexes]


def hints(record: Any, namespace: dict[str, Any]) -> dict[str, Any]:
    """A record's types, read in the module's own namespace - a module run by `exec` has no
    `sys.modules` entry for `typing.get_type_hints` to find."""
    return {
        key: eval(hint.__forward_arg__, namespace) if isinstance(hint, typing.ForwardRef) else hint
        for key, hint in record.__annotations__.items()
    }


def own_names(written: str, namespace: dict[str, Any]) -> set[str]:
    """Every name a module binds at the top level that is not a table or a record: its
    imports, `metadata`, `MIGRATIONS_FINGERPRINT`, `SCHEMA_FORMAT` - what a table must not take.
    ``namespace`` is the module's, run."""
    tables = {k for k, v in namespace.items() if isinstance(v, Table)}
    records = {k for k in namespace if k.endswith("Record")}
    bound: set[str] = set()
    for node in ast.parse(written).body:
        if isinstance(node, ast.Import):
            bound |= {a.asname or a.name.partition(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            bound |= {a.asname or a.name for a in node.names}
        elif isinstance(node, ast.Assign):
            bound |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return bound - tables - records - {"__all__"}
