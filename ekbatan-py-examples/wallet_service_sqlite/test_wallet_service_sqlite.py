"""The example's own test.

Lives with the example, runs separately from the framework's suite, and exists for one reason:
an example nobody runs is an example that rots. It asserts what the README claims, so a change
that makes the prose false makes this red.

    uv run pytest ekbatan-py-examples
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

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
from .schema import EVENTS, SCHEDULED_TASKS, WALLETS


@pytest.fixture
def service(tmp_path: Path) -> Iterator[WalletService]:
    built = build(f"sqlite:///{tmp_path / 'wallet.db'}", clock=VirtualClock(SystemClock().now()))
    try:
        yield built
    finally:
        built.close()


def a_wallet(service: WalletService, balance: str = "100.00"):
    return service.executor.execute(
        OpenWalletAction(service.executor.clock),
        OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal(balance)),
    )


def outbox(service: WalletService) -> list:
    with service.registry.default.connection(readonly=True) as conn:
        return list(conn.execute(select(EVENTS).order_by(EVENTS.c.event_date, EVENTS.c.id)))


def test_a_deposit_writes_the_balance_and_its_event_together(service):
    wallet = a_wallet(service)

    service.executor.execute(
        DepositMoneyAction(service.wallets, service.executor.clock),
        DepositParams(wallet_id=wallet.id, amount=Decimal("25.50"), recipient="ada@example.com"),
    )

    assert service.wallets.get(wallet.id).balance == Decimal("125.5000")
    deposited = [r for r in outbox(service) if r.event_type == "MoneyDepositedEvent"]
    assert len(deposited) == 1
    assert deposited[0].payload == {
        "amount": "25.50",
        "new_balance": "125.5000",
        # The event's own identity travels *inside* the payload as well as in its columns,
        # because a consumer deserialising the payload into a typed event needs them there -
        # Java's `@JsonCreator` requires both, and a handler reading a row written without
        # them would fail, retry, and expire. Spelled as Java spells them.
        "modelId": str(wallet.id),
        "modelName": "Wallet",
    }


def test_the_handler_reacts_and_reads_what_the_event_does_not_carry(service):
    """The README's claim: the recipient travels as an action parameter, not in the event."""
    wallet = a_wallet(service)
    service.executor.execute(
        DepositMoneyAction(service.wallets, service.executor.clock),
        DepositParams(wallet_id=wallet.id, amount=Decimal("10.00"), recipient="ada@example.com"),
    )

    service.deliver_pending_events()

    alerts = service.alerts.all()
    assert len(alerts) == 1
    assert alerts[0].recipient == "ada@example.com"
    assert "10.00" in alerts[0].message

    deposited = next(r for r in outbox(service) if r.event_type == "MoneyDepositedEvent")
    assert "recipient" not in deposited.payload, "the event stays clean"
    assert deposited.action_params["recipient"] == "ada@example.com"


def test_writing_an_entity_emits_nothing_and_is_still_recorded(service):
    wallet = a_wallet(service)
    service.executor.execute(
        DepositMoneyAction(service.wallets, service.executor.clock),
        DepositParams(wallet_id=wallet.id, amount=Decimal("1.00"), recipient="ada@example.com"),
    )
    service.deliver_pending_events()

    alert_rows = [r for r in outbox(service) if r.action_name == "RaiseAlertAction"]
    assert len(alert_rows) == 1
    assert alert_rows[0].event_type is None, "an entity's changes are not events"
    assert len(service.alerts.all()) == 1, "and the row was still written"


def test_closing_twice_changes_nothing_the_second_time(service):
    wallet = a_wallet(service, "0.00")
    service.executor.execute(
        CloseWalletAction(service.wallets, service.executor.clock),
        WalletIdParams(wallet_id=wallet.id),
    )
    before = service.wallets.get(wallet.id)

    service.executor.execute(
        CloseWalletAction(service.wallets, service.executor.clock),
        WalletIdParams(wallet_id=wallet.id),
    )

    assert service.wallets.get(wallet.id).version == before.version
    closed = [r for r in outbox(service) if r.event_type == "WalletClosedEvent"]
    assert len(closed) == 1, "the second close emitted nothing"


