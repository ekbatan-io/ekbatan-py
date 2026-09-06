"""`DaemonThreadPool` - the worker pool executions run on.

It exists because `ThreadPoolExecutor`'s workers are non-daemon, and the interpreter joins every
non-daemon thread at exit. `tests/integration/test_shutdown_exit.py` is what proves the
consequence; these check the pool behaves like a pool.
"""

from __future__ import annotations

import threading
import time

import pytest

from db_scheduler.pool import DaemonThreadPool, WorkerPool

#: Real threads need a real bound, generous enough not to flake on a loaded machine.
PATIENCE = 10.0


def a_pool(workers: int = 2) -> DaemonThreadPool:
    return DaemonThreadPool(workers, thread_name_prefix="test-worker-")


class TestRunningWork:
    def test_it_runs_what_it_is_given(self) -> None:
        pool = a_pool(1)
        done = threading.Event()

        pool.submit(done.set)

        assert done.wait(PATIENCE)
        pool.shutdown()

    def test_several_workers_run_in_parallel(self) -> None:
        """Otherwise `threads(10)` would be a lie and every execution would queue behind one.

        The barrier is the proof: it only releases once all three jobs are running at the same
        moment, so a pool that ran them one after another would sit here until it timed out.
        """
        pool = a_pool(3)
        all_three = threading.Barrier(3, timeout=PATIENCE)
        cleared = threading.Semaphore(0)

        def wait_for_the_others() -> None:
            all_three.wait()
            cleared.release()

        for _ in range(3):
            pool.submit(wait_for_the_others)

        for _ in range(3):
            assert cleared.acquire(timeout=PATIENCE), "the jobs did not run concurrently"
        pool.shutdown()

    def test_every_job_runs(self) -> None:
        pool = a_pool(4)
        counter = threading.Semaphore(0)

        for _ in range(50):
            pool.submit(counter.release)

        for _ in range(50):
            assert counter.acquire(timeout=PATIENCE)
        pool.shutdown()

    def test_the_workers_are_daemon_threads(self) -> None:
        """The whole point: the interpreter must not join them at exit."""
        pool = a_pool(2)

        assert all(thread.daemon for thread in pool._threads)
        pool.shutdown()

    def test_the_threads_are_named(self) -> None:
        """So a stack dump during an incident says which threads are the scheduler's."""
        pool = DaemonThreadPool(2, thread_name_prefix="db-scheduler-worker-")

        assert all(t.name.startswith("db-scheduler-worker-") for t in pool._threads)
        pool.shutdown()


class TestFailingWork:
    def test_a_job_that_raises_does_not_kill_its_worker(self) -> None:
        """A worker that dies takes a slot with it, and the pool silently shrinks until nothing
        runs at all."""
        pool = a_pool(1)
        after = threading.Event()

        def explode() -> None:
            raise RuntimeError("boom")

        pool.submit(explode)
        pool.submit(after.set)

        assert after.wait(PATIENCE), "the worker died with the job"
        assert pool.alive_workers == 1
        pool.shutdown()

    def test_and_it_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        pool = a_pool(1)
        done = threading.Event()

        def explode() -> None:
            try:
                raise RuntimeError("boom")
            finally:
                done.set()

        with caplog.at_level("ERROR", logger="db_scheduler.pool"):
            pool.submit(explode)
            assert done.wait(PATIENCE)
            pool.shutdown()

        assert "Unhandled exception in worker thread" in caplog.text


class TestShutdown:
    def test_it_stops_the_workers(self) -> None:
        pool = a_pool(3)

        pool.shutdown(wait=True)

        assert pool.alive_workers == 0
        assert pool.is_shutdown is True

    def test_submitting_afterwards_is_refused(self) -> None:
        """`ThreadPoolExecutor` raises the same way, and the scheduler relies on it to keep its
        in-flight count straight."""
        pool = a_pool(1)
        pool.shutdown()

        with pytest.raises(RuntimeError, match="after shutdown"):
            pool.submit(lambda: None)

    def test_queued_work_can_be_cancelled(self) -> None:
        """The closest Python has to Java's `shutdownNow()`, which also interrupts the running
        ones."""
        pool = a_pool(1)
        blocking, release = threading.Event(), threading.Event()
        never_ran = threading.Event()

        def hold() -> None:
            blocking.set()
            release.wait(PATIENCE)

        pool.submit(hold)
        assert blocking.wait(PATIENCE)
        pool.submit(never_ran.set)

        pool.shutdown(wait=False, cancel_futures=True)
        release.set()
        time.sleep(0.3)

        assert not never_ran.is_set(), "a queued job ran after cancel_futures"

    def test_it_waits_for_running_work_when_asked(self) -> None:
        pool = a_pool(1)
        finished = threading.Event()

        def slow() -> None:
            time.sleep(0.2)
            finished.set()

        pool.submit(slow)
        pool.shutdown(wait=True)

        assert finished.is_set()

    def test_shutting_down_twice_is_harmless(self) -> None:
        pool = a_pool(2)

        pool.shutdown()
        pool.shutdown()

        assert pool.alive_workers == 0

    def test_shutdown_does_not_block_on_a_hanging_job_unless_asked(self) -> None:
        """`wait=False` must return; the caller decides how long to give it."""
        pool = a_pool(1)
        started, never = threading.Event(), threading.Event()

        def hangs() -> None:
            started.set()
            never.wait(60)

        try:
            pool.submit(hangs)
            assert started.wait(PATIENCE)

            began = time.monotonic()
            pool.shutdown(wait=False, cancel_futures=True)

            assert time.monotonic() - began < 1.0
        finally:
            never.set()


class TestTheProtocol:
    def test_the_pool_satisfies_it(self) -> None:
        pool = a_pool(1)
        assert isinstance(pool, WorkerPool)
        pool.shutdown()

    def test_a_thread_pool_executor_does_too(self) -> None:
        """So a caller who wants the standard pool's semantics can still pass one - and accept
        what it costs at exit."""
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as standard:
            assert isinstance(standard, WorkerPool)

    def test_the_worker_count_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            DaemonThreadPool(0)

    def test_repr(self) -> None:
        pool = a_pool(2)
        assert "workers=2" in repr(pool)
        pool.shutdown()
