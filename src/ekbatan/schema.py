"""The framework's own tables, as SQLAlchemy metadata - **shape only, not physical design.**

Both shapes are contracts, not implementation details: a Debezium connector, a CDC pipeline, or
another language's Ekbatan service binds to these column names and types. Declaring them here
rather than asking every application to transcribe them means there is one definition to be
right about, and migrations can be generated from it.

This is what Java's framework does, and it declares no more than this. Its repositories reach
for the same tables through `DSL.table(DSL.name("eventlog", "events"))` and every column through
`DSL.field(...)` - hand-written references, so a query can be built. There is no `CREATE TABLE`
and no `CREATE INDEX` anywhere in `ekbatan-core`, `ekbatan-events` or `ekbatan-distributed-jobs`.

**So there are no indexes here either, as of `EKB-0215`.** There used to be three, and they were
the reason PostgreSQL went without the partial indexes Java gives it: one declaration cannot be
`WHERE delivered = FALSE` on PostgreSQL and a plain composite on MySQL, so it was the composite
everywhere - measured at 3,528 kB against Java's 56 kB on 500,000 rows with a 1% backlog.
Indexes belong to whoever creates the table, which is the application's migrations, where Java
puts them too: `V0001__eventlog.sql`.

Two rules that outlive any one dialect:

- **Timestamp columns carry no zone**, and hold exactly the value written: the framework
  never shifts a time or gives it a zone, on the way in or out.
- **``model_id`` is a string.** Not every model is keyed by a UUID; typing it as one would
  exclude every aggregate keyed by an account number or an ISIN.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import (
    CHAR,
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine, UserDefinedType

__all__ = [
    "Converter",
    "JsonValue",
    "converted",
    "json_column",
    "notifications_table",
    "outbox_table",
    "scheduled_tasks_table",
    "sql_type",
    "timestamp_column",
    "uuid_column",
]


#: What a JSON column reads back: an object, an array, text, a number, a boolean or ``None``.
#:
#: The type a generated record gives every ``json_column()`` (and every other JSON column). A
#: number with a fraction reads as a ``Decimal`` on an engine this framework built - its JSON is
#: read that way so nothing is lost - and as a ``float`` on one it did not, so both are here.
#: Objects and arrays are ``Mapping`` and ``Sequence`` rather than ``dict`` and ``list`` so that a
#: ``dict[str, Decimal]`` of your own can be written into one without a cast.
type JsonValue = (
    Mapping[str, JsonValue] | Sequence[JsonValue] | str | int | float | Decimal | bool | None
)


class _Timestamp(TypeDecorator[datetime]):
    """A zone-less timestamp column with microseconds, on every dialect.

    Nothing more: a value is handed to the driver exactly as given and read back exactly as the
    driver returns it. No zone is added, removed or moved.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name in ("mysql", "mariadb"):
            from sqlalchemy.dialects import mysql

            # MySQL's bare DATETIME keeps whole seconds and silently drops the rest.
            return dialect.type_descriptor(mysql.DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime())


def timestamp_column() -> _Timestamp:
    """The type every timestamp column has: no zone, microsecond precision.

    What you write is what is stored and what you read back - the framework does not convert
    it. Write UTC (``SystemClock`` gives UTC with no zone attached) and keep the database
    session in UTC; see the "UTC everywhere" docs.

    The precision is not decoration. MySQL's bare ``DATETIME`` stores whole seconds and
    silently drops the rest, so a value written at 10:00:00.123456 reads back as 10:00:00 and
    every ordering derived from it is wrong in a way nothing reports.
    """
    return _Timestamp()


class _CanonicalUuid(TypeDecorator[uuid.UUID]):
    """A uuid stored as its canonical 36-character text, for dialects with no uuid type.

    SQLAlchemy's default for those is ``CHAR(32)`` - the hex digits with the hyphens stripped.
    That round-trips perfectly well within one application and is unreadable to everything
    else: a Java service writing ``d5143465-787a-...`` and a Python service writing
    ``d5143465787a...`` cannot join on ``model_id``, and neither notices, because both queries
    succeed and return nothing.
    """

    impl = CHAR(36)
    cache_ok = True

    def process_bind_param(self, value: object, dialect: object) -> str | None:
        return None if value is None else str(value)

    def process_result_value(self, value: object, dialect: object) -> uuid.UUID | None:
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def uuid_column() -> Uuid[uuid.UUID]:
    """The type a uuid column has: native where the dialect has one, canonical text where not."""
    return Uuid().with_variant(_CanonicalUuid(), "mysql", "mariadb")