def test_the_stipend_job_pays_every_open_wallet(service):
    """Driven through a registry holding only the stipend.

    The service registers three - fan-out, handling and the stipend - and that is what a
    deployment looks like. But an event job is one execution that loops for the life of the
    process, the same as Java's, so running one to completion on this thread would never
    return. No Java test schedules them either; they call `fanoutJob.drainOneRound()`.
    """
    from db_scheduler.clock import SettableClock

    from ekbatan.distributedjobs import job_registry

    from .jobs import PayStipendJob

    wallet = a_wallet(service, "0.00")
    service.jobs.start()  # all three get their row
    stipend_only = (
        job_registry()
        .engine(service.registry.default.primary)
        .with_jobs([PayStipendJob(service.executor, service.wallets)])
        .build_manual(SettableClock(SystemClock().now()))
    )
    stipend_only.start()

    stipend_only.scheduler.run_any_due_executions()
    assert service.wallets.get(wallet.id).balance == Decimal("1.0000")
    stipend_only.scheduler.run_any_due_executions()  # its slot rescheduled; nothing due


def test_the_event_pipeline_runs_as_two_distributed_jobs(service):
    """Turning committed events into deliveries is work a cluster does once, not once per
    instance - so it is claimed through `scheduled_tasks` like any other job."""
    from ekbatan.events.jobs import FANOUT_JOB_NAME, HANDLING_JOB_NAME

    service.jobs.start()
    with service.registry.default.connection(readonly=True) as conn:
        registered = {r[0] for r in conn.execute(select(SCHEDULED_TASKS.c.task_name))}

    assert {FANOUT_JOB_NAME, HANDLING_JOB_NAME} <= registered

    wallet = a_wallet(service, "10.00")
    service.executor.execute(
        DepositMoneyAction,
        DepositParams(wallet_id=wallet.id, amount=Decimal("5.00"), recipient="ada@example.com"),
    )

    service.deliver_pending_events()  # a round of each, as `drainOneRound()` is in Java's tests
    assert service.alerts.find_for_wallet(wallet.id), "the handler ran, driven by the job"


def test_a_rejected_deposit_leaves_nothing_behind(service):
    from ekbatan.errors import ValidationError

    wallet = a_wallet(service)
    before = len(outbox(service))

    with pytest.raises(ValidationError, match="amount"):
        service.executor.execute(
            DepositMoneyAction(service.wallets, service.executor.clock),
            DepositParams(wallet_id=wallet.id, amount=Decimal("-1"), recipient="ada@example.com"),
        )

    assert service.wallets.get(wallet.id).balance == Decimal("100.0000")
    assert len(outbox(service)) == before


def test_the_script_runs(tmp_path, capsys):
    """The README promises the module runs as a script. This is that promise."""
    from .__main__ import run

    built = build(f"sqlite:///{tmp_path / 'script.db'}")
    try:
        run(built)
    finally:
        built.close()

    printed = capsys.readouterr().out
    assert "open a wallet" in printed
    assert "what a consumer reads from the outbox" in printed
    assert "sentinel" in printed


# --------------------------------------------------------------- the repository's own queries


def test_wallets_are_found_by_owner_across_shards(service):
    owner = uuid4()
    for _ in range(2):
        service.executor.execute(
            OpenWalletAction(service.executor.clock),
            OpenWalletParams(owner_id=owner, currency="EUR", opening_balance=Decimal("1.00")),
        )
    service.executor.execute(
        OpenWalletAction(service.executor.clock),
        OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal("1.00")),
    )

    assert len(service.wallets.find_all_by_owner(owner)) == 2


def test_a_domain_query_still_hides_deleted_rows(service):
    """The filter is applied once, in the framework, rather than remembered at each call site -
    which is what stops a reporting query from disagreeing with the application's own screens."""
    owner = uuid4()
    wallet = service.executor.execute(
        OpenWalletAction(service.executor.clock),
        OpenWalletParams(owner_id=owner, currency="EUR", opening_balance=Decimal("1.00")),
    )
    service.wallets.update(wallet.delete())

    assert service.wallets.find_all_by_owner(owner) == []


