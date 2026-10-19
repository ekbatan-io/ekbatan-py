"""Converters: a plain class of the application's, wrapped around a column's own type.

Each test runs the whole pipeline on a real database: migrations, ``generate``, the module
imported from the file it wrote, values written and read back through the converter, a
``WHERE`` that converts its value, and ``metadata.create_all`` building the same column again.

    uv run pytest tests/integration/test_schemagen_converters.py --dialects=all

The pgvector test needs the ``pgvector/pgvector:pg17`` image and runs on PostgreSQL only; it
starts its own container and removes it.
"""

from __future__ import annotations

import ipaddress
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, insert, select, text

from backends import Backend
from ekbatan.errors import ValidationError
from ekbatan.schemagen import ForcedType, SchemagenConfig, generate
from schemagen_support import column_sources, engine, fresh, load, migrations, normal, raw_types

WHEN = datetime(2026, 1, 2, 3, 4, 5, 123456)

CONVERTERS = '''"""Converters an application might write: plain classes, no SQLAlchemy in them."""
import ipaddress
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from ekbatan.schema import Converter


@dataclass(frozen=True)
class Money:
    amount: Decimal


class MoneyConverter(Converter[Decimal, Money]):
    """With the optional base class."""

    def from_db(self, value: Decimal) -> Money:
        assert value is not None, "None must never reach a converter"
        return Money(value)

    def to_db(self, value: Money) -> Decimal:
        assert value is not None, "None must never reach a converter"
        return value.amount


class IsoText:
    """Without it: any object with from_db and to_db. Checks it is handed what the column's
    helper gives - the value as stored, no zone added - and not a string."""

    def from_db(self, value: datetime) -> str:
        assert isinstance(value, datetime) and value.tzinfo is None, f"got {value!r}"
        return value.isoformat()

    def to_db(self, value: str) -> datetime:
        return datetime.fromisoformat(value)


@dataclass(frozen=True)
class Doc:
    xml: str


class DocConverter:
    def from_db(self, value: str) -> Doc:
        return Doc(value)

    def to_db(self, value: Doc) -> str:
        return value.xml


class UuidBytes:
    """A uuid in 16 bytes."""

    def from_db(self, value: bytes) -> uuid.UUID:
        return uuid.UUID(bytes=bytes(value))

    def to_db(self, value: uuid.UUID) -> bytes:
        return value.bytes


class UuidHex:
    """A uuid as 32 hex digits, for SQLite."""

    def from_db(self, value: str) -> uuid.UUID:
        return uuid.UUID(value)

    def to_db(self, value: uuid.UUID) -> str:
        return value.hex


class Ipv6:
    def from_db(self, value: str) -> ipaddress.IPv6Address:
        return ipaddress.IPv6Address(value)

    def to_db(self, value: ipaddress.IPv6Address) -> str:
        return str(value)


class Vector:
    """pgvector's text form, "[0.1,0.2,0.3]", as a list of floats - no pgvector package."""

    def from_db(self, value: str) -> list[float]:
        return [float(x) for x in value.strip("[]").split(",")]

    def to_db(self, value: list[float]) -> str:
        return "[" + ",".join(repr(float(x)) for x in value) + "]"


class NotAConverter:
    def from_db(self, value):
        return value
'''


@pytest.fixture
def converters(tmp_path: Path) -> Iterator[str]:
    """The application's converters, in a package of its own on the path."""
    package = f"my_converters_{uuid.uuid4().hex[:8]}"
    root = tmp_path / "user"
    (root / package).mkdir(parents=True)
    (root / package / "__init__.py").write_text("")
    (root / package / "converters.py").write_text(CONVERTERS)
    sys.path.insert(0, str(root))
    yield f"{package}.converters"
    sys.path.remove(str(root))
    for name in [m for m in sys.modules if m.startswith(package)]:
        sys.modules.pop(name)


LEDGER = {
    "postgres": "CREATE TABLE ledger (id INTEGER PRIMARY KEY, amount NUMERIC(19, 4) NOT NULL, "
    "rate NUMERIC(19, 4), doc XML, created_date TIMESTAMP NOT NULL);\n",
    "mysql": "CREATE TABLE ledger (id INTEGER PRIMARY KEY, amount DECIMAL(19, 4) NOT NULL, "
    "rate DECIMAL(19, 4), account_id BINARY(16), created_date DATETIME(6) NOT NULL);\n",
    "mariadb": "CREATE TABLE ledger (id INTEGER PRIMARY KEY, amount DECIMAL(19, 4) NOT NULL, "
    "rate DECIMAL(19, 4), address INET6, created_date DATETIME(6) NOT NULL);\n",
    "sqlite": "CREATE TABLE ledger (id INTEGER PRIMARY KEY, amount NUMERIC(19, 4) NOT NULL, "
    "rate NUMERIC(19, 4), owner_id CHAR(32), created_date DATETIME NOT NULL);\n",
}


