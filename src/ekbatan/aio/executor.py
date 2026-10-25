"""Running an async unit of work, and committing what it staged.

The counterpart to :class:`ekbatan.action.ActionExecutor`, and the same promise: one
invocation is one transaction per shard, the rows and their events committed together or not at
all. Which action runs, the retry policy, how a plan is split by shard, the cross-shard refusal,
and every log line and span attribute are the sync executor's code. What is here awaits
``perform``, the transaction and the writes::

    wallet = await executor.execute(WalletDepositMoneyAction, params)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from .._tracing import span
from ..action.action import _Binding
from ..action.executor import DEFAULT, ExecutionConfiguration, _ExecutorBuilder, _ExecutorCore
from ..action.plan import ActionPlan, PersistableChanges
from ..action.registry import ActionRegistry
from ..clock import Clock
from ..repository import RepositoryRegistry
from ..sharding import ShardIdentifier
from .action import AsyncAction
from .outbox import AsyncEventPersister, AsyncSingleTableJsonEventPersister
from .persistence import AsyncDatabaseRegistry
from .persister import AsyncChangePersister
from .repository import AsyncRepository

__all__ = ["AsyncActionExecutor", "AsyncActionExecutorBuilder"]


class AsyncActionExecutor(_ExecutorCore):
    """Runs async units of work.

    Built as the sync executor is, from an :class:`~ekbatan.aio.AsyncDatabaseRegistry`, async
    repositories and :class:`~ekbatan.aio.AsyncAction` s. It refuses a sync action: a service
    is all-sync or all-async.
    """

    __slots__ = ()

    _RUNS = AsyncAction

    registry: AsyncDatabaseRegistry
    outbox: AsyncEventPersister
    changes: AsyncChangePersister

    def __init__(
        self,
        *,
        namespace: str,
        registry: AsyncDatabaseRegistry,
        repositories: RepositoryRegistry | Iterable[AsyncRepository[Any]],
        actions: ActionRegistry | Iterable[AsyncAction[Any, Any]] = (),
        outbox: AsyncEventPersister | None = None,
        clock: Clock | None = None,
        config: ExecutionConfiguration = DEFAULT,
    ) -> None:
        self._setup(namespace, registry, repositories, actions, clock, config)
        self.outbox = outbox if outbox is not None else AsyncSingleTableJsonEventPersister(registry)
        # The one RepositoryRegistry: a map from model type to repository that never calls
        # one, so it holds an async repository as well as a sync one.
        self.changes = AsyncChangePersister(
            repositories
            if isinstance(repositories, RepositoryRegistry)
            else RepositoryRegistry(repositories),
            self.outbox,
        )

    @classmethod
    def builder(cls) -> AsyncActionExecutorBuilder:
        return AsyncActionExecutorBuilder()

    async def execute[P, R](
        self,
        action: type[AsyncAction[P, R]],
        params: P,
        *,
        principal: str | None = None,
        config: ExecutionConfiguration | None = None,
    ) -> R:
        """Run one unit of work and commit what it staged."""
        running, policy = self._running(action, config)
        name = type(running).__name__
        retried = 0

        with self._started(name, principal) as execution:
            started_at = time.monotonic()
            while True:
                started = self.clock.instant()
                # A fresh plan per attempt, as in sync.
                plan = ActionPlan(self.clock)
                try:
                    with span("ekbatan.action.perform"), _Binding(plan):
                        result: R = await running.perform(principal, params)
                    await self._commit(name, plan, params, started, policy)
                except BaseException as error:
                    delay = self._retry_delay(policy, error, retried, name, execution)
                    if delay is None:
                        raise
                    retried += 1
                    await asyncio.sleep(delay)
                else:
                    self._succeeded(name, principal, retried, started_at, execution)
                    return result

    async def _commit(
        self,
        action_name: str,
        plan: ActionPlan,
        params: Any,
        started: Any,
        policy: ExecutionConfiguration,
    ) -> None:
        with span("ekbatan.action.persist") as persisting:
            by_shard = self._shards_to_commit(action_name, plan, policy, persisting)
            action_id = uuid.uuid4()
            committed: list[ShardIdentifier] = []
            for shard, slices in by_shard.items():
                try:
                    await self._commit_shard(shard, slices, action_name, action_id, params, started)
                    committed.append(shard)
                except BaseException as error:
                    self._partially_committed(action_name, committed, shard, error, persisting)
                    raise

    async def _commit_shard(
        self,
        shard: ShardIdentifier,
        staged: Mapping[type, PersistableChanges],
        action_name: str,
        action_id: uuid.UUID,
        params: Any,
        started: Any,
    ) -> None:
        """One shard, one transaction."""
        async with self.registry.manager(shard).transaction() as tx:
            await self.changes.persist(
                tx,
                staged,
                namespace=self.namespace,
                action_name=action_name,
                params=params,
                started=started,
                completed=self.clock.instant(),
                action_id=action_id,
            )


class AsyncActionExecutorBuilder(
    _ExecutorBuilder[AsyncDatabaseRegistry, AsyncEventPersister, AsyncActionExecutor]
):
    """Fluent builder for :class:`AsyncActionExecutor` - the sync
    :class:`~ekbatan.action.ActionExecutorBuilder`'s setters and checks, taking an
    :class:`~ekbatan.aio.AsyncDatabaseRegistry` and an
    :class:`~ekbatan.aio.AsyncEventPersister`. Obtain via :meth:`AsyncActionExecutor.builder`."""

    __slots__ = ()
    _builds = AsyncActionExecutor
