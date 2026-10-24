from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from ekbatan import (
    Action,
    ActionExecutor,
    Clock,
    ExecutionConfiguration,
    ShardIdentifier,
)
from ekbatan.aio import AsyncAction
from ekbatan.domain import ModelEvent
from ekbatan.errors import CrossShardError, StaleRecordError, ValidationError
from ekbatan.ids import Id, ShardedId
from ekbatan.repository import AbstractRepository
from fixtures.actions import (
    WalletCreateAction,
    WalletDepositMoneyAction,
)
from fixtures.domain import NOW, Notification, Wallet, WalletMoneyDepositedEvent
from fixtures.executing import run
from fixtures.schema import EVENTS
from modes import stager_twin, twin

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)


# --------------------------------------------------------------------------- retry


class FlakyDepositAction(Action[WalletDepositMoneyAction.Params, Wallet]):
    """Fails the first attempt the way a lost version race does, then behaves."""

    def __init__(
        self, wallet_repository: AbstractRepository[Wallet, UUID], clock: Clock, failures: int = 1
    ) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository
        self.remaining = failures
        self.attempts = 0

    def perform(self, principal: str | None, params: WalletDepositMoneyAction.Params) -> Wallet:
        self.attempts += 1
        wallet = self.wallet_repository.get(params.wallet_id)
        staged = self.plan.update(wallet.deposit(params.amount))
        if self.remaining:
            self.remaining -= 1
            raise StaleRecordError("someone else got there first")
        return staged


@twin(FlakyDepositAction)
class AsyncFlakyDepositAction(AsyncAction[WalletDepositMoneyAction.Params, Wallet]):
    def __init__(self, wallet_repository: Any, clock: Clock, failures: int = 1) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository
        self.remaining = failures
        self.attempts = 0

    @property
    def name(self) -> str:
        return "FlakyDepositAction"

    async def perform(
        self, principal: str | None, params: WalletDepositMoneyAction.Params
    ) -> Wallet:
        self.attempts += 1
        wallet = await self.wallet_repository.get(params.wallet_id)
        staged = self.plan.update(wallet.deposit(params.amount))
        if self.remaining:
            self.remaining -= 1
            raise StaleRecordError("someone else got there first")
        return staged


def _new_wallet(executor, balance="0.00"):
    return run(
        executor,
        WalletCreateAction(executor.clock),
        WalletCreateAction.Params(
            owner_id=uuid4(), currency="EUR", initial_balance=Decimal(balance)
        ),
    )


def test_a_conflict_is_retried_once_by_default(executor, wallet_repository, outbox):
    wallet = _new_wallet(executor)
    action = FlakyDepositAction(wallet_repository, executor.clock)

    run(
        executor,
        action,
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("10"), recipient="ada@x"
        ),
    )

    assert action.attempts == 2
    assert wallet_repository.get(wallet.id).balance == Decimal("10.0000")


def test_a_retry_starts_from_an_empty_plan(executor, wallet_repository, outbox):
    wallet = _new_wallet(executor)
    run(
        executor,
        FlakyDepositAction(wallet_repository, executor.clock),
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("10"), recipient="ada@x"
        ),
    )

    deposits = [r for r in outbox() if r["event_type"] == "WalletMoneyDepositedEvent"]
    assert len(deposits) == 1, (
        "the failed attempt's staged change must not be committed alongside the retry's"
    )
    assert wallet_repository.get(wallet.id).balance == Decimal("10.0000")


def test_retries_can_be_switched_off(executor, wallet_repository, outbox):
    wallet = _new_wallet(executor)
    action = FlakyDepositAction(wallet_repository, executor.clock)
    before = len(outbox())

    with pytest.raises(StaleRecordError):
        run(
            executor,
            action,
            WalletDepositMoneyAction.Params(
                wallet_id=wallet.id, amount=Decimal("10"), recipient="ada@x"
            ),
            config=ExecutionConfiguration.no_retry(),
        )

    assert action.attempts == 1
    assert wallet_repository.get(wallet.id).balance == Decimal("0.0000")
    assert len(outbox()) == before


# ------------------------------------------------------------------------ sharding


