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
    from sqlalchemy import Sequence

    # A sequence's constant is named as a table is, and a database refuses a table by a
    # sequence's name (they share one namespace), so it is no name a table could take.
    tables = {k for k, v in namespace.items() if isinstance(v, (Table, Sequence))}
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


# -- the round trip: what a database says it is, to compare a migrated one with a rebuilt one ----

#: Tables a migrated database has and a rebuilt one never does.
_BOOKKEEPING = ("ekbatan_schema_history",)


def describe_database(url: str, schemas: tuple[str, ...] = ()) -> list[str]:
    """Everything a database says about its schema, as sorted lines, from its own catalog.

    Two databases built differently - one by the migrations, one by ``create_all`` from the
    generated module - describe themselves line for line the same when the module lost nothing.
    PostgreSQL: relations and their kind, persistence, partitioning and inheritance; columns with
    their type, NOT NULL, default, identity, generation, collation, dimensions and comment; every
    constraint and index as the server prints it; sequences, domains, enums. MySQL and MariaDB:
    ``SHOW CREATE TABLE`` (keys sorted - their order is the order they were made in) and
    sequences. SQLite: its ``PRAGMA``s, each index's text, and the table's own options.
    """
    db = create_engine(url)
    try:
        with db.connect() as connection:
            name = db.dialect.name
            if name == "postgresql":
                return _describe_postgres(connection, schemas)
            if name in ("mysql", "mariadb"):
                return _describe_mysql(connection, name, schemas)
            return _describe_sqlite(connection)
    finally:
        db.dispose()


def _describe_postgres(connection: Any, schemas: tuple[str, ...]) -> list[str]:
    from sqlalchemy import bindparam, text

    wanted = ["public", *schemas]

    def rows(sql: str) -> list[Any]:
        statement = text(sql).bindparams(bindparam("s", expanding=True))
        return list(connection.execute(statement, {"s": wanted}).all())

    out: list[str] = []
    for row in rows(
        "SELECT n.nspname, c.relname, c.relkind, c.relpersistence, pg_get_partkeydef(c.oid), "
        "pg_get_expr(c.relpartbound, c.oid), obj_description(c.oid, 'pg_class'), "
        "(SELECT string_agg(p.relname, ',' ORDER BY p.relname) FROM pg_inherits i "
        " JOIN pg_class p ON p.oid = i.inhparent WHERE i.inhrelid = c.oid), "
        "c.relrowsecurity, c.relforcerowsecurity, c.reloptions, c.relreplident "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname IN :s AND c.relkind IN ('r', 'p', 'f')"
    ):
        out.append(f"relation {tuple(row)}")
    for row in rows(
        "SELECT n.nspname, c.relname, a.attnum, a.attname, format_type(a.atttypid, a.atttypmod), "
        "a.attnotnull, pg_get_expr(d.adbin, d.adrelid), a.attidentity, a.attgenerated, "
        "co.collname, a.attndims, col_description(c.oid, a.attnum), a.attstorage, "
        "a.attcompression::text, COALESCE(a.attstattarget::int, -1), a.attoptions "
        "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "LEFT JOIN pg_collation co ON co.oid = a.attcollation AND a.attcollation <> "
        "(SELECT typcollation FROM pg_type WHERE oid = a.atttypid) "
        "WHERE n.nspname IN :s AND c.relkind IN ('r', 'p', 'f') "
        "AND a.attnum > 0 AND NOT a.attisdropped"
    ):
        out.append(f"column {tuple(row)}")
    for row in rows(
        "SELECT n.nspname, c.relname, con.conname, pg_get_constraintdef(con.oid) "
        "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname IN :s"
    ):
        out.append(f"constraint {tuple(row)}")
    for row in rows(
        "SELECT schemaname, indexname, indexdef, "
        "obj_description(format('%I.%I', schemaname, indexname)::regclass, 'pg_class') "
        "FROM pg_indexes WHERE schemaname IN :s"
    ):
        out.append(f"index {tuple(row)}")
    for row in rows(
        "SELECT schemaname, tablename, policyname, permissive, roles::text, cmd, qual, "
        "with_check FROM pg_policies WHERE schemaname IN :s"
    ):
        out.append(f"policy {tuple(row)}")
    for row in rows(
        "SELECT schemaname, tablename, rulename, definition FROM pg_rules WHERE schemaname IN :s"
    ):
        out.append(f"rule {tuple(row)}")
    for row in rows(
        "SELECT n.nspname, s.stxname, pg_get_statisticsobjdef(s.oid) FROM pg_statistic_ext s "
        "JOIN pg_namespace n ON n.oid = s.stxnamespace WHERE n.nspname IN :s"
    ):
        out.append(f"statistics {tuple(row)}")
    for row in rows(
        "SELECT n.nspname, co.collname, co.collprovider, co.collcollate, co.collctype, "
        "co.collisdeterministic FROM pg_collation co "
        "JOIN pg_namespace n ON n.oid = co.collnamespace WHERE n.nspname IN :s"
    ):
        out.append(f"collation {tuple(row)}")
    for row in rows(
        "SELECT schemaname, sequencename, data_type, start_value, min_value, max_value, "
        "increment_by, cycle, cache_size FROM pg_sequences WHERE schemaname IN :s"
    ):
        out.append(f"sequence {tuple(row)}")
    for row in rows(
        "SELECT n.nspname, t.typname, t.typtype, format_type(t.typbasetype, t.typtypmod), "
        "t.typnotnull, t.typdefault, co.collname, "
        "(SELECT string_agg(pg_get_constraintdef(k.oid), ' AND ' ORDER BY k.conname) "
        " FROM pg_constraint k WHERE k.contypid = t.oid), "
        "(SELECT string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder) FROM pg_enum e "
        " WHERE e.enumtypid = t.oid) "
        "FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
        "LEFT JOIN pg_collation co ON co.oid = t.typcollation AND t.typtype = 'd' "
        "AND t.typcollation <> (SELECT typcollation FROM pg_type WHERE oid = t.typbasetype) "
        "WHERE n.nspname IN :s AND t.typtype IN ('d', 'e')"
    ):
        out.append(f"type {tuple(row)}")
    return sorted(line for line in out if not any(b in line for b in _BOOKKEEPING))


