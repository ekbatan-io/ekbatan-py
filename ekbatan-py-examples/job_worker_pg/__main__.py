"""Run the worker until you stop it.

    cd ekbatan-py-examples && uv run python -m job_worker_pg

Defaults to a local SQLite file so it runs with nothing set up. Point it at Postgres to see the
part that only matters in a cluster - start two of these and watch each slot run once:

    WORKER_DATABASE_URL=postgresql+psycopg://ekbatan:ekbatan@localhost:5432/wallet \\
      cd ekbatan-py-examples && uv run python -m job_worker_pg
"""

from __future__ import annotations

import logging
import signal
import threading
from decimal import Decimal
from uuid import uuid4

from .actions import OpenWalletAction, OpenWalletParams
from .app import Worker, build


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    worker = build()

    # Something for the stipend job to find, so the worker has visible work on a fresh
    # database. A real worker would not seed anything.
    worker.executor.execute(
        OpenWalletAction,
        OpenWalletParams(owner_id=uuid4(), currency="EUR", opening_balance=Decimal("0.00")),
    )

    run(worker)


def run(worker: Worker) -> None:
    """Start, wait, and stop cleanly on SIGINT or SIGTERM.

    This is the half the framework will not do for you unless asked. Java's `JobRegistry`
    registers a JVM shutdown hook by default and nobody minds, because those are additive: any
    number can be registered and none displaces another. CPython has **one** handler slot per
    signal, so installing one takes the application's SIGTERM away from it - which is why
    `register_shutdown_hook()` exists and is off until you call it.

    A worker process like this one is the case it was written for, and the two ways are equal:

        job_registry().engine(...).with_jobs(...).register_shutdown_hook().build()

    would do all of the below, and correctly. It is spelled out here instead because an example
    that hides the mechanism teaches nothing about it.

    **Note what the previous handler does.** An earlier version of this function wrote::

        signal.signal(received, lambda *_: stopping.set())

    which is correct here - this is `__main__`, nothing had installed anything - and wrong the
    moment it is copied into a gunicorn or uvicorn worker, where the server has handlers of its
    own and they would be silently dropped. Keep what you find, and call it.

    Without any of this, a container stop kills the process with executions still picked, and
    their rows wait out the dead-execution window before another instance can take them.
    """
    stopping = threading.Event()
    previous: dict[int, object] = {}

    def wind_up(number: int, frame: object) -> None:
        stopping.set()
        before = previous.get(number)
        if callable(before):
            before(number, frame)

    for received in (signal.SIGINT, signal.SIGTERM):
        previous[received] = signal.getsignal(received)
        signal.signal(received, wind_up)

    worker.start()
    logging.getLogger("examples.job_worker_pg").info("worker started; ctrl-c to stop")
    try:
        stopping.wait()
    finally:
        worker.close()
        logging.getLogger("examples.job_worker_pg").info("worker stopped")


if __name__ == "__main__":
    main()