def _numeric(dialect: str) -> str:
    return "DECIMAL" if dialect in ("mysql", "mariadb") else "NUMERIC"


def _generate(
    tmp_path: Path, backend: Backend, dialect: str, *forced: ForcedType
) -> tuple[Any, str, dict[tuple[str, str], str]]:
    source = migrations(tmp_path / "migrations", LEDGER[dialect])
    url = fresh(backend, "conv")
    out = tmp_path / "app" / "schema.py"
    written = generate(source, out, url=url, config=SchemagenConfig(forced_types=forced))
    return load(out), url, column_sources(written)


def _rebuilt(backend: Backend, module: Any) -> str:
    """A second fresh database, built by `create_all` from the generated module."""
    url = fresh(backend, "conv_b")
    db = engine(url)
    try:
        module.metadata.create_all(db)
    finally:
        db.dispose()
    return url


def _types(module: Any) -> Any:
    return sys.modules[type(module.LEDGER.c.amount.type.converter).__module__]


def test_a_converter_picked_by_column_name(
    tmp_path: Path, backend: Backend, dialect: str, converters: str
) -> None:
    """`include-expression`: the named columns keep their database type, the converter is
    wrapped around it, and the application sees its own `Money` - written, read, compared in a
    `WHERE`, and `NULL` left as `None` without the converter being called."""
    module, url, sources = _generate(
        tmp_path,
        backend,
        dialect,
        ForcedType(
            converter=f"{converters}.MoneyConverter", include_expression=r".*\.(amount|rate)"
        ),
    )
    numeric = _numeric(dialect)
    assert sources[("LEDGER", "amount")] == normal(
        f"converted({numeric}(precision=19, scale=4), MoneyConverter())"
    )
    assert sources[("LEDGER", "rate")] == sources[("LEDGER", "amount")]
    assert sources[("LEDGER", "created_date")] == "timestamp_column()", "only the named columns"

    money = _types(module).Money
    rebuilt = _rebuilt(backend, module)
    migrated_raw, rebuilt_raw = raw_types(url), raw_types(rebuilt)
    assert rebuilt_raw[("ledger", "amount")] == migrated_raw[("ledger", "amount")]
    for where in (url, rebuilt):
        db = engine(where)
        table = module.LEDGER
        try:
            with db.begin() as connection:
                connection.execute(
                    insert(table).values(
                        id=1, amount=money(Decimal("10.5")), rate=None, created_date=WHEN
                    )
                )
                row = connection.execute(select(table.c.amount, table.c.rate)).one()
                found = connection.execute(
                    select(table.c.id).where(table.c.amount == money(Decimal("10.5000")))
                ).scalar_one()
                more = connection.execute(
                    select(table.c.id).where(table.c.amount > money(Decimal("10")))
                ).scalar_one()
                stored = connection.execute(text("SELECT amount FROM ledger")).scalar_one()
        finally:
            db.dispose()
        assert row.amount == money(Decimal("10.5000"))
        assert row.rate is None
        assert (found, more) == (1, 1)
        assert Decimal(str(stored)) == Decimal("10.5"), "the database holds the plain number"


