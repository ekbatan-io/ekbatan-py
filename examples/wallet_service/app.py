"""The wiring, all of it in one place.

Which database, which shard, which pool, which handler: configuration, expressed once, away
from the code that expresses domain behaviour. Nothing above this file knows any of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ekbatan import ActionExecutor, DatabaseRegistry, Repository, ShardingConfig
from ekbatan.clock import Clock, SystemClock
from ekbatan.events import EventDispatch, EventFanout, EventHandlerRegistry
from ekbatan.jobs import JobRegistry

from .handlers import NotifyOnDeposit
from .jobs import PayStipend
from .model import Alert, Wallet
from .schema import ALERTS, EVENT_NOTIFICATIONS, EVENTS, SCHEDULED_TASKS, WALLETS, metadata


@dataclass(frozen=True, slots=True)
class WalletService:
    """Everything wired together, and the only object the outside world needs."""

    registry: DatabaseRegistry
    wallets: Repository[Wallet, Any]
    alerts: Repository[Alert, Any]
    executor: ActionExecutor
    fanout: EventFanout
    dispatch: EventDispatch
    jobs: JobRegistry

    def deliver_pending_events(self) -> None:
        """Turn committed events into work, then do it.

        In a service this runs on a timer or as a distributed job. Called directly here so the
        example stays a single-threaded script you can read top to bottom.
        """
        self.fanout.drain_until_idle()
        self.dispatch.drain_until_idle()

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

    wallets: Repository[Wallet, Any] = Repository(Wallet, WALLETS, registry, clock=running)
    alerts: Repository[Alert, Any] = Repository(Alert, ALERTS, registry, clock=running)

    executor = ActionExecutor(
        namespace="example.wallet_service",
        registry=registry,
        repositories=[wallets, alerts],
        events=EVENTS,
        clock=running,
    )

    handlers = EventHandlerRegistry([NotifyOnDeposit(executor)])
    return WalletService(
        registry=registry,
        wallets=wallets,
        alerts=alerts,
        executor=executor,
        fanout=EventFanout(
            registry,
            handlers,
            events=EVENTS,
            notifications=EVENT_NOTIFICATIONS,
            clock=running,
        ),
        dispatch=EventDispatch(
            registry, handlers, notifications=EVENT_NOTIFICATIONS, clock=running
        ),
        jobs=JobRegistry(
            registry, [PayStipend(executor, wallets)], table=SCHEDULED_TASKS, clock=running
        ),
    )
