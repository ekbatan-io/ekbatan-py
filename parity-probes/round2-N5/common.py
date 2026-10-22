"""Shared pieces of area N5's Python probes: the three databases, the tables Java's
local-event-handler integration tests migrate, and a Widget model shaped as Java's.

The tables are created by `setup.py` from Java's own migration files, so both ports write and read
the very same columns.
"""

from __future__ import annotations

import dataclasses
import logging
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as dtime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Column, MetaData, String, Table, text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ekbatan import (  # noqa: E402
    Action,
    ActionExecutor,
    ActionRegistry,
    DatabaseRegistry,
    ModelRepository,
    RepositoryRegistry,
    SingleTableJsonEventPersister,
    engine_for,
)
from ekbatan.config import DataSourceConfig  # noqa: E402
from ekbatan.domain import Model, ModelBuilder, ModelEvent  # noqa: E402
from ekbatan.ids import ShardedId  # noqa: E402
from ekbatan.persistence import TransactionManager  # noqa: E402
from ekbatan.schema import (  # noqa: E402
    notifications_table,
    outbox_table,
    scheduled_tasks_table,
    timestamp_column,
    uuid_column,
)
from ekbatan.sharding import DEFAULT_SHARD, EmbeddedBitsShardingStrategy, ShardIdentifier  # noqa: E402

MARKER = "N5-s3cret-marker"
SCRATCH = Path(
    "/private/tmp/claude-501/-Users-mike-workspace-ekbatan/73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad"
)

PORTS = {"pg": 58601, "mysql": 58602, "mariadb": 58603}
SCHEMES = {"pg": "postgresql+psycopg", "mysql": "mysql+pymysql", "mariadb": "mariadb+pymysql"}


def url(dialect: str, db: str = "testdb") -> str:
    return f"{SCHEMES[dialect]}://localhost:{PORTS[dialect]}/{db}"


def cfg(dialect: str, db: str = "testdb", pool: int = 5, **extra: Any) -> DataSourceConfig:
    return DataSourceConfig(url=url(dialect, db), username="test", password=MARKER, pool_size=pool, **extra)


def p(key: str, value: object) -> None:
    text_ = str(value)
    assert MARKER not in text_, f"password leaked in {key}"
    print(f"PROBE {key}: {text_}", flush=True)


def yn(b: bool) -> str:
    return "yes" if b else "no"


def short(e: BaseException) -> str:
    """The exception's class and the first line of its message, as Java's probe prints them."""
    msg = str(e).strip().splitlines()[0] if str(e).strip() else ""
    return f"{type(e).__name__}: {msg}"[:200]


# ------------------------------------------------------------------ tables (Java's migrations)

