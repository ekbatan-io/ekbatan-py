"""A started registry drains when the process ends - measured in a real process, every way it ends.

Java's `registerShutdownHook` defaults to on because a JVM shutdown hook only adds to a list.
`atexit` is that list here, and `stop_on_exit` - on by default - puts the registry on it. What
`atexit` does not do is run on SIGTERM: the JVM turns SIGTERM into an orderly exit and CPython
kills the process. So SIGTERM is the one case left to `register_shutdown_hook()`, which is
opt-in because it takes the process's one handler slot.

Each case below starts a child process with a job that is part way through a two-second body,
ends the process, and reads whether the job got to finish. Two traps are handled on purpose,
because each produced a convincing wrong answer while this was being written:

- A child started from a non-interactive shell inherits SIGINT as *ignored*, so Ctrl-C does
  nothing and the child simply runs out its sleep - which looks exactly like a drain. The child
  puts back the handler an interactive interpreter has.
- Each case writes its own log, created empty before the child starts, so a line left over from
  an earlier run cannot be read as this one's.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from ekbatan.distributedjobs import DistributedJob, job_registry

ROOT = Path(__file__).resolve().parents[2]

CHILD = r"""
import signal, sys, time
signal.signal(signal.SIGINT, signal.default_int_handler)  # what an interactive terminal gives

from datetime import timedelta
from pathlib import Path
from sqlalchemy import MetaData, create_engine
from db_scheduler.task.schedule import FixedDelay
from ekbatan.distributedjobs import DistributedJob, job_registry
from ekbatan.schema import scheduled_tasks_table

log, db, end = Path(sys.argv[1]), sys.argv[2], sys.argv[5]
stop_on_exit, hooked = sys.argv[3] == "1", sys.argv[4] == "1"

import logging
logging.basicConfig(filename=str(log), level=logging.INFO, format="%(message)s")
logging.getLogger("db_scheduler").setLevel(logging.WARNING)

def note(line):
    with open(log, "a") as f:
        f.write(line + "\n")

engine = create_engine(f"sqlite:///{db}")
metadata = MetaData()
scheduled_tasks_table(metadata)
metadata.create_all(engine)

class Slow(DistributedJob):
    def name(self): return "slow"
    def schedule(self): return FixedDelay(timedelta(hours=1))
    def execute(self, ctx):
        note("started")
        time.sleep(2)
        note("finished")

builder = job_registry().engine(engine).with_job(Slow()).poll_interval(timedelta(milliseconds=100))
builder = builder.stop_on_exit(stop_on_exit)
if hooked:
    builder = builder.register_shutdown_hook()
builder.build().start()

while "started" not in log.read_text():
    time.sleep(0.02)
note("running")
if end == "return":
    sys.exit(0)
time.sleep(60)
"""


def run(tmp_path: Path, *, end: str, stop_on_exit: bool = True, hooked: bool = False):
    """Start the child, end it the given way once the job is mid-body, and report."""
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    log = tmp_path / "run.log"
    log.write_text("")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    child = subprocess.Popen(
        [
            sys.executable,
            str(script),
            str(log),
            str(tmp_path / "jobs.db"),
            "1" if stop_on_exit else "0",
            "1" if hooked else "0",
            end,
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 30
    while "running" not in log.read_text():
        assert child.poll() is None, f"the child died early: {child.stderr.read().decode()}"
        assert time.monotonic() < deadline, "the job never started"
        time.sleep(0.05)
    if end != "return":
        child.send_signal(getattr(signal, end))
    code = child.wait(timeout=30)
    return code, log.read_text().splitlines()


# -- the table, case by case --------------------------------------------------------------------


def test_a_normal_exit_lets_the_running_job_finish(tmp_path):
    code, lines = run(tmp_path, end="return")

    assert code == 0
    assert "finished" in lines, "the registry was not drained at exit"


def test_ctrl_c_lets_the_running_job_finish(tmp_path):
    code, lines = run(tmp_path, end="SIGINT")

    assert code == -signal.SIGINT, "and the process still dies of the signal (130 in a shell)"
    assert "finished" in lines


def test_turned_off_the_running_job_is_cut_off(tmp_path):
    code, lines = run(tmp_path, end="return", stop_on_exit=False)

    assert code == 0
    assert "finished" not in lines, "something drained a registry that asked not to be"


def test_sigterm_alone_does_not_drain(tmp_path):
    """The limit, pinned so nobody documents it away: CPython kills the process on SIGTERM
    and no `atexit` hook runs. This is why `register_shutdown_hook()` exists."""
    code, lines = run(tmp_path, end="SIGTERM")

    assert code == -signal.SIGTERM
    assert "finished" not in lines


def test_sigterm_with_the_hook_drains(tmp_path):
    code, lines = run(tmp_path, end="SIGTERM", hooked=True)

    assert code == -signal.SIGTERM, "drained, and then still died of the signal"
    assert "finished" in lines


def test_ctrl_c_with_the_hook_drains_once(tmp_path):
    """Both installed: the hook drains and takes the `atexit` entry off, so the exit does not
    drain a second time."""
    code, lines = run(tmp_path, end="SIGINT", hooked=True)

    assert code == -signal.SIGINT
    assert "finished" in lines
    assert lines.count("JobRegistry stopped") == 1, "drained by the hook and again at exit"


# -- the list itself, in this process -----------------------------------------------------------


class NeverDue(DistributedJob):
    def name(self) -> str:
        return "never-due"

    def schedule(self):
        from db_scheduler.task.schedule import DisabledSchedule

        return DisabledSchedule()

    def execute(self, ctx) -> None:  # pragma: no cover - never becomes due
        raise AssertionError("a disabled job ran")


@pytest.fixture
def engine(registry):
    return registry.default.primary


def test_starting_adds_one_entry_and_stopping_takes_it_off(engine):
    before = atexit._ncallbacks()
    jobs = (
        job_registry()
        .engine(engine)
        .with_job(NeverDue())
        .poll_interval(timedelta(seconds=1))
        .build()
    )

    jobs.start()
    assert atexit._ncallbacks() == before + 1

    jobs.stop()
    assert atexit._ncallbacks() == before, "a stopped registry was left on the list"


def test_building_alone_adds_nothing(engine):
    """Only a started registry has anything to drain."""
    before = atexit._ncallbacks()

    job_registry().engine(engine).with_job(NeverDue()).build()

    assert atexit._ncallbacks() == before


def test_turned_off_it_is_never_on_the_list(engine):
    before = atexit._ncallbacks()
    jobs = (
        job_registry()
        .engine(engine)
        .with_job(NeverDue())
        .poll_interval(timedelta(seconds=1))
        .stop_on_exit(False)
        .build()
    )
    jobs.start()
    try:
        assert atexit._ncallbacks() == before
    finally:
        jobs.stop()


def test_a_manual_registry_is_never_on_the_list(engine):
    """A test that starts one and forgets it must not have it drained, against a database the
    suite has already torn down, as the interpreter exits."""
    before = atexit._ncallbacks()
    jobs = job_registry().engine(engine).with_job(NeverDue()).build_manual()
    jobs.start()
    try:
        assert atexit._ncallbacks() == before
    finally:
        jobs.stop()
