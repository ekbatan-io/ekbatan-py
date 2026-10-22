"""An event's times reach the outbox as their declared kinds, and a wrong one saves nothing.

The JSON is written inside `executor.execute`, in the transaction that writes the rows. So a
time the writer refuses - a field declared plain `datetime`, an aware value in an `Instant`
field, a plain `datetime` in the action's params - fails the action, and neither the model's
row nor any outbox row is left behind. Both modes, every database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from ekbatan import DEFAULT_SHARD, Action
from ekbatan.domain import ModelEvent
from ekbatan.errors import ValidationError
from ekbatan.ids import ShardedId
from ekbatan.time import Instant, LocalDateTime, OffsetDateTime
from fixtures.domain import Wallet
from fixtures.executing import run
from modes import stager_twin

MODES = ("sync", "async")

TEHRAN = timezone(timedelta(hours=3, minutes=30))
WALL = datetime(2026, 3, 1, 9, 0)


@dataclass(frozen=True, kw_only=True)
class WalletScheduledEvent(ModelEvent["Wallet"]):
    planned_at: Instant
    opens_at: LocalDateTime
    told_at: OffsetDateTime


@dataclass(frozen=True, kw_only=True)
class WalletStampedPlainEvent(ModelEvent["Wallet"]):
    stamped_at: datetime


@dataclass(frozen=True)
class ScheduleParams:
    at: Instant | None = None
    plain: datetime | None = None
    told: Any = None


def _wallet(clock: Any) -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].generate(DEFAULT_SHARD),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        created_date=clock.instant(),
    )


class ScheduleAction(Action[ScheduleParams, Wallet]):
    def perform(self, principal: str | None, params: ScheduleParams) -> Wallet:
        wallet = _wallet(self.clock)
        return self.plan.add(
            wallet.evolve(
                emit=WalletScheduledEvent(
                    model_id=str(wallet.id),
                    planned_at=self.instant(),
                    opens_at=LocalDateTime(WALL),
                    told_at=OffsetDateTime(WALL.replace(tzinfo=TEHRAN)),
                )
            )
        )


class AwareInstantAction(Action[ScheduleParams, Wallet]):
    def perform(self, principal: str | None, params: ScheduleParams) -> Wallet:
        wallet = _wallet(self.clock)
        return self.plan.add(
            wallet.evolve(
                emit=WalletScheduledEvent(
                    model_id=str(wallet.id),
                    planned_at=Instant(WALL.replace(tzinfo=TEHRAN)),
                    opens_at=LocalDateTime(WALL),
                    told_at=OffsetDateTime(WALL.replace(tzinfo=TEHRAN)),
                )
            )
        )


class PlainTimeEventAction(Action[ScheduleParams, Wallet]):
    def perform(self, principal: str | None, params: ScheduleParams) -> Wallet:
        wallet = _wallet(self.clock)
        return self.plan.add(
            wallet.evolve(
                emit=WalletStampedPlainEvent(model_id=str(wallet.id), stamped_at=self.instant())
            )
        )


for _action in (ScheduleAction, AwareInstantAction, PlainTimeEventAction):
    stager_twin(_action)


def _nothing_saved(wallets: Any, outbox: Any) -> None:
    assert wallets.count() == 0, "the model's row was rolled back"
    assert outbox() == [], "and no outbox row was written"


def test_each_kind_reaches_the_outbox_as_java_writes_it(executor, wallets, outbox, clock):
    clock.pause_at(datetime(2026, 3, 1, 9, 30, 0, 120000))
    run(executor, ScheduleAction(executor.clock), ScheduleParams(at=clock.instant()))

    rows = [r for r in outbox() if r["event_type"] == "WalletScheduledEvent"]
    assert len(rows) == 1
    payload, params = rows[0]["payload"], rows[0]["action_params"]
    assert payload["plannedAt"] == "2026-03-01T09:30:00.120Z", "an Instant: Java's Z"
    assert payload["opensAt"] == "2026-03-01T09:00:00", "a LocalDateTime: no zone"
    assert payload["toldAt"] == "2026-03-01T09:00:00+03:30", "an OffsetDateTime: its offset"
    assert params == {"at": "2026-03-01T09:30:00.120Z", "plain": None, "told": None}


@pytest.mark.parametrize(
    ("action", "params", "message"),
    [
        (
            "PlainTimeEventAction",
            ScheduleParams(),
            "event WalletStampedPlainEvent field stamped_at is a plain datetime; declare it "
            "Instant, LocalDateTime or OffsetDateTime",
        ),
        (
            "AwareInstantAction",
            ScheduleParams(),
            "event WalletScheduledEvent field planned_at is declared Instant but holds an aware",
        ),
        (
            "ScheduleAction",
            ScheduleParams(plain=WALL),
            "dataclass ScheduleParams field plain is a plain datetime",
        ),
        (
            "ScheduleAction",
            ScheduleParams(told=WALL),
            "dataclass ScheduleParams field told is a datetime nothing declares a kind for",
        ),
    ],
    ids=["plain-event-field", "aware-instant", "plain-params-field", "untyped-params-field"],
)
def test_a_refused_time_fails_the_action_and_saves_nothing(
    executor, wallets, outbox, action, params, message
):
    # By name: under the async run the harness puts each action's twin in its place.
    with pytest.raises(ValidationError) as raised:
        run(executor, globals()[action](executor.clock), params)
    assert message in str(raised.value)
    _nothing_saved(wallets, outbox)
