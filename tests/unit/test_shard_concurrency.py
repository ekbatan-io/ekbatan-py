"""One round over many shards, run concurrently - the half of Java's virtual threads that ports.

Java forks a virtual thread per shard in both event jobs and joins them. A thread here is an OS
thread, so the shape needs a ceiling and a reason to skip the pool entirely; what it does not
need is a different contract. These pin the contract.

Measured on the helper alone, one 40 ms round trip per shard: eight shards go from 348 ms to
45 ms, a 7.8x speed-up, and sixteen from 696 ms to 47 ms. A single shard is untouched, which is
the deployment most of them are.

No knob. Java has none, and a setting this port has and that one does not is a divergence
bought for nothing.
"""

from __future__ import annotations

import threading
import time

import pytest

from ekbatan.events._across import across_shards


def test_a_single_shard_runs_inline_with_no_thread_at_all():
    """The unsharded case, which is most of them. A pool here would be pure cost."""
    ran_on = []

    across_shards([0], lambda shard: ran_on.append(threading.current_thread().name))

    assert ran_on == [threading.current_thread().name]


def test_no_shards_at_all_is_not_an_error():
    assert across_shards([], lambda shard: 1) == []


def test_shards_run_at_the_same_time_rather_than_one_after_another():
    """Four shards that each block until all four have arrived. Sequentially this deadlocks;
    the barrier is what proves they are genuinely concurrent rather than merely fast."""
    together = threading.Barrier(4, timeout=5)

    result = across_shards(list(range(4)), lambda shard: together.wait() is not None)

    assert len(result) == 4


def test_results_come_back_in_shard_order_not_completion_order():
    def work(shard: int) -> int:
        time.sleep(0.02 if shard == 0 else 0)  # shard 0 finishes last
        return shard

    assert across_shards([0, 1, 2, 3], work) == [0, 1, 2, 3]


def test_one_shard_failing_does_not_hold_back_the_rest_of_the_round():
    """Java submits the whole round before walking the futures, so a bad shard does not stop
    its siblings. The first failure in shard order is what the caller sees."""
    visited = []

    def work(shard: int) -> int:
        visited.append(shard)
        if shard in (1, 2):
            raise RuntimeError(f"shard {shard} is unreachable")
        return shard

    with pytest.raises(RuntimeError, match="shard 1 is unreachable"):
        across_shards([0, 1, 2, 3], work)

    assert sorted(visited) == [0, 1, 2, 3], "every shard was still given its turn"


def test_the_pool_is_never_wider_than_the_shards_it_has():
    """One thread per shard, as Java forks one virtual thread per shard - and not one more."""
    names = set()

    across_shards(
        list(range(3)),
        lambda shard: names.add(threading.current_thread().name) or time.sleep(0.01),
    )

    assert len(names) <= 3
