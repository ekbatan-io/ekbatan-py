"""The record schemagen writes beside each table: ``WALLETS`` and ``WalletsRecord``.

No database: tables are built by hand and rendered, as ``test_schemagen_config`` does. Each
source of a column's type has a case - a plain type, a nullable one, a forced helper, a forced
SQLAlchemy type, a library type, a converter read off its ``from_db`` annotation, one read off
its ``Converter[DbT, UserT]`` base, one that says neither - and so do the names: a record's own,
an import that clashes with one, a column named like a type.

Every rendered module is also executed and its records' hints read back with
``typing.get_type_hints``, so a record that reads well but means something else fails here.
The proof that a type checker enforces them is ``test_typed_records_mypy``.
"""

from __future__ import annotations

import logging
import sys
import types
import typing
import uuid
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, NotRequired

import pytest
from sqlalchemy import (
    BIGINT,
    BOOLEAN,
    DATE,
    INTEGER,
    NUMERIC,
    TEXT,
    TIMESTAMP,
    UUID,
    VARCHAR,
    Column,
    Computed,
    Enum,
    Identity,
    LargeBinary,
    MetaData,
    Table,
    text,
)
from sqlalchemy.dialects import mysql, postgresql

from ekbatan.errors import ValidationError
from ekbatan.schema import JsonValue, sql_type
from ekbatan.schemagen import ForcedType, SchemagenConfig, render
from ekbatan.schemagen._records import record_name


def _render(
    *columns: Column[Any],
    forced: tuple[ForcedType, ...] = (),
    dialect: str = "postgresql",
    name: str = "wallets",
    framework: tuple[str, ...] = (),
    extra: tuple[Table, ...] = (),
) -> str:
    table = Table(name, MetaData(), Column("id", INTEGER(), primary_key=True), *columns)
    return render(
        [table, *extra],
        framework=framework,
        migrations_fingerprint="x",
        module=None,
        dialect=dialect,
        config=SchemagenConfig(forced_types=forced),
    )


_COUNT = iter(range(1_000_000))


def _run(text_: str) -> types.ModuleType:
    """Execute a rendered module - imports, records and all - as a real module would be."""
    module = types.ModuleType(f"rendered_records_{next(_COUNT)}")
    module.__file__ = f"{module.__name__}.py"  # check_fingerprint wants one; module=None skips it
    sys.modules[module.__name__] = module
    exec(compile(text_, module.__name__, "exec"), module.__dict__)
    return module


def _hints(text_: str, record: str) -> dict[str, Any]:
    return typing.get_type_hints(getattr(_run(text_), record), include_extras=True)


def _record(text_: str, record: str) -> str:
    """The record's source, from its `class` line to the blank line after it."""
    start = text_.index(f"class {record}(")
    end = text_.index("\n\n\n", start)
    return text_[start:end]


# -- names ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "record"),
    [
        ("wallets", "WalletsRecord"),
        ("event_notifications", "EventNotificationsRecord"),
        ("WALLETS", "WalletsRecord"),
        ("2024_sales", "T2024SalesRecord"),
        ("order-lines", "OrderLinesRecord"),
        ("audit_events", "AuditEventsRecord"),
    ],
)
def test_the_record_is_named_as_jooq_names_it(table: str, record: str) -> None:
    assert record_name(table) == record


def test_each_table_has_its_record_beside_it_and_exported() -> None:
    text_ = _render(Column("balance", NUMERIC(19, 4), nullable=False))
    assert text_.index("WALLETS = Table(") < text_.index("class WalletsRecord(TypedDict):")
    module = _run(text_)
    assert module.__all__ == ["WALLETS", "WalletsRecord", "metadata"]
    assert typing.is_typeddict(module.WalletsRecord)


# -- plain types ------------------------------------------------------------------------------


