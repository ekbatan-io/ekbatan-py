"""The example's own test.

Lives with the example, runs separately from the framework's suite, and exists for one reason:
an example nobody runs is an example that rots. It asserts what the README claims, so a change
that makes the prose false makes this red.

    make

Each test is a coroutine run on a loop of its own (`drive`), with the service built, started and
closed around it - the async pools belong to the loop they are used on.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from ekbatan import ShardIdentifier
from ekbatan.clock import SystemClock, VirtualClock
from ekbatan.errors import ValidationError

from .actions import (
    CloseWalletAction,
    DepositMoneyAction,
    DepositParams,
    OpenWalletAction,
    OpenWalletParams,
    WalletIdParams,
)
from .app import WalletService, build
from .model import Wallet
from .schema import EVENT_NOTIFICATIONS, EVENTS, SCHEDULED_TASKS, WALLETS

Scenario = Callable[[WalletService], Awaitable[None]]


def drive(tmp_path: Path, scenario: Scenario) -> None:
    """Build the service, start its jobs, run `scenario` on a fresh loop, close it all."""

    async def main() -> None:
        service = build(
            {"WALLET_DATABASE_URL": f"sqlite:///{tmp_path / 'wallet.db'}"},
            clock=VirtualClock(SystemClock().now()),
        )
        # Started before any wallet exists, as a service is: the jobs poll from here on.
        service.start()
        try:
            await scenario(service)
        finally:
            await service.aclose()

    asyncio.run(main())


async def settled(service: WalletService, within: float = 15.0) -> None:
    """Wait until the handler has run for every deposit so far - the fan-out and handling jobs
    run in the background, as in a service. Every deposit fanned out, and no delivery waiting."""
    deadline = time.monotonic() + within
    while True:
        async with service.registry.default.connection(readonly=True) as conn:
            undelivered = (
                await conn.execute(
                    select(func.count())
                    .select_from(EVENTS)
                    .where(EVENTS.c.delivered.is_(False))
                    .where(EVENTS.c.event_type == "MoneyDepositedEvent")
                )
            ).scalar_one()
            waiting = (
                await conn.execute(
                    select(func.count())
                    .select_from(EVENT_NOTIFICATIONS)
                    .where(EVENT_NOTIFICATIONS.c.state.in_(("PENDING", "FAILED")))
                )
            ).scalar_one()
        if undelivered == 0 and waiting == 0:
            return
        if time.monotonic() > deadline:
            raise AssertionError(
                f"after {within}s: {undelivered} deposits not fanned out, {waiting} waiting"
            )
        await asyncio.sleep(0.05)


async def a_wallet(service: WalletService, balance: str = "100.00") -> Wallet:
    return await service.executor.execute(
        OpenWalletAction,
        OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal(balance)),
    )


async def deposit(service: WalletService, wallet: Wallet, amount: str) -> Wallet:
    return await service.executor.execute(
        DepositMoneyAction,
        DepositParams(wallet_id=wallet.id, amount=Decimal(amount), recipient="ada@example.com"),
    )


async def outbox(service: WalletService) -> list:
    async with service.registry.default.connection(readonly=True) as conn:
        return list(await conn.execute(select(EVENTS).order_by(EVENTS.c.event_date, EVENTS.c.id)))


def test_a_deposit_writes_the_balance_and_its_event_together(tmp_path: Path) -> None:
    async def scenario(service: WalletService) -> None:
        wallet = await a_wallet(service)

        await deposit(service, wallet, "25.50")

        assert (await service.wallets.get(wallet.id)).balance == Decimal("125.5000")
        deposited = [r for r in await outbox(service) if r.event_type == "MoneyDepositedEvent"]
        assert len(deposited) == 1
        # The payload's JSON: camelCase field names, decimals as JSON numbers (read back as
        # Decimal, scale kept).
        assert deposited[0].payload == {
            "modelId": str(wallet.id),
            "modelName": "Wallet",
            "amount": Decimal("25.50"),
            "newBalance": Decimal("125.5000"),
        }

    drive(tmp_path, scenario)


def test_the_handler_reacts_and_reads_what_the_event_does_not_carry(tmp_path: Path) -> None:
    """The README's claim: the recipient travels as an action parameter, not in the event."""

    async def scenario(service: WalletService) -> None:
        wallet = await a_wallet(service)
        await deposit(service, wallet, "10.00")

        await settled(service)

        alerts = await service.alerts.all()
        assert len(alerts) == 1
        assert alerts[0].recipient == "ada@example.com"
        assert "10.00" in alerts[0].message

        rows = await outbox(service)
        deposited = next(r for r in rows if r.event_type == "MoneyDepositedEvent")
        assert "recipient" not in deposited.payload, "the event stays clean"
        assert deposited.action_params["recipient"] == "ada@example.com"

    drive(tmp_path, scenario)


