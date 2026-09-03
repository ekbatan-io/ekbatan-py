from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from ekbatan import (
    Action,
    ActionExecutor,
    ExecutionConfig,
    Repository,
    ShardIdentifier,
)
from ekbatan.action.outbox import OutboxRecord
from ekbatan.domain import ModelEvent
from ekbatan.errors import CrossShardError, StaleRecordError, ValidationError
from fixtures.actions import (
    CreateWallet,
    CreateWalletParams,
    DepositMoney,
    DepositParams,
)
from fixtures.domain import NotificationId, Wallet, WalletId, WalletMoneyDeposited
from fixtures.schema import EVENTS

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)


# --------------------------------------------------------------------------- retry


class FlakyDeposit(Action[DepositParams, Wallet]):
    """Fails the first attempt the way a lost version race does, then behaves."""

    def __init__(self, wallets: Repository[Wallet, UUID], failures: int = 1) -> None:
        self.wallets = wallets
        self.remaining = failures
        self.attempts = 0

    def perform(self, params: DepositParams) -> Wallet:
        self.attempts += 1
        wallet = self.wallets.get(params.wallet_id)
        staged = self.plan.update(wallet.deposit(params.amount))
        if self.remaining:
            self.remaining -= 1
            raise StaleRecordError("someone else got there first")
        return staged


def _new_wallet(executor, balance="0.00"):
    return executor.execute(
        CreateWallet(),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal(balance)),
    )


def test_a_conflict_is_retried_once_by_default(executor, wallets, outbox):
    wallet = _new_wallet(executor)
    action = FlakyDeposit(wallets)

    executor.execute(
        action, DepositParams(wallet_id=wallet.id, amount=Decimal("10"), recipient="ada@x")
    )

    assert action.attempts == 2
    assert wallets.get(wallet.id).balance == Decimal("10.0000")


def test_a_retry_starts_from_an_empty_plan(executor, wallets, outbox):
    wallet = _new_wallet(executor)
    executor.execute(
        FlakyDeposit(wallets),
        DepositParams(wallet_id=wallet.id, amount=Decimal("10"), recipient="ada@x"),
    )

    deposits = [r for r in outbox() if r["event_type"] == "WalletMoneyDeposited"]
    assert len(deposits) == 1, (
        "the failed attempt's staged change must not be committed alongside the retry's"
    )
    assert wallets.get(wallet.id).balance == Decimal("10.0000")


def test_retries_can_be_switched_off(executor, wallets, outbox):
    wallet = _new_wallet(executor)
    action = FlakyDeposit(wallets)
    before = len(outbox())

    with pytest.raises(StaleRecordError):
        executor.execute(
            action,
            DepositParams(wallet_id=wallet.id, amount=Decimal("10"), recipient="ada@x"),
            config=ExecutionConfig.no_retry(),
        )

    assert action.attempts == 1
    assert wallets.get(wallet.id).balance == Decimal("0.0000")
    assert len(outbox()) == before


# ------------------------------------------------------------------------ sharding


def test_a_row_goes_to_the_shard_its_id_names(sharded, sharded_registry):
    executor, wallets = sharded

    executor.execute(
        CreateWallet(),
        CreateWalletParams(
            wallet_id=WalletId.generate(GLOBAL),
            owner_id=uuid4(),
            currency="EUR",
            initial_balance=Decimal("1.00"),
        ),
    )
    there = executor.execute(
        CreateWallet(),
        CreateWalletParams(
            wallet_id=WalletId.generate(MEXICO),
            owner_id=uuid4(),
            currency="MXN",
            initial_balance=Decimal("2.00"),
        ),
    )

    assert wallets.count(shard=GLOBAL) == 1
    assert wallets.count(shard=MEXICO) == 1
    assert wallets.find(there.id).currency == "MXN"
    assert _events_on(sharded_registry, GLOBAL) == 1
    assert _events_on(sharded_registry, MEXICO) == 1, (
        "events live on the same shard as the rows they describe, or the write spans two "
        "databases and the guarantee is gone"
    )


class CreateOnBothShards(Action[None, tuple[Wallet, Wallet]]):
    def perform(self, params: None) -> tuple[Wallet, Wallet]:
        return (
            self.plan.add(
                Wallet.open(
                    id=WalletId.generate(GLOBAL),
                    owner_id=uuid4(),
                    currency="EUR",
                    balance=Decimal("1.00"),
                )
            ),
            self.plan.add(
                Wallet.open(
                    id=WalletId.generate(MEXICO),
                    owner_id=uuid4(),
                    currency="MXN",
                    balance=Decimal("2.00"),
                )
            ),
        )


