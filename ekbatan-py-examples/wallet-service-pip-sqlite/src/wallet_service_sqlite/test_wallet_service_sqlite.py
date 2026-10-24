"""The example's own test.

Lives with the example, runs separately from the framework's suite, and exists for one reason:
an example nobody runs is an example that rots. It asserts what the README claims, so a change
that makes the prose false makes this red.

    make
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from ekbatan.clock import SystemClock, VirtualClock
from ekbatan.errors import ValidationError
from ekbatan.time import Instant

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


def point_at(patch: pytest.MonkeyPatch, database: Path) -> None:
    """Fill `ekbatan.yaml`'s placeholder as a deployment does, in the environment: the url of a
    file in the test's own directory. `patch` puts the old value back afterwards."""
    patch.setenv("WALLET_DATABASE_URL", f"sqlite:///{database}")


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[WalletService]:
    point_at(monkeypatch, tmp_path / "wallet.db")
    built = build(clock=VirtualClock(SystemClock().instant()))
    # Started before any wallet exists, as a service is: the jobs poll from here on, and
    # `start()` returns once the stipend's run at startup - on an empty database - is over.
    built.start()
    try:
        yield built
    finally:
        built.close()


def settled(service: WalletService, within: float = 15.0) -> None:
    """Wait until the handler has run for every deposit so far - the fan-out and handling jobs
    run in the background, as in a service. Every deposit fanned out, and no delivery waiting."""
    deadline = time.monotonic() + within
    while True:
        with service.registry.default.connection(readonly=True) as conn:
            undelivered = conn.execute(
                select(func.count())
                .select_from(EVENTS)
                .where(EVENTS.c.delivered.is_(False))
                .where(EVENTS.c.event_type == "MoneyDepositedEvent")
            ).scalar_one()
            waiting = conn.execute(
                select(func.count())
                .select_from(EVENT_NOTIFICATIONS)
                .where(EVENT_NOTIFICATIONS.c.state.in_(("PENDING", "FAILED")))
            ).scalar_one()
        if undelivered == 0 and waiting == 0:
            return
        if time.monotonic() > deadline:
            raise AssertionError(
                f"after {within}s: {undelivered} deposits not fanned out, {waiting} waiting"
            )
        time.sleep(0.05)


def a_wallet(service: WalletService, balance: str = "100.00"):
    return service.executor.execute(
        OpenWalletAction,
        OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal(balance)),
    )


def outbox(service: WalletService) -> list:
    with service.registry.default.connection(readonly=True) as conn:
        return list(conn.execute(select(EVENTS).order_by(EVENTS.c.event_date, EVENTS.c.id)))


def test_a_deposit_writes_the_balance_and_its_event_together(service):
    wallet = a_wallet(service)

    service.executor.execute(
        DepositMoneyAction,
        DepositParams(wallet_id=wallet.id, amount=Decimal("25.50"), recipient="ada@example.com"),
    )

    assert service.wallets.get(wallet.id).balance == Decimal("125.5000")
    deposited = [r for r in outbox(service) if r.event_type == "MoneyDepositedEvent"]
    assert len(deposited) == 1
    # The payload's JSON: camelCase field names, decimals as JSON numbers (read back as Decimal,
    # scale kept). The event's own identity travels *inside* the payload as well as in its
    # columns, so a consumer reading the payload alone knows what it is about.
    assert deposited[0].payload == {
        "modelId": str(wallet.id),
        "modelName": "Wallet",
        "amount": Decimal("25.50"),
        "newBalance": Decimal("125.5000"),
    }
    assert str(deposited[0].payload["amount"]) == "25.50"
    opened = [r for r in outbox(service) if r.event_type == "WalletOpenedEvent"]
    assert opened[0].payload["openedAt"].endswith("Z"), (
        "the event's Instant: UTC digits with Java's Z"
    )


