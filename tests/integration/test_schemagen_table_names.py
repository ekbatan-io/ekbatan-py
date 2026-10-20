"""A table named like anything the generated module writes, on each database, through reflection.

The pure-render twin, `tests/unit/test_schemagen_table_names.py`, says why: a table's constant is
its name in capitals, and a module that imported `TEXT` from `sqlalchemy` lost it to a table named
`text`. Here the same, for real, on every database:

1. one migration builds a table with a column of every type the type table's cases cover
   (`schemagen_type_cases`), a table with every feature - an identity or auto-increment, a
   computed column, defaults, unique, check and foreign keys, indexes, comments - and a view;
2. ``generate`` writes the module from it - the baseline;
3. a second database gets the same migration and then a table named after every name schemagen
   can write (`emittable_names`, read off its own source) and every name the baseline module
   binds itself (`metadata`, `MIGRATIONS_FINGERPRINT`, `check_fingerprint`, ...);
4. that module must import, export each new table under its own constant, build every other
   table exactly as the baseline does, keep each record's types and its own names - and
   ``create_all`` from it must build a third database.

    uv run pytest tests/integration/test_schemagen_table_names.py --dialects=all
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import Table, inspect

from backends import Backend
from ekbatan.schemagen import generate
from ekbatan.schemagen._render import constant
from schemagen_support import (
    column_sources,
    describe,
    emittable_names,
    engine,
    fresh,
    hints,
    load,
    migrations,
    normal,
    own_names,
)
from schemagen_type_cases import CASES_BY_DIALECT as TYPE_CASES
from schemagen_type_cases import PREAMBLE

#: The table with every feature the writer writes a SQLAlchemy name for, per database.
FEATURES = {
    "postgres": """
CREATE TABLE owners (id BIGINT PRIMARY KEY);
CREATE SEQUENCE features_counter_seq;
CREATE TABLE features (
    id BIGINT GENERATED ALWAYS AS IDENTITY (START WITH 5 INCREMENT BY 2) PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    code VARCHAR(10) NOT NULL,
    qty INTEGER NOT NULL DEFAULT 1,
    doubled INTEGER GENERATED ALWAYS AS (qty * 2) STORED,
    counter INTEGER DEFAULT nextval('features_counter_seq'),
    note TEXT,
    CONSTRAINT features_owner FOREIGN KEY (owner_id) REFERENCES owners (id) ON DELETE CASCADE,
    CONSTRAINT features_code UNIQUE (code),
    CONSTRAINT features_qty CHECK (qty > 0)
);
CREATE TABLE pairs (
    a INTEGER NOT NULL, b INTEGER NOT NULL, CONSTRAINT pairs_key PRIMARY KEY (a, b)
);
COMMENT ON TABLE features IS 'every feature';
COMMENT ON COLUMN features.code IS 'a code';
CREATE INDEX features_qty_ix ON features (qty);
CREATE INDEX features_lower_note ON features (lower(note));
CREATE UNIQUE INDEX features_code_partial ON features (code) WHERE note IS NULL;
CREATE VIEW feature_codes AS SELECT id, code FROM features;
""",
    "mysql": """
CREATE TABLE owners (id BIGINT PRIMARY KEY);
CREATE TABLE features (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    code VARCHAR(10) NOT NULL COMMENT 'a code',
    qty INT NOT NULL DEFAULT 1,
    doubled INT GENERATED ALWAYS AS (qty * 2) STORED,
    note TEXT,
    CONSTRAINT features_owner FOREIGN KEY (owner_id) REFERENCES owners (id) ON DELETE CASCADE,
    CONSTRAINT features_code UNIQUE (code),
    CONSTRAINT features_qty CHECK (qty > 0),
    KEY features_qty_ix (qty)
) COMMENT 'every feature';
CREATE TABLE pairs (a INT NOT NULL, b INT NOT NULL, PRIMARY KEY (a, b));
CREATE VIEW feature_codes AS SELECT id, code FROM features;
""",
    "sqlite": """