def test_a_row_goes_to_the_shard_its_id_names(sharded, sharded_registry):
    executor, wallet_repository = sharded

    run(
        executor,
        WalletCreateAction(executor.clock),
        WalletCreateAction.Params(
            wallet_id=ShardedId[Wallet].generate(GLOBAL),
            owner_id=uuid4(),
            currency="EUR",
            initial_balance=Decimal("1.00"),
        ),
    )
    there = run(
        executor,
        WalletCreateAction(executor.clock),
        WalletCreateAction.Params(
            wallet_id=ShardedId[Wallet].generate(MEXICO),
            owner_id=uuid4(),
            currency="MXN",
            initial_balance=Decimal("2.00"),
        ),
    )

    assert wallet_repository.count(shard=GLOBAL) == 1
    assert wallet_repository.count(shard=MEXICO) == 1
    assert wallet_repository.find(there.id).currency == "MXN"
    assert _events_on(sharded_registry, GLOBAL) == 1
    assert _events_on(sharded_registry, MEXICO) == 1, (
        "events live on the same shard as the rows they describe, or the write spans two "
        "databases and the guarantee is gone"
    )


class CreateOnBothShardsAction(Action[None, tuple[Wallet, Wallet]]):
    def perform(self, principal: str | None, params: None) -> tuple[Wallet, Wallet]:
        return (
            self.plan.add(
                Wallet.open(
                    id=ShardedId[Wallet].generate(GLOBAL),
                    owner_id=uuid4(),
                    currency="EUR",
                    balance=Decimal("1.00"),
                    created_date=self.clock.instant(),
                )
            ),
            self.plan.add(
                Wallet.open(
                    id=ShardedId[Wallet].generate(MEXICO),
                    owner_id=uuid4(),
                    currency="MXN",
                    balance=Decimal("2.00"),
                    created_date=self.clock.instant(),
                )
            ),
        )


stager_twin(CreateOnBothShardsAction)


def test_a_unit_of_work_spanning_shards_is_refused_before_anything_is_written(
    sharded, sharded_registry
):
    executor, wallet_repository = sharded

    with pytest.raises(CrossShardError, match="no cross-shard transaction"):
        run(executor, CreateOnBothShardsAction(executor.clock), None)

    assert wallet_repository.count(shard=GLOBAL) == 0
    assert wallet_repository.count(shard=MEXICO) == 0
    assert _events_on(sharded_registry, GLOBAL) == 0
    assert _events_on(sharded_registry, MEXICO) == 0


def test_crossing_shards_can_be_opted_into(sharded, sharded_registry):
    executor, wallet_repository = sharded

    run(
        executor,
        CreateOnBothShardsAction(executor.clock),
        None,
        config=ExecutionConfiguration(allow_cross_shard=True),
    )

    assert wallet_repository.count(shard=GLOBAL) == 1
    assert wallet_repository.count(shard=MEXICO) == 1
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


def test_a_namespace_must_be_spellable_downstream(registry, wallet_repository):
    for bad in ("my-service", "", "9lives", "a..b"):
        with pytest.raises(ValidationError, match="namespace"):
            ActionExecutor(namespace=bad, registry=registry, repositories=[wallet_repository])
    ok = ActionExecutor(
        namespace="my_service.wallets", registry=registry, repositories=[wallet_repository]
    )
    assert ok.namespace == "my_service.wallets"


def test_two_event_types_with_one_name_are_refused(executor, wallet_repository, outbox):
    @dataclass(frozen=True, kw_only=True)
    class Impostor(ModelEvent["Wallet"]):
        pass

    Impostor.__name__ = "WalletMoneyDepositedEvent"

    _new_wallet(executor)
    wallet = wallet_repository.all()[0]

    class EmitColliding(Action[None, None]):
        def perform(self, principal: str | None, params: None) -> None:
            self.plan.update(
                wallet.evolve(
                    emit=[
                        WalletMoneyDepositedEvent(
                            model_id=str(wallet.id), amount=Decimal("1"), new_balance=Decimal("1")
                        ),
                        Impostor(model_id=str(wallet.id)),
                    ]
                )
            )

    before = len(outbox())

    with pytest.raises(ValidationError, match="collision"):
        run(executor, EmitColliding(executor.clock), None)

    assert len(outbox()) == before


def test_staging_a_model_with_no_repository_says_so(registry, wallet_repository, clock):
    executor = ActionExecutor(
        namespace="example.wallet",
        registry=registry,
        repositories=[wallet_repository],
        clock=clock,
    )

    class WritesAnUnknownThing(Action[None, None]):
        def perform(self, principal: str | None, params: None) -> None:

            self.plan.add(
                Notification.create(
                    id=Id[Notification].random(),
                    wallet_id=ShardedId[Wallet].random(),
                    kind="X",
                    recipient="ada@x",
                )
            )

    with pytest.raises(ValidationError, match="no repository is registered for Notification"):
        run(executor, WritesAnUnknownThing(executor.clock), None)


# ----------------------------------------------------------------------- extension