def test_the_handler_reacts_and_reads_what_the_event_does_not_carry(service):
    """The README's claim: the recipient travels as an action parameter, not in the event."""
    wallet = a_wallet(service)
    service.executor.execute(
        DepositMoneyAction,
        DepositParams(wallet_id=wallet.id, amount=Decimal("10.00"), recipient="ada@example.com"),
    )

    settled(service)

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
        DepositMoneyAction,
        DepositParams(wallet_id=wallet.id, amount=Decimal("1.00"), recipient="ada@example.com"),
    )
    settled(service)

    alert_rows = [r for r in outbox(service) if r.action_name == "RaiseAlertAction"]
    assert len(alert_rows) == 1
    assert alert_rows[0].event_type is None, "an entity's changes are not events"
    assert len(service.alerts.all()) == 1, "and the row was still written"


def test_closing_twice_changes_nothing_the_second_time(service):
    wallet = a_wallet(service, "0.00")
    service.executor.execute(
        CloseWalletAction,
        WalletIdParams(wallet_id=wallet.id),
    )
    before = service.wallets.get(wallet.id)

    service.executor.execute(
        CloseWalletAction,
        WalletIdParams(wallet_id=wallet.id),
    )

    assert service.wallets.get(wallet.id).version == before.version
    closed = [r for r in outbox(service) if r.event_type == "WalletClosedEvent"]
    assert len(closed) == 1, "the second close emitted nothing"


def test_the_stipend_job_pays_every_open_wallet(service):
    """The stipend ran at startup, before this wallet existed, and is next due in a day. A
    scheduler whose clock says a day has passed picks the same row and runs it - once."""
    from datetime import timedelta

    from db_scheduler.clock import SettableClock

    from ekbatan.distributedjobs import job_registry

    from .jobs import PayStipendJob

    wallet = a_wallet(service, "0.00")
    a_day_later = SettableClock(SystemClock().instant() + timedelta(days=1, seconds=1))
    stipend_only = (
        job_registry()
        .data_source(service.registry.default.primary)
        .with_jobs([PayStipendJob(service.executor, service.wallets)])
        .build_manual(a_day_later)
    )
    stipend_only.start()

    stipend_only.scheduler.run_any_due_executions()
    assert service.wallets.get(wallet.id).balance == Decimal("1.0000")
    stipend_only.scheduler.run_any_due_executions()  # its slot rescheduled; nothing due
    assert service.wallets.get(wallet.id).balance == Decimal("1.0000")


def test_the_event_pipeline_runs_as_two_distributed_jobs(service):
    """Turning committed events into deliveries is work a cluster does once, not once per
    instance - so it is claimed through `scheduled_tasks` like any other job."""
    from ekbatan.events.jobs import FANOUT_JOB_NAME, HANDLING_JOB_NAME

    with service.registry.default.connection(readonly=True) as conn:
        registered = {r[0] for r in conn.execute(select(SCHEDULED_TASKS.c.task_name))}

    assert {FANOUT_JOB_NAME, HANDLING_JOB_NAME} <= registered

    wallet = a_wallet(service, "10.00")
    service.executor.execute(
        DepositMoneyAction,
        DepositParams(wallet_id=wallet.id, amount=Decimal("5.00"), recipient="ada@example.com"),
    )

    settled(service)
    assert service.alerts.find_for_wallet(wallet.id), "the handler ran, driven by the job"


def test_a_rejected_deposit_leaves_nothing_behind(service):
    from ekbatan.errors import ValidationError

    wallet = a_wallet(service)
    before = len(outbox(service))

    with pytest.raises(ValidationError, match="amount"):
        service.executor.execute(
            DepositMoneyAction,
            DepositParams(wallet_id=wallet.id, amount=Decimal("-1"), recipient="ada@example.com"),
        )

    assert service.wallets.get(wallet.id).balance == Decimal("100.0000")
    assert len(outbox(service)) == before


def test_the_script_runs(tmp_path, capsys, monkeypatch):
    """The README promises the module runs as a script. This is that promise."""
    from .__main__ import run

    point_at(monkeypatch, tmp_path / "script.db")
    built = build()
    try:
        run(built)
    finally:
        built.close()

    printed = capsys.readouterr().out
    assert "open a wallet" in printed
    assert "alert for ada@example.com" in printed, "the jobs delivered the deposit"
    assert "what a consumer reads from the outbox" in printed
    assert "sentinel" in printed


# --------------------------------------------------------------- the repository's own queries


