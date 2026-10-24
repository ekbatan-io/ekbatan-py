"""The wallet application's tables.

Written by hand here so the example is readable in one place. In a real project this file is
generated from the database - ``python -m ekbatan.schemagen`` - and regenerated when a
migration lands, so the Python and the schema cannot drift.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, overload
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    Column,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)

from ekbatan.schema import (
    JsonValue,
    json_column,
    notifications_table,
    outbox_table,
    scheduled_tasks_table,
    timestamp_column,
    uuid_column,
)
from ekbatan.time import Instant

metadata = MetaData()

WALLETS = Table(
    "wallets",
    metadata,
    Column("id", uuid_column(), primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("owner_id", uuid_column(), nullable=False),
    Column("currency", CHAR(3), nullable=False),
    Column("balance", Numeric(19, 4), nullable=False),
    Column("created_date", timestamp_column(), nullable=False),
    Column("updated_date", timestamp_column(), nullable=False),
    # Two columns that exist only so the repository suite has something to catch with.
    # Java's `Dummy` fixture carries the same pair for the same reasons.
    #
    # `aliases` is a value that can only reach the database through a type mapper - a write
    # path binding from the value's runtime class rather than from the declared field type
    # would discard it, and nothing else here would notice.
    #
    # `reward_points` is nullable on purpose and nothing may default it: a batch write in
    # which every row leaves it null is what exposes an untyped NULL bind, which some drivers
    # refuse outright because they cannot infer the parameter's type.
    Column("aliases", json_column()),
    Column("reward_points", Integer),
)


class WalletsRecord(dict[str, Any]):
    """A row of `wallets`, written by hand as schemagen would write it."""

    __slots__ = ()

    def __init__(
        self,
        *,
        id: UUID,
        version: int,
        state: str,
        owner_id: UUID,
        currency: str,
        balance: Decimal,
        created_date: Instant,
        updated_date: Instant,
        aliases: JsonValue | None,
        reward_points: int | None,
    ) -> None:
        super().__init__(
            id=id,
            version=version,
            state=state,
            owner_id=owner_id,
            currency=currency,
            balance=balance,
            created_date=created_date,
            updated_date=updated_date,
            aliases=aliases,
            reward_points=reward_points,
        )

    if TYPE_CHECKING:

        @overload  # type: ignore[override]
        def __getitem__(self, key: Literal["id"]) -> UUID: ...
        @overload
        def __getitem__(self, key: Literal["version"]) -> int: ...
        @overload
        def __getitem__(self, key: Literal["state"]) -> str: ...
        @overload
        def __getitem__(self, key: Literal["owner_id"]) -> UUID: ...
        @overload
        def __getitem__(self, key: Literal["currency"]) -> str: ...
        @overload
        def __getitem__(self, key: Literal["balance"]) -> Decimal: ...
        @overload
        def __getitem__(self, key: Literal["created_date"]) -> Instant: ...
        @overload
        def __getitem__(self, key: Literal["updated_date"]) -> Instant: ...
        @overload
        def __getitem__(self, key: Literal["aliases"]) -> JsonValue | None: ...
        @overload
        def __getitem__(self, key: Literal["reward_points"]) -> int | None: ...
        def __getitem__(self, key: str) -> Any: ...

    def column_id(self) -> UUID:
        return self["id"]

    def column_version(self) -> int:
        return self["version"]

    def column_state(self) -> str:
        return self["state"]

    def column_owner_id(self) -> UUID:
        return self["owner_id"]

    def column_currency(self) -> str:
        return self["currency"]

    def column_balance(self) -> Decimal:
        return self["balance"]

    def column_created_date(self) -> Instant:
        return self["created_date"]

    def column_updated_date(self) -> Instant:
        return self["updated_date"]

    def column_aliases(self) -> JsonValue | None:
        return self["aliases"]

    def column_reward_points(self) -> int | None:
        return self["reward_points"]


NOTIFICATIONS = Table(
    "notifications",
    metadata,
    Column("id", uuid_column(), primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("wallet_id", uuid_column(), nullable=False),
    Column("kind", String(48), nullable=False),
    Column("recipient", String(255), nullable=False),
    Column("message", Text),
)


class NotificationsRecord(dict[str, Any]):
    """A row of `notifications`."""

    __slots__ = ()

    def __init__(
        self,
        *,
        id: UUID,
        version: int,
        state: str,
        wallet_id: UUID,
        kind: str,
        recipient: str,
        message: str | None,
    ) -> None:
        super().__init__(
            id=id,
            version=version,
            state=state,
            wallet_id=wallet_id,
            kind=kind,
            recipient=recipient,
            message=message,
        )

    if TYPE_CHECKING:

        @overload  # type: ignore[override]
        def __getitem__(self, key: Literal["id"]) -> UUID: ...
        @overload
        def __getitem__(self, key: Literal["version"]) -> int: ...
        @overload
        def __getitem__(self, key: Literal["state"]) -> str: ...
        @overload
        def __getitem__(self, key: Literal["wallet_id"]) -> UUID: ...
        @overload
        def __getitem__(self, key: Literal["kind"]) -> str: ...
        @overload
        def __getitem__(self, key: Literal["recipient"]) -> str: ...
        @overload
        def __getitem__(self, key: Literal["message"]) -> str | None: ...
        def __getitem__(self, key: str) -> Any: ...

    def column_id(self) -> UUID:
        return self["id"]

    def column_version(self) -> int:
        return self["version"]

    def column_state(self) -> str:
        return self["state"]

    def column_wallet_id(self) -> UUID:
        return self["wallet_id"]

    def column_kind(self) -> str:
        return self["kind"]

    def column_recipient(self) -> str:
        return self["recipient"]

    def column_message(self) -> str | None:
        return self["message"]


#: The framework's own tables. Their shape is a contract - a Debezium connector, a CDC
#: pipeline, or another language's Ekbatan service binds to these column names - so they are
#: built rather than transcribed.
EVENTS = outbox_table(metadata)
EVENT_NOTIFICATIONS = notifications_table(metadata)
SCHEDULED_TASKS = scheduled_tasks_table(metadata)


def in_sql(table: Table, dialect: str) -> str:
    """The table's name as hand-written SQL has to spell it on ``dialect``. A ``text()``
    statement gets no ``schema_translate_map``, so the event tables are ``eventlog.events`` on
    PostgreSQL and plain ``events`` everywhere else (ekbatan.schema.eventlog_translation)."""
    from ekbatan.schema import eventlog_translation

    translation = eventlog_translation(dialect) or {}
    return table.name if table.schema in translation else table.fullname
