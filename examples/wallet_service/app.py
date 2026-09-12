"""The wiring, all of it in one place.

Which database, which shard, which pool, which handler: configuration, expressed once, away
from the code that expresses domain behaviour. Nothing above this file knows any of it.
"""

from __future__ import annotations

from dataclasses import dataclass

from db_scheduler.clock import SettableClock

from ekbatan import (
    ActionExecutor,
    ActionRegistry,
    DatabaseRegistry,
    RepositoryRegistry,
    ShardingConfig,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.distributedjobs import ManualJobRegistry, job_registry
from ekbatan.events import (
    EventFanoutJob,
    EventHandlerRegistry,
    EventHandlingJob,
    HandlingConfig,
    LocalEventHandlerConfig,
)

from .actions import CloseWalletAction, DepositMoneyAction, OpenWalletAction, RaiseAlertAction
from .handlers import MoneyDepositedEventHandler
from .jobs import PayStipendJob
from .repository import AlertRepository, WalletRepository
from .schema import EVENT_NOTIFICATIONS, EVENTS, metadata


@dataclass(frozen=True, slots=True)
class WalletService:
    """Everything wired together, and the only object the outside world needs."""

    registry: DatabaseRegistry
    wallets: WalletRepository
    alerts: AlertRepository
    executor: ActionExecutor
    fanout: EventFanoutJob
    dispatch: EventHandlingJob
    jobs: ManualJobRegistry

    def deliver_pending_events(self) -> None:
        """Turn committed events into work, then do it.

        In a service this runs on a timer or as a distributed job. Called directly here so the
        example stays a single-threaded script you can read top to bottom.
        """
        self.fanout.fanout.drain_until_idle()
        self.dispatch.dispatch.drain_until_idle()

    def close(self) -> None:
        self.registry.close()


def build(url: str, *, clock: Clock | None = None) -> WalletService:
    """Wire the service against one database."""
    running = clock or SystemClock()
    registry = DatabaseRegistry.from_config(ShardingConfig.single(url))

    # Development only. A real deployment migrates from files - `ekbatan.migrations.migrate` -
    # because create_all has no answer for a column that changed.
    for shard in registry.shards:
        metadata.create_all(registry.manager(shard).primary)

    wallets = WalletRepository(registry, clock=running)
    alerts = AlertRepository(registry, clock=running)

    # Which repository writes which model. The builder is worth the two extra words here
    # because it declares the kind: saying model of something that is not one is caught now
    # rather than by a missing outbox row weeks later.
    repositories = (
        RepositoryRegistry.builder()
        .with_model_repository(wallets)
        .with_entity_repository(alerts)
        .build()
    )

    # Each action, built once with what it needs. This is the only place that knows what that
    # is - a caller runs `executor.execute(DepositMoneyAction, params)` and depends on neither the
    # repository nor the constructor.
    actions = (
        ActionRegistry.builder()
        .with_action(OpenWalletAction())
        .with_action(DepositMoneyAction(wallets))
        .with_action(CloseWalletAction(wallets))
        .with_action(RaiseAlertAction())
        .build()
    )

    executor = ActionExecutor(
        namespace="example.wallet_service",
        registry=registry,
        repositories=repositories,
        actions=actions,
        events=EVENTS,
        clock=running,
    )

    handlers = EventHandlerRegistry([MoneyDepositedEventHandler(executor)])

    # Turning committed events into deliveries is work a cluster must do once, not once per
    # instance - so it is two distributed jobs, claimed through `scheduled_tasks` like any
    # other. Handling is off by default; a deployment draining through Kafka would leave it so.
    events_config = LocalEventHandlerConfig(handling=HandlingConfig(enabled=True))
    fanout = EventFanoutJob(
        registry,
        handlers,
        events=EVENTS,
        notifications=EVENT_NOTIFICATIONS,
        config=events_config,
        clock=running,
    )
    dispatch = EventHandlingJob(
        registry,
        handlers,
        notifications=EVENT_NOTIFICATIONS,
        config=events_config,
        clock=running,
    )

    return WalletService(
        registry=registry,
        wallets=wallets,
        alerts=alerts,
        executor=executor,
        fanout=fanout,
        dispatch=dispatch,
        # The jobs run on db-scheduler, against the same `scheduled_tasks` table the Java
        # implementation uses - so a Java instance and a Python instance coordinate through one
        # row per job rather than each running the stipend.
        #
        # A real deployment would give this a *dedicated* engine, separate from the one serving
        # requests: db-scheduler polls continuously, and a task body holding the last connection
        # would starve the heartbeat and get its own execution declared dead. One engine here
        # keeps the example a single readable script.
        jobs=(
            job_registry()
            .engine(registry.default.primary)
            .with_jobs([fanout, dispatch, PayStipendJob(executor, wallets)])
            .build_manual(SettableClock(running.now()))
        ),
    )