def test_a_converter_picked_by_sql_type(
    tmp_path: Path, backend: Backend, dialect: str, converters: str
) -> None:
    """`include-types`, with each database's own case - two of them types SQLAlchemy has no
    class for, which the column keeps as `sql_type(...)` under the converter:

    - PostgreSQL `XML` as the application's `Doc`;
    - MySQL `BINARY(16)` named `*_id` as a `uuid.UUID`;
    - MariaDB `INET6` as an `ipaddress.IPv6Address`;
    - SQLite `CHAR(32)` named `*_id` as a `uuid.UUID`.
    """
    column, forced, source, value_of, where = {
        "postgres": (
            "doc",
            ForcedType(converter=f"{converters}.DocConverter", include_types="XML"),
            'converted(sql_type("xml"), DocConverter())',
            lambda types: types.Doc("<a>1</a>"),
            False,  # PostgreSQL has no = for xml
        ),
        "mysql": (
            "account_id",
            ForcedType(
                converter=f"{converters}.UuidBytes",
                include_types=r"BINARY\(16\)",
                include_expression=".*_id",
            ),
            "converted(BINARY(length=16), UuidBytes())",
            lambda types: uuid.uuid4(),
            True,
        ),
        "mariadb": (
            "address",
            ForcedType(converter=f"{converters}.Ipv6", include_types="INET6"),
            'converted(sql_type("inet6"), Ipv6())',
            lambda types: ipaddress.IPv6Address("2001:db8::1"),
            True,
        ),
        "sqlite": (
            "owner_id",
            ForcedType(
                converter=f"{converters}.UuidHex",
                include_types=r"CHAR\(32\)",
                include_expression=".*_id",
            ),
            "converted(CHAR(length=32), UuidHex())",
            lambda types: uuid.uuid4(),
            True,
        ),
    }[dialect]
    module, url, sources = _generate(tmp_path, backend, dialect, forced)
    assert sources[("LEDGER", column)] == normal(source)

    value = value_of(sys.modules[converters])
    rebuilt = _rebuilt(backend, module)
    assert raw_types(rebuilt)[("ledger", column)] == raw_types(url)[("ledger", column)]
    table = module.LEDGER
    for at in (url, rebuilt):
        db = engine(at)
        try:
            with db.begin() as connection:
                connection.execute(
                    insert(table).values(
                        {"id": 1, "amount": Decimal(1), "created_date": WHEN, column: value}
                    )
                )
                connection.execute(
                    insert(table).values(
                        {"id": 2, "amount": Decimal(2), "created_date": WHEN, column: None}
                    )
                )
                back = connection.execute(
                    select(table.c[column]).where(table.c.id == 1)
                ).scalar_one()
                empty = connection.execute(
                    select(table.c[column]).where(table.c.id == 2)
                ).scalar_one()
                if where:
                    found = connection.execute(
                        select(table.c.id).where(table.c[column] == value)
                    ).scalar_one()
                    assert found == 1
        finally:
            db.dispose()
        assert back == value and type(back) is type(value)
        assert empty is None


def test_a_converter_is_handed_what_the_column_would_give_without_it(
    tmp_path: Path, backend: Backend, dialect: str, converters: str
) -> None:
    """A converter on a timestamp column wraps Ekbatan's `timestamp_column()`, so it receives
    the `datetime` the driver read, as it is, on every database."""
    module, url, sources = _generate(
        tmp_path,
        backend,
        dialect,
        ForcedType(converter=f"{converters}.IsoText", include_expression="created_date"),
    )
    assert sources[("LEDGER", "created_date")] == "converted(timestamp_column(), IsoText())"

    db = engine(url)
    table = module.LEDGER
    try:
        with db.begin() as connection:
            connection.execute(
                insert(table).values(id=1, amount=Decimal(1), created_date=WHEN.isoformat())
            )
            back = connection.execute(select(table.c.created_date)).scalar_one()
    finally:
        db.dispose()
    assert back == WHEN.isoformat()


def test_a_type_sqlalchemy_cannot_read_builds_and_round_trips_as_text(
    tmp_path: Path, backend: Backend, dialect: str
) -> None:
    """With no forced type at all: PostgreSQL's `xml` and MariaDB's `INET6` are written as the
    database spells them, so `create_all` builds them, and values come back as strings. (The
    type matrix covers more; this is the plain case next to the converter ones.)"""
    if dialect not in ("postgres", "mariadb"):
        pytest.skip("PostgreSQL's xml and MariaDB's INET6")
    column, spelled, value = {
        "postgres": ("doc", "xml", "<a>1</a>"),
        "mariadb": ("address", "inet6", "2001:db8::1"),
    }[dialect]
    module, url, sources = _generate(tmp_path, backend, dialect)
    assert sources[("LEDGER", column)] == normal(f'sql_type("{spelled}")')

    rebuilt = _rebuilt(backend, module)
    assert raw_types(rebuilt)[("ledger", column)] == spelled
    for at in (url, rebuilt):
        db = engine(at)
        try:
            with db.begin() as connection:
                connection.execute(
                    insert(module.LEDGER).values(
                        {"id": 1, "amount": Decimal(1), "created_date": WHEN, column: value}
                    )
                )
                back = connection.execute(select(module.LEDGER.c[column])).scalar_one()
        finally:
            db.dispose()
        assert back == value


def test_a_converter_that_is_not_one_fails_before_any_database_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, converters: str
) -> None:
    """A typo in the path, or a class missing `to_db`, is reported naming the forced type -
    before a container starts, and not as a generated module that fails later."""
    import ekbatan.schemagen as schemagen

    def no_container(image: str) -> Any:
        raise AssertionError("a container was started")

    monkeypatch.setattr(schemagen, "_start", no_container)
    source = migrations(tmp_path / "migrations", "CREATE TABLE t (id INTEGER PRIMARY KEY);")

    for path, message in (
        (f"{converters}.MoneyConvertor", "has no MoneyConvertor"),
        ("nowhere_at_all.MoneyConverter", "cannot import nowhere_at_all"),
        (f"{converters}.NotAConverter", r"NotAConverter has no from_db\(\) and to_db\(\)"),
    ):
        with pytest.raises(ValidationError, match=message):
            generate(
                source,
                tmp_path / "schema.py",
                config=SchemagenConfig(
                    forced_types=(ForcedType(converter=path, include_types="X"),)
                ),
            )