def json_column() -> JSON:
    """The type a payload column has: ``jsonb`` on Postgres, ``json`` on MySQL and MariaDB.

    Not text holding JSON. The column type is part of what a consumer binds to, and jsonb is
    queryable where a text column is a string somebody has to parse first. The engine is given
    this library's serialiser, so what lands in it is the text Java's Ekbatan writes.

    ``None`` is SQL ``NULL``, as Java leaves it - not the JSON value ``null``, which reads back as
    ``None`` just the same and which a consumer's ``WHERE payload IS NULL`` does not find.
    """
    from sqlalchemy.dialects.postgresql import JSONB

    return JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class Converter[DbT, UserT](Protocol):
    """Turns a column's value into your own value and back. **Optional** - for type hints only.

    Any object with these two methods is a converter; it does not have to inherit from this.
    ``DbT`` is what the column gives (a ``Decimal`` from ``NUMERIC``, a ``str`` from text);
    ``UserT`` is your own class. ``None`` never reaches either method: a ``NULL`` stays ``None``.

        class MoneyConverter(Converter[Decimal, Money]):
            def from_db(self, value: Decimal) -> Money:
                return Money(value)

            def to_db(self, value: Money) -> Decimal:
                return value.amount
    """

    def from_db(self, value: DbT) -> UserT:
        """The column's value, as your own value."""
        ...

    def to_db(self, value: UserT) -> DbT:
        """Your own value, as the column's value."""
        ...


class _Same:
    """A cache-key part that is equal only to itself: the converter, by identity.

    Used for a converter that cannot be hashed (a plain ``@dataclass`` is one). It holds the
    converter, so the id cannot be reused while a cache entry still refers to it.
    """

    __slots__ = ("value",)

    def __init__(self, value: object) -> None:
        self.value = value

    def __hash__(self) -> int:
        return id(self.value)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Same) and other.value is self.value


