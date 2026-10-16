"""A keyed lock refuses to be taken on an asyncio event loop's own thread.

A holder is a thread, as in Java. On an event loop every request shares one, so a second
request would "re-enter" the first one's lock and both would get in - found by running it in the
Dracula sweep (bug 3). Ekbatan is synchronous, so the answer is a worker thread per call, where
each request is its own holder again; taking a lock anywhere else is refused, loudly.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta

import pytest
from sqlalchemy import create_engine

from ekbatan.errors import ValidationError
from ekbatan.locks import (
    MariaDBKeyedLockProvider,
    MySQLKeyedLockProvider,
    PostgresKeyedLockProvider,
)
from ekbatan.locks.base import check

HOLD = timedelta(seconds=5)


def on_the_loop[T](call: object) -> T:
    async def main() -> T:
        return call()  # type: ignore[operator, no-any-return]

    return asyncio.run(main())


def test_taking_a_lock_on_the_loop_is_refused() -> None:
    with pytest.raises(ValidationError, match="asyncio event loop") as raised:
        on_the_loop(lambda: check("wallet:1", HOLD))
    assert "asyncio.to_thread" in str(raised.value), "and it says what to do instead"


@pytest.mark.parametrize(
    "provider", [PostgresKeyedLockProvider, MySQLKeyedLockProvider, MariaDBKeyedLockProvider]
)
def test_every_provider_refuses_before_touching_its_database(provider: type) -> None:
    """The engine points nowhere a lock could be taken; the refusal comes first."""
    locks = provider(create_engine("sqlite://"))
    with pytest.raises(ValidationError, match="asyncio event loop"):
        on_the_loop(lambda: locks.acquire("wallet:1", HOLD))
    with pytest.raises(ValidationError, match="asyncio event loop"):
        on_the_loop(lambda: locks.try_acquire("wallet:1", timedelta(0), HOLD))


def _gevent_patched_threads() -> bool:
    monkey = sys.modules.get("gevent.monkey")
    return monkey is not None and monkey.is_module_patched("threading")


@pytest.mark.skipif(
    _gevent_patched_threads(),
    reason="under gevent a worker 'thread' is a greenlet on the loop's own OS thread",
)
def test_a_worker_thread_from_the_loop_is_fine() -> None:
    """`asyncio.to_thread` - and a plain `def` endpoint, which runs on such a thread."""

    async def main() -> None:
        await asyncio.to_thread(check, "wallet:1", HOLD)

    asyncio.run(main())


def test_outside_any_loop_is_fine() -> None:
    check("wallet:1", HOLD)  # asyncio imported, no loop running
