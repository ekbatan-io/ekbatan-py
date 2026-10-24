"""The wiring, all of it in one place.

Which database, which pool, which handler: configuration, expressed once, away from the code
that expresses domain behaviour. Nothing above this file knows any of it. The database, the
namespace and the handling switch are in `ekbatan.yaml`.

**Async where it waits, threads where they poll, one config file.** The units of work run on
`ekbatan.aio`: an `AsyncDatabaseRegistry`, async repositories and an `AsyncActionExecutor`. The
event handler is `async def` and awaits an executor of its own. What polls - db-scheduler, and
the fan-out and dispatch jobs it runs - stays on threads, through the sync API. All of it is
built from the same `ekbatan.yaml`: the sync side connects with sqlite3, the async side with
aiosqlite.

Three pools over the one file, each for what uses it: the requests' (async), the handler's
(async, used only on dispatch's handler loop - an async pool belongs to the loop it is used on -
by the event handler and by the stipend job's work) and the jobs' (sync).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from ekbatan import ActionRegistry, DatabaseRegistry, RepositoryRegistry
from ekbatan.aio import (
    AsyncActionExecutor,
    AsyncDatabaseRegistry,
    AsyncDataSource,
    AsyncTransactionManager,
)
from ekbatan.clock import Clock, SystemClock
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob, HandlerLoop

from .actions import CloseWalletAction, DepositMoneyAction, OpenWalletAction, RaiseAlertAction
from .handlers import MoneyDepositedEventHandler
from .jobs import STIPEND_JOB_NAME, PayStipendJob
from .repository import AlertRepository, WalletRepository
from .schema import SCHEDULED_TASKS, metadata

#: The service's Ekbatan settings, read once at startup.
CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class WalletService:
    """Everything wired together, and the only object the outside world needs."""

    #: The units of work, on `ekbatan.aio`.
    registry: AsyncDatabaseRegistry
    wallets: WalletRepository
    alerts: AlertRepository
    executor: AsyncActionExecutor
    #: The handler side: its own async registry and executor, used only on `handler_loop` -
    #: by the event handler and by the stipend job's work.
    handler_loop: HandlerLoop
    handler_registry: AsyncDatabaseRegistry
    handler_wallets: WalletRepository
    handler_executor: AsyncActionExecutor
    #: What polls, sync, on threads: the scheduler, and fan-out and dispatch over it.
    events_registry: DatabaseRegistry
    jobs: JobRegistry

    async def start(self) -> None:
        """Begin polling. Fan-out and handling are two of the jobs, so this is what starts the
        event pipeline - on its own threads, off the event loop: from here on, a committed event
        reaches its handler on its own.

        Returns once the stipend's first run is over. A `FixedDelay` job is due the moment it is
        registered, so the stipend pays every open wallet right at startup; waiting for that
        here means a wallet opened afterwards is not paid by surprise half a second later.
        Awaited, so the wait does not hold up the event loop; starting - sync, it registers the
        jobs in the database - runs on a worker thread for the same reason.
        """
        await asyncio.to_thread(self.jobs.start)
        deadline = time.monotonic() + 15
        while not await self._stipend_has_run():
            if time.monotonic() > deadline:
                raise RuntimeError("the stipend's first run did not finish within 15 seconds")
            await asyncio.sleep(0.05)

    async def _stipend_has_run(self) -> bool:
        async with self.registry.default.connection(readonly=True) as conn:
            found = await conn.execute(
                select(SCHEDULED_TASKS.c.last_success).where(
                    SCHEDULED_TASKS.c.task_name == STIPEND_JOB_NAME
                )
            )
            return found.scalar_one_or_none() is not None

    async def aclose(self) -> None:
        # Stop taking work before closing what the work runs on. Stopping the jobs waits for a
        # dispatch in flight, so it runs on a thread, off this loop.
        await asyncio.to_thread(self.jobs.stop)
        # The handler's pool belongs to the handler loop: closed there, then the loop.
        await asyncio.to_thread(self.handler_loop.run, self.handler_registry.aclose())
        self.handler_loop.close()
        self.events_registry.close()
        await self.registry.aclose()


def build(
    *,
    config_file: Path = CONFIG_FILE,
    clock: Clock | None = None,
) -> WalletService:
    """Wire the service on `config_file`. Nothing here awaits: building a pool opens no
    connection."""
    running = clock or SystemClock()
    # `ekbatan.yaml`'s `${...}` placeholders - the url - are read from the process environment
    # as the file is loaded, each by its name.
    ekbatan = load_ekbatan_config(config_file)
    layout = ekbatan.sharding

    # -- what polls, on threads -----------------------------------------------------------------
    events_registry = DatabaseRegistry.from_config(layout)

    # Development only. A real deployment migrates from files - `ekbatan.migrations.migrate`,
    # which is sync too - because create_all has no answer for a column that changed.
    for shard in events_registry.shards:
        metadata.create_all(events_registry.manager(shard).primary.sqlalchemy_engine)

    # -- the units of work, async ---------------------------------------------------------------
    registry = AsyncDatabaseRegistry.from_config(layout)
    wallets = WalletRepository(registry, clock=running)
    alerts = AlertRepository(registry, clock=running)

    # Each action, built once with what it needs. A caller runs
    # `await executor.execute(DepositMoneyAction, params)` and depends on neither the
    # repository nor the constructor.
    executor = (
        AsyncActionExecutor.builder()
        .namespace(ekbatan.namespace)
        .database_registry(registry)
        .repository_registry(
            RepositoryRegistry.builder()
            .with_model_repository(wallets)
            .with_entity_repository(alerts)
            .build()
        )
        .action_registry(
            ActionRegistry.builder()
            .with_action(OpenWalletAction(running))
            .with_action(DepositMoneyAction(wallets, running))
            .with_action(CloseWalletAction(wallets, running))
            .build()
        )
        .clock(running)
        .build()
    )

    # -- the handler side, async on dispatch's handler loop ------------------------------------
    # The stipend job hands its work to the same loop, so it runs on this side too.
    member = layout.groups[0].members[0]
    primary = member.config_for("primaryConfig")
    if primary is None:
        raise ValueError("groups[0].members[0].configs.primaryConfig is required")
    handler_loop = HandlerLoop()
    handler_registry = (
        AsyncDatabaseRegistry.builder()
        .with_default_database(
            AsyncTransactionManager(layout.default_shard, AsyncDataSource.from_config(primary))
        )
        .build()
    )
    handler_wallets = WalletRepository(handler_registry, clock=running)
    handler_executor = (
        AsyncActionExecutor.builder()
        .namespace(ekbatan.namespace)
        .database_registry(handler_registry)
        .repository_registry(
            RepositoryRegistry.builder()
            .with_model_repository(handler_wallets)
            .with_entity_repository(AlertRepository(handler_registry, clock=running))
            .build()
        )
        .action_registry(
            ActionRegistry.builder()
            .with_action(DepositMoneyAction(handler_wallets, running))
            .with_action(RaiseAlertAction(running))
            .build()
        )
        .clock(running)
        .build()
    )
    handlers = (
        EventHandlerRegistry.builder()
        .with_handler(MoneyDepositedEventHandler(handler_executor))
        .build()
    )

    # Turning committed events into deliveries is work a cluster must do once, not once per
    # instance - so it is two distributed jobs, claimed through `scheduled_tasks` like any
    # other. `ekbatan.yaml` switches handling on.
    events_config = ekbatan.local_event_handler
    fanout = (
        EventFanoutJob.builder()
        .registry(events_registry)
        .handlers(handlers)
        .config(events_config)
        .clock(running)
        .build()
    )
    dispatch = (
        EventHandlingJob.builder()
        .registry(events_registry)
        .handlers(handlers)
        # The loop the async handler runs on - ours, so `aclose` can close the handler's pool
        # on it first.
        .handler_loop(handler_loop)
        .config(events_config)
        .enabled(events_config.handling.enabled)
        .clock(running)
        .build()
    )

    return WalletService(
        registry=registry,
        wallets=wallets,
        alerts=alerts,
        executor=executor,
        handler_loop=handler_loop,
        handler_registry=handler_registry,
        handler_wallets=handler_wallets,
        handler_executor=handler_executor,
        events_registry=events_registry,
        # The jobs run on db-scheduler, sync, on threads of their own. A real deployment would
        # give them a dedicated pool (`jobsConfig`), as the server-database examples do: a job
        # body holding the last connection would starve the heartbeat.
        jobs=(
            job_registry()
            .data_source(events_registry.default.primary)
            .with_jobs(
                [fanout, dispatch, PayStipendJob(handler_loop, handler_executor, handler_wallets)]
            )
            .with_config(ekbatan.jobs)
            .build()
        ),
    )
