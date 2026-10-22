"""A table may be named like anything the generated module writes - `text`, `date`, `json`,
`index`, `table`, `metadata`, `schema_format` - and the module still imports and means the same.

The bug this pins: a table's constant is its name in capitals (`text` -> `TEXT`), and the module
once imported SQLAlchemy's types by their own capital names (`from sqlalchemy import TEXT`). A
table named `text` replaced the type, and the next table that wrote `TEXT()` called a `Table`:
`TypeError: 'Table' object is not callable`, on import. Now every SQLAlchemy name is written
through `import sqlalchemy` - `sqlalchemy.TEXT()` - and nothing a constant could replace is
imported bare.

The names come from schemagen's own source (`emittable_names`), not from a list here, plus every
name the generated module itself binds. For each one, on each database's rules: a module with a
table of that name and a table that uses every type and every feature - a key, an identity, a
computed column, defaults, a sequence, unique, check and foreign keys, indexes, comments - must
import, export the new table, and build the other table exactly as it is built without it.

The same against real databases, through reflection:
`tests/integration/test_schemagen_table_names.py`.
"""

from __future__ import annotations

import ast
from functools import cache
from typing import Any

import pytest
import sqlalchemy
from sqlalchemy import (
    BIGINT,
    INTEGER,
    TEXT,
    VARCHAR,
    CheckConstraint,
    Column,
    Computed,
    ForeignKeyConstraint,
    Identity,
    Index,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.types import NullType, TypeEngine

from ekbatan.schemagen import render
from ekbatan.schemagen._render import constant
from schemagen_support import describe, emittable_names, hints, own_names

DIALECTS = ("postgresql", "mysql", "mariadb", "sqlite")

_MODULES: dict[str, Any] = {
    "sqlalchemy": sqlalchemy,
    "sqlalchemy.types": sqlalchemy.types,
    "postgresql": postgresql,
    "mysql": mysql,
    "sqlite": sqlite,
}

#: The types that cannot be built bare, and what to build them with.
_ARGUMENTS: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {
    "ARRAY": ((INTEGER(),), {}),
    "Enum": (("a", "b"), {"name": "e_generic"}),
    "ENUM": (("a", "b"), {"name": "e_dialect"}),
    "SET": (("a", "b"), {}),
    "DOMAIN": (("d_positive", INTEGER()), {}),
}


def _instance(name: str, module: str) -> TypeEngine[Any] | None:
    """A column type of this name, or None for what is not a column type or cannot be one
    without a subclass (``TypeDecorator``, ``AbstractRange``)."""
    found = getattr(_MODULES[module], name)
    if not (isinstance(found, type) and issubclass(found, TypeEngine)):
        return None
    args, kwargs = _ARGUMENTS.get(name, ((), {}))
    try:
        built = found(*args, **kwargs)
    except Exception:
        return None
    return built


def _column(name: str) -> str:
    return f"c_{name}_{_MODULES_SHORT[_NAMES[name]]}"


_NAMES = emittable_names()
_MODULES_SHORT = {
    "sqlalchemy": "sa",
    "sqlalchemy.types": "types",
    "postgresql": "pg",
    "mysql": "my",
    "sqlite": "lite",
}


def _tables() -> list[Table]:
    """`owners`, a table with a key named on its own, and `uses`: a column of every type, and
    every feature the writer writes with a SQLAlchemy name."""
    metadata = MetaData()
    owners = Table("owners", metadata, Column("id", BIGINT(), primary_key=True))
    pairs = Table(
        "pairs",
        metadata,
        Column("a", INTEGER(), nullable=False),
        Column("b", INTEGER(), nullable=False),
        PrimaryKeyConstraint("a", "b", name="pairs_key"),
    )
    columns = [
        Column(_column(name), built)
        for name, module in _NAMES.items()
        if (built := _instance(name, module)) is not None
    ]
    uses = Table(
        "uses",
        metadata,
        Column("id", BIGINT(), Identity(start=5, increment=2), primary_key=True),
        Column("owner_id", BIGINT(), nullable=False),
        Column("qty", INTEGER(), nullable=False, server_default=text("1")),
        Column("doubled", INTEGER(), Computed("qty * 2", persisted=True)),
        Column("counter", INTEGER(), server_default=text("nextval('uses_counter_seq'::regclass)")),
        Column("code", VARCHAR(10), nullable=False, comment="a code"),
        Column("note", TEXT()),
        Column("odd", NullType()),
        *columns,
        UniqueConstraint("code", name="uses_code"),
        CheckConstraint("qty > 0", name="uses_qty"),
        ForeignKeyConstraint(["owner_id"], ["owners.id"], name="uses_owner", ondelete="CASCADE"),
        Index("uses_qty_ix", "qty"),
        Index("uses_lower_note", text("lower(note)")),
        Index("uses_code_partial", "code", unique=True, postgresql_where=text("note IS NULL")),
        comment="every type",
    )
    return [owners, pairs, uses]


def _render(dialect: str, *extra: str) -> str:
    tables = _tables()
    metadata = tables[0].metadata
    named = [Table(name, metadata, Column("id", INTEGER(), primary_key=True)) for name in extra]
    return render(
        [*named, *tables],
        framework=["events", "event_notifications", "scheduled_tasks"],
        migrations_fingerprint="x",
        module=None,
        dialect=dialect,
    )


def _run(written: str) -> dict[str, Any]:
    namespace: dict[str, Any] = {"__file__": "schema.py"}
    exec(compile(written, "schema.py", "exec"), namespace)
    return namespace


@cache
def _baseline(dialect: str) -> tuple[str, dict[str, Any]]:
    written = _render(dialect)
    return written, _run(written)


def _all_names() -> list[str]:
    own = own_names(*_baseline("postgresql")) | own_names(*_baseline("mysql"))
    return sorted({n.lower() for n in [*_NAMES, *own]})


def test_the_names_cover_what_the_writer_and_the_type_table_write() -> None:
    """The derivation finds what it must, so a test over it is not quietly over nothing."""
    names = set(_all_names())
    for expected in (
        "text",
        "table",
        "column",
        "metadata",
        "index",
        "sequence",
        "computed",
        "identity",
        "nulltype",
        "uniqueconstraint",
        "foreignkeyconstraint",
        "checkconstraint",
        "primarykeyconstraint",
        "json",
        "jsonb",
        "uuid",
        "char",
        "boolean",
        "integer",
        "date",
        "time",
        "migrations_fingerprint",
        "schema_format",
        "sqlalchemy",
        "postgresql",
        "check_fingerprint",
        "outbox_table",
        "type_checking",
        "overload",
        "literal",
        "any",
        "unset",
    ):
        assert expected in names, expected


@pytest.mark.parametrize("rules", DIALECTS)
@pytest.mark.parametrize("name", _all_names())
def test_a_table_named_like_anything_the_module_writes_changes_nothing_else(
    name: str, rules: str
) -> None:
    baseline_text, baseline = _baseline(rules)

    written = _render(rules, name)
    namespace = _run(written)

    # The new table is there, under its own name.
    exported = constant(name)
    assert isinstance(namespace[exported], Table) and namespace[exported].name == name
    assert exported in namespace["__all__"]
    # Every other table is built exactly as it is without it. Over a snapshot: the baseline is
    # shared by every case, and nothing here may depend on it holding still.
    for key, value in list(baseline.items()):
        if isinstance(value, Table):
            assert describe(namespace[key]) == describe(value), key
    # Every record still names the same types - an import the new table's constant takes,
    # `from uuid import UUID` beside a table `uuid`, comes in under another name.
    for key, value in list(baseline.items()):
        if key.endswith("Record"):
            assert hints(namespace[key], namespace) == hints(value, baseline), key
    # And the module's own names still mean what they meant, but for the one the table takes.
    for own in own_names(baseline_text, baseline) - {exported}:
        before, after = baseline[own], namespace[own]
        if isinstance(before, (str, int)) or callable(before) or isinstance(before, type(ast)):
            assert after is before or after == before, own
        else:
            assert type(after) is type(before), own
    assert namespace["MIGRATIONS_FINGERPRINT"] == "x"


@pytest.mark.parametrize("rules", DIALECTS)
def test_every_column_is_the_type_its_source_names(rules: str) -> None:
    """The `uses` table's columns, with a table named after each name at once: each column is
    exactly the class its source writes (`sqlalchemy.TEXT()` is a `TEXT`), never a Table."""
    names = _all_names()
    written = _render(rules, *names)
    namespace = _run(written)

    uses = namespace["USES"]
    sources = {
        ast.literal_eval(call.args[0]): call.args[1]
        for call in ast.walk(ast.parse(written))
        if isinstance(call, ast.Call)
        and ast.unparse(call.func) == "sqlalchemy.Column"
        and isinstance(call.args[0], ast.Constant)
    }
    checked = 0
    for column in uses.columns:
        source = sources[column.name]
        assert isinstance(column.type, TypeEngine), column.name
        if isinstance(source, ast.Call) and isinstance(source.func, ast.Attribute):
            owner = ast.unparse(source.func.value)
            if owner in _MODULES:
                assert type(column.type) is getattr(_MODULES[owner], source.func.attr), column.name
                checked += 1
    assert checked > 50
    # Nothing from SQLAlchemy is imported bare that a constant - capitals - could replace: the
    # modules, and a record's `Range` or `MultiRange`, which no table's constant can be.
    imported = {
        a.asname or a.name
        for node in ast.parse(written).body
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sqlalchemy")
        for a in node.names
    }
    assert all(n in ("postgresql", "mysql", "sqlite") or n != n.upper() for n in imported)
    assert "\nimport sqlalchemy\n" in written


def test_reading_a_record_s_hints_leaves_the_module_s_namespace_as_it_was() -> None:
    """The flake `dictionary changed size during iteration` (`abstractrange-sqlite`, once in
    500): a hint is evaluated with the module's namespace as its globals, and a warning raised
    while it runs - an unclosed SQLite connection's ResourceWarning, when the garbage collector
    happens to run then - is registered in that frame's globals as `__warningregistry__`. The
    cached baseline namespace grew while the test iterated it. Made certain here with a hint
    whose evaluation warns."""
    import warnings

    from schemagen_support import hints

    def warns() -> type:
        warnings.warn("a warning while a hint is read", ResourceWarning, stacklevel=2)
        return int

    namespace: dict[str, Any] = {"warns": warns}
    exec(
        "from __future__ import annotations\nfrom typing import Any\n\n"
        "class R(dict[str, Any]):\n"
        "    def __init__(self, *, a: warns()) -> None:\n"
        "        super().__init__(a=a)\n",
        namespace,
    )
    assert isinstance(namespace["R"].__init__.__annotations__["a"], str)
    before = dict(namespace)
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        assert hints(namespace["R"], namespace) == {"a": int}
    assert namespace == before
    assert issubclass(namespace["R"], dict)