def _describe_mysql(connection: Any, name: str, schemas: tuple[str, ...]) -> list[str]:
    import re

    from sqlalchemy import text

    out: list[str] = []
    for schema in [None, *schemas]:
        where = "DATABASE()" if schema is None else f"'{schema}'"
        prefix = "" if schema is None else f"`{schema}`."
        tables = connection.execute(
            text(
                "SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES "
                f"WHERE TABLE_SCHEMA = {where} ORDER BY TABLE_NAME"
            )
        ).all()
        for table, kind in tables:
            if table in _BOOKKEEPING:
                continue
            if kind == "VIEW":
                continue  # compared by describe_views
            what = "SEQUENCE" if kind == "SEQUENCE" else "TABLE"
            created = str(
                connection.execute(text(f"SHOW CREATE {what} {prefix}`{table}`")).all()[0][1]
            )
            created = re.sub(r" AUTO_INCREMENT=\d+", "", created)
            # A default naming a sequence names its database, which differs between two.
            created = re.sub(r"nextval\(`[^`]+`\.", "nextval(", created)
            lines = created.splitlines()
            keys = sorted(
                line.rstrip(",")
                for line in lines
                if re.match(r"\s*(UNIQUE |FULLTEXT |SPATIAL )?KEY ", line)
            )
            body = [line.rstrip(",") for line in lines if line.rstrip(",") not in keys]
            out += [f"{schema}.{table}: {line}" for line in body + keys]
    return out


def _describe_sqlite(connection: Any) -> list[str]:
    import re

    from sqlalchemy import inspect as inspect_
    from sqlalchemy.dialects import sqlite

    from ekbatan.schemagen._reflect import index_parts, sqlite_table_parts, unquote

    def quoted(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    out: list[str] = []
    inspector = inspect_(connection)
    for table, sql in connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).all():
        if table in _BOOKKEEPING:
            continue
        parts = sqlite_table_parts(str(sql))
        collations = sorted(parts.collations.items())
        out.append(f"{table}: options {parts.options}, collations {collations}")
        columns = {c["name"]: c for c in inspector.get_columns(table)}
        keyed = {
            r[1] for r in connection.exec_driver_sql(f"PRAGMA table_info({quoted(table)})") if r[5]
        }
        for cid, name, declared, notnull, default, pk, hidden in connection.exec_driver_sql(
            f"PRAGMA table_xinfo({quoted(table)})"
        ).all():
            try:
                spelled = str(columns[name]["type"].compile(dialect=sqlite.dialect()))
            except Exception:  # a column with no type: SQLAlchemy has nothing to compile
                spelled = str(declared)
            # SQLite lets a key column hold NULL unless it says NOT NULL; create_all says it.
            notnull = None if name in keyed else notnull
            out.append(f"{table}: column {(cid, name, spelled, notnull, default, pk, hidden)}")
        for row in connection.exec_driver_sql(f"PRAGMA foreign_key_list({quoted(table)})"):
            out.append(f"{table}: fk {tuple(row)[2:]}")
        for check in inspector.get_check_constraints(table):
            out.append(f"{table}: check {check['name']} {check['sqltext']}")
        for row in connection.exec_driver_sql(f"PRAGMA index_list({quoted(table)})").all():
            index = row[1]
            out.append(f"{table}: index {index} unique={row[2]} origin={row[3]} partial={row[4]}")
            for info in connection.exec_driver_sql(f"PRAGMA index_xinfo({quoted(index)})"):
                out.append(f"{table}:   {index} {tuple(info)}")
    for index, sql in connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
    ).all():
        found = index_parts(str(sql))
        if found is not None:
            elements, tail = found
            # `"a" ASC` and `a` say the same; what an element says beyond its name is kept.
            said = [
                unquote(e.removesuffix(" ASC").strip())
                if re.fullmatch(r'("[^"]+"|\[[^\]]+\]|`[^`]+`|\w+)( ASC)?', e.strip(), re.I)
                else e
                for e in elements
            ]
            out.append(f"index {index}: {said} {re.sub(r'\s+', ' ', tail)}")
    return sorted(line for line in out if not any(b in line for b in _BOOKKEEPING))