def test_writing_an_entity_emits_nothing_and_is_still_recorded(tmp_path: Path) -> None:
    async def scenario(service: WalletService) -> None:
        wallet = await a_wallet(service)
        await deposit(service, wallet, "1.00")
        await settled(service)

        alert_rows = [r for r in await outbox(service) if r.action_name == "RaiseAlertAction"]
        assert len(alert_rows) == 1
        assert alert_rows[0].event_type is None, "an entity's changes are not events"
        assert len(await service.alerts.all()) == 1, "and the row was still written"

    drive(tmp_path, scenario)


def test_closing_twice_changes_nothing_the_second_time(tmp_path: Path) -> None:
    async def scenario(service: WalletService) -> None:
        wallet = await a_wallet(service, "0.00")
        await service.executor.execute(CloseWalletAction, WalletIdParams(wallet_id=wallet.id))
        before = await service.wallets.get(wallet.id)

        await service.executor.execute(CloseWalletAction, WalletIdParams(wallet_id=wallet.id))

        assert (await service.wallets.get(wallet.id)).version == before.version
        closed = [r for r in await outbox(service) if r.event_type == "WalletClosedEvent"]
        assert len(closed) == 1, "the second close emitted nothing"

    drive(tmp_path, scenario)


def test_the_event_pipeline_runs_as_two_distributed_jobs_on_threads(tmp_path: Path) -> None:
    """Turning committed events into deliveries is work a cluster does once, not once per
    instance - so it is claimed through `scheduled_tasks` like any other job, sync, on threads,
    beside units of work that are awaited."""
    from ekbatan.events.jobs import FANOUT_JOB_NAME, HANDLING_JOB_NAME

    async def scenario(service: WalletService) -> None:
        async with service.registry.default.connection(readonly=True) as conn:
            registered = {r[0] for r in await conn.execute(select(SCHEDULED_TASKS.c.task_name))}
        assert {FANOUT_JOB_NAME, HANDLING_JOB_NAME} <= registered

        wallet = await a_wallet(service, "10.00")
        await deposit(service, wallet, "5.00")

        await settled(service)
        assert await service.alerts.find_for_wallet(wallet.id), "the handler ran, driven by the job"

    drive(tmp_path, scenario)


def test_a_rejected_deposit_leaves_nothing_behind(tmp_path: Path) -> None:
    async def scenario(service: WalletService) -> None:
        wallet = await a_wallet(service)
        before = len(await outbox(service))

        with pytest.raises(ValidationError, match="amount"):
            await deposit(service, wallet, "-1")

        assert (await service.wallets.get(wallet.id)).balance == Decimal("100.0000")
        assert len(await outbox(service)) == before

    drive(tmp_path, scenario)


