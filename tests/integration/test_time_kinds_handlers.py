"""Each time kind comes back to a handler as it went in: action, outbox, fan-out, dispatch, decode.

The action declares an `Instant`, a `LocalDateTime` and an `OffsetDateTime` on its event; the
outbox writes each as its kind (`Z`, no zone, the offset); fan-out and dispatch carry the row to
a handler, which gets the event rebuilt from that JSON. The instant comes back naive with the
same digits, the wall time naive, the offset time aware with its offset - nothing converted on
the way. The executor runs both modes; fan-out and dispatch are sync in both (they run on
threads), so an async run hands them a sync registry over the same database. Handlers run sync
and async.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

import ekbatan.persistence as sync_persistence
from ekbatan import DEFAULT_SHARD, Action
from ekbatan.config import ShardGroupConfig, ShardingConfig, ShardMemberConfig
from ekbatan.domain import ModelEvent
from ekbatan.events import EventDispatch, EventFanout, EventHandlerRegistry
from ekbatan.ids import ShardedId
from ekbatan.time import Instant, LocalDateTime, OffsetDateTime
from fixtures.domain import Wallet
from fixtures.executing import run
from fixtures.handlers import RecordingHandler, of_kind
from fixtures.logins import data_source
from modes import stager_twin

MODES = ("sync", "async")
HANDLER_KINDS = ("sync", "async")

TEHRAN = timezone(timedelta(hours=3, minutes=30))
#: Naive UTC with microseconds - what `SystemClock().instant()` gives.
AT = datetime(2026, 3, 1, 9, 30, 0, 123456)
WALL = datetime(2026, 3, 1, 9, 0, 0, 120000)
TOLD = datetime(2026, 3, 1, 13, 0, 0, 5000, TEHRAN)


@dataclass(frozen=True, kw_only=True)
class WalletTimedEvent(ModelEvent["Wallet"]):
    planned_at: Instant
    opens_at: LocalDateTime
    told_at: OffsetDateTime


@dataclass(frozen=True)
class TimedParams:
    at: Instant


class TimedAction(Action[TimedParams, Wallet]):
    def perform(self, principal: str | None, params: TimedParams) -> Wallet:
        wallet = Wallet.open(
            id=ShardedId[Wallet].generate(DEFAULT_SHARD),
            owner_id=uuid4(),
            currency="EUR",
            balance=Decimal("1.00"),
            created_date=self.clock.instant(),
        )
        return self.plan.add(
            wallet.evolve(
                emit=WalletTimedEvent(
                    model_id=str(wallet.id),
                    planned_at=params.at,
                    opens_at=LocalDateTime(WALL),
                    told_at=OffsetDateTime(TOLD),
                )
            )
        )


stager_twin(TimedAction)


@pytest.fixture
def sync_registry(registry: Any, backend: Any, mode: str) -> Iterator[Any]:
    """The registry fan-out and dispatch run on: the test's own in a sync run, a sync one over
    the same database in an async run. Reached through its module: an async run rebinds this
    module's own names to their async twins (tests/modes.py)."""
    if mode == "sync":
        yield registry
        return
    config = ShardingConfig.builder().default_shard(DEFAULT_SHARD)
    built = sync_persistence.DatabaseRegistry.from_config(
        config.groups(
            [
                ShardGroupConfig.builder()
                .group(0)
                .name("group-0")
                .with_member(
                    ShardMemberConfig.builder()
                    .member(0)
                    .name("ekbatan_main")
                    .primary_config(data_source(backend.url("ekbatan_main"), pool_size=5))
                    .build()
                )
                .build()
            ]
        ).build()
    )
    try:
        yield built
    finally:
        built.close()


def test_each_kind_reaches_the_handler_as_it_was_written(
    executor, sync_registry, clock, handler_kind
):
    run(executor, TimedAction(executor.clock), TimedParams(at=Instant(AT)))

    recorder = RecordingHandler("timed", WalletTimedEvent)
    subscriptions = EventHandlerRegistry([of_kind(handler_kind, recorder)])
    fanout = EventFanout(
        sync_registry,
        subscriptions,
        clock=clock,
    )
    dispatch = EventDispatch(sync_registry, subscriptions, clock=clock)
    try:
        assert fanout.drain_once() == 1
        dispatch.drain_once()
    finally:
        dispatch.close()

    assert recorder.calls == 1
    event = recorder.received[0].event
    assert isinstance(event, WalletTimedEvent)

    assert event.planned_at == AT and event.planned_at.tzinfo is None, (
        "an Instant: naive, the same UTC digits"
    )
    assert event.opens_at == WALL and event.opens_at.tzinfo is None, "a LocalDateTime: naive"
    assert event.told_at == TOLD, "an OffsetDateTime: the same moment"
    assert event.told_at.utcoffset() == timedelta(hours=3, minutes=30), "with its own offset"
    assert (event.told_at.hour, event.told_at.microsecond) == (13, 5000), "and its own digits"
    assert recorder.received[0].action_params == {"at": "2026-03-01T09:30:00.123456Z"}
