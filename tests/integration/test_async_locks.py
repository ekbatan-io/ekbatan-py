"""What only `ekbatan.aio.locks` does, tested natively, as coroutines.

The sync lock suites run both ways through `tests/modes.py` (`test_locks.py`,
`test_redis_locks.py`). What is here has no sync counterpart:

- **the holder is the task**: the same task re-enters; a task it starts inside the held region -
  `asyncio.gather` included - is another holder and waits;
- **a cancelled or timed-out wait** strands no connection and leaves the key free;
- **a cancelled holder** gives the key back on its way out of `async with`.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest

from conftest import NOT_UNDER_GEVENT, under_gevent
from ekbatan.aio import AsyncDatabaseRegistry
from ekbatan.aio.locks import (
    AsyncMariaDBKeyedLockProvider,
    AsyncMySQLKeyedLockProvider,
    AsyncPostgresKeyedLockProvider,
)
from fixtures.logins import single
from fixtures.schema import metadata

pytestmark = pytest.mark.skipif(under_gevent(), reason=NOT_UNDER_GEVENT)

HOLD = timedelta(seconds=30)
NOW = timedelta(0)

PROVIDERS = {
    "postgres": AsyncPostgresKeyedLockProvider,
    "mysql": AsyncMySQLKeyedLockProvider,
    "mariadb": AsyncMariaDBKeyedLockProvider,
}


def run_sql(backend: Any, dialect: str, scenario: Any) -> None:
    """``scenario(locks, registry)`` on a fresh async registry, on its own event loop."""
    if dialect not in PROVIDERS:
        pytest.skip(f"{dialect} has no advisory lock")
    backend.prepare("ekbatan_async_locks", metadata)

    async def main() -> None:
        registry = AsyncDatabaseRegistry.from_config(single(backend.url("ekbatan_async_locks")))
        try:
            await scenario(PROVIDERS[dialect](registry.default.primary), registry)
        finally:
            await registry.aclose()

    asyncio.run(main())


def run_redis(redis_client: Any, scenario: Any) -> None:
    import redis.asyncio

    from ekbatan.aio.locks.redis import AsyncRedisKeyedLockProvider

    where = redis_client.connection_pool.connection_kwargs

    async def main() -> None:
        client = redis.asyncio.Redis(host=where["host"], port=where["port"])
        try:
            await scenario(AsyncRedisKeyedLockProvider(client, namespace="t-async"), None)
        finally:
            await client.aclose()

    asyncio.run(main())


def in_use(registry: AsyncDatabaseRegistry | None) -> int:
    return 0 if registry is None else registry.default.primary.sync_engine.pool.checkedout()


# ------------------------------------------------------------------------- the scenarios


async def a_task_re_enters_and_another_task_waits(locks: Any, registry: Any) -> None:
    async with locks.acquire("k-tasks", HOLD):
        async with locks.acquire("k-tasks", HOLD):
            pass  # the same task: re-entry, at once

        async def other() -> Any:
            return await locks.try_acquire("k-tasks", NOW, HOLD)

        # Started inside the held region, and still another holder - so it is kept out.
        assert await asyncio.gather(other(), other()) == [None, None]
        assert await asyncio.create_task(other()) is None
    lease = await locks.try_acquire("k-tasks", NOW, HOLD)
    assert lease is not None, "free once the holder left"
    await lease.release()


async def a_waiter_cancelled_by_a_timeout_strands_nothing(locks: Any, registry: Any) -> None:
    async with locks.acquire("k-timeout", HOLD):
        during = in_use(registry)

        async def waiter() -> None:
            async with asyncio.timeout(0.5):
                await locks.acquire("k-timeout", HOLD)

        with pytest.raises(TimeoutError):
            await asyncio.create_task(waiter())
        await asyncio.sleep(0.1)
        assert in_use(registry) == during, "the cancelled wait gave its connection back"
    assert in_use(registry) == 0
    lease = await locks.try_acquire("k-timeout", NOW, HOLD)
    assert lease is not None, "and took no lock with it"
    await lease.release()


async def a_cancelled_holder_gives_the_key_back(locks: Any, registry: Any) -> None:
    holding = asyncio.Event()

    async def holder() -> None:
        async with locks.acquire("k-cancel", HOLD):
            holding.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(holder())
    await holding.wait()
    assert await locks.try_acquire("k-cancel", NOW, HOLD) is None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    lease = await locks.try_acquire("k-cancel", NOW, HOLD)
    assert lease is not None, "released on the way out of `async with`"
    await lease.release()
    assert in_use(registry) == 0


async def tasks_contending_serialise(locks: Any, registry: Any) -> None:
    counter = {"n": 0}

    async def increment() -> None:
        async with locks.acquire("k-count", HOLD):
            seen = counter["n"]
            await asyncio.sleep(0.005)
            counter["n"] = seen + 1

    await asyncio.gather(*(increment() for _ in range(12)))
    assert counter["n"] == 12


SCENARIOS = [
    a_task_re_enters_and_another_task_waits,
    a_waiter_cancelled_by_a_timeout_strands_nothing,
    a_cancelled_holder_gives_the_key_back,
    tasks_contending_serialise,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.__name__)
def test_sql(backend, dialect, scenario):
    run_sql(backend, dialect, scenario)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.__name__)
def test_redis(redis_client, scenario):
    run_redis(redis_client, scenario)