def test_wallets_are_found_by_owner_across_shards(service):
    owner = uuid4()
    for _ in range(2):
        service.executor.execute(
            OpenWalletAction,
            OpenWalletParams(owner_id=owner, currency="EUR", opening_balance=Decimal("1.00")),
        )
    service.executor.execute(
        OpenWalletAction,
        OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal("1.00")),
    )

    assert len(service.wallets.find_all_by_owner(owner)) == 2


def test_a_domain_query_still_hides_deleted_rows(service):
    """The filter is applied once, in the framework, rather than remembered at each call site -
    which is what stops a reporting query from disagreeing with the application's own screens."""
    owner = uuid4()
    wallet = service.executor.execute(
        OpenWalletAction,
        OpenWalletParams(owner_id=owner, currency="EUR", opening_balance=Decimal("1.00")),
    )
    service.wallets.update(wallet.delete())

    assert service.wallets.find_all_by_owner(owner) == []


def test_open_wallets_running_low_can_be_listed(service):
    rich = a_wallet(service, "500.00")
    poor = a_wallet(service, "2.00")
    closed = a_wallet(service, "1.00")
    service.executor.execute(
        CloseWalletAction,
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
        DepositMoneyAction,
        DepositParams(wallet_id=wallet.id, amount=Decimal("5.00"), recipient="ada@example.com"),
    )
    settled(service)

    found = service.alerts.find_for_wallet(wallet.id)
    assert len(found) == 1
    assert found[0].recipient == "ada@example.com"


def test_the_repository_writes_its_row_mapping(service):
    """Every repository implements `to_record` and `from_record`, as of `EKB-0217`, typed by
    the table's record.

    This test used to assert the opposite - that neither was written, because the mapping was
    by field name unless you said otherwise. That default was the one piece of magic left in
    the read and write paths: rename a column without renaming the field and it silently
    stopped matching, with no error anywhere.
    """
    from .repository import WalletRepository

    assert "from_record" in vars(WalletRepository), "the mapping is written, not inherited"
    assert "to_record" in vars(WalletRepository)
    hints = WalletRepository.from_record.__annotations__
    assert hints["record"] == "WalletsRecord", "it takes the table's record"

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


def test_the_service_runs_on_the_settings_in_ekbatan_yaml(service) -> None:
    """`ekbatan.yaml` is the service's Ekbatan config: the database, the namespace and the
    handling job switched on all come from it."""
    assert service.executor.namespace == "example.wallet_service_sqlite"
    assert any("event-handling" in name for name in service.jobs._names_requiring_registration), (
        "a switched-off handling job is registered but never scheduled"
    )
    assert service.registry.default.primary.url.database.endswith("wallet.db")


#: A marker for a password. SQLite has no login, so nothing reads it: it is here to show nothing
#: holds the environment it sits in.
SECRET = "Mk-7f3a-SECRET"


def test_no_service_object_shows_the_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A password is a marker, set in the environment as a deployment sets one. SQLite reads no
    login, so this proves the other half: nothing the service builds holds the environment, so
    nothing shows it in its repr or str."""
    point_at(monkeypatch, tmp_path / "secret.db")
    monkeypatch.setenv("WALLET_DATABASE_PASSWORD", SECRET)
    monkeypatch.setenv("SOME_OTHER_PASSWORD", SECRET)

    built = build()
    try:
        with built.registry.default.connection(readonly=True) as conn:
            assert conn.execute(select(func.count()).select_from(WALLETS)).scalar_one() == 0
        source = built.registry.default.primary
        engine = source.sqlalchemy_engine
        shown = [built, built.registry, built.executor, built.jobs, source, engine, engine.pool]
        shown += [getattr(built, name) for name in built.__dataclass_fields__]
        for thing in shown:
            assert SECRET not in repr(thing), type(thing)
            assert SECRET not in str(thing), type(thing)
    finally:
        built.close()


def test_a_wallet_never_holds_a_negative_balance() -> None:
    """`Wallet.validate()` is the wallet's one place for its own rules, and ekbatan runs it
    every time a wallet is made - here, opened with less than nothing."""
    with pytest.raises(ValidationError, match="balance cannot be negative"):
        Wallet.open(
            owner_id=uuid4(),
            currency="EUR",
            balance=Decimal("-0.01"),
            created_date=Instant(datetime(2026, 1, 1, 12, 0)),
        )
