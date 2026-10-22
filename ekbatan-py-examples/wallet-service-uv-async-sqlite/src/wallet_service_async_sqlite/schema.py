"""The tables.

Written by hand here so the whole example reads in one place. In a real project this file is
generated - `python -m ekbatan.schemagen` starts a container, runs the migrations into it and
reflects the result - so the Python and the database cannot drift.
`fastapi_wallet_rest_pg` is the example that does it that way.

Each table has its record beside it - `WALLETS` and `WalletsRecord` - written by hand here as
schemagen writes it: a `dict` with a key per column, built by keyword
(`WalletsRecord(id=..., ...)`), each column typed as it reads, `| None` where it may be NULL.
Read a column as `record["owner_id"]` or `record.column_owner_id()`; the `__getitem__`
overloads are for mypy only. It is what a repository's `to_record` returns and `from_record`
takes, so mypy checks the mapping against the table. To SQLAlchemy it is the `dict` an insert
binds.

The framework's own tables are built rather than transcribed: their shape is a contract that a
Debezium connector or another service binds to, so there is one definition to be right about.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, overload
from uuid import UUID

from sqlalchemy import CHAR, BigInteger, Column, MetaData, Numeric, String, Table, Text

from ekbatan.schema import (
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
)


class WalletsRecord(dict[str, Any]):
    """A row of `wallets`: what `to_record` writes and `from_record` reads."""

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


ALERTS = Table(
    "alerts",
    metadata,
    Column("id", uuid_column(), primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("wallet_id", uuid_column(), nullable=False),
    Column("recipient", String(255), nullable=False),
    Column("message", Text, nullable=False),
)


class AlertsRecord(dict[str, Any]):
    """A row of `alerts`."""

    __slots__ = ()

    def __init__(
        self,
        *,
        id: UUID,
        version: int,
        state: str,
        wallet_id: UUID,
        recipient: str,
        message: str,
    ) -> None:
        super().__init__(
            id=id,
            version=version,
            state=state,
            wallet_id=wallet_id,
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
        def __getitem__(self, key: Literal["recipient"]) -> str: ...
        @overload
        def __getitem__(self, key: Literal["message"]) -> str: ...
        def __getitem__(self, key: str) -> Any: ...

    def column_id(self) -> UUID:
        return self["id"]

    def column_version(self) -> int:
        return self["version"]

    def column_state(self) -> str:
        return self["state"]

    def column_wallet_id(self) -> UUID:
        return self["wallet_id"]

    def column_recipient(self) -> str:
        return self["recipient"]

    def column_message(self) -> str:
        return self["message"]


#: The outbox, the local handler's queue, and the scheduler's table.
EVENTS = outbox_table(metadata)
EVENT_NOTIFICATIONS = notifications_table(metadata)
SCHEDULED_TASKS = scheduled_tasks_table(metadata)

__all__ = [
    "ALERTS",
    "EVENTS",
    "EVENT_NOTIFICATIONS",
    "SCHEDULED_TASKS",
    "WALLETS",
    "AlertsRecord",
    "WalletsRecord",
    "metadata",
]