class Converted[DbT, UserT](TypeDecorator[UserT]):
    """A column of type ``impl`` whose values pass through ``converter``. Built by
    :func:`converted`; see there."""

    impl: TypeEngine[Any] | type[TypeEngine[Any]] = TypeEngine  # replaced in __init__
    cache_ok = True

    def __init__(
        self, impl: TypeEngine[DbT] | type[TypeEngine[DbT]], converter: Converter[DbT, UserT]
    ) -> None:
        for method in ("from_db", "to_db"):
            if not callable(getattr(converter, method, None)):
                raise TypeError(
                    f"{converter!r} is not a converter: it has no {method}() method. A converter "
                    "is any object with from_db(value) and to_db(value)"
                )
        super().__init__()
        self.impl = impl() if isinstance(impl, type) else impl
        self._converter = converter
        # SQLAlchemy keys its statement cache on the attributes named after `__init__`'s
        # parameters (`cache_ok`'s documented rule): `impl`, and this. The converter itself when it
        # can be hashed - a plain class compares by identity, so two instances never share a
        # cached statement whatever state they hold - and by identity when it cannot (a plain
        # `@dataclass`). Keying on the converter's class alone would be wrong: `Scaled(2)` and
        # `Scaled(4)` would share one statement's bind processing.
        try:
            hash(converter)
            self.converter: object = converter
        except TypeError:
            self.converter = _Same(converter)

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        return None if value is None else self._converter.to_db(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        return None if value is None else self._converter.from_db(value)

    def __repr__(self) -> str:
        return f"converted({self.impl_instance!r}, {self._converter!r})"


def converted[DbT, UserT](
    impl: TypeEngine[DbT] | type[TypeEngine[DbT]], converter: Converter[DbT, UserT]
) -> Converted[DbT, UserT]:
    """A column type that keeps the database type ``impl`` and hands you your own values.

    ``converter`` is any object with ``from_db`` and ``to_db``; ``None`` (SQL ``NULL``) is
    passed through and never reaches it. Comparisons convert too: ``column == Money("9.99")``
    and ``column.op("<->")(value)`` send ``to_db(value)``.

        sqlalchemy.Column("amount", converted(sqlalchemy.NUMERIC(19, 4), MoneyConverter()))
        sqlalchemy.Column("embedding", converted(sql_type("vector(3)"), VectorConverter()))
    """
    return Converted(impl, converter)


class _SqlType(UserDefinedType[Any]):
    """A column type given as the database spells it - ``vector(3)``, ``xml``, ``inet6``."""

    cache_ok = True

    def __init__(self, spelling: str) -> None:
        self.spelling = spelling

    def get_col_spec(self, **kw: Any) -> str:
        return self.spelling

    def __repr__(self) -> str:
        return f"sql_type({self.spelling!r})"


def sql_type(spelling: str) -> TypeEngine[Any]:
    """A column type SQLAlchemy has no class for, written as the database spells it.

    ``create_all`` builds the column with exactly this text, so ``sql_type("vector(3)")`` makes a
    ``vector(3)`` column. Values go in and come out as the database driver gives them - usually
    text; wrap it in :func:`converted` to get your own class.
    """
    return _SqlType(spelling)


def outbox_table(metadata: MetaData, *, name: str = "events", schema: str | None = None) -> Table:
    """``eventlog.events``: one row per emitted event, plus one per unit of work that emitted
    none."""
    return Table(
        name,
        metadata,
        Column("id", uuid_column(), primary_key=True),
        Column("namespace", String(255), nullable=False),
        Column("action_id", uuid_column(), nullable=False),
        Column("action_name", String(255), nullable=False),
        # Not null: every row carries the full input of the unit of work that produced it, so
        # one row is a complete audit record without a join - and so a consumer can read a
        # value the domain event deliberately does not carry.
        Column("action_params", json_column(), nullable=False),
        Column("started_date", timestamp_column(), nullable=False),
        Column("completion_date", timestamp_column(), nullable=False),
        Column("model_id", String(255)),
        Column("model_type", String(255)),
        Column("event_type", String(255)),
        Column("payload", json_column()),
        Column("event_date", timestamp_column(), nullable=False),
        # The outbox's only mutable column, and it belongs to the consumer. A producer that
        # flips it loses the event for every consumer that had not yet read it.
        Column("delivered", Boolean, nullable=False),
        schema=schema,
    )


def notifications_table(
    metadata: MetaData, *, name: str = "event_notifications", schema: str | None = None
) -> Table:
    """``eventlog.event_notifications``: one row per (event, subscriber).

    The event's context is denormalised onto it so that dispatch never has to read the events
    table again - and so a handler's envelope is one row.

    Uniqueness on ``(event_id, handler_name)`` is what makes fan-out safe to re-run. Fan-out is
    not transactional with dispatch, so it will pass over rows it has already seen; the
    constraint is what stops that from doubling the work, and it belongs in the schema rather
    than in a fan-out that tries to remember.
    """
    return Table(
        name,
        metadata,
        Column("id", uuid_column(), primary_key=True),
        Column("event_id", uuid_column(), nullable=False),
        Column("handler_name", String(255), nullable=False),
        Column("namespace", String(255), nullable=False),
        Column("action_id", uuid_column(), nullable=False),
        Column("action_name", String(255), nullable=False),
        Column("action_params", json_column(), nullable=False),
        Column("started_date", timestamp_column(), nullable=False),
        Column("completion_date", timestamp_column(), nullable=False),
        Column("model_id", String(255)),
        Column("model_type", String(255)),
        Column("event_type", String(255), nullable=False),
        Column("payload", json_column()),
        Column("event_date", timestamp_column(), nullable=False),
        Column("state", String(24), nullable=False),
        Column("attempts", Integer, nullable=False, default=0),
        Column("next_retry_at", timestamp_column(), nullable=False),
        Column("created_date", timestamp_column(), nullable=False),
        Column("updated_date", timestamp_column(), nullable=False),
        UniqueConstraint("event_id", "handler_name", name=f"uq_{name}_event_handler"),
        schema=schema,
    )


def scheduled_tasks_table(
    metadata: MetaData, *, name: str = "scheduled_tasks", schema: str | None = None
) -> Table:
    """``scheduled_tasks``: db-scheduler's table, defined by db-scheduler.

    This used to be a second, hand-written definition of the same table, and the two had already
    drifted: it declared ``varchar(255)`` keys where db-scheduler uses 100, and it had no
    ``priority`` column at all - which db-scheduler writes on every insert. A row could not have
    been written into it.

    So it delegates. One definition, in the module that owns the table, and a Java service and a
    Python service still coordinate through one shape.

    ``schema`` is applied afterwards because db-scheduler's builder does not take one: Ekbatan
    supports schema-qualified tables and db-scheduler has no need to.
    """
    from db_scheduler.jdbc import scheduled_tasks_table as db_scheduler_table

    table = db_scheduler_table(metadata, name)
    if schema is not None:
        table.schema = schema
    return table