def test_plain_types_are_what_the_column_reads_as() -> None:
    text_ = _render(
        Column("version", BIGINT(), nullable=False),
        Column("state", VARCHAR(24), nullable=False),
        Column("balance", NUMERIC(19, 4), nullable=False),
        Column("rate", postgresql.DOUBLE_PRECISION(), nullable=False),
        Column("active", BOOLEAN(), nullable=False),
        Column("born", DATE(), nullable=False),
        Column("blob", LargeBinary(), nullable=False),
        Column("note", TEXT(), nullable=False),
        Column("tags", postgresql.ARRAY(TEXT()), nullable=False),
        Column("mood", Enum("happy", "sad", name="mood"), nullable=False),
        Column("span", postgresql.INTERVAL(), nullable=False),
        Column("ip", postgresql.INET(), nullable=False),
        Column("seats", postgresql.INT4RANGE(), nullable=False),
    )
    assert _record(text_, "WalletsRecord").splitlines()[3:] == [
        "    id: int",
        "    version: int",
        "    state: str",
        "    balance: Decimal",
        "    rate: float",
        "    active: bool",
        "    born: date",
        "    blob: bytes",
        "    note: str",
        "    tags: list[str]",
        '    mood: Literal["happy", "sad"]',
        "    span: timedelta",
        "    ip: IPv4Address | IPv6Address",
        "    seats: Range[int]",
    ]
    hints = _hints(text_, "WalletsRecord")
    assert hints["balance"] is Decimal and hints["born"] is date
    assert hints["mood"] == Literal["happy", "sad"]
    assert "from sqlalchemy.dialects.postgresql.ranges import Range" in text_


def test_mysql_types_with_no_python_type_of_their_own() -> None:
    text_ = _render(
        Column("year", mysql.YEAR(), nullable=False),
        Column("flags", mysql.BIT(8), nullable=False),
        Column("picks", mysql.SET("a", "b"), nullable=False),
        Column("small", mysql.TINYINT(unsigned=True), nullable=False),
        Column("ratio", mysql.DOUBLE(), nullable=False),
        dialect="mysql",
    )
    hints = _hints(text_, "WalletsRecord")
    assert hints["year"] is int and hints["flags"] is int and hints["small"] is int
    assert hints["picks"] == set[str]
    # The generic DOUBLE schemagen writes for MySQL's own reads a float, not a Decimal.
    assert hints["ratio"] is float


def test_a_nullable_column_may_be_none() -> None:
    text_ = _render(
        Column("note", TEXT()),
        Column("points", INTEGER()),
        Column("state", VARCHAR(24), nullable=False),
    )
    record = _record(text_, "WalletsRecord")
    assert "    note: str | None" in record
    assert "    points: int | None" in record
    assert "    state: str\n" in record + "\n"
    assert _hints(text_, "WalletsRecord")["note"] == str | None


def test_a_column_the_database_fills_may_be_left_out() -> None:
    """A server default, an identity, a computed column: an insert may leave each out - and
    must leave out a computed one - so the key is not required."""
    text_ = _render(
        Column("created", TIMESTAMP(), server_default=text("now()"), nullable=False),
        Column("seq", BIGINT(), Identity(), nullable=False),
        Column("doubled", INTEGER(), Computed("id * 2")),
        Column("plain", INTEGER(), nullable=False),
    )
    hints = _hints(text_, "WalletsRecord")
    assert typing.get_origin(hints["created"]) is NotRequired
    assert typing.get_origin(hints["seq"]) is NotRequired
    assert typing.get_origin(hints["doubled"]) is NotRequired
    assert typing.get_origin(hints["plain"]) is not NotRequired


# -- forced types -----------------------------------------------------------------------------


def test_the_built_in_forced_types_give_what_their_helpers_read() -> None:
    """A uuid reads as a ``UUID``; a timestamp as the ``datetime`` written, no zone added; JSON
    as a JSON value."""
    text_ = _render(
        Column("owner_id", UUID(), nullable=False),
        Column("created_date", TIMESTAMP(), nullable=False),
        Column("payload", postgresql.JSONB()),
    )
    assert "uuid_column()" in text_ and "timestamp_column()" in text_
    hints = _hints(text_, "WalletsRecord")
    assert hints["owner_id"] is uuid.UUID
    assert hints["created_date"] is datetime
    assert hints["payload"] == JsonValue | None
    assert "from ekbatan.schema import JsonValue" in text_ or "JsonValue," in text_


def test_a_forced_sqlalchemy_type_by_name() -> None:
    text_ = _render(
        Column("code", INTEGER(), nullable=False),
        forced=(ForcedType(name="Text", include_expression="code"),),
    )
    assert _hints(text_, "WalletsRecord")["code"] is str


def test_the_framework_tables_have_records_too() -> None:
    text_ = _render(framework=("events", "event_notifications", "scheduled_tasks"))
    events = _hints(text_, "EventsRecord")
    assert events["id"] is uuid.UUID
    assert events["event_date"] is datetime
    assert events["payload"] == JsonValue | None
    assert events["delivered"] is bool
    assert _hints(text_, "ScheduledTasksRecord")["task_data"] == bytes | None
    assert _hints(text_, "EventNotificationsRecord")["attempts"] is int


