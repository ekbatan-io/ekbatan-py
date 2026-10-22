"""The record schemagen writes beside each table: ``WALLETS`` and ``WalletsRecord``.

No database: tables are built by hand and rendered, as ``test_schemagen_config`` does. Each
source of a column's type has a case - a plain type, a nullable one, a forced helper, a forced
SQLAlchemy type, a library type, a converter read off its ``from_db`` annotation, one read off
its ``Converter[DbT, UserT]`` base, one that says neither - and so do the names: a record's own,
an import that clashes with one, a column named like a type.

Every rendered module is also executed and its records' types read back from what the record
does - ``tests/records.py`` passes each keyword a marker and sees which key it lands under - so a
record that reads well but means something else fails here. The proof that a type checker
enforces them is ``test_typed_records_mypy``.
"""

from __future__ import annotations

import logging
import sys
import types
import uuid
from collections.abc import Iterator
from datetime import date
from datetime import time as time_of_day
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

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
from ekbatan.time import Instant, LocalDateTime, OffsetDateTime
from records import filled, keywords
from records import hints as record_hints


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
    module = _run(text_)
    return record_hints(getattr(module, record), vars(module))


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
    table_at = text_.index("WALLETS = sqlalchemy.Table(")
    assert table_at < text_.index("class WalletsRecord(dict[str, Any]):")
    module = _run(text_)
    assert module.__all__ == ["WALLETS", "WalletsRecord", "metadata"]
    assert issubclass(module.WalletsRecord, dict)


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
    record = _record(text_, "WalletsRecord")
    assert record[
        record.index("        *,\n") + 11 : record.index("    ) -> None:")
    ].splitlines() == [
        "        id: int,",
        "        version: int,",
        "        state: str,",
        "        balance: Decimal,",
        "        rate: float,",
        "        active: bool,",
        "        born: date,",
        "        blob: bytes,",
        "        note: str,",
        "        tags: list[str],",
        '        mood: Literal["happy", "sad"],',
        "        span: timedelta,",
        "        ip: IPv4Address | IPv6Address,",
        "        seats: Range[int],",
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
    assert "        state: str,\n" in record
    assert _hints(text_, "WalletsRecord")["note"] == str | None


def test_a_column_the_database_fills_may_be_left_out() -> None:
    """A server default, an identity, a computed column: an insert may leave each out - and
    must leave out a computed one - so its keyword defaults to UNSET, and left at that the key
    is not in the record at all."""
    text_ = _render(
        Column("created", TIMESTAMP(), server_default=text("now()"), nullable=False),
        Column("seq", BIGINT(), Identity(), nullable=False),
        Column("doubled", INTEGER(), Computed("id * 2")),
        Column("plain", INTEGER(), nullable=False),
    )
    module = _run(text_)
    record = module.WalletsRecord
    assert filled(record) == {"created", "seq", "doubled"}
    assert record_hints(record, vars(module))["created"] is Instant, "read, it is there"
    assert "        created: Instant | Unset = UNSET,\n" in text_
    assert "        doubled: int | Unset | None = UNSET,\n" in text_
    left_out = record(id=1, plain=2)
    assert left_out == {"id": 1, "plain": 2} and "created" not in left_out
    assert record(id=1, plain=2, seq=7)["seq"] == 7
    assert record(id=1, plain=2, doubled=None) == {"id": 1, "plain": 2, "doubled": None}


# -- forced types -----------------------------------------------------------------------------


def test_the_built_in_forced_types_give_what_their_helpers_read() -> None:
    """A uuid reads as a ``UUID``; a zone-less timestamp as an ``Instant`` (the ``datetime``
    written, no zone added); JSON as a JSON value."""
    text_ = _render(
        Column("owner_id", UUID(), nullable=False),
        Column("created_date", TIMESTAMP(), nullable=False),
        Column("payload", postgresql.JSONB()),
    )
    assert "uuid_column()" in text_ and "timestamp_column()" in text_
    hints = _hints(text_, "WalletsRecord")
    assert hints["owner_id"] is uuid.UUID
    assert hints["created_date"] is Instant
    assert "from ekbatan.time import Instant" in text_
    assert hints["payload"] == JsonValue | None
    assert "from ekbatan.schema import JsonValue" in text_ or "JsonValue," in text_


# -- time kinds ---------------------------------------------------------------------------------


def test_each_time_column_gets_its_kind() -> None:
    """Zone-less is an Instant, zoned an OffsetDateTime, TIME and DATE Python's own - on
    PostgreSQL, where TIMESTAMPTZ keeps its own type (and precision) and only the record's kind
    comes from the built-in forced type."""
    from sqlalchemy import TIME

    text_ = _render(
        Column("at", TIMESTAMP(), nullable=False),
        Column("zoned", postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        Column("opens", TIME(), nullable=False),
        Column("day", DATE(), nullable=False),
    )
    hints = _hints(text_, "WalletsRecord")
    assert hints["at"] is Instant
    assert hints["zoned"] is OffsetDateTime
    assert hints["opens"] is time_of_day
    assert hints["day"] is date
    assert '"at", timestamp_column()' in text_
    assert '"zoned", postgresql.TIMESTAMP(timezone=True, precision=3)' in text_
    assert "from ekbatan.time import Instant, OffsetDateTime" in text_


def test_mysql_datetime_and_timestamp_are_instants() -> None:
    text_ = _render(
        Column("at", mysql.DATETIME(fsp=6), nullable=False),
        Column("stamped", mysql.TIMESTAMP(), nullable=False),
        dialect="mysql",
    )
    hints = _hints(text_, "WalletsRecord")
    assert hints["at"] is Instant and hints["stamped"] is Instant


def test_a_column_forced_to_local_date_time_says_so() -> None:
    """The opt-out: a wall-clock column is forced by name, tried before the built-in Instant.
    The column is the same zone-less timestamp; only its record's kind differs."""
    text_ = _render(
        Column("at", TIMESTAMP(), nullable=False),
        Column("opens_at", TIMESTAMP(), nullable=False),
        forced=(
            ForcedType(
                name="LocalDateTime",
                include_types="TIMESTAMP|DATETIME",
                include_expression=r"wallets\.opens_at",
            ),
        ),
    )
    hints = _hints(text_, "WalletsRecord")
    assert hints["at"] is Instant
    assert hints["opens_at"] is LocalDateTime
    assert '"opens_at", timestamp_column()' in text_


def test_a_zone_less_column_forced_to_offset_date_time_is_refused() -> None:
    """It used to be built zoned, with a record saying OffsetDateTime - of a column that reads
    back naive and keeps no offset (the time review, T4)."""
    with pytest.raises(ValidationError, match=r"wallets\.at, which is TIMESTAMP and has no zone"):
        _render(
            Column("at", TIMESTAMP(), nullable=False),
            forced=(ForcedType(name="OffsetDateTime", include_expression="at"),),
        )


@pytest.mark.parametrize("kind", ["Instant", "LocalDateTime", "OffsetDateTime"])
@pytest.mark.parametrize("column_type", [VARCHAR(20), INTEGER(), TEXT(), DATE()])
def test_a_time_kind_on_a_column_that_holds_no_timestamp_is_refused(
    kind: str, column_type: Any
) -> None:
    with pytest.raises(ValidationError, match="not a timestamp"):
        _render(
            Column("c", column_type, nullable=False),
            forced=(ForcedType(name=kind, include_expression="c"),),
        )


def test_a_time_kind_takes_no_arguments() -> None:
    with pytest.raises(ValidationError, match="a time kind takes no arguments"):
        _render(
            Column("at", TIMESTAMP(), nullable=False),
            forced=(ForcedType(name="Instant(3)", include_expression="at"),),
        )


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
    assert events["event_date"] is Instant
    assert events["payload"] == JsonValue | None
    assert events["delivered"] is bool
    assert _hints(text_, "ScheduledTasksRecord")["task_data"] == bytes | None
    assert _hints(text_, "EventNotificationsRecord")["attempts"] is int


def test_a_type_nothing_can_say_anything_about_is_any_and_said(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="io.ekbatan.schemagen"):
        text_ = _render(Column("doc", sql_type("xml"), nullable=False))
    assert "        doc: Any," in _record(text_, "WalletsRecord")
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
        "from typing import TYPE_CHECKING, Any, TypeVar\n\n"
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
        "class AnyWithBase(Converter[str, Currency]):\n"
        "    def from_db(self, value: str) -> Any:\n"
        "        return Currency(value)\n"
        "    def to_db(self, value: Any) -> str:\n"
        "        return value.code\n\n"
        "class AnyOnly:\n"
        "    def from_db(self, value: str) -> Any:\n"
        "        return value\n"
        "    def to_db(self, value: Any) -> str:\n"
        "        return value\n\n"
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
    assert "        amount: Money," in _record(text_, "WalletsRecord")
    assert "from record_user_types.model import Money" in text_
    assert _hints(text_, "WalletsRecord")["amount"].__module__ == "record_user_types.model"


def test_without_an_annotation_the_converter_base_says_it(user_types: Path) -> None:
    text_ = _converted("BaseOnly", Column("currency", VARCHAR(3), nullable=False))
    assert "        currency: Currency," in _record(text_, "WalletsRecord")


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
    assert "        amount: Money," in _record(text_, "WalletsRecord")
    with pytest.raises(ValidationError, match="TYPE_CHECKING"):
        _converted("Hidden", Column("amount", NUMERIC(19, 4), nullable=False))


def test_a_converter_annotated_any_falls_back_to_its_base(user_types: Path) -> None:
    text_ = _converted("AnyWithBase", Column("currency", VARCHAR(3), nullable=False))
    assert "        currency: Currency," in _record(text_, "WalletsRecord")


def test_a_converter_that_says_only_any_is_any_and_said(
    user_types: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="io.ekbatan.schemagen"):
        text_ = _converted("AnyOnly", Column("currency", VARCHAR(3), nullable=False))
    assert "        currency: Any," in _record(text_, "WalletsRecord")
    assert any(
        "wallets.currency" in r.message
        and "record_user_types.converters.AnyOnly" in r.message
        and "Any" in r.message
        for r in caplog.records
    ), [r.message for r in caplog.records]


def test_a_converter_s_none_and_the_column_s_null_are_said_once(user_types: Path) -> None:
    text_ = _converted("Nullable", Column("currency", VARCHAR(3)))
    assert "        currency: Currency | None,\n" in _record(text_, "WalletsRecord")


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
    assert issubclass(module.WalletsRecord, dict), "the record keeps its name"
    assert "import WalletsRecord as record_user_types_model_WalletsRecord" in text_
    hints = record_hints(module.WalletsRecord, vars(module))
    assert hints["doc"].__module__ == "record_user_types.model"


def test_a_converter_named_like_a_record_is_aliased(user_types: Path) -> None:
    text_ = _converted("WalletsRecordConverter", Column("doc", VARCHAR(10), nullable=False))
    assert issubclass(_run(text_).WalletsRecord, dict)


def test_two_tables_that_would_share_a_record_are_refused() -> None:
    with pytest.raises(ValidationError, match="FooBarRecord"):
        _render(name="foo_bar", extra=(Table("foo__bar", MetaData(), Column("id", INTEGER())),))


# -- names: the keywords and the methods --------------------------------------------------------


def test_a_column_named_like_its_own_type_still_means_the_type() -> None:
    """``date: date`` is a keyword whose annotation is read where the class is, not inside the
    signature, so it still means the type - and the method is ``column_date``."""
    text_ = _render(
        Column("date", DATE(), nullable=False), Column("str", VARCHAR(5), nullable=False)
    )
    module = _run(text_)
    hints = record_hints(module.WalletsRecord, vars(module))
    assert hints["date"] is date and hints["str"] is str
    record = module.WalletsRecord(id=1, date=date(2026, 1, 2), str="x")
    assert record.column_date() == date(2026, 1, 2) and record.column_str() == "x"


@pytest.mark.parametrize(
    ("column", "keyword", "method"),
    [
        ("class", "class_", "column_class"),
        ("keys", "keys", "column_keys"),
        ("items", "items", "column_items"),
        ("get", "get", "column_get"),
        ("order date", "order_date", "column_order_date"),
        ("2fa", "_2fa", "column_2fa"),
        ("__meta", "_meta", "column___meta"),
        ("self", "self_", "column_self"),
        ("super", "super_", "column_super"),
        ("e-mail", "e_mail", "column_e_mail"),
        ("größe", "gr__e", "column_gr__e"),
        ("Mixed Case", "Mixed_Case", "column_Mixed_Case"),
    ],
)
def test_every_column_gets_a_keyword_and_a_column_method(
    column: str, keyword: str, method: str
) -> None:
    """The method is always ``column_`` and the name, a character no Python name holds made
    ``_`` - so it can never be a ``dict`` method, a keyword or start with a digit. The keyword is
    the name where it is one; else the same characters made ``_``, a ``_`` before a digit, one
    ``_`` where a class body would mangle two, and a ``_`` after a keyword or a name ``__init__``
    needs. The key is always the column's real name."""
    text_ = _render(Column(column, VARCHAR(5), nullable=False))
    module = _run(text_)
    record_type = module.WalletsRecord
    assert keywords(record_type) == {"id": "id", column: keyword}
    record = record_type(id=1, **{keyword: "v"})
    assert record == {"id": 1, column: "v"}
    assert getattr(record, method)() == "v"
    assert record[column] == "v"
    assert f"self[{column!r}]".replace("'", '"') in text_ or column == "größe"
    # The dict's own methods are untouched by a column called after one.
    assert list(record.keys()) == ["id", column]
    assert dict(record.items()) == {"id": 1, column: "v"}


def test_two_columns_that_make_one_name_get_a_number_and_a_note(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``order_date`` and ``order date`` both make ``column_order_date`` and ``order_date``. The
    column whose name needed no change keeps it, whatever the order; the other gets ``_2`` -
    the same every run - and the run says so."""
    with caplog.at_level(logging.WARNING, logger="io.ekbatan.schemagen"):
        text_ = _render(
            Column("order date", VARCHAR(5), nullable=False),
            Column("order_date", VARCHAR(5), nullable=False),
            Column("order-date", VARCHAR(5), nullable=False),
        )
    module = _run(text_)
    assert keywords(module.WalletsRecord) == {
        "id": "id",
        "order date": "order_date_2",
        "order_date": "order_date",
        "order-date": "order_date_3",
    }
    record = module.WalletsRecord(id=1, order_date_2="a", order_date="b", order_date_3="c")
    assert record.column_order_date_2() == "a"
    assert record.column_order_date() == "b"
    assert record.column_order_date_3() == "c"
    messages = [r.message for r in caplog.records]
    assert any("wallets.order date" in m and "column_order_date_2()" in m for m in messages)
    assert any("wallets.order-date" in m and "order_date_3=" in m for m in messages)
    assert (
        _render(
            Column("order date", VARCHAR(5), nullable=False),
            Column("order_date", VARCHAR(5), nullable=False),
            Column("order-date", VARCHAR(5), nullable=False),
        )
        == text_
    ), "the same every run"


def test_a_column_named_like_the_unset_marker_is_a_keyword_of_its_own() -> None:
    """``UNSET`` is the default the filled columns' keywords use, so a column of that name -
    when a filled column needs the marker - is passed as ``UNSET_``."""
    text_ = _render(
        Column("UNSET", VARCHAR(5), nullable=False),
        Column("created", TIMESTAMP(), server_default=text("now()"), nullable=False),
    )
    module = _run(text_)
    assert keywords(module.WalletsRecord) == {"id": "id", "UNSET": "UNSET_", "created": "created"}
    assert module.WalletsRecord(id=1, UNSET_="x") == {"id": 1, "UNSET": "x"}


def test_the_keywords_are_keyword_only_and_each_one_is_required() -> None:
    text_ = _render(Column("note", TEXT()))
    record_type = _run(text_).WalletsRecord
    with pytest.raises(TypeError, match="positional"):
        record_type(1, None)
    with pytest.raises(TypeError, match="note"):
        record_type(id=1)
    assert record_type(id=1, note=None) == {"id": 1, "note": None}


def test_a_record_is_a_dict_with_no_room_for_anything_else() -> None:
    """``__slots__ = ()``: no ``__dict__`` per record, so a record costs what its ``dict``
    costs, and an attribute set by mistake fails rather than vanishing from every write."""
    record = _run(_render(Column("note", TEXT()))).WalletsRecord(id=1, note="n")
    assert type(record).__mro__[1] is dict
    assert not hasattr(record, "__dict__")
    with pytest.raises(AttributeError):
        record.note = "x"  # type: ignore[attr-defined]


def test_the_overloads_are_for_the_type_checker_only() -> None:
    """At run time ``record[key]`` is ``dict``'s own: the overloads sit under
    ``TYPE_CHECKING``, which is false when the module runs."""
    module = _run(_render(Column("note", TEXT())))
    assert module.WalletsRecord.__getitem__ is dict.__getitem__
    assert "    if TYPE_CHECKING:\n" in _record(_render(Column("note", TEXT())), "WalletsRecord")


def test_a_table_of_one_column_is_written_without_overloads() -> None:
    """``@overload`` needs two; one column is one plain signature."""
    text_ = _render()
    assert "@overload" not in _record(text_, "WalletsRecord")
    assert '        def __getitem__(self, key: Literal["id"]) -> int: ...' in text_
    assert _run(text_).WalletsRecord(id=3).column_id() == 3
