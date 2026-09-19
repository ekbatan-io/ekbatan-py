"""Running one bounded batch on every shard, at the same time rather than one after another.

Java forks a virtual thread per shard in both event jobs and joins them. This does the same
with a thread pool, one thread per shard, and takes no configuration - because Java takes none
either, and a knob this port has and that one does not is a divergence bought for nothing.

The work is a database round trip per shard against a *different* database, so it is I/O the
whole way down: the GIL is released for all of it, and this is one of the cases where threads
in CPython buy real wall-clock. A deployment with many shards already holds a connection pool
per shard, so a thread per shard for the length of a round is in proportion to what it already
has - the same judgement Java makes by forking one virtual thread each.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor

LOG = logging.getLogger("ekbatan.events")


def across_shards[S, T](shards: Sequence[S], work: Callable[[S], T]) -> list[T]:
    """``work`` on every shard, concurrently, with the results in ``shards`` order.

    Runs inline - no pool, no thread - for a single shard, which is the unsharded case and
    most deployments.

    Every shard is started before any failure is raised, as Java's version does: it submits
    the whole round and only then walks the futures. A shard that fails therefore does not
    hold back the rest of the round, and the first failure in shard order is what reaches the
    caller.
    """
    if len(shards) <= 1:
        return [work(shard) for shard in shards]

    with ThreadPoolExecutor(max_workers=len(shards), thread_name_prefix="ekbatan-shard") as pool:
        futures = [pool.submit(work, shard) for shard in shards]
        results: list[T] = []
        failure: BaseException | None = None
        for shard, future in zip(shards, futures, strict=True):
            try:
                results.append(future.result())
            except BaseException as error:  # re-raised below, once the round has finished
                LOG.warning("shard %s failed this round: %s", shard, error)
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure
        return results