def test_the_script_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The README promises the module runs as a script. This is that promise."""
    from .__main__ import serve

    asyncio.run(serve(build({"WALLET_DATABASE_URL": f"sqlite:///{tmp_path / 'script.db'}"})))

    printed = capsys.readouterr().out
    assert "open a wallet" in printed
    assert "alert for ada@example.com" in printed, "the jobs delivered the deposit"
    assert "what a consumer reads from the outbox" in printed
    assert "sentinel" in printed


# --------------------------------------------------------------- the repository's own queries


def test_wallets_are_found_by_owner_across_shards(tmp_path: Path) -> None:
    async def scenario(service: WalletService) -> None:
        owner = uuid4()
        for who in (owner, owner, uuid4()):
            await service.executor.execute(
                OpenWalletAction,
                OpenWalletParams(owner_id=who, currency="EUR", opening_balance=Decimal("1.00")),
            )

        assert len(await service.wallets.find_all_by_owner(owner)) == 2

    drive(tmp_path, scenario)


def test_a_domain_query_still_hides_deleted_rows(tmp_path: Path) -> None:
    async def scenario(service: WalletService) -> None:
        owner = uuid4()
        wallet = await service.executor.execute(
            OpenWalletAction,
            OpenWalletParams(owner_id=owner, currency="EUR", opening_balance=Decimal("1.00")),
        )
        await service.wallets.update(wallet.delete())

        assert await service.wallets.find_all_by_owner(owner) == []

    drive(tmp_path, scenario)


def test_open_wallets_running_low_can_be_listed(tmp_path: Path) -> None:
    async def scenario(service: WalletService) -> None:
        rich = await a_wallet(service, "500.00")
        poor = await a_wallet(service, "2.00")
        closed = await a_wallet(service, "1.00")
        await service.executor.execute(CloseWalletAction, WalletIdParams(wallet_id=closed.id))

        found = {w.id for w in await service.wallets.find_open_below(Decimal("10.00"))}

        assert found == {poor.id}, "the closed one is not open, and the rich one is not low"
        assert rich.id not in found

    drive(tmp_path, scenario)


def test_a_sum_across_shards_drops_to_sql(tmp_path: Path) -> None:
    async def scenario(service: WalletService) -> None:
        assert await service.wallets.total_balance() == Decimal("0"), "no rows is zero, not null"

        await a_wallet(service, "10.00")
        await a_wallet(service, "2.50")

        assert await service.wallets.total_balance() == Decimal("12.5000")

    drive(tmp_path, scenario)


def test_a_shard_can_be_asked_directly(tmp_path: Path) -> None:
    async def scenario(service: WalletService) -> None:
        wallet = await a_wallet(service)
        here = ShardIdentifier(0, 0)

        assert [w.id for w in await service.wallets.find_on_shard(here)] == [wallet.id]
        assert await service.wallets.exists_on_shard(here, wallet.id) is True

    drive(tmp_path, scenario)


def test_alerts_are_found_by_the_wallet_they_are_about(tmp_path: Path) -> None:
    async def scenario(service: WalletService) -> None:
        wallet = await a_wallet(service)
        await deposit(service, wallet, "5.00")
        await settled(service)

        found = await service.alerts.find_for_wallet(wallet.id)
        assert len(found) == 1
        assert found[0].recipient == "ada@example.com"

    drive(tmp_path, scenario)


def test_the_repository_writes_its_row_mapping(tmp_path: Path) -> None:
    """Every repository implements `to_row` and `from_row`, as of `EKB-0217`.

    This test used to assert the opposite - that neither was written, because the mapping was
    by field name unless you said otherwise. That default was the one piece of magic left in
    the read and write paths: rename a column without renaming the field and it silently
    stopped matching, with no error anywhere.
    """
    from .repository import WalletRepository

    assert "from_row" in vars(WalletRepository), "the mapping is written, not inherited"
    assert "to_row" in vars(WalletRepository)

    async def scenario(service: WalletService) -> None:
        wallet = await a_wallet(service, "3.00")
        assert (await service.wallets.get(wallet.id)).balance == Decimal("3.0000")

    drive(tmp_path, scenario)


def test_an_empty_wallet_can_be_erased_for_real(tmp_path: Path) -> None:
    async def scenario(service: WalletService) -> None:
        wallet = await a_wallet(service, "0.00")

        assert await service.wallets.erase(wallet.id) is True
        assert await service.wallets.find(wallet.id) is None
        async with service.registry.default.connection(readonly=True) as conn:
            remaining = (
                await conn.execute(select(WALLETS.c.id).where(WALLETS.c.id == wallet.id.value))
            ).first()
        assert remaining is None, "soft delete leaves the row; this one does not"

        assert await service.wallets.erase(wallet.id) is False, "erasing what is gone is fine"

    drive(tmp_path, scenario)


def test_a_wallet_with_money_in_it_is_not_erased(tmp_path: Path) -> None:
    async def scenario(service: WalletService) -> None:
        wallet = await a_wallet(service, "25.00")

        with pytest.raises(ValidationError, match="still holds 25"):
            await service.wallets.erase(wallet.id)

        assert (await service.wallets.get(wallet.id)).balance == Decimal("25.0000")

    drive(tmp_path, scenario)


def test_erasing_joins_a_transaction_the_caller_opened(tmp_path: Path) -> None:
    async def scenario(service: WalletService) -> None:
        wallet = await a_wallet(service, "0.00")

        with pytest.raises(RuntimeError):
            async with service.registry.default.transaction():
                await service.wallets.erase(wallet.id)
                raise RuntimeError("changed my mind")

        assert await service.wallets.find(wallet.id) is not None, "the rollback took the erase"

    drive(tmp_path, scenario)


def test_the_service_runs_on_the_settings_in_ekbatan_yaml(tmp_path: Path) -> None:
    """The database, the namespace and the handling job switched on all come from the file -
    one file for the async side and the sync one."""

    async def scenario(service: WalletService) -> None:
        assert service.executor.namespace == "example.wallet_service_async_sqlite"
        assert any("event-handling" in name for name in service.jobs._names_requiring_registration)
        assert service.registry.default.primary.url.database.endswith("wallet.db")
        assert service.registry.default.primary.url.drivername == "sqlite+aiosqlite"
        assert service.events_registry.default.primary.url.drivername == "sqlite"

    drive(tmp_path, scenario)