def describe_views(
    url: str, schemas: tuple[str, ...] = (), tables: list[tuple[str | None, str]] | None = None
) -> list[str]:
    """The columns of every view - name, type, NOT NULL - as sorted lines; or, given
    ``tables`` as (schema, name), the same of those tables: the ones ``view_metadata.create_all``
    built from the module. A view's generated ``Table`` is right when the two say the same."""
    import re

    from sqlalchemy import bindparam, text

    db = create_engine(url)
    try:
        with db.connect() as connection:
            name = db.dialect.name
            out: list[str] = []
            if name == "postgresql":
                wanted = ["public", *schemas]
                kinds = "('v', 'm')" if tables is None else "('r')"
                statement = text(
                    "SELECT n.nspname, c.relname, a.attnum, a.attname, "
                    "format_type(a.atttypid, a.atttypmod), a.attnotnull, co.collname "
                    "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "LEFT JOIN pg_collation co ON co.oid = a.attcollation AND a.attcollation <> "
                    "(SELECT typcollation FROM pg_type WHERE oid = a.atttypid) "
                    f"WHERE n.nspname IN :s AND c.relkind IN {kinds} "
                    "AND a.attnum > 0 AND NOT a.attisdropped"
                ).bindparams(bindparam("s", expanding=True))
                for row in connection.execute(statement, {"s": wanted}).all():
                    out.append(f"view column {tuple(row)}")
            elif name in ("mysql", "mariadb"):
                for schema in [None, *schemas]:
                    where = "DATABASE()" if schema is None else f"'{schema}'"
                    kind = "c.TABLE_NAME IN (SELECT TABLE_NAME FROM information_schema.TABLES t "
                    kind += f"WHERE t.TABLE_SCHEMA = {where} AND t.TABLE_TYPE = 'VIEW')"
                    for row in connection.execute(
                        text(
                            "SELECT c.TABLE_NAME, c.ORDINAL_POSITION, c.COLUMN_NAME, "
                            "c.COLUMN_TYPE, c.IS_NULLABLE, c.COLLATION_NAME "
                            f"FROM information_schema.COLUMNS c WHERE c.TABLE_SCHEMA = {where}"
                            + ("" if tables is not None else f" AND {kind}")
                        )
                    ).all():
                        # An integer's display width is not data: MariaDB gives a view's
                        # computed one a width of its own, and a table the type's default.
                        spelled = re.sub(r"^(\w*int)\(\d+\)$", r"\1", str(row[3]))
                        row = (*row[:3], spelled, *row[4:])
                        out.append(f"view column {schema} {tuple(row)}")
            else:
                kind = "view" if tables is None else "table"
                for (view,) in connection.exec_driver_sql(
                    f"SELECT name FROM sqlite_master WHERE type = '{kind}'"
                ).all():
                    quoted = '"' + str(view).replace('"', '""') + '"'
                    for row in connection.exec_driver_sql(f"PRAGMA table_info({quoted})"):
                        out.append(f"view column {view} {tuple(row)[:4]}")
            if tables is not None:
                own = {(s or ("public" if name == "postgresql" else None), t) for s, t in tables}
                out = [line for line in out if any(_names(line, s, t) for s, t in own)]
            return sorted(out)
    finally:
        db.dispose()


def _names(line: str, schema: str | None, table: str) -> bool:
    """Whether a `describe_views` line is of this table."""
    if line.startswith("view column ("):
        return line.startswith(f"view column {(schema, table)!r}"[:-1])
    if line.startswith(f"view column {schema} ("):
        return line.startswith(f"view column {schema} {(table,)!r}"[:-2])
    return line.startswith(f"view column {table} (")