CREATE TABLE owners (id INTEGER PRIMARY KEY);
CREATE TABLE features (
    id INTEGER PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    code VARCHAR(10) NOT NULL,
    qty INTEGER NOT NULL DEFAULT 1,
    doubled INTEGER GENERATED ALWAYS AS (qty * 2) STORED,
    note TEXT,
    CONSTRAINT features_owner FOREIGN KEY (owner_id) REFERENCES owners (id) ON DELETE CASCADE,
    CONSTRAINT features_code UNIQUE (code),
    CONSTRAINT features_qty CHECK (qty > 0)
);
CREATE TABLE pairs (a INTEGER NOT NULL, b INTEGER NOT NULL, PRIMARY KEY (a, b));
CREATE INDEX features_qty_ix ON features (qty);
CREATE VIEW feature_codes AS SELECT id, code FROM features;
""",
}
FEATURES["mariadb"] = FEATURES["mysql"]


#: Names a database will not give a table: on PostgreSQL a table is also a type, and the citext
#: extension's type already has the name in the same schema. The render test covers it.
REFUSED = {"postgres": {"citext"}}


def _quoted(dialect: str, name: str) -> str:
    return f"`{name}`" if dialect in ("mysql", "mariadb") else f'"{name}"'


def _types_table(dialect: str) -> str:
    columns = ",\n".join(f"    {c.name} {c.sql}" for c in TYPE_CASES[dialect])
    return f"CREATE TABLE t_types (\n    pk INTEGER NOT NULL PRIMARY KEY,\n{columns}\n);\n"


@dataclass
class Pair:
    names: list[str]
    baseline_text: str
    baseline: ModuleType
    clashed_text: str
    clashed: ModuleType
    rebuilt: str


#: By backend, as `test_schemagen_types` caches its matrix.
_BUILT: dict[int, tuple[Backend, Pair]] = {}


def _build(backend: Backend, dialect: str, root: Path) -> Pair:
    first = PREAMBLE.get(dialect, "") + _types_table(dialect) + FEATURES[dialect]

    plain = migrations(root / "plain", first)
    baseline_text = generate(plain, root / "plain_app" / "schema.py", url=fresh(backend, "tn_a"))
    baseline = load(root / "plain_app" / "schema.py")

    own = own_names(baseline_text, dict(vars(baseline)))
    names = sorted({n.lower() for n in [*emittable_names(), *own]} - REFUSED.get(dialect, set()))
    second = "".join(
        f"CREATE TABLE {_quoted(dialect, n)} (id INTEGER NOT NULL PRIMARY KEY);\n" for n in names
    )
    clashing = migrations(root / "clashing", first, second)
    clashed_text = generate(
        clashing, root / "clashing_app" / "schema.py", url=fresh(backend, "tn_b")
    )
    clashed = load(root / "clashing_app" / "schema.py")

    rebuilt = fresh(backend, "tn_c")
    built = engine(rebuilt)
    try:
        if dialect == "postgres":
            with built.begin() as connection:
                connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS citext")
        clashed.metadata.create_all(built)
    finally:
        built.dispose()
    return Pair(names, baseline_text, baseline, clashed_text, clashed, rebuilt)


@pytest.fixture
def pair(backend: Backend, dialect: str, tmp_path_factory: pytest.TempPathFactory) -> Pair:
    cached = _BUILT.get(id(backend))
    if cached is None or cached[0] is not backend:
        built = _build(backend, dialect, tmp_path_factory.mktemp(f"names-{dialect}"))
        cached = _BUILT[id(backend)] = (backend, built)
    return cached[1]


def test_every_table_named_like_a_name_the_module_writes_is_exported_under_it(pair: Pair) -> None:
    assert len(pair.names) > 100
    for name in pair.names:
        exported = constant(name)
        table = getattr(pair.clashed, exported)
        assert isinstance(table, Table) and table.name == name, name
        assert exported in pair.clashed.__all__, name


def test_every_type_is_written_as_without_those_tables(pair: Pair, dialect: str) -> None:
    sources = column_sources(pair.clashed_text)
    for case in TYPE_CASES[dialect]:
        assert sources[("T_TYPES", case.name)] == normal(case.source), case.id
        column = pair.clashed.T_TYPES.c[case.name]
        assert type(column.type) is type(pair.baseline.T_TYPES.c[case.name].type), case.id


def test_every_other_table_and_view_is_built_as_without_them(pair: Pair) -> None:
    compared = 0
    for key, value in vars(pair.baseline).items():
        if isinstance(value, Table):
            assert describe(getattr(pair.clashed, key)) == describe(value), key
            compared += 1
    assert compared >= 5  # t_types, features, owners, pairs, the view


def test_every_record_names_the_same_types(pair: Pair) -> None:
    for key, value in vars(pair.baseline).items():
        if key.endswith("Record"):
            after = hints(getattr(pair.clashed, key), dict(vars(pair.clashed)))
            assert after == hints(value, dict(vars(pair.baseline))), key


def test_the_module_s_own_names_mean_what_they_meant(pair: Pair) -> None:
    baseline = dict(vars(pair.baseline))
    exported = {constant(n) for n in pair.names}
    # The fingerprint differs - the migrations do - and is checked on its own below.
    for own in own_names(pair.baseline_text, baseline) - exported - {"MIGRATIONS_FINGERPRINT"}:
        before, after = baseline[own], getattr(pair.clashed, own)
        if isinstance(before, (str, int, type(pytest))) or callable(before):
            assert after is before or after == before, own
        else:
            assert type(after) is type(before), own
    assert isinstance(pair.clashed.MIGRATIONS_FINGERPRINT, str)
    assert pair.clashed.MIGRATIONS_FINGERPRINT != pair.baseline.MIGRATIONS_FINGERPRINT
    assert pair.clashed.SCHEMA_FORMAT == pair.baseline.SCHEMA_FORMAT


def test_create_all_from_it_builds_every_table(pair: Pair) -> None:
    built = engine(pair.rebuilt)
    try:
        found = set(inspect(built).get_table_names())
    finally:
        built.dispose()
    assert {"t_types", "features", "owners", "pairs", *pair.names} <= found