def test_a_unit_of_work_spanning_shards_is_refused_before_anything_is_written(
    sharded, sharded_registry
):
    executor, wallets = sharded

    with pytest.raises(CrossShardError, match="no cross-shard transaction"):
        executor.execute(CreateOnBothShards(), None)

    assert wallets.count(shard=GLOBAL) == 0
    assert wallets.count(shard=MEXICO) == 0
    assert _events_on(sharded_registry, GLOBAL) == 0
    assert _events_on(sharded_registry, MEXICO) == 0


def test_crossing_shards_can_be_opted_into(sharded, sharded_registry):
    executor, wallets = sharded

    executor.execute(CreateOnBothShards(), None, config=ExecutionConfig(allow_cross_shard=True))

    assert wallets.count(shard=GLOBAL) == 1
    assert wallets.count(shard=MEXICO) == 1
    assert _events_on(sharded_registry, GLOBAL) == 1
    assert _events_on(sharded_registry, MEXICO) == 1
    assert len(_action_ids(sharded_registry)) == 1, (
        "each shard commits on its own, but a consumer must still see it was one unit of work"
    )


def _events_on(registry, shard) -> int:
    with registry.manager(shard).connection(readonly=True) as conn:
        return int(conn.execute(select(func.count()).select_from(EVENTS)).scalar_one())


def _action_ids(registry) -> set:
    ids = set()
    for shard in registry.shards:
        with registry.manager(shard).connection(readonly=True) as conn:
            ids |= {r[0] for r in conn.execute(select(EVENTS.c.action_id))}
    return ids


# ---------------------------------------------------------------------- validation


def test_a_namespace_must_be_spellable_downstream(registry, wallets):
    for bad in ("my-service", "", "9lives", "a..b"):
        with pytest.raises(ValidationError, match="namespace"):
            ActionExecutor(namespace=bad, registry=registry, repositories=[wallets], events=EVENTS)
    ok = ActionExecutor(
        namespace="my_service.wallets", registry=registry, repositories=[wallets], events=EVENTS
    )
    assert ok.namespace == "my_service.wallets"


def test_two_event_types_with_one_name_are_refused(executor, wallets, outbox):
    @dataclass(frozen=True, kw_only=True)
    class Impostor(ModelEvent):
        pass

    Impostor.__name__ = "WalletMoneyDeposited"

    class EmitColliding(Action[None, None]):
        def perform(self, params: None) -> None:
            wallet = wallets.all()[0]
            self.plan.update(
                wallet.evolve(
                    emit=[
                        WalletMoneyDeposited(amount=Decimal("1"), new_balance=Decimal("1")),
                        Impostor(),
                    ]
                )
            )

    _new_wallet(executor)
    before = len(outbox())

    with pytest.raises(ValidationError, match="collision"):
        executor.execute(EmitColliding(), None)

    assert len(outbox()) == before


def test_staging_a_model_with_no_repository_says_so(registry, wallets, clock):
    executor = ActionExecutor(
        namespace="example.wallet",
        registry=registry,
        repositories=[wallets],
        events=EVENTS,
        clock=clock,
    )

    class WritesAnUnknownThing(Action[None, None]):
        def perform(self, params: None) -> None:
            from fixtures.domain import Notification

            self.plan.add(
                Notification.create(
                    id=NotificationId.random(),
                    wallet_id=WalletId.random(),
                    kind="X",
                    recipient="ada@x",
                )
            )

    with pytest.raises(ValidationError, match="no repository was given for Notification"):
        executor.execute(WritesAnUnknownThing(), None)


# ----------------------------------------------------------------------- extension


def test_the_event_store_can_be_replaced_without_touching_anything_else(
    registry, wallets, clock, outbox
):
    """Rule 7: extension without forking. The seam is inside the transaction."""
    written: list[OutboxRecord] = []

    class Recording:
        def persist(self, conn, record: OutboxRecord) -> None:
            written.append(record)

    executor = ActionExecutor(
        namespace="example.wallet",
        registry=registry,
        repositories=[wallets],
        outbox=Recording(),
        clock=clock,
    )
    wallet = _new_wallet(executor)
    executor.execute(
        DepositMoney(wallets),
        DepositParams(wallet_id=wallet.id, amount=Decimal("5"), recipient="ada@x"),
    )

    assert [r.action_name for r in written] == ["CreateWallet", "DepositMoney"]
    assert [e.event.event_type for e in written[1].events] == ["WalletMoneyDeposited"]
    assert outbox() == [], "nothing reached the default table"
    assert wallets.get(wallet.id).balance == Decimal("5.0000"), "the state change still committed"