def test_the_event_store_can_be_replaced_without_touching_anything_else(
    registry, wallet_repository, clock, outbox, mode
):
    """Rule 7: extension without forking. The seam is inside the transaction."""
    written: list[tuple[str, list[ModelEvent]]] = []

    class Recording:
        def persist_action_events(
            self,
            namespace,
            action_name,
            started_date,
            completion_date,
            action_params,
            model_events,
            shard,
            action_event_id,
        ) -> None:
            written.append((action_name, list(model_events)))

    class AsyncRecording(Recording):
        async def persist_action_events(self, *args) -> None:  # type: ignore[override]
            super().persist_action_events(*args)

    executor = ActionExecutor(
        namespace="example.wallet",
        registry=registry,
        repositories=[wallet_repository],
        outbox=Recording() if mode == "sync" else AsyncRecording(),
        clock=clock,
    )
    wallet = _new_wallet(executor)
    run(
        executor,
        WalletDepositMoneyAction(wallet_repository, executor.clock),
        WalletDepositMoneyAction.Params(
            wallet_id=wallet.id, amount=Decimal("5"), recipient="ada@x"
        ),
    )

    assert [name for name, _ in written] == ["WalletCreateAction", "WalletDepositMoneyAction"]
    assert [e.event_type for e in written[1][1]] == ["WalletMoneyDepositedEvent"]
    assert outbox() == [], "nothing reached the default table"
    assert wallet_repository.get(wallet.id).balance == Decimal("5.0000"), (
        "the state change still committed"
    )


