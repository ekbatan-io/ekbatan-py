"""Doing a round's work at the same time rather than one item after another.

Java forks a virtual thread per item in both event jobs - one per shard in a round, and one
per notification in a batch - and joins them. This is the same shape with the only threads
CPython has, and it takes no configuration, because Java takes none either: a knob this port
has and that one does not is a divergence bought for nothing.

**The pool is built once and reused.** A round happens every poll delay, forever, and building
a pool each time would pay for the threads again on every one. A `ThreadPoolExecutor` also
grows lazily - declaring one of a hundred costs nothing until a hundred things are actually
submitted, and a quiet round of three creates three threads - so the size is a ceiling rather
than a reservation, and there is no reason to size it to the work in front of you.

Threads earn their keep here for the reason they usually do not in CPython: a shard's batch is
a round trip to a *different* database, and a handler is application code that is almost always
waiting on something. The GIL is released for both.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor


class Concurrently:
    """A lazily built, reused pool, and a way to run one round through it."""

    __slots__ = ("_log", "_name", "_pool", "_size")

    def __init__(self, size: int, *, name: str, log: logging.Logger) -> None:
        self._size = max(size, 1)
        self._name = name
        self._log = log  # the owning job's, so a failed round is logged under its class
        self._pool: ThreadPoolExecutor | None = None

    def run[I, T](self, items: Sequence[I], work: Callable[[I], T]) -> list[T]:
        """``work`` on every item, concurrently, with the results in ``items`` order.

        Runs inline - no pool, no thread - for a single item. That is the unsharded round and
        the quiet batch, which between them are most of what a deployment does.

        Every item is started before any failure is raised, as Java's version does: it submits
        the whole round and only then walks the futures. One item that fails therefore does not
        hold back the rest, and the first failure in order is what reaches the caller.
        """
        if len(items) <= 1:
            return [work(item) for item in items]

        pool = self._ensure()
        futures = [pool.submit(work, item) for item in items]
        results: list[T] = []
        failure: BaseException | None = None
        for item, future in zip(items, futures, strict=True):
            try:
                results.append(future.result())
            except BaseException as error:  # re-raised below, once the round has finished
                self._log.warning("%s: %r failed this round: %s", self._name, item, error)
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure
        return results

    def _ensure(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=self._size, thread_name_prefix=self._name)
        return self._pool

    def close(self) -> None:
        """Let the threads go. Safe to call twice, and safe never to call at all - an
        executor left open is joined at interpreter exit."""
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None