def test_a_type_nothing_can_say_anything_about_is_any_and_said(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="io.ekbatan.schemagen"):
        text_ = _render(Column("doc", sql_type("xml"), nullable=False))
    assert "    doc: Any" in _record(text_, "WalletsRecord")
    assert any("wallets.doc" in r.message and "Any" in r.message for r in caplog.records)


# -- converters -------------------------------------------------------------------------------


@pytest.fixture
def user_types(tmp_path: Path) -> Iterator[Path]:
    """A package of the user's own: a model module and converters of every kind."""
    package = tmp_path / "record_user_types"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "model.py").write_text(
        "from dataclasses import dataclass\n"
        "from decimal import Decimal\n\n"
        "@dataclass(frozen=True)\n"
        "class Money:\n"
        "    amount: Decimal\n\n"
        "@dataclass(frozen=True)\n"
        "class Currency:\n"
        "    code: str\n\n"
        "class UUID:\n"
        "    '''A user class that happens to share uuid.UUID's name.'''\n\n"
        "class WalletsRecord:\n"
        "    '''A user class that happens to share the record's name.'''\n"
    )
    (package / "converters.py").write_text(
        "from __future__ import annotations\n\n"
        "from decimal import Decimal\n"
        "from typing import TYPE_CHECKING, TypeVar\n\n"
        "from ekbatan.schema import Converter\n\n"
        "from .model import UUID, Currency, Money, WalletsRecord\n\n"
        "if TYPE_CHECKING:\n"
        "    from .model import Money as HiddenMoney\n\n"
        "T = TypeVar('T')\n\n"
        "class Annotated:\n"
        "    '''No base class: the annotation alone says what it gives.'''\n"
        "    def from_db(self, value: Decimal) -> Money:\n"
        "        return Money(value)\n"
        "    def to_db(self, value: Money) -> Decimal:\n"
        "        return value.amount\n\n"
        "class BaseOnly(Converter[str, Currency]):\n"
        "    '''No annotations: the generic base says it.'''\n"
        "    def from_db(self, value):  # type: ignore[no-untyped-def]\n"
        "        return Currency(value)\n"
        "    def to_db(self, value):  # type: ignore[no-untyped-def]\n"
        "        return value.code\n\n"
        "class Neither:\n"
        "    def from_db(self, value):\n"
        "        return value\n"
        "    def to_db(self, value):\n"
        "        return value\n\n"
        "class Generic:\n"
        "    def from_db(self, value: T) -> T:\n"
        "        return value\n"
        "    def to_db(self, value: T) -> T:\n"
        "        return value\n\n"
        "class Hidden:\n"
        "    def from_db(self, value: Decimal) -> HiddenMoney:\n"
        "        return Money(value)\n"
        "    def to_db(self, value: HiddenMoney) -> Decimal:\n"
        "        return value.amount\n\n"
        "class HiddenWithBase(Converter[Decimal, Money]):\n"
        "    def from_db(self, value: Decimal) -> HiddenMoney:\n"
        "        return Money(value)\n"
        "    def to_db(self, value: HiddenMoney) -> Decimal:\n"
        "        return value.amount\n\n"
        "class Clashing:\n"
        "    def from_db(self, value: str) -> UUID:\n"
        "        return UUID()\n"
        "    def to_db(self, value: UUID) -> str:\n"
        "        return ''\n\n"
        "class NamedLikeARecord:\n"
        "    def from_db(self, value: str) -> WalletsRecord:\n"
        "        return WalletsRecord()\n"
        "    def to_db(self, value: WalletsRecord) -> str:\n"
        "        return ''\n\n"
        "class Nullable:\n"
        "    def from_db(self, value: str) -> Currency | None:\n"
        "        return Currency(value) if value else None\n"
        "    def to_db(self, value: Currency) -> str:\n"
        "        return value.code\n\n"
        "class WalletsRecordConverter:\n"
        "    def from_db(self, value: str) -> str:\n"
        "        return value\n"
        "    def to_db(self, value: str) -> str:\n"
        "        return value\n"
    )
    sys.path.insert(0, str(tmp_path))
    yield package
    sys.path.remove(str(tmp_path))
    for name in [m for m in sys.modules if m.startswith("record_user_types")]:
        del sys.modules[name]


def _converted(converter: str, column: Column[Any], **kwargs: Any) -> str:
    return _render(
        column,
        forced=(
            ForcedType(
                converter=f"record_user_types.converters.{converter}",
                include_expression=column.name,
            ),
        ),
        **kwargs,
    )


