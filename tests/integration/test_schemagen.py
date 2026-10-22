"""Generating table definitions from the schema the migrations produce.

Java's build starts a container, runs Flyway into it and points jOOQ at the result. This is that
pipeline, so the tests run it: real databases (the session's containers), real migrations, real
reflection, the module imported from the file it wrote.

    uv run pytest tests/integration/test_schemagen.py --dialects=all

The type table, type by type, is `test_schemagen_types.py`. This file is everything else: the
framework's tables, keys, indexes, defaults and views; schemas; forced types end to end; includes
and excludes; the configuration file and the command line; the stale-module guard; and one test
per round-2 finding that failed on the generator before this one.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import MetaData, Table, create_engine, insert, inspect, select, text
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateTable

from backends import Backend
from ekbatan.errors import ValidationError
from ekbatan.schemagen import (
    SCHEMA_FORMAT,
    ForcedType,
    SchemagenConfig,
    check_fingerprint,
    fingerprint,
    generate,
    render,
)
from schemagen_support import (
    SQLALCHEMY_NAME,
    column_sources,
    engine,
    fresh,
    load,
    migrations,
    normal,
    notes,
    unused_and_undefined,
)

ROOT = Path(__file__).resolve().parents[2]

#: One application table, per dialect family: a uuid key, text, JSON, timestamps, a unique key.
APP = {
    "postgres": """
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
""",
    "mysql": """
CREATE TABLE widgets (
    id CHAR(36) NOT NULL PRIMARY KEY,
    version BIGINT NOT NULL,
    state VARCHAR(24) NOT NULL,
    name VARCHAR(80) NOT NULL,
    tags JSON,
    retired_at DATETIME(6),
    created_date DATETIME(6) NOT NULL,
    updated_date DATETIME(6) NOT NULL,
    CONSTRAINT uq_widgets_name UNIQUE (name)
);
""",
    "sqlite": """