def test_the_old_user_type_key_says_what_it_is_now(tmp_path: Path) -> None:
    """`user-type` was renamed: the command says to what, before any database starts."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ekbatan.schemagen]\nmigrations = "m"\nout = "s.py"\n\n'
        '[[tool.ekbatan.schemagen.forced-types]]\nuser-type = "app.Money"\n'
        'include-types = "X"\n'
    )
    result = subprocess.run(
        [sys.executable, "-m", "ekbatan.schemagen"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "user-type is now two keys. Use converter" in result.stderr
    assert "or sqlalchemy-type for a ready-made SQLAlchemy type" in result.stderr


# -- pgvector, with a converter and nothing else -----------------------------------------------

PGVECTOR_IMAGE = "pgvector/pgvector:pg17"

PGVECTOR = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE items (
    id INTEGER PRIMARY KEY,
    name VARCHAR(20) NOT NULL,
    embedding vector(3) NOT NULL,
    spare vector(3)
);
"""


@pytest.fixture
def pgvector(dialect: str) -> Iterator[Any]:
    """A PostgreSQL with the pgvector extension, for this test alone; removed afterwards."""
    if dialect != "postgres":
        pytest.skip("pgvector is a PostgreSQL extension")
    from testcontainers.community.postgres import PostgresContainer

    container = PostgresContainer(PGVECTOR_IMAGE, driver="psycopg")
    container.start()
    try:
        yield container
    finally:
        container.stop()


def _database(container: Any, name: str) -> str:
    admin = create_engine(container.get_connection_url(), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {name}")
    finally:
        admin.dispose()
    return container.get_connection_url().rsplit("/", 1)[0] + f"/{name}"


def test_pgvector_with_a_plain_converter(tmp_path: Path, pgvector: Any, converters: str) -> None:
    """The pgvector case without the pgvector package: the column is `sql_type("vector(3)")`,
    a converter turns its text into a list of floats, `create_all` builds `vector(3)` again, and
    `.op("<->")` orders by distance, nearest first. A vector column with no forced type comes
    back as pgvector's text."""
    source = migrations(tmp_path / "migrations", PGVECTOR)
    url = _database(pgvector, "migrated")
    out = tmp_path / "app" / "schema.py"
    written = generate(
        source,
        out,
        url=url,
        config=SchemagenConfig(
            forced_types=(
                ForcedType(converter=f"{converters}.Vector", include_expression="embedding"),
            )
        ),
    )
    module = load(out)
    sources = column_sources(written)
    assert sources[("ITEMS", "embedding")] == normal('converted(sql_type("vector(3)"), Vector())')
    assert sources[("ITEMS", "spare")] == normal('sql_type("vector(3)")')

    rebuilt = _database(pgvector, "rebuilt")
    db = engine(rebuilt)
    try:
        with db.begin() as connection:
            connection.exec_driver_sql("CREATE EXTENSION vector")
        module.metadata.create_all(db)
    finally:
        db.dispose()
    assert raw_types(rebuilt)[("items", "embedding")] == "vector(3)"
    assert raw_types(rebuilt)[("items", "spare")] == "vector(3)"

    items = module.ITEMS
    for at in (url, rebuilt):
        db = engine(at)
        try:
            with db.begin() as connection:
                connection.execute(
                    insert(items),
                    [
                        {"id": 1, "name": "far", "embedding": [9.0, 9.0, 9.0], "spare": None},
                        {"id": 2, "name": "near", "embedding": [0.1, 0.2, 0.3], "spare": None},
                        {"id": 3, "name": "middle", "embedding": [1.0, 1.0, 1.0], "spare": None},
                    ],
                )
                connection.execute(items.update().where(items.c.id == 2).values(spare="[1,2,3]"))
                ordered = connection.execute(
                    select(items.c.name).order_by(items.c.embedding.op("<->")([0.0, 0.0, 0.0]))
                ).scalars()
                names = list(ordered)
                near = connection.execute(
                    select(items.c.embedding, items.c.spare).where(items.c.id == 2)
                ).one()
        finally:
            db.dispose()
        assert names == ["near", "middle", "far"]
        assert near.embedding == pytest.approx([0.1, 0.2, 0.3])
        assert near.spare == "[1,2,3]"
