"""Area N5 (c): data written by one port and read by the other through the same tables.

Usage: probe_c.py write <d>   - Python executes PriceAction at a fixed clock; prints the stored text
       probe_c.py read <d>    - Python fans out and handles the rows Java wrote; prints what it got
Twin of ParityN5ProbeTest#c2_writeJava / #c1_readPython.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as dtime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DEFAULT_SHARD,
    EVENTS,
    NOTIFS,
    Action,
    DatabaseRegistry,
    PricedEvent,
    ShardedId,
    ShardIdentifier,
    TransactionManager,
    Widget,
    WidgetBuilder,
    WidgetCreatedEvent,
    WidgetRepository,
    WidgetState,
    cfg,
    engine_for,
    executor_for,
    p,
    quiet,
    raw_engine,
    scalar,
    truncate,
    yn,
)

from ekbatan import _json  # noqa: E402
from ekbatan.clock import VirtualClock  # noqa: E402
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob  # noqa: E402
from ekbatan.events.config import LocalEventHandlerConfig  # noqa: E402
from ekbatan.sharding import read_shard  # noqa: E402

MODE, D = sys.argv[1], sys.argv[2]
RAW = raw_engine(D)
T1 = datetime(2026, 3, 29, 1, 30, 0, 123456, tzinfo=UTC)
REF = UUID("123e4567-e89b-12d3-a456-426614174000")
OWNER = UUID("01a14c40-e6a9-7bdc-8000-cdb4562ddd98")


@dataclass(frozen=True, kw_only=True)
class PriceParams:
    shard: ShardIdentifier
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
    owner: ShardedId[Widget]


def price_params() -> PriceParams:
    return PriceParams(
        shard=DEFAULT_SHARD,
        amount=Decimal("10.50"),
        tiny=Decimal("0.0000001"),
        big=Decimal("12345678901234567890.123456789"),
        ratio=0.1,
        day=date(2026, 3, 29),
        at=datetime(2026, 3, 29, 1, 30, 0, 123456, tzinfo=UTC),
        local=datetime(2026, 3, 29, 2, 30, 0, 500000),
        clock=dtime(13, 45, 30, 250000),
        blob=bytes([0, 1, 2, 255]),
        ref=REF,
        owner=ShardedId["Widget"].of(OWNER),
    )


class PriceAction(Action[PriceParams, Widget]):
    def perform(self, principal: str | None, params: PriceParams) -> Widget:
        wid = ShardedId["Widget"].generate(params.shard)
        event = PricedEvent(
            model_id=str(wid.value),
            amount=params.amount,
            tiny=params.tiny,
            big=params.big,
            ratio=params.ratio,
            day=params.day,
            at=params.at,
            local=params.local,
            clock=params.clock,
            blob=params.blob,
            ref=params.ref,
        )
        w = (
            WidgetBuilder()
            .id(wid)
            .state(WidgetState.ACTIVE)
            .name("priced")
            .color("gold")
            .created_date(self.clock.now())
            .with_initial_version()
            .with_event(event)
            .build()
        )
        return self.plan.add(w)


def j(v) -> str:
    """A value printed as Java's toString prints its Java type (Instant, LocalDateTime...)."""
    return _json.dumps(v).strip('"')


def dec(d) -> str:
    if d is None:
        return "null"
    return f"{d} (scale {-d.as_tuple().exponent})"


def json_text(col: str) -> str:
    return f"{col}::text" if D == "pg" else f"cast({col} as char)"


def ts_text(col: str) -> str:
    if D == "pg":
        return f"to_char({col}, 'YYYY-MM-DD HH24:MI:SS.US')"
    return f"date_format({col}, '%Y-%m-%d %H:%i:%s.%f')"


def build(clock):
    tm = TransactionManager(DEFAULT_SHARD, engine_for(cfg(D, pool=3)))
    reg = DatabaseRegistry({DEFAULT_SHARD: tm})
    repo = WidgetRepository(reg)
    return reg, repo, executor_for(reg, repo, clock, PriceAction(clock))


def write() -> None:
    truncate(RAW, D)
    clock = VirtualClock(T1)
    reg, repo, ex = build(clock)
    ex.execute(PriceAction, price_params(), principal="tester")
    k = f"c.{D}.python_wrote."
    p(k + "payload_text", scalar(RAW, f"select {json_text('payload')} from eventlog.events where event_type = 'PricedEvent'"))
    p(k + "params_text", scalar(RAW, f"select {json_text('action_params')} from eventlog.events where event_type = 'PricedEvent'"))
    p(
        k + "event_dates_text",
        scalar(
            RAW,
            f"select concat({ts_text('started_date')}, ' | ', {ts_text('completion_date')}, ' | ', {ts_text('event_date')}) "
            "from eventlog.events where event_type = 'PricedEvent'",
        ),
    )
    p(k + "widget_dates_text", scalar(RAW, f"select concat({ts_text('created_date')}, ' | ', {ts_text('updated_date')}) from widgets where name = 'priced'"))
    reg.close()


def read() -> None:
    clock = VirtualClock(T1 + timedelta(seconds=60))
    reg, repo, ex = build(clock)
    k = f"c.{D}.read_other."
    got = []

    class Reader:
        name = "n5-price-reader"
        event_type = PricedEvent

        def handle(self, env) -> None:
            got.append(env)

    handlers = EventHandlerRegistry.builder().with_handler(Reader()).build()
    conf = LocalEventHandlerConfig.defaults()
    EventFanoutJob.builder().registry(reg).handlers(handlers).events(EVENTS).notifications(NOTIFS).config(conf).clock(clock).build().fanout.drain_once()
    EventHandlingJob.builder().registry(reg).handlers(handlers).notifications(NOTIFS).config(conf).clock(clock).build().dispatch.drain_once()
    p(k + "notification", scalar(RAW, "select concat(state, '/', attempts) from eventlog.event_notifications where handler_name = 'n5-price-reader'"))
    if not got:
        p(k + "handled", "no")
        reg.close()
        return
    env = got[0]
    e = env.event
    p(k + "event.amount", dec(e.amount))
    p(k + "event.tiny", dec(e.tiny))
    p(k + "event.big", dec(e.big))
    p(k + "event.ratio", repr(e.ratio))
    p(k + "event.day", j(e.day))
    p(k + "event.at", j(e.at))
    p(k + "event.local", j(e.local))
    p(k + "event.clock", j(e.clock))
    p(k + "event.blob", e.blob.hex())
    p(k + "event.ref", e.ref)
    p(k + "event.model_id_matches_column", yn(e.model_id == env.model_id))
    p(k + "envelope.dates", f"{j(env.started_date)} | {j(env.completion_date)} | {j(env.event_date)}")
    p(k + "envelope.model_type", env.model_type)
    try:
        params = _json.decode(PriceParams, env.action_params)
        p(k + "params.decoded", "yes")
        p(k + "params.amount", dec(params.amount))
        p(k + "params.big", dec(params.big))
        p(k + "params.at", j(params.at))
        p(k + "params.owner", params.owner)
    except Exception as ex_:  # noqa: BLE001
        p(k + "params.decoded", f"no: {type(ex_).__name__}: {ex_}")
    w = next(x for x in repo.all() if x.name == "priced")
    p(k + "widget.created_date", j(w.created_date))
    p(k + "widget.updated_date", j(w.updated_date))
    p(k + "widget.id_shard", f"{read_shard(w.id.value).group}:{read_shard(w.id.value).member}")
    reg.close()


if __name__ == "__main__":
    quiet()
    {"write": write, "read": read}[MODE]()
_ = WidgetCreatedEvent