CREATE TABLE widgets (
    id CHAR(32) NOT NULL PRIMARY KEY,
    version BIGINT NOT NULL,
    state VARCHAR(24) NOT NULL,
    name VARCHAR(80) NOT NULL,
    tags JSON,
    retired_at DATETIME,
    created_date DATETIME NOT NULL,
    updated_date DATETIME NOT NULL,
    CONSTRAINT uq_widgets_name UNIQUE (name)
);
""",
}


def _family(dialect: str) -> str:
    return "mysql" if dialect == "mariadb" else dialect


def _framework_ddl(dialect: str) -> str:
    """The framework's own tables, as a real application's migration creates them - which is
    what gives the generator something to *decline* to reflect."""
    from ekbatan.migrations import emit_ddl
    from ekbatan.schema import notifications_table, outbox_table, scheduled_tasks_table

    framework = MetaData()
    outbox_table(framework)
    notifications_table(framework)
    scheduled_tasks_table(framework)
    return emit_ddl(framework, SQLALCHEMY_NAME[dialect])


@pytest.fixture
def app_migrations(tmp_path: Path, dialect: str) -> Path:
    return migrations(tmp_path / "migrations", APP[_family(dialect)], _framework_ddl(dialect))


#: What a MySQL or MariaDB project adds to say its CHAR(36) ids hold uuids - schemagen does not
#: guess it. On PostgreSQL there is no CHAR(36) to match, and the UUID type needs no rule.
UUID_IDS = ForcedType(
    name="uuid_column", include_types=r"CHAR\(36\)", include_expression=r".*\.id|.*_id"
)


@pytest.fixture
def generated(app_migrations: Path, tmp_path: Path, backend: Backend) -> Path:
    out = tmp_path / "app" / "schema.py"
    generate(
        app_migrations, out, url=fresh(backend), config=SchemagenConfig(forced_types=(UUID_IDS,))
    )
    return out


# -- what it produces --------------------------------------------------------------------------


def test_it_generates_the_application_tables(generated: Path) -> None:
    text_ = generated.read_text()

    assert "WIDGETS = sqlalchemy.Table(" in text_
    assert 'sqlalchemy.Column("name", sqlalchemy.VARCHAR(length=80), nullable=False)' in text_


def test_the_framework_s_own_tables_are_built_not_reflected(generated: Path) -> None:
    """Their shape is a contract a CDC connector and other ports bind to, so there is one
    definition to be right about - and it is `ekbatan.schema`, not a reflection of one server."""
    text_ = generated.read_text()

    assert "EVENTS = outbox_table(metadata)" in text_
    assert "EVENT_NOTIFICATIONS = notifications_table(metadata)" in text_
    assert "SCHEDULED_TASKS = scheduled_tasks_table(metadata)" in text_
    assert 'Column("action_params"' not in text_, "reflected, when it should have been built"


def test_the_migration_bookkeeping_tables_are_left_out(generated: Path) -> None:
    assert "ekbatan_schema_history" not in generated.read_text()


def test_a_unique_key_survives(generated: Path, dialect: str) -> None:
    """Not decoration: the saga example relies on one for its idempotency. MySQL has no unique
    constraint apart from its unique index, so there it is an index."""
    text_ = generated.read_text()
    if _family(dialect) == "mysql":
        assert 'sqlalchemy.Index("uq_widgets_name", "name", unique=True)' in text_
    else:
        assert 'sqlalchemy.UniqueConstraint("name", name="uq_widgets_name")' in text_ or (
            'sqlalchemy.UniqueConstraint("name")' in text_
        )


def test_nullable_and_the_ekbatan_types_survive(generated: Path, dialect: str) -> None:
    text_ = generated.read_text()

    if dialect != "sqlite":  # a CHAR(32) on SQLite is only a uuid if you force it
        assert 'sqlalchemy.Column("id", uuid_column(), primary_key=True)' in text_
    assert 'sqlalchemy.Column("tags", json_column())' in text_, (
        "nullable columns carry no nullable= flag"
    )
    assert 'sqlalchemy.Column("created_date", timestamp_column(), nullable=False)' in text_


def test_what_it_writes_imports_and_gives_real_tables(generated: Path) -> None:
    module = load(generated)

    assert isinstance(module.WIDGETS, Table)
    assert {c.name for c in module.WIDGETS.columns} >= {"id", "name", "tags"}
    assert isinstance(module.EVENTS, Table)


def test_the_forced_types_survive_a_move_to_mysql(generated: Path, dialect: str) -> None:
    """The whole point, and the thing reflection alone gets wrong. A uuid used against MySQL
    as SQLAlchemy's `Uuid` is `CHAR(32)` - hex with the hyphens stripped, which no Java service
    can join against - and a timestamp is `TIMESTAMP`, with a 2038 ceiling. The framework's
    helpers carry the right type for every database."""
    if dialect == "sqlite":
        pytest.skip("SQLite's uuid is a CHAR(32) unless forced; see the forced-type tests")
    module = load(generated)
    for target, uuid_type, stamp in (
        (postgresql.dialect(), "UUID", "TIMESTAMP WITHOUT TIME ZONE"),
        (mysql.dialect(), "CHAR(36)", "DATETIME(6)"),
    ):
        ddl = " ".join(str(CreateTable(module.WIDGETS).compile(dialect=target)).split())
        assert f"id {uuid_type}" in ddl, ddl
        assert f"created_date {stamp}" in ddl, ddl


def test_a_char_36_id_is_text_until_the_project_says_it_holds_a_uuid(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """No guess from the name: without the project's rule a CHAR(36) `id` or `*_id` is text,
    as in a Java build that adds no converter. With it, it is a uuid - whatever its character
    set, since the rule matches the type in every spelling."""
    if _family(dialect) != "mysql":
        pytest.skip("a uuid has its own type here, or is a CHAR(32) - see the forced-type tests")
    source = migrations(
        tmp_path / "migrations",
        "CREATE TABLE accounts (id CHAR(36) NOT NULL PRIMARY KEY, "
        "owner_id CHAR(36) CHARACTER SET ascii NOT NULL, note CHAR(36));\n",
    )

    plain = generate(source, tmp_path / "a" / "schema.py", url=fresh(backend))
    forced = generate(
        source,
        tmp_path / "b" / "schema.py",
        url=fresh(backend),
        config=SchemagenConfig(forced_types=(UUID_IDS,)),
    )

    assert 'sqlalchemy.Column("id", sqlalchemy.CHAR(length=36), primary_key=True)' in plain
    assert 'sqlalchemy.Column("owner_id", uuid_column()' not in plain
    assert 'sqlalchemy.Column("id", uuid_column(), primary_key=True)' in forced
    assert 'sqlalchemy.Column("owner_id", uuid_column(), nullable=False)' in forced
    assert 'sqlalchemy.Column("note", uuid_column()' not in forced, "not an id-like name"


def test_one_table_left_out_of_the_uuid_rule_takes_a_rule_of_its_own(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """The schemagen page's example, as written there: every id is a uuid except in one table,
    whose CHAR(36) columns get another type."""
    if _family(dialect) != "mysql":
        pytest.skip("a uuid has its own type here, or is a CHAR(32)")
    source = migrations(
        tmp_path / "migrations",
        "CREATE TABLE wallets (id CHAR(36) NOT NULL PRIMARY KEY, owner_id CHAR(36) NOT NULL, "
        "note CHAR(36));\n"
        "CREATE TABLE legacy_accounts (id CHAR(36) NOT NULL PRIMARY KEY, "
        "owner_id CHAR(36) NOT NULL);\n",
    )
    rules = (
        ForcedType(
            name="uuid_column",
            include_types=r"CHAR\(36\)",
            include_expression=r".*\.id|.*_id",
            exclude_expression=r"legacy_accounts\..*",
        ),
        ForcedType(
            name="String(36)",
            include_types=r"CHAR\(36\)",
            include_expression=r"legacy_accounts\..*",
        ),
    )

    text_ = generate(
        source,
        tmp_path / "app" / "schema.py",
        url=fresh(backend),
        config=SchemagenConfig(forced_types=rules),
    )
    legacy, wallets = text_.split("LEGACY_ACCOUNTS = sqlalchemy.Table(")[1].split(
        "WALLETS = sqlalchemy.Table("
    )

    assert 'sqlalchemy.Column("id", sqlalchemy.String(36), primary_key=True)' in legacy
    assert 'sqlalchemy.Column("owner_id", sqlalchemy.String(36), nullable=False)' in legacy
    assert 'sqlalchemy.Column("id", uuid_column(), primary_key=True)' in wallets
    assert 'sqlalchemy.Column("owner_id", uuid_column(), nullable=False)' in wallets
    assert 'sqlalchemy.Column("note", sqlalchemy.CHAR(length=36))' in wallets


def test_a_naive_reflection_would_have_got_that_wrong() -> None:
    """Evidence rather than assertion: the same column, reflected and left alone, is the bug."""
    from sqlalchemy import Column, DateTime, Uuid

    naive = Table("n", MetaData(), Column("id", Uuid), Column("at", DateTime))
    ddl = " ".join(str(CreateTable(naive).compile(dialect=mysql.dialect())).split())

    assert "id CHAR(32)" in ddl, "the join-breaking form"
    assert "at DATETIME," in ddl or ddl.endswith("at DATETIME )"), "the second-precision form"


# -- keys, indexes, defaults, checks, views: create_all builds what the migrations built --------

SHAPE = {
    "postgres": """
