"""A sync transaction belongs to the thread that opened it.

The open transaction lives in a `ContextVar`, and `asyncio.to_thread` - with every helper built on
it - copies the caller's context into the new thread. Without a check, that thread found the
transaction and used its connection while the opening thread was using it too: one connection,
two threads, statements from both mixed into one transaction. The other thread is now refused,
loudly, rather than handed a connection of its own: its work was meant to be inside the
transaction, and outside it a rollback would leave that work behind.

`ThreadPoolExecutor.submit` copies no context, so a pool worker never finds the transaction at
all - `test_concurrency` pins that.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from ekbatan import TransactionThreadError


def test_a_thread_started_with_to_thread_is_refused_the_transaction(registry):
    manager = registry.default

    with manager.transaction(), pytest.raises(TransactionThreadError) as refused:
        asyncio.run(asyncio.to_thread(manager.current))

    message = str(refused.value)
    assert f"shard {manager.label} was opened by another thread" in message
    assert "asyncio.to_thread" in message


def test_a_repository_s_connection_in_such_a_thread_is_refused_too(registry):
    """`connection()` is what a repository's reads and writes go through."""
    manager = registry.default

    def read() -> object:
        with manager.connection() as conn:
            return conn

    with manager.transaction(), pytest.raises(TransactionThreadError):
        asyncio.run(asyncio.to_thread(read))


def test_updates_gathered_onto_threads_inside_a_transaction_are_all_refused(registry):
    """The shape that would have mixed three threads' statements on one connection."""
    manager = registry.default
    reached: list[int] = []

    def update() -> None:
        reached.append(threading.get_ident())
        with manager.connection():
            pass

    async def all_three() -> list[BaseException | None]:
        return await asyncio.gather(
            *(asyncio.to_thread(update) for _ in range(3)), return_exceptions=True
        )

    with manager.transaction():
        outcomes = asyncio.run(all_three())

    assert len(reached) == 3
    assert all(isinstance(o, TransactionThreadError) for o in outcomes), outcomes


def test_the_opening_thread_still_finds_its_own_transaction(registry):
    manager = registry.default

    with manager.transaction() as tx:
        assert manager.current() is tx.connection
        with manager.connection() as conn:
            assert conn is tx.connection


def test_once_the_transaction_ends_another_thread_is_not_refused(registry):
    manager = registry.default

    with manager.transaction():
        pass

    assert asyncio.run(asyncio.to_thread(manager.current)) is None