def test_a_converter_s_type_is_read_off_its_from_db_annotation(user_types: Path) -> None:
    text_ = _converted("Annotated", Column("amount", NUMERIC(19, 4), nullable=False))
    assert "    amount: Money" in _record(text_, "WalletsRecord")
    assert "from record_user_types.model import Money" in text_
    assert _hints(text_, "WalletsRecord")["amount"].__module__ == "record_user_types.model"


def test_without_an_annotation_the_converter_base_says_it(user_types: Path) -> None:
    text_ = _converted("BaseOnly", Column("currency", VARCHAR(3), nullable=False))
    assert "    currency: Currency" in _record(text_, "WalletsRecord")


def test_a_converter_that_says_neither_is_refused_naming_it(user_types: Path) -> None:
    with pytest.raises(ValidationError) as refused:
        _converted("Neither", Column("amount", NUMERIC(19, 4), nullable=False))
    message = str(refused.value)
    assert "record_user_types.converters.Neither" in message
    assert "from_db() has no return annotation" in message
    assert "Converter[DbT, UserT]" in message
    assert "Annotate it" in message


def test_a_converter_returning_a_type_variable_is_refused(user_types: Path) -> None:
    with pytest.raises(ValidationError, match="names no real type"):
        _converted("Generic", Column("amount", NUMERIC(19, 4), nullable=False))


def test_an_annotation_naming_a_type_checking_import_falls_back_to_the_base(
    user_types: Path,
) -> None:
    text_ = _converted("HiddenWithBase", Column("amount", NUMERIC(19, 4), nullable=False))
    assert "    amount: Money" in _record(text_, "WalletsRecord")
    with pytest.raises(ValidationError, match="TYPE_CHECKING"):
        _converted("Hidden", Column("amount", NUMERIC(19, 4), nullable=False))


def test_a_converter_s_none_and_the_column_s_null_are_said_once(user_types: Path) -> None:
    text_ = _converted("Nullable", Column("currency", VARCHAR(3)))
    assert "    currency: Currency | None\n" in _record(text_, "WalletsRecord") + "\n"


# -- clashes ----------------------------------------------------------------------------------


def test_a_user_type_named_like_another_import_is_aliased(user_types: Path) -> None:
    """``record_user_types.model.UUID`` and ``uuid.UUID`` in one module: the second to come is
    imported under an alias, and each column still means its own."""
    text_ = _converted(
        "Clashing",
        Column("tag", VARCHAR(10), nullable=False),
        extra=(Table("owners", MetaData(), Column("id", UUID(), primary_key=True)),),
    )
    hints = _hints(text_, "WalletsRecord")
    assert hints["tag"].__module__ == "record_user_types.model"
    assert _hints(text_, "OwnersRecord")["id"] is uuid.UUID


def test_a_user_type_named_like_a_record_is_aliased(user_types: Path) -> None:
    text_ = _converted("NamedLikeARecord", Column("doc", VARCHAR(10), nullable=False))
    module = _run(text_)
    assert typing.is_typeddict(module.WalletsRecord), "the record keeps its name"
    assert "import WalletsRecord as record_user_types_model_WalletsRecord" in text_
    hints = typing.get_type_hints(module.WalletsRecord)
    assert hints["doc"].__module__ == "record_user_types.model"


def test_a_converter_named_like_a_record_is_aliased(user_types: Path) -> None:
    text_ = _converted("WalletsRecordConverter", Column("doc", VARCHAR(10), nullable=False))
    assert typing.is_typeddict(_run(text_).WalletsRecord)


def test_two_tables_that_would_share_a_record_are_refused() -> None:
    with pytest.raises(ValidationError, match="FooBarRecord"):
        _render(name="foo_bar", extra=(Table("foo__bar", MetaData(), Column("id", INTEGER())),))


def test_a_column_named_like_its_own_type_makes_the_call_form() -> None:
    """``date: date`` in a class body names the field, not the type - so the record is written
    as a call, and still means a ``date``."""
    text_ = _render(
        Column("date", DATE(), nullable=False), Column("class", VARCHAR(5), nullable=False)
    )
    assert 'WalletsRecord = TypedDict(\n    "WalletsRecord",' in text_
    hints = _hints(text_, "WalletsRecord")
    assert hints["date"] is date and hints["class"] is str


def test_a_column_named_like_a_builtin_it_uses_makes_the_call_form() -> None:
    text_ = _render(Column("str", VARCHAR(5), nullable=False))
    assert _hints(text_, "WalletsRecord")["str"] is str
