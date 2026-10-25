"""Run the worker until you stop it.

    make run

The settings are in `ekbatan.yaml`, defaulted to the two servers in compose.yaml. Point it
elsewhere through the variables its placeholders read - and start two of these to see the part
that only matters in a cluster, each slot running once:

    WALLET_GLOBAL_URL=postgresql+psycopg://localhost:5432/wallet_global \\
    WALLET_MEXICO_URL=postgresql+psycopg://localhost:5433/wallet_mexico \\
    WALLET_DATABASE_USERNAME=ekbatan WALLET_DATABASE_PASSWORD=ekbatan \\
      make run
"""

from __future__ import annotations

import logging
import threading

from .app import Worker, build


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    worker = build()

    run(worker)


def run(worker: Worker) -> None:
    """Start, and run until the process is stopped.

    No signal handling here, as in Java's worker: the job registry handles SIGTERM itself, since
    nothing else in this process does. On a container stop the jobs in flight finish, then the
    process stops. Ctrl-C ends the wait below, and `close()` stops the jobs and closes the pools.
    """
    worker.start()
    logging.getLogger("examples.job_worker_async_pg").info("worker started; ctrl-c to stop")
    try:
        threading.Event().wait()
    finally:
        worker.close()
        logging.getLogger("examples.job_worker_async_pg").info("worker stopped")


if __name__ == "__main__":
    main()