def test_a_partial_commit_is_reported_even_when_what_killed_it_is_not_an_exception(
    sharded, sharded_registry, caplog, monkeypatch, mode
):
    """Java's comment on this catch block is the whole reason it is a `Throwable`:

        Caught as Exception, an Error on shard B produced partially-committed data in silence.

    A cross-shard action that commits shard A and fails on shard B does not roll back. Java's
    `CRITICAL: ... PARTIALLY COMMITTED!` line, at ERROR with the exception, and its span
    attributes are the only notice anyone gets, so what killed the
    second shard must not decide whether the notice is written. `KeyboardInterrupt` between two
    commits is the realistic one - a deploy restarting a pod mid-action.
    """
    import logging

    executor, _ = sharded
    calls = {"n": 0}
    real = executor.__class__._commit_shard
    # On asyncio the realistic one is a cancelled task: the event loop itself stops on a
    # KeyboardInterrupt raised inside a task.
    killer = (
        KeyboardInterrupt("the pod is going away")
        if mode == "sync"
        else asyncio.CancelledError("the request was cancelled")
    )

    def second_shard_dies(self, shard, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise killer
        return real(self, shard, *args, **kwargs)

    monkeypatch.setattr(executor.__class__, "_commit_shard", second_shard_dies)

    with caplog.at_level(logging.ERROR), pytest.raises(type(killer)):
        run(
            executor,
            CreateOnBothShardsAction(executor.clock),
            None,
            config=ExecutionConfiguration(allow_cross_shard=True),
        )

    (notice,) = [r for r in caplog.records if "PARTIALLY COMMITTED" in r.message]
    assert notice.levelno == logging.ERROR, "Java logs it at ERROR"
    assert notice.message.startswith("CRITICAL: Cross-shard action CreateOnBothShardsAction ")
    assert notice.exc_info is not None, "with what killed the second shard attached"


def test_a_failure_before_any_shard_commits_is_not_called_a_partial_commit(
    sharded, caplog, monkeypatch
):
    """Java's `failure_before_any_shard_commits_is_not_flagged_as_partial`.

    The alarm is only worth having if it is quiet when nothing is wrong. A first shard that
    fails rolled itself back and left nothing behind - an ordinary failure, not a partial
    commit - and crying wolf here would teach everyone to ignore the one case that matters.
    """
    import logging

    executor, _ = sharded

    def first_shard_dies(self, shard, *args, **kwargs):
        raise RuntimeError("the very first shard failed")

    monkeypatch.setattr(executor.__class__, "_commit_shard", first_shard_dies)

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
        run(
            executor,
            CreateOnBothShardsAction(executor.clock),
            None,
            config=ExecutionConfiguration(allow_cross_shard=True),
        )

    assert not any("PARTIALLY COMMITTED" in r.message for r in caplog.records), (
        "nothing was committed, so there is nothing partial about it"
    )


@dataclass(frozen=True, kw_only=True)
class StaleOnMexico:
    """The id of a wallet on MEXICO, to be updated at a version it no longer has."""

    wallet_id: ShardedId[Wallet]


class CreateOnGlobalThenFailOnMexicoAction(Action[StaleOnMexico, None]):
    """Commits one shard, then fails the other - Java's `WalletCreateThenFailMultiShardAction`.

    The failure is an optimistic-lock miss rather than a thrown exception, because that is the
    realistic shape: the second shard's write is refused by the database, not by the action.
    """

    def __init__(self, wallet_repository, clock: Clock) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    def perform(self, principal: str | None, params: StaleOnMexico) -> None:
        self.plan.add(
            Wallet.open(
                id=ShardedId[Wallet].generate(GLOBAL),
                owner_id=uuid4(),
                currency="EUR",
                balance=Decimal("1.00"),
                created_date=self.clock.instant(),
            )
        )
        stale = self.wallet_repository.get(params.wallet_id)
        # Two versions behind, so the `WHERE version = ?` matches nothing.
        self.plan.update(stale.evolve(version=stale.version + 5).deposit(Decimal("1.00")))
        return None


@twin(CreateOnGlobalThenFailOnMexicoAction)
class AsyncCreateOnGlobalThenFailOnMexicoAction(AsyncAction[StaleOnMexico, None]):
    def __init__(self, wallet_repository: Any, clock: Clock) -> None:
        super().__init__(clock)
        self.wallet_repository = wallet_repository

    @property
    def name(self) -> str:
        return "CreateOnGlobalThenFailOnMexicoAction"

    async def perform(self, principal: str | None, params: StaleOnMexico) -> None:
        self.plan.add(
            Wallet.open(
                id=ShardedId[Wallet].generate(GLOBAL),
                owner_id=uuid4(),
                currency="EUR",
                balance=Decimal("1.00"),
                created_date=self.clock.instant(),
            )
        )
        stale = await self.wallet_repository.get(params.wallet_id)
        self.plan.update(stale.evolve(version=stale.version + 5).deposit(Decimal("1.00")))
        return None


def test_a_partial_commit_keeps_the_committed_shard_and_rolls_back_the_failing_one(
    sharded, sharded_registry, caplog
):
    """Java's `cross_shard_partial_commit_keeps_committed_shard_and_rolls_back_the_failing_one`.

    The property the alarm is *about*, and the one an operator acts on. There is no cross-shard
    rollback and the framework does not pretend otherwise: the shard that committed stays
    committed, and only the shard that failed is undone. Opting into `allow_cross_shard` is
    opting into exactly this.
    """
    import logging

    executor, wallet_repository = sharded

    # Placed directly rather than through an action: `WalletCreateAction` mints its id with
    # `ShardedId[Wallet].random()`, which lands on the default shard, and this needs one that
    # does not.
    on_mexico = wallet_repository.add(
        Wallet.open(
            id=ShardedId[Wallet].generate(MEXICO),
            owner_id=uuid4(),
            currency="MXN",
            balance=Decimal("5.00"),
            created_date=NOW,
        )
    )
    assert wallet_repository.shard_of(on_mexico) == MEXICO

    before_global = wallet_repository.count(shard=GLOBAL)
    before_mexico = wallet_repository.count(shard=MEXICO)

    # Retries off, so the action runs exactly once and the count arithmetic is unambiguous.
    config = ExecutionConfiguration(allow_cross_shard=True, retry_configs=())

    with caplog.at_level(logging.ERROR), pytest.raises(StaleRecordError):
        run(
            executor,
            CreateOnGlobalThenFailOnMexicoAction(wallet_repository, executor.clock),
            StaleOnMexico(wallet_id=on_mexico.id),
            config=config,
        )

    assert wallet_repository.count(shard=GLOBAL) == before_global + 1, (
        "the shard that committed stays committed - there is no cross-shard rollback"
    )
    assert wallet_repository.count(shard=MEXICO) == before_mexico, (
        "and the failing shard was undone"
    )
    assert any("PARTIALLY COMMITTED" in r.message for r in caplog.records), (
        "and the only notice anyone gets was written"
    )


# --------------------------------------------------------------------------- inside a transaction


def test_an_action_run_inside_an_open_transaction_commits_on_its_own(
    executor, registry, wallet_repository, outbox
):
    """Java's executor opens its own transaction (`inTransactionChecked`) on its own connection,
    so an action run inside a transaction the caller opened commits by itself and survives the
    caller's rollback. Joined, it vanished with the caller's transaction."""
    with pytest.raises(RuntimeError), registry.default.transaction():
        wallet = _new_wallet(executor, "5.00")
        raise RuntimeError("the caller's transaction fails")

    assert wallet_repository.get(wallet.id).balance == Decimal("5.0000")
    assert [row["action_name"] for row in outbox()] == ["WalletCreateAction"]