def test_open_wallets_running_low_can_be_listed(service):
    rich = a_wallet(service, "500.00")
    poor = a_wallet(service, "2.00")
    closed = a_wallet(service, "1.00")
    service.executor.execute(
        CloseWalletAction(service.wallets, service.executor.clock),
        WalletIdParams(wallet_id=closed.id),
    )

    found = {w.id for w in service.wallets.find_open_below(Decimal("10.00"))}

    assert found == {poor.id}, "the closed one is not open, and the rich one is not low"
    assert rich.id not in found


def test_a_sum_across_shards_drops_to_sql(service):
    """The one question the inherited reads cannot answer, so the repository writes it."""
    assert service.wallets.total_balance() == Decimal("0"), "no rows is zero, not null"

    a_wallet(service, "10.00")
    a_wallet(service, "2.50")

    assert service.wallets.total_balance() == Decimal("12.5000")


def test_a_shard_can_be_asked_directly(service):
    from ekbatan import ShardIdentifier

    wallet = a_wallet(service)
    here = ShardIdentifier(0, 0)

    assert [w.id for w in service.wallets.find_on_shard(here)] == [wallet.id]
    assert service.wallets.exists_on_shard(here, wallet.id) is True
    assert service.wallets.exists_on_shard(ShardIdentifier(0, 0), wallet.id) is True


def test_alerts_are_found_by_the_wallet_they_are_about(service):
    wallet = a_wallet(service)
    service.executor.execute(
        DepositMoneyAction(service.wallets, service.executor.clock),
        DepositParams(wallet_id=wallet.id, amount=Decimal("5.00"), recipient="ada@example.com"),
    )
    service.deliver_pending_events()

    found = service.alerts.find_for_wallet(wallet.id)
    assert len(found) == 1
    assert found[0].recipient == "ada@example.com"


def test_the_repository_writes_its_row_mapping(service):
    """Java declares `fromRecord` and `toRecord` abstract, so every repository implements both.
    So does every repository here, as of `EKB-0217`.

    This test used to assert the opposite - that neither was written, because the mapping was
    by field name unless you said otherwise. That default was the one piece of magic left in
    the read and write paths: rename a column without renaming the field and it silently
    stopped matching, where Java gets a compile error.
    """
    from .repository import WalletRepository

    assert "from_row" in vars(WalletRepository), "the mapping is written, not inherited"
    assert "to_row" in vars(WalletRepository)

    wallet = a_wallet(service, "3.00")
    assert service.wallets.get(wallet.id).balance == Decimal("3.0000")


def test_an_empty_wallet_can_be_erased_for_real(service):
    """Unlike `delete`, which keeps the row - see WalletRepository.erase."""
    wallet = a_wallet(service, "0.00")

    assert service.wallets.erase(wallet.id) is True
    assert service.wallets.find(wallet.id) is None
    with service.registry.default.connection(readonly=True) as conn:
        remaining = conn.execute(
            select(WALLETS.c.id).where(WALLETS.c.id == wallet.id.value)
        ).first()
    assert remaining is None, "soft delete leaves the row; this one does not"

    assert service.wallets.erase(wallet.id) is False, "erasing what is gone is not an error"


def test_a_wallet_with_money_in_it_is_not_erased(service):
    wallet = a_wallet(service, "25.00")

    with pytest.raises(ValidationError, match="still holds 25"):
        service.wallets.erase(wallet.id)

    assert service.wallets.get(wallet.id).balance == Decimal("25.0000"), "and nothing moved"


def test_erasing_joins_a_transaction_the_caller_opened(service):
    wallet = a_wallet(service, "0.00")

    try:
        with service.registry.default.transaction():
            service.wallets.erase(wallet.id)
            raise RuntimeError("changed my mind")
    except RuntimeError:
        pass

    assert service.wallets.find(wallet.id) is not None, "the rollback took the erase with it"
