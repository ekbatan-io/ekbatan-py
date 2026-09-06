"""A scheduler must never keep the process alive.

The one that matters. `stop()` returning is not the same as the process being able to exit:
Python joins every non-daemon thread at interpreter shutdown, so a handler that never returns
outlives `stop()` and hangs the process for ever. There is no `Thread.interrupt()` to shake it
loose, so the pool's workers are daemon threads and a stuck execution is *abandoned* instead.

Abandoning it is safe precisely because it is the failure this library exists to survive: the
row stays picked, its heartbeat stops, and the dead-execution detector revives it. Hanging the
process would turn that recoverable state into an unrecoverable one.

This runs in a subprocess because that is the only place the behaviour exists - nothing about it
is observable from inside the test process.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

#: Generous: the work itself takes well under a second, so anything approaching this means the
#: process is not exiting rather than merely being slow.
EXIT_PATIENCE = 60

SCRIPT = """
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import MetaData, create_engine

from db_scheduler.builder import create
from db_scheduler.jdbc import scheduled_tasks_table
from db_scheduler.task.helper import Tasks

engine = create_engine("sqlite:///{db}")
metadata = MetaData()
scheduled_tasks_table(metadata)
metadata.create_all(engine)

started, never = threading.Event(), threading.Event()


def hangs(instance, context):
    started.set()
    never.wait(3600)          # a handler that ignores shutdown entirely


task = Tasks.one_time("hangs").execute(hangs)
scheduler = (
    create(engine)
    .add_tasks(task)
    .threads(1)
    .polling_interval(timedelta(milliseconds=50))
    .shutdown_max_wait(timedelta(seconds=1))
    .build()
)
scheduler.start()
scheduler.schedule_if_not_exists(task.instance("an-id"), datetime.now(UTC))
assert started.wait(30), "the task never started"
scheduler.stop()
print("STOPPED", flush=True)
"""


def test_a_hanging_execution_does_not_keep_the_process_alive(tmp_path: Path) -> None:
    script = tmp_path / "hangs.py"
    script.write_text(textwrap.dedent(SCRIPT).format(db=tmp_path / "exit.db"))

    try:
        finished = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=EXIT_PATIENCE,
            cwd=Path(__file__).resolve().parent.parent.parent,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"the process did not exit within {EXIT_PATIENCE}s with an execution still "
            "running - a stuck handler is keeping it alive"
        )

    assert "STOPPED" in finished.stdout, finished.stderr
    assert finished.returncode == 0, finished.stderr
