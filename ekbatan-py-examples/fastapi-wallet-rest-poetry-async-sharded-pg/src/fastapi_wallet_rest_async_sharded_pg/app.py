"""The wiring - and the only file that knows how many databases there are.

Add a shard by adding a member to a group - or a group - in `ekbatan.yaml`. No code changes: the
repository already routes by id, and the actions already never name a database. The layout is
Java's, in the same keys as its `application.yml`: groups of members, each member a named map of
datasources.

**Async where it waits, threads where they poll, one config file.** The HTTP requests run on
`ekbatan.aio`: an `AsyncDatabaseRegistry` over every shard, async repositories, an
`AsyncActionExecutor`, an async keyed lock. The event handler is `async def` and awaits an
executor of its own. What polls - db-scheduler, and the fan-out and dispatch jobs it runs - stays
on threads, through a sync registry over every shard. The urls name psycopg; the async side
reads the same urls and connects through asyncpg.

The pools, each sized for what uses it:

**Requests** get each shard's `primaryConfig`, async. Past `pool_size` they queue for a
connection, and one that waits longer than `pool_timeout` fails rather than queueing for ever.

**Locks** get the global member's `lockConfig`, async. A lease holds its connection for as long
as it is held. One database holds every key: a lock is about a wallet's key, not its rows.

**The handler** gets the global member's `handlersConfig`, async, used only on dispatch's
handler loop. Its alerts all live on the default shard, so that is the one database it needs.

**Jobs** get each shard's `jobsConfig`, sync: one scheduler on the global one, and fan-out and
dispatch reading and writing every shard's `events` and `event_notifications`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from ekbatan import (
    ActionRegistry,
    DatabaseRegistry,
    RepositoryRegistry,
    ShardIdentifier,
    TransactionManager,
    engine_for,
)
from ekbatan.aio import (
    AsyncActionExecutor,
    AsyncDatabaseRegistry,
    AsyncSingleTableJsonEventPersister,
    AsyncTransactionManager,
    async_engine_for,
)
from ekbatan.aio.locks import AsyncKeyedLockProvider, AsyncPostgresKeyedLockProvider
from ekbatan.clock import Clock, SystemClock
from ekbatan.config import DataSourceConfig, ShardMemberConfig
from ekbatan.config_files import load_ekbatan_config
from ekbatan.distributedjobs import JobRegistry, job_registry
from ekbatan.events import EventFanoutJob, EventHandlerRegistry, EventHandlingJob, HandlerLoop
from ekbatan.migrations import migrate

from .actions import (
    GLOBAL,
    MEXICO,
    DepositMoneyAction,
    OpenWalletAction,
    RaiseAlertAction,
    TransferWithinShardAction,
)
from .handlers import MoneyDepositedEventHandler
from .repository import AlertRepository, WalletRepository
from .schema import EVENT_NOTIFICATIONS, EVENTS

#: Where the migration files live - `V<version>__<description>.sql`. Migrations run from files
#: rather than from `create_all`, which is the difference between an example and something you
#: could deploy: `create_all` has no answer for a column that changed.
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

#: The service's Ekbatan settings - the shard layout and the namespace - in the same keys as the
#: Java examples' `application.yml`.
CONFIG_FILE = Path(__file__).resolve().parent / "ekbatan.yaml"


@dataclass(frozen=True, slots=True)
class ShardedWallets:
    #: The request side, on `ekbatan.aio`, over every shard.
    registry: AsyncDatabaseRegistry
    wallets: WalletRepository
    alerts: AlertRepository
    executor: AsyncActionExecutor
    #: The deposit's keyed lock, async, over the global member's `lockConfig`.
    locks: AsyncKeyedLockProvider
    lock_engine: AsyncEngine
    #: The handler side: its own async registry and executor, used only on `handler_loop`.
    handler_loop: HandlerLoop
    handler_registry: AsyncDatabaseRegistry
    handler_executor: AsyncActionExecutor
    #: What polls, sync, on threads: the scheduler, and fan-out and dispatch over every shard.
    events_registry: DatabaseRegistry
    jobs: JobRegistry
    #: Each shard's `jobsConfig` pool. The registries own only what they built, so these are
    #: disposed here.
    jobs_engines: tuple[Engine, ...]

    def start(self) -> None:
        """Begin polling. Fan-out and handling are two of the jobs, so this is what starts the
        event pipeline - across every shard, from one scheduler, off the event loop."""
        self.jobs.start()

    async def aclose(self) -> None:
        # Order matters: stop taking work before closing what the work runs on. Stopping the
        # jobs waits for a dispatch in flight, so it runs on a thread, off this loop.
        await asyncio.to_thread(self.jobs.stop)
        # The handler's pool belongs to the handler loop: closed there, then the loop.
        await asyncio.to_thread(self.handler_loop.run, self.handler_registry.aclose())
        self.handler_loop.close()
        self.events_registry.close()
        for engine in self.jobs_engines:
            engine.dispose()
        self.locks.close()
        await self.lock_engine.dispose()
        await self.registry.aclose()


def build(
    environ: Mapping[str, str] | None = None,
    *,
    config_file: Path = CONFIG_FILE,
    clock: Clock | None = None,
) -> ShardedWallets:
    """Wire the layout `config_file` describes - two databases here, two hundred later.
    `environ` fills its `${...}` placeholders, `os.environ` when left out. Nothing here awaits:
    building a pool opens no connection."""
    running = clock or SystemClock()
    ekbatan = load_ekbatan_config(config_file, environ=environ)
    layout = ekbatan.sharding

    # Every shard, one at a time, fail-fast - and refused outright if a revision that was
    # already applied has since been edited. Sync, once, before any pool is opened.
    migrate(layout, MIGRATIONS)

    # -- the request side ---------------------------------------------------------------------
    registry = AsyncDatabaseRegistry.from_config(layout)
    wallets = WalletRepository(registry, clock=running)
    alerts = AlertRepository(registry, clock=running)
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
            .with_action(TransferWithinShardAction(wallets, running))
            .build()
        )
        .event_persister(AsyncSingleTableJsonEventPersister(registry, EVENTS))
        .clock(running)
        .build()
    )

    default = layout.groups[0].members[0]
    lock_engine = async_engine_for(_required(default, "groups[0].members[0]", "lockConfig"))
    locks = AsyncPostgresKeyedLockProvider.builder().engine(lock_engine).build()

    # -- the handler side ---------------------------------------------------------------------
    # Async, on dispatch's handler loop: an async pool belongs to the loop it is used on, so the
    # handler's registry is its own. Alerts have plain ids, so they all live on the default
    # shard - the one database the handler writes to.
    handler_loop = HandlerLoop()
    handler_registry = (
        AsyncDatabaseRegistry.builder()
        .with_default_database(
            AsyncTransactionManager(
                layout.default_shard,
                async_engine_for(_required(default, "groups[0].members[0]", "handlersConfig")),
            )
        )
        .build()
    )
    handler_executor = (
        AsyncActionExecutor.builder()
        .namespace(ekbatan.namespace)
        .database_registry(handler_registry)
        .repository_registry(
            RepositoryRegistry.builder()
            .with_entity_repository(AlertRepository(handler_registry, clock=running))
            .build()
        )
        .action_registry(ActionRegistry.builder().with_action(RaiseAlertAction(running)).build())
        .event_persister(AsyncSingleTableJsonEventPersister(handler_registry, EVENTS))
        .clock(running)
        .build()
    )
    handlers = (
        EventHandlerRegistry.builder()
        .with_handler(MoneyDepositedEventHandler(handler_executor))
        .build()
    )

    # -- what polls, on threads -----------------------------------------------------------------
    # A sync registry over every shard, on each member's `jobsConfig`: an event commits on its
    # wallet's shard, so fan-out and dispatch must reach every one. One scheduler for the whole
    # layout, on the default shard's pool - one job each, not one per shard.
    jobs_engines: dict[ShardIdentifier, Engine] = {}
    events_builder = DatabaseRegistry.builder()
    for shard, member in layout.members():
        engine = engine_for(_required(member, f"shard {shard}", "jobsConfig"))
        jobs_engines[shard] = engine
        manager = TransactionManager(shard, engine)
        if shard == layout.default_shard:
            events_builder.with_default_database(manager)
        else:
            events_builder.with_database(manager)
    events_registry = events_builder.build()
    jobs_engine = jobs_engines[layout.default_shard]
    events_config = ekbatan.local_event_handler

    return ShardedWallets(
        registry=registry,
        wallets=wallets,
        alerts=alerts,
        executor=executor,
        locks=locks,
        lock_engine=lock_engine,
        handler_loop=handler_loop,
        handler_registry=handler_registry,
        handler_executor=handler_executor,
        events_registry=events_registry,
        jobs_engines=tuple(jobs_engines.values()),
        jobs=(
            job_registry()
            .engine(jobs_engine)
            .with_jobs(
                [
                    EventFanoutJob.builder()
                    .registry(events_registry)
                    .handlers(handlers)
                    .events(EVENTS)
                    .notifications(EVENT_NOTIFICATIONS)
                    .config(events_config)
                    .clock(running)
                    .build(),
                    EventHandlingJob.builder()
                    .registry(events_registry)
                    .handlers(handlers)
                    # The loop the async handler runs on - ours, so `aclose` can close the
                    # handler's pool on it first.
                    .handler_loop(handler_loop)
                    .notifications(EVENT_NOTIFICATIONS)
                    .config(events_config)
                    # Off unless `handling.enabled` is set in `ekbatan.yaml`.
                    .enabled(events_config.handling.enabled)
                    .clock(running)
                    .build(),
                ]
            )
            .with_config(ekbatan.jobs)
            .build()
        ),
    )


def _required(member: ShardMemberConfig, place: str, name: str) -> DataSourceConfig:
    """The named pool's settings, or an error naming where the setting goes."""
    found = member.config_for(name)
    if found is None:
        raise ValueError(f"{place}.configs.{name} is required")
    return found


__all__ = ["GLOBAL", "MEXICO", "ShardedWallets", "build"]