MD = MetaData()
EVENTS = outbox_table(MD, schema="eventlog")
NOTIFS = notifications_table(MD, schema="eventlog")
SCHEDULED = scheduled_tasks_table(MetaData())
WIDGETS = Table(
    "widgets",
    MD,
    Column("id", uuid_column(), primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("name", String(255), nullable=False),
    Column("color", String(24), nullable=False),
    Column("created_date", timestamp_column(), nullable=False),
    Column("updated_date", timestamp_column(), nullable=False),
)


# ------------------------------------------------------------------ the model, as Java's Widget


class WidgetState(Enum):
    ACTIVE = "ACTIVE"
    DELETED = "DELETED"


class WidgetBuilder(ModelBuilder["Widget", "ShardedId[Widget]", "WidgetState"]):
    builds_ref = "common:Widget"

    def name(self, value: str) -> WidgetBuilder:
        return self.set(name=value)

    def color(self, value: str) -> WidgetBuilder:
        return self.set(color=value)


@dataclass(frozen=True, kw_only=True)
class WidgetCreatedEvent(ModelEvent["Widget"]):
    name: str
    color: str


@dataclass(frozen=True, kw_only=True)
class PricedEvent(ModelEvent["Widget"]):
    """Twin of the Java probe's PricedEvent: every awkward type in one payload."""

    amount: Decimal
    tiny: Decimal
    big: Decimal
    ratio: float
    day: date
    at: datetime
    local: datetime
    clock: dtime
    blob: bytes
    ref: UUID


@dataclass(frozen=True, kw_only=True)
class Widget(Model[ShardedId["Widget"], WidgetState, WidgetBuilder]):
    name: str
    color: str

    def validate(self) -> None:
        pass  # no rules beyond its types

    @classmethod
    def create(cls, shard: ShardIdentifier, name: str, color: str, now: datetime, *extra: ModelEvent) -> Widget:
        wid = ShardedId["Widget"].generate(shard)
        b = (
            WidgetBuilder()
            .id(wid)
            .state(WidgetState.ACTIVE)
            .name(name)
            .color(color)
            .created_date(now)
            .with_initial_version()
            .with_event(WidgetCreatedEvent(model_id=str(wid.value), name=name, color=color))
        )
        for e in extra:
            b = b.with_event(e)
        return b.build()


class WidgetRepository(ModelRepository[Widget, ShardedId[Widget]]):
    def __init__(self, registry: DatabaseRegistry) -> None:
        super().__init__(Widget, WIDGETS, registry, sharding=EmbeddedBitsShardingStrategy())

    def to_row(self, w: Widget) -> dict[str, Any]:
        return self.columns_by_field_name(w)

    def from_row(self, row: Any) -> Widget:
        return self.model_by_field_name(row)


# ------------------------------------------------------------------ actions


@dataclass(frozen=True, kw_only=True)
class CreateParams:
    shard: ShardIdentifier
    name: str
    color: str


class WidgetCreateAction(Action[CreateParams, Widget]):
    def perform(self, principal: str | None, params: CreateParams) -> Widget:
        return self.plan.add(Widget.create(params.shard, params.name, params.color, self.clock.now()))


@dataclass(frozen=True, kw_only=True)
class RenameParams:
    id: UUID
    name: str


class WidgetRenameAction(Action[RenameParams, Widget]):
    def __init__(self, widgets: WidgetRepository, clock: Any) -> None:
        super().__init__(clock)
        self.widgets = widgets

    def perform(self, principal: str | None, params: RenameParams) -> Widget:
        found = self.widgets.find(ShardedId["Widget"].of(params.id))
        if found is None:
            raise LookupError(f"widget {params.id} not visible to the action")
        return self.plan.update(found.copy().name(params.name).build())


class ThrowAction(Action[CreateParams, Widget]):
    def perform(self, principal: str | None, params: CreateParams) -> Widget:
        self.plan.add(Widget.create(params.shard, params.name, params.color, self.clock.now()))
        raise RuntimeError("action failed on purpose")


def executor_for(registry: DatabaseRegistry, widgets: WidgetRepository, clock: Any, *extra: Action) -> ActionExecutor:
    reg = ActionRegistry.builder().with_action(WidgetCreateAction(clock)).with_action(
        WidgetRenameAction(widgets, clock)
    ).with_action(ThrowAction(clock))
    for a in extra:
        reg = reg.with_action(a)
    return (
        ActionExecutor.builder()
        .namespace("test.local_event_handler")
        .database_registry(registry)
        .repository_registry(RepositoryRegistry.builder().with_model_repository(widgets).build())
        .action_registry(reg.build())
        .event_persister(SingleTableJsonEventPersister(registry, EVENTS))
        .clock(clock)
        .build()
    )


# ------------------------------------------------------------------ side connections


def raw_engine(dialect: str, db: str = "testdb"):
    """A plain SQLAlchemy engine for checks from outside the framework (autocommit)."""
    from sqlalchemy import create_engine

    return create_engine(
        url(dialect, db).replace("://", f"://test:{MARKER}@", 1),
        isolation_level="AUTOCOMMIT",
        pool_size=2,
    )


def scalar(engine, sql: str) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql)).scalar()


def truncate(engine, dialect: str) -> None:
    with engine.connect() as c:
        for t in ("eventlog.event_notifications", "eventlog.events", "widgets", "side_rows", "scheduled_tasks"):
            c.execute(text(f"delete from {t}"))


def threads_named() -> list[str]:
    return sorted(t.name for t in threading.enumerate())


def timed(fn) -> tuple[float, Any, BaseException | None]:
    t0 = time.monotonic()
    try:
        return time.monotonic() - t0, fn(), None
    except BaseException as e:  # noqa: BLE001
        return time.monotonic() - t0, None, e


def secs(x: float) -> str:
    """Seconds rounded to the nearest half second, so timing noise does not show in a diff."""
    return f"{round(x * 2) / 2:.1f}s"


def quiet() -> None:
    logging.basicConfig(level=logging.WARNING)


__all__ = [n for n in dir() if not n.startswith("__")]
_ = (dataclasses, UTC, DEFAULT_SHARD, TransactionManager, engine_for)
