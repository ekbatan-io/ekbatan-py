"""Run the worker until you stop it.

    uv run python -m examples.job_worker_pg

Defaults to a local SQLite file so it runs with nothing set up. Point it at Postgres to see the
part that only matters in a cluster - start two of these and watch each slot run once:

    WORKER_DATABASE_URL=postgresql+psycopg://ekbatan:ekbatan@localhost:5432/wallet \\
      uv run python -m examples.job_worker_pg
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

    The handler is here rather than inside the framework on purpose. A library that installed
    a process-wide `signal.signal` would overwrite whatever the application - or gunicorn, or
    uvicorn - had already put there, and there is only one handler per signal per process. Java
    can register a JVM shutdown hook without that problem, and its `JobRegistry` does so by
    default; the equivalent here is six lines the application owns.

    Without this, a container stop kills the process with executions still picked, and their
    rows wait out the dead-execution window before another instance can take them.
    """
    stopping = threading.Event()
    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, lambda *_: stopping.set())

    worker.start()
    logging.getLogger("examples.job_worker_pg").info("worker started; ctrl-c to stop")
    try:
        stopping.wait()
    finally:
        worker.close()
        logging.getLogger("examples.job_worker_pg").info("worker stopped")


if __name__ == "__main__":
    main()