CREATE TABLE parents (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(10) NOT NULL DEFAULT 'NEW',
    qty INTEGER NOT NULL DEFAULT 0,
    doubled INTEGER GENERATED ALWAYS AS (qty * 2) STORED,
    seq_no BIGINT GENERATED ALWAYS AS IDENTITY,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    note TEXT,
    CONSTRAINT ck_parents_qty CHECK (qty >= 0),
    CONSTRAINT uq_parents_code UNIQUE (code)
);
COMMENT ON TABLE parents IS 'the parents';
COMMENT ON COLUMN parents.note IS 'free text';
CREATE INDEX ix_parents_qty ON parents (qty);
CREATE INDEX ix_parents_active ON parents (qty) WHERE active = FALSE;
CREATE INDEX ix_parents_lower ON parents (lower(note));
CREATE UNIQUE INDEX ux_parents_note ON parents (note);
CREATE TABLE children (
    parent_id BIGINT NOT NULL REFERENCES parents (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    label VARCHAR(20),
    CONSTRAINT pk_children PRIMARY KEY (position, parent_id)
);
CREATE VIEW parent_totals AS SELECT id, qty FROM parents;
""",
    "mysql": """
CREATE TABLE parents (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(10) NOT NULL DEFAULT 'NEW',
    qty INT NOT NULL DEFAULT 0,
    doubled INT GENERATED ALWAYS AS (qty * 2) STORED,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    touched DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    note VARCHAR(200) COMMENT 'free text',
    CONSTRAINT ck_parents_qty CHECK (qty >= 0),
    CONSTRAINT uq_parents_code UNIQUE (code),
    INDEX ix_parents_qty (qty),
    UNIQUE INDEX ux_parents_note (note)
) COMMENT 'the parents';
CREATE TABLE children (
    parent_id BIGINT NOT NULL,
    position INT NOT NULL,
    label VARCHAR(20),
    PRIMARY KEY (position, parent_id),
    CONSTRAINT fk_children_parent FOREIGN KEY (parent_id) REFERENCES parents (id) ON DELETE CASCADE
);
CREATE VIEW parent_totals AS SELECT id, qty FROM parents;
""",
    "sqlite": """
CREATE TABLE parents (
    id INTEGER NOT NULL PRIMARY KEY,
    code VARCHAR(10) NOT NULL DEFAULT 'NEW',
    qty INTEGER NOT NULL DEFAULT 0,
    active BOOLEAN NOT NULL DEFAULT 1,
    note TEXT,
    CONSTRAINT ck_parents_qty CHECK (qty >= 0),
    CONSTRAINT uq_parents_code UNIQUE (code)
);
CREATE INDEX ix_parents_qty ON parents (qty);
CREATE UNIQUE INDEX ux_parents_note ON parents (note);
CREATE TABLE children (
    parent_id INTEGER NOT NULL REFERENCES parents (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    label VARCHAR(20),
    CONSTRAINT pk_children PRIMARY KEY (position, parent_id)
);
CREATE VIEW parent_totals AS SELECT id, qty FROM parents;
""",
}
#: SQLite's foreign keys spelled every way its reflection reads differently: a column's own
#: REFERENCES with actions, and a table-level one with DEFERRABLE on a line of its own.
SHAPE["sqlite"] = SHAPE["sqlite"].replace(
    "    label VARCHAR(20),\n    CONSTRAINT pk_children",
    "    label VARCHAR(20),\n    code VARCHAR(10),\n"
    "    FOREIGN KEY (code) REFERENCES parents (code) ON UPDATE CASCADE\n"
    "        DEFERRABLE INITIALLY DEFERRED,\n    CONSTRAINT pk_children",
)


def _shape(url: str) -> dict[str, Any]:
    """What a database says about the two tables, in a form two databases can be compared by."""
    db = create_engine(url)
    try:
        found = inspect(db)
        shape: dict[str, Any] = {}
        for table in ("parents", "children"):
            shape[f"{table}.columns"] = [
                (
                    c["name"],
                    c["nullable"],
                    " ".join(str(c["default"]).lower().split()) if c.get("default") else None,
                    c.get("comment"),
                    bool(c.get("computed")),
                    bool(c.get("identity")),
                )
                for c in found.get_columns(table)
            ]
            shape[f"{table}.pk"] = found.get_pk_constraint(table)["constrained_columns"]
            shape[f"{table}.indexes"] = sorted(
                (
                    i["name"],
                    tuple(str(x) for x in (i.get("column_names") or [])),
                    bool(i["unique"]),
                    str(i.get("dialect_options", {}).get("postgresql_where")),
                )
                for i in found.get_indexes(table)
            )
            shape[f"{table}.uniques"] = sorted(
                (u["name"], tuple(u["column_names"])) for u in found.get_unique_constraints(table)
            )
            shape[f"{table}.fks"] = sorted(
                (
                    tuple(f["constrained_columns"]),
                    f["referred_table"],
                    tuple(f["referred_columns"]),
                    # SQLite's comes from PRAGMA foreign_key_list below: SQLAlchemy reads
                    # no action of a column's own REFERENCES.
                    f.get("options", {}).get("ondelete") if db.dialect.name != "sqlite" else "",
                )
                for f in found.get_foreign_keys(table)
            )
            shape[f"{table}.checks"] = sorted(
                (str(c.get("name")), " ".join(str(c["sqltext"]).split()))
                for c in found.get_check_constraints(table)
            )
            try:
                shape[f"{table}.comment"] = found.get_table_comment(table).get("text")
            except NotImplementedError:
                shape[f"{table}.comment"] = None
            if db.dialect.name == "sqlite":
                # SQLAlchemy's own reading of a SQLite foreign key misses a column's
                # REFERENCES actions, so both sides would agree on nothing: SQLite's own word.
                from ekbatan.schemagen._reflect import sqlite_table_parts

                with db.connect() as connection:
                    shape[f"{table}.pragma_fks"] = [
                        tuple(row)[2:]
                        for row in connection.exec_driver_sql(f"PRAGMA foreign_key_list({table})")
                    ]
                    sql = connection.exec_driver_sql(
                        "SELECT sql FROM sqlite_master WHERE name = ?", (table,)
                    ).scalar_one()
                shape[f"{table}.deferrable"] = [
                    (k.columns, k.deferrable, k.initially_deferred)
                    for k in sqlite_table_parts(str(sql)).foreign_keys
                ]
        shape["views"] = sorted(found.get_view_names())
        shape["tables"] = sorted(found.get_table_names())
        return shape
    finally:
        db.dispose()


@pytest.fixture
def shaped(tmp_path: Path, backend: Backend, dialect: str) -> Iterator[tuple[Any, str, str]]:
    source = migrations(tmp_path / "migrations", SHAPE[_family(dialect)])
    migrated = fresh(backend, "shape_a")
    out = tmp_path / "app" / "schema.py"
    generate(source, out, url=migrated)
    module = load(out)
    rebuilt = fresh(backend, "shape_b")
    db = create_engine(rebuilt)
    try:
        module.metadata.create_all(db)
    finally:
        db.dispose()
    yield module, migrated, rebuilt


def test_create_all_builds_the_keys_indexes_defaults_and_checks_the_migrations_built(
    shaped: tuple[Any, str, str],
) -> None:
    """What jOOQ generates as `Keys`, `Indexes` and `defaultValue(...)`. Ekbatan uses none of
    it at run time; it is there so that `create_all` in a test builds the database the
    migrations build, and the module does not misdescribe it."""
    _, migrated, rebuilt = shaped
    before, after = _shape(migrated), _shape(rebuilt)
    for key in before:
        if key in ("views", "tables"):
            continue
        assert after[key] == before[key], key


def test_the_shape_module_imports_exactly_what_it_uses(shaped: tuple[Any, str, str]) -> None:
    """Keys, indexes, defaults, checks, identity and views: each brings its own imports."""
    module, _, _ = shaped
    assert unused_and_undefined(Path(module.__file__).read_text()) == (set(), set())


def test_views_are_generated_apart_and_not_created_as_tables(
    shaped: tuple[Any, str, str],
) -> None:
    module, migrated, rebuilt = shaped

    assert "PARENT_TOTALS" in module.__all__ and "view_metadata" in module.__all__
    assert module.PARENT_TOTALS.metadata is module.view_metadata
    assert "parent_totals" not in module.metadata.tables
    assert _shape(rebuilt)["views"] == [], "create_all builds tables, not views"
    assert "parent_totals" not in _shape(rebuilt)["tables"]
    db = create_engine(migrated)
    try:
        with db.begin() as connection:
            connection.execute(insert(module.PARENTS).values(id=5, code="A", qty=3, active=True))
            assert connection.execute(select(module.PARENT_TOTALS.c.qty)).scalar_one() == 3
    finally:
        db.dispose()


def test_views_can_be_left_out(tmp_path: Path, backend: Backend, dialect: str) -> None:
    source = migrations(tmp_path / "migrations", SHAPE[_family(dialect)])
    out = tmp_path / "app" / "schema.py"

    generate(source, out, url=fresh(backend), config=SchemagenConfig(views=False))

    assert "PARENT_TOTALS" not in out.read_text() and "view_metadata" not in out.read_text()


# -- other schemas ------------------------------------------------------------------------------


def _other_schema(dialect: str, name: str) -> str:
    create = "SCHEMA" if dialect == "postgres" else "DATABASE"
    return (
        f"CREATE {create} {name};\n"
        f"CREATE TABLE {name}.entries (id INTEGER PRIMARY KEY, note TEXT NOT NULL);\n"
    )


def test_another_schema_is_noted_and_generated_when_named(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """Round 2 found a second schema skipped without a word. Now it is said, and listing it in
    `schemas` generates it qualified - the connection's own schema stays unqualified, as jOOQ's
    `outputSchemaToDefault` keeps it."""
    if dialect == "sqlite":
        pytest.skip("SQLite has no schemas")
    other = f"audit_{uuid.uuid4().hex[:8]}"
    sql = APP[_family(dialect)] + _other_schema(dialect, other)
    first = tmp_path / "one" / "schema.py"
    with notes() as said:
        generate(migrations(tmp_path / "m1", sql), first, url=fresh(backend))
    assert any(f"schema {other} has 1 table(s), not generated" in n for n in said), said
    assert "entries" not in first.read_text()

    second = tmp_path / "two" / "schema.py"
    generate(
        migrations(tmp_path / "m2", sql.replace(other, other + "b")),
        second,
        url=fresh(backend),
        config=SchemagenConfig(schemas=(other + "b",)),
    )
    module = load(second)
    entries = getattr(module, f"{other.upper()}B_ENTRIES")
    assert entries.schema == other + "b"
    assert module.WIDGETS.schema is None


# -- forced types, end to end ------------------------------------------------------------------


@pytest.fixture
def user_types(tmp_path: Path) -> Iterator[str]:
    """Ready-made SQLAlchemy types, as a library brings them (for `sqlalchemy-type`), and one
    plain converter (for `converter`), in a package of their own."""
    package = f"my_types_{uuid.uuid4().hex[:8]}"
    root = tmp_path / "user"
    (root / package).mkdir(parents=True)
    (root / package / "__init__.py").write_text("")
    (root / package / "types.py").write_text(
        '''"""Column types an application might bring."""
import uuid
from decimal import Decimal

from sqlalchemy import BINARY, Numeric
from sqlalchemy.types import TypeDecorator, UserDefinedType


class Money(TypeDecorator):
    """A Decimal, always to two places."""

    impl = Numeric
    cache_ok = True

    def __init__(self, scale: int = 2) -> None:
        super().__init__(precision=19, scale=scale)
        self.places = Decimal(1).scaleb(-scale)

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value).quantize(self.places)


class UuidBinary(TypeDecorator):
    """A uuid in 16 bytes - Java's mysql.UuidBinaryConverter."""

    impl = BINARY(16)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else value.bytes

    def process_result_value(self, value, dialect):
        return None if value is None else uuid.UUID(bytes=bytes(value))


class Spelled(UserDefinedType):
    """A type SQLAlchemy cannot read, named so create_all builds it: PostgreSQL's xml,
    MariaDB's INET6."""

    cache_ok = True

    def __init__(self, name: str) -> None:
        self.name = name

    def get_col_spec(self, **kw):
        return self.name


def xml():
    return Spelled("XML")


def inet6():
    return Spelled("INET6")


class MoneyConverter:
    """A plain converter: no SQLAlchemy in it."""

    def from_db(self, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))

    def to_db(self, value):
        return value
'''
    )
    sys.path.insert(0, str(root))
    yield f"{package}.types"
    sys.path.remove(str(root))
    for name in [m for m in sys.modules if m.startswith(package)]:
        sys.modules.pop(name)


LEDGER = {
    "postgres": "CREATE TABLE ledger (id UUID PRIMARY KEY, amount NUMERIC(19, 4) NOT NULL, "
    "rate NUMERIC(19, 4), doc XML, created_date TIMESTAMP NOT NULL);\n",
    "mysql": "CREATE TABLE ledger (id CHAR(36) PRIMARY KEY, amount DECIMAL(19, 4) NOT NULL, "
    "rate DECIMAL(19, 4), account_id BINARY(16), created_date DATETIME(6) NOT NULL);\n",
    "mariadb": "CREATE TABLE ledger (id CHAR(36) PRIMARY KEY, amount DECIMAL(19, 4) NOT NULL, "
    "rate DECIMAL(19, 4), account_id BINARY(16), address INET6, "
    "created_date DATETIME(6) NOT NULL);\n",
    "sqlite": "CREATE TABLE ledger (id CHAR(32) PRIMARY KEY, amount NUMERIC(19, 4) NOT NULL, "
    "rate NUMERIC(19, 4), owner_id CHAR(32), created_date DATETIME NOT NULL);\n",
}


def _ledger(
    tmp_path: Path, backend: Backend, dialect: str, *forced: ForcedType, **settings: Any
) -> tuple[Any, str, dict[tuple[str, str], str]]:
    source = migrations(tmp_path / "migrations", LEDGER[dialect])
    url = fresh(backend)
    out = tmp_path / "app" / "schema.py"
    text_ = generate(source, out, url=url, config=SchemagenConfig(forced_types=forced, **settings))
    return load(out), url, column_sources(text_)


def test_a_column_is_forced_to_a_sqlalchemy_type_by_its_name(
    tmp_path: Path, backend: Backend, dialect: str, user_types: str
) -> None:
    """Java's `includeExpression` with a `userType`: `.*\\.amount` becomes a ready-made
    SQLAlchemy type, by dotted path - and a value goes through it on the real database."""
    module, url, sources = _ledger(
        tmp_path,
        backend,
        dialect,
        ForcedType(sqlalchemy_type=f"{user_types}.Money(2)", include_expression=r".*\.amount"),
    )

    assert sources[("LEDGER", "amount")] == "Money(2)"
    assert sources[("LEDGER", "rate")] == normal(
        "sqlalchemy.DECIMAL(precision=19, scale=4)"
        if _family(dialect) == "mysql"
        else "sqlalchemy.NUMERIC(precision=19, scale=4)"
    ), "only the column the expression names"
    db = engine(url)
    try:
        with db.begin() as connection:
            connection.execute(
                insert(module.LEDGER).values(
                    id=_an_id(dialect), amount=Decimal("10.5"), created_date=_now()
                )
            )
            assert connection.execute(select(module.LEDGER.c.amount)).scalar_one() == Decimal(
                "10.50"
            )
    finally:
        db.dispose()


@pytest.mark.parametrize("target", ["sqlalchemy-type", "converter"])
def test_an_imported_type_leaves_the_stale_module_guard_working(
    tmp_path: Path, backend: Backend, dialect: str, user_types: str, target: str
) -> None:
    """Importing a type or a converter must not change where the module looks for its
    migrations - the import loop once wrote the imported module there instead, and the guard
    then found no directory and kept quiet."""
    forced = (
        ForcedType(sqlalchemy_type=f"{user_types}.Money(2)", include_expression=r".*\.amount")
        if target == "sqlalchemy-type"
        else ForcedType(converter=f"{user_types}.MoneyConverter", include_expression=r".*\.amount")
    )
    _ledger(tmp_path, backend, dialect, forced)
    written = (tmp_path / "app" / "schema.py").read_text()
    assert f"from {user_types} import " in written, "the module imports the user's own"
    assert (
        'check_fingerprint(__file__, MIGRATIONS_FINGERPRINT, "../migrations", SCHEMA_FORMAT)'
        in written
    )
    (tmp_path / "migrations" / "V0099__more.sql").write_text(
        "CREATE TABLE gadgets (id INTEGER PRIMARY KEY);"
    )

    with pytest.raises(ValidationError, match="Regenerate it"):
        load(tmp_path / "app" / "schema.py")


def _an_id(dialect: str) -> Any:
    return uuid.uuid4() if dialect != "sqlite" else uuid.uuid4().hex


def _now() -> Any:
    from datetime import datetime

    return datetime(2026, 1, 2, 3, 4, 5)


def test_a_column_is_forced_by_its_sql_type(
    tmp_path: Path, backend: Backend, dialect: str, user_types: str
) -> None:
    """Java's `includeTypes`: each database's own case.

    - PostgreSQL `XML`, MariaDB `INET6`: types SQLAlchemy has no class for, given a ready-made
      type of the application's (without one they are `sql_type(...)`, see the type matrix).
    - MySQL `BINARY(16)` named `*_id`: Java's `UuidBinaryConverter`, as a TypeDecorator.
    - SQLite `CHAR(32)` named `*_id`: a uuid, which SQLite cannot say on its own.
    """
    case = {
        "postgres": (
            "doc",
            ForcedType(sqlalchemy_type=f"{user_types}.xml", include_types="XML"),
            "xml()",
            "<a>1</a>",
        ),
        "mysql": (
            "account_id",
            ForcedType(
                sqlalchemy_type=f"{user_types}.UuidBinary",
                include_types=r"BINARY\(16\)",
                include_expression=".*_id",
            ),
            "UuidBinary()",
            uuid.uuid4(),
        ),
        "mariadb": (
            "address",
            ForcedType(sqlalchemy_type=f"{user_types}.inet6", include_types="INET6"),
            "inet6()",
            "::1",
        ),
        "sqlite": (
            "owner_id",
            ForcedType(
                name="uuid_column", include_types=r"CHAR\(32\)", include_expression=r".*\.id|.*_id"
            ),
            "uuid_column()",
            uuid.uuid4(),
        ),
    }[dialect]
    column, forced, source, value = case
    module, url, sources = _ledger(tmp_path, backend, dialect, forced)

    assert sources[("LEDGER", column)] == source
    db = engine(url)
    try:
        with db.begin() as connection:
            row_id = uuid.uuid4() if dialect == "sqlite" else _an_id(dialect)
            connection.execute(
                insert(module.LEDGER).values(
                    {"id": row_id, "amount": Decimal(1), "created_date": _now(), column: value}
                )
            )
            back = connection.execute(select(module.LEDGER.c[column])).scalar_one()
    finally:
        db.dispose()
    assert back == value
    rebuilt = fresh(backend, "forced_b")
    db = engine(rebuilt)
    try:
        module.metadata.create_all(db)  # a type it could not read before, now buildable
    finally:
        db.dispose()


def test_the_user_s_forced_type_wins_over_ekbatan_s_and_the_first_one_wins(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """As in jOOQ: forced types are tried in order, the user's before the built-in ones, and the
    first that matches a column is the one it gets."""
    _, _, sources = _ledger(
        tmp_path,
        backend,
        dialect,
        ForcedType(name="DateTime", include_expression="created_date"),
        ForcedType(name="Text", include_expression="created_date"),
    )

    assert sources[("LEDGER", "created_date")] == "sqlalchemy.DateTime()", (
        "not timestamp_column(), not Text()"
    )


def test_ekbatan_s_forced_types_can_be_turned_off(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """Then every column is the type table's: the database's own types, faithfully."""
    _, _, sources = _ledger(tmp_path, backend, dialect, default_forced_types=False)

    expected = {
        "postgres": ("sqlalchemy.UUID()", "sqlalchemy.TIMESTAMP()"),
        "mysql": ("sqlalchemy.CHAR(length=36)", "mysql.DATETIME(fsp=6)"),
        "mariadb": ("sqlalchemy.CHAR(length=36)", "mysql.DATETIME(fsp=6)"),
        "sqlite": ("sqlalchemy.CHAR(length=32)", "sqlalchemy.DATETIME()"),
    }[dialect]
    assert (sources[("LEDGER", "id")], sources[("LEDGER", "created_date")]) == tuple(
        normal(e) for e in expected
    )


def test_a_wrong_forced_type_fails_before_any_database_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo in a forced type is reported naming it - not after a container has started, and
    not as a generated module that does not import."""
    import ekbatan.schemagen as schemagen

    def no_container(image: str) -> Any:
        raise AssertionError("a container was started")

    monkeypatch.setattr(schemagen, "_start", no_container)
    source = migrations(tmp_path / "migrations", "CREATE TABLE t (id INTEGER PRIMARY KEY);")

    with pytest.raises(ValidationError, match="'Txet' is not a SQLAlchemy type"):
        generate(
            source,
            tmp_path / "schema.py",
            config=SchemagenConfig(forced_types=(ForcedType(name="Txet", include_types="X"),)),
        )
    with pytest.raises(ValidationError, match="cannot import nowhere_at_all"):
        generate(
            source,
            tmp_path / "schema.py",
            config=SchemagenConfig(
                forced_types=(
                    ForcedType(sqlalchemy_type="nowhere_at_all.Money", include_types="X"),
                )
            ),
        )


# -- includes and excludes ---------------------------------------------------------------------


def test_includes_and_excludes_choose_the_tables(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """jOOQ's `includes` and `excludes`, whole-name regular expressions - the framework's tables
    among them, as twelve of Java's Maven builds exclude `scheduled_tasks`."""
    family = _family(dialect)
    sql = APP[family] + LEDGER[dialect].replace("ledger", "ledger_archive")
    source = migrations(tmp_path / "migrations", sql, _framework_ddl(dialect))
    out = tmp_path / "app" / "schema.py"

    generate(
        source,
        out,
        url=fresh(backend),
        config=SchemagenConfig(
            includes="widgets|ledger_.*|events|scheduled_tasks", excludes="scheduled_tasks"
        ),
    )

    module = load(out)
    assert module.__all__ == [
        "EVENTS",
        "LEDGER_ARCHIVE",
        "WIDGETS",
        "EventsRecord",
        "LedgerArchiveRecord",
        "WidgetsRecord",
        "metadata",
    ]


# -- the configuration file and the command line -------------------------------------------------


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), env.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-m", "ekbatan.schemagen", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


def _project(tmp_path: Path, dialect: str, table: str) -> Path:
    project = tmp_path / "project"
    migrations(project / "src" / "app" / "migrations", APP[_family(dialect)] + LEDGER[dialect])
    (project / "pyproject.toml").write_text(
        f'[project]\nname = "app"\n\n[tool.ekbatan.schemagen]\n{table}'
    )
    return project


def test_the_command_reads_tool_ekbatan_schemagen_from_pyproject(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """The settings where a Python project keeps build-time settings; the command needs only
    the database, and a Makefile line becomes `python -m ekbatan.schemagen`."""
    project = _project(
        tmp_path,
        dialect,
        'migrations = "src/app/migrations"\nout = "src/app/schema.py"\nexcludes = "ledger"\n\n'
        "[[tool.ekbatan.schemagen.forced-types]]\n"
        'name = "Text"\ninclude-expression = "widgets.state"\n',
    )

    done = _run(project, "--url", fresh(backend))

    assert done.returncode == 0, done.stderr
    written = (project / "src" / "app" / "schema.py").read_text()
    assert 'sqlalchemy.Column("state", sqlalchemy.Text(), nullable=False)' in written, (
        "the file's forced type"
    )
    assert "LEDGER" not in written, "the file's excludes"
    assert (
        'check_fingerprint(__file__, MIGRATIONS_FINGERPRINT, "migrations", SCHEMA_FORMAT)'
        in written
    )


def test_a_flag_wins_over_the_file(tmp_path: Path, backend: Backend, dialect: str) -> None:
    project = _project(
        tmp_path,
        dialect,
        'migrations = "src/app/migrations"\nout = "src/app/schema.py"\nexcludes = "ledger"\n',
    )

    done = _run(
        project,
        "--url",
        fresh(backend),
        "--exclude",
        "widgets",
        "--out",
        "other.py",
        "--no-default-forced-types",
    )

    assert done.returncode == 0, done.stderr
    written = (project / "other.py").read_text()
    assert "LEDGER = sqlalchemy.Table(" in written and "WIDGETS" not in written
    assert "timestamp_column" not in written


def test_another_config_file_can_be_named(tmp_path: Path, backend: Backend, dialect: str) -> None:
    project = _project(tmp_path, dialect, 'migrations = "nowhere"\n')
    (project / "schemagen.toml").write_text(
        '[tool.ekbatan.schemagen]\nmigrations = "src/app/migrations"\nout = "gen/schema.py"\n'
    )

    done = _run(project, "--config", "schemagen.toml", "--url", fresh(backend))

    assert done.returncode == 0, done.stderr
    assert (project / "gen" / "schema.py").exists()


def test_a_mistake_in_the_file_is_an_error_naming_it(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[tool.ekbatan.schemagen]\nmigratons = "m"\n')

    done = _run(project)

    assert done.returncode == 1
    assert "unknown key(s) ['migratons']" in done.stderr


def test_without_a_file_the_flags_are_enough(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """The command as every example ran it before the configuration file existed."""
    source = migrations(tmp_path / "migrations", APP[_family(dialect)])
    out = tmp_path / "cli" / "schema.py"

    done = _run(tmp_path, "--migrations", str(source), "--out", str(out), "--url", fresh(backend))

    assert done.returncode == 0, done.stderr
    assert "WIDGETS" in out.read_text()


def test_nothing_set_says_what_to_set(tmp_path: Path) -> None:
    done = _run(tmp_path)

    assert done.returncode == 1
    assert "set `migrations` in [tool.ekbatan.schemagen]" in done.stderr


# -- secrets ----------------------------------------------------------------------------------

SECRET = "S3cret-schemagen-Pw-7731"


def _login_with_secret(backend: Backend, dialect: str) -> str:
    """A url for a fresh database reached by a user whose password is SECRET."""
    from sqlalchemy.engine import make_url

    admin = fresh(backend, "secret")
    name = make_url(admin).database
    user = f"sg_{uuid.uuid4().hex[:8]}"
    db = create_engine(admin, isolation_level="AUTOCOMMIT")
    try:
        with db.connect() as connection:
            if dialect == "postgres":
                connection.execute(text(f"CREATE ROLE {user} LOGIN PASSWORD '{SECRET}'"))
                connection.execute(text(f"ALTER DATABASE {name} OWNER TO {user}"))
                connection.execute(text(f"GRANT ALL ON SCHEMA public TO {user}"))
            else:
                connection.execute(text(f"CREATE USER '{user}'@'%' IDENTIFIED BY '{SECRET}'"))
                connection.execute(text(f"GRANT ALL ON {name}.* TO '{user}'@'%'"))
    finally:
        db.dispose()
    return make_url(admin).set(username=user, password=SECRET).render_as_string(hide_password=False)


def test_the_password_is_never_printed_or_written(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """Not on success, not in a note, not when the migration fails, not in the module."""
    if dialect == "sqlite":
        pytest.skip("SQLite has no login")
    url = _login_with_secret(backend, dialect)
    good = migrations(tmp_path / "good", APP[_family(dialect)] + "CREATE TABLE t (c TIMESTAMP);\n")
    out = tmp_path / "app" / "schema.py"

    ran = _run(tmp_path, "--migrations", str(good), "--out", str(out), "--url", url)
    assert ran.returncode == 0, ran.stderr.replace(SECRET, "***")
    assert SECRET not in ran.stdout + ran.stderr
    assert SECRET not in out.read_text()

    bad = migrations(tmp_path / "bad", "CREATE TABLE broken (;")
    failed = _run(
        tmp_path,
        "--migrations",
        str(bad),
        "--out",
        str(tmp_path / "x.py"),
        "--url",
        _login_with_secret(backend, dialect),
    )
    assert failed.returncode != 0
    assert SECRET not in failed.stdout + failed.stderr


# -- staleness ---------------------------------------------------------------------------------


def test_importing_a_stale_module_fails_loudly(generated: Path, app_migrations: Path) -> None:
    """The guarantee Gradle gets for free. Nothing in Python runs before `python -m app`, so a
    stale schema would otherwise just disagree with the database in front of it."""
    load(generated)  # current: imports
    (app_migrations / "V0003__more.sql").write_text(
        "CREATE TABLE gadgets (id INTEGER PRIMARY KEY);"
    )

    with pytest.raises(ValidationError) as raised:
        load(generated)
    assert "Regenerate it" in str(raised.value)
    assert "schemagen" in str(raised.value)


def test_the_fingerprint_changes_when_a_migration_changes(tmp_path: Path) -> None:
    root = migrations(tmp_path / "m", APP["postgres"])
    before = fingerprint(root)
    (root / "V0003__more.sql").write_text("CREATE TABLE gadgets (id UUID PRIMARY KEY);")

    assert fingerprint(root) != before


@pytest.mark.parametrize(
    "name",
    ["2026/V0003__more.sql", "R__totals.sql", "post-apply.sql", "V0004__caps.SQL"],
    ids=["in a subdirectory", "repeatable", "post-apply hook", "in capitals"],
)
def test_the_fingerprint_covers_every_file_the_migrator_reads(tmp_path: Path, name: str) -> None:
    """The migrator reads subdirectories, repeatable files, hooks and `.SQL` - a change to any of
    them changes the schema, so it has to make the module stale."""
    root = migrations(tmp_path / "m", APP["postgres"])
    before = fingerprint(root)
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("CREATE TABLE gadgets (id UUID PRIMARY KEY);")

    assert fingerprint(root) != before


def test_the_fingerprint_of_a_flat_directory_is_what_it_always_was(tmp_path: Path) -> None:
    """Every module generated before subdirectories were read stays current."""
    import hashlib

    from ekbatan.migrations import checksum

    root = migrations(tmp_path / "m", APP["postgres"], "CREATE TABLE x (id INTEGER);")
    digest = hashlib.sha256()
    for path in sorted(root.glob("*.sql")):
        digest.update(path.name.encode())
        digest.update(checksum(path).encode())

    assert fingerprint(root) == digest.hexdigest()[:16]


def test_the_fingerprint_ignores_what_is_not_a_migration(tmp_path: Path) -> None:
    """An `__init__.py`, a `README`, a cache: none is schema."""
    root = migrations(tmp_path / "m", APP["postgres"])
    before = fingerprint(root)
    (root / "__init__.py").write_text("")
    (root / "README.md").write_text("notes")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.pyc").write_bytes(b"junk")

    assert fingerprint(root) == before


def test_the_fingerprint_ignores_line_endings(tmp_path: Path) -> None:
    root = migrations(tmp_path / "m", APP["postgres"])
    before = fingerprint(root)
    path = root / "V0001__step.sql"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    assert fingerprint(root) == before


def test_the_check_is_quiet_when_the_migrations_are_not_shipped(tmp_path: Path) -> None:
    """A wheel carries the generated module and not the migration files."""
    check_fingerprint(str(tmp_path / "schema.py"), "whatever", "migrations", SCHEMA_FORMAT)


def test_the_check_is_skipped_rather_than_recording_a_nonsense_path(tmp_path: Path) -> None:
    check_fingerprint(str(tmp_path / "schema.py"), "whatever", None, SCHEMA_FORMAT)


def test_a_missing_migration_directory_says_so(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="no migration directory"):
        generate(tmp_path / "nope", tmp_path / "schema.py")


def test_migrations_that_create_no_application_tables_are_refused(
    tmp_path: Path, backend: Backend
) -> None:
    """Silence would be worse: an empty generated module imports fine and every query then
    fails somewhere else."""
    root = tmp_path / "empty"
    root.mkdir()

    with pytest.raises(ValidationError, match="no application tables"):
        generate(root, tmp_path / "schema.py", url=fresh(backend))


def test_render_needs_no_database() -> None:
    from sqlalchemy import BigInteger, Column

    table = Table("things", MetaData(), Column("n", BigInteger, nullable=False))
    text_ = render(
        [table], framework=["events"], migrations_fingerprint="abc123", module="migrations"
    )

    assert "THINGS = sqlalchemy.Table(" in text_
    assert "EVENTS = outbox_table(metadata)" in text_
    assert 'MIGRATIONS_FINGERPRINT = "abc123"' in text_
