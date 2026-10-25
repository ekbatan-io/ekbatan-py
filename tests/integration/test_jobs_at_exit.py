"""A started registry lets its running jobs finish when the process stops - measured in a real
process, every way it stops.

Java's `registerShutdownHook` is on by default, and the JVM runs its shutdown hooks however the
process ends. CPython has two mechanisms for that, and `register_shutdown_hook` - on by default
here too - uses both: `atexit` when the program ends by itself, and a SIGTERM handler when
nothing else in the process handles SIGTERM. Turned off, it uses neither.

Each case below starts a child process with a job that is part way through a two-second body,
stops the process, and reads whether the job got to finish. Two traps are handled on purpose,
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
import os, signal, sys, time
signal.signal(signal.SIGINT, signal.default_int_handler)  # what an interactive terminal gives

from datetime import timedelta
from pathlib import Path
from sqlalchemy import MetaData
from db_scheduler.task.schedule import FixedDelay
from ekbatan import DataSource
from ekbatan.config import DataSourceConfig
from ekbatan.distributedjobs import DistributedJob, job_registry
from ekbatan.schema import scheduled_tasks_table

log, db, hook, end, apps_own = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]

import logging
logging.basicConfig(filename=str(log), level=logging.INFO, format="%(message)s")
logging.getLogger("db_scheduler").setLevel(logging.WARNING)

def note(line):
    with open(log, "a") as f:
        f.write(line + "\n")

if apps_own == "1":
    def the_apps_own(number, frame):
        note("the app's own handler")
        os._exit(7)

    signal.signal(signal.SIGTERM, the_apps_own)

source = DataSource.from_config(DataSourceConfig(url=f"sqlite:///{db}"))
metadata = MetaData()
scheduled_tasks_table(metadata)
metadata.create_all(source.sqlalchemy_engine)

class Slow(DistributedJob):
    def name(self): return "slow"
    def schedule(self): return FixedDelay(timedelta(hours=1))
    def execute(self, ctx):
        note("started")
        time.sleep(2)
        note("finished")

builder = job_registry().data_source(source).with_job(Slow())
builder = builder.poll_interval(timedelta(milliseconds=100))
if hook != "default":
    builder = builder.register_shutdown_hook(hook == "on")
builder.build().start()

while "started" not in log.read_text():
    time.sleep(0.02)
note("running")
if end == "return":
    sys.exit(0)
time.sleep(60)
"""


def run(
    tmp_path: Path,
    *,
    end: str,
    hook: str = "default",
    apps_own: bool = False,
    again_after: str | None = None,
):
    """Start the child, stop it the given way once the job is mid-body, and report.

    `again_after`: once this line is in the log, send the same signal a second time.
    """
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
            hook,
            end,
            "1" if apps_own else "0",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    wait_for(child, log, "running")
    if end != "return":
        child.send_signal(getattr(signal, end))
        if again_after is not None:
            wait_for(child, log, again_after)
            child.send_signal(getattr(signal, end))
    code = child.wait(timeout=30)
    return code, log.read_text().splitlines()


def wait_for(child: subprocess.Popen[bytes], log: Path, line: str) -> None:
    deadline = time.monotonic() + 30
    while line not in log.read_text():
        assert child.poll() is None, (
            f"the child ended before logging {line!r}: {child.stderr.read().decode()}"  # type: ignore[union-attr]
        )
        assert time.monotonic() < deadline, f"the child never logged {line!r}"
        time.sleep(0.05)


# -- on, as it is by default --------------------------------------------------------------------


def test_a_normal_exit_lets_the_running_job_finish(tmp_path):
    code, lines = run(tmp_path, end="return")

    assert code == 0
    assert "finished" in lines, "the registry was not drained at exit"


def test_ctrl_c_lets_the_running_job_finish_once(tmp_path):
    code, lines = run(tmp_path, end="SIGINT")

    assert code == -signal.SIGINT, "and the process still dies of the signal (130 in a shell)"
    assert "finished" in lines
    assert lines.count("JobRegistry stopped") == 1, "stopped more than once"


def test_sigterm_lets_the_running_job_finish(tmp_path):
    """A container stop. Nothing else in this process handles SIGTERM, so the registry does: the
    job finishes, and then the process still dies of the signal, as it would have."""
    code, lines = run(tmp_path, end="SIGTERM")

    assert code == -signal.SIGTERM, "drained, and then still died of the signal (143 in a shell)"
    assert "finished" in lines, "the running job was cut off"
    assert lines.count("JobRegistry stopped") == 1
    assert any(line.startswith("Nothing else in this process handles SIGTERM") for line in lines)


def test_a_second_sigterm_stops_the_process_at_once(tmp_path):
    """For someone who will not wait: the first SIGTERM puts the default back before the jobs
    are waited for, so a second one ends the process on the spot."""
    code, lines = run(tmp_path, end="SIGTERM", again_after="Stopping JobRegistry")

    assert code == -signal.SIGTERM
    assert "finished" not in lines, "the second SIGTERM waited for the job"


def test_an_application_that_handles_sigterm_keeps_it(tmp_path):
    """Its handler was set before the registry started, so the registry leaves SIGTERM to it -
    as it leaves it to uvicorn - and its shutdown code is what calls stop()."""
    code, lines = run(tmp_path, end="SIGTERM", apps_own=True)

    assert code == 7, "the application's own handler did not run"
    assert "the app's own handler" in lines
    assert "finished" not in lines, "the registry took SIGTERM from the application"
    assert not any("Nothing else in this process handles SIGTERM" in line for line in lines)


# -- turned off ---------------------------------------------------------------------------------


def test_turned_off_a_normal_exit_cuts_the_job_off(tmp_path):
    code, lines = run(tmp_path, end="return", hook="off")

    assert code == 0
    assert "finished" not in lines, "something drained a registry that asked not to be"


def test_turned_off_ctrl_c_cuts_the_job_off(tmp_path):
    code, lines = run(tmp_path, end="SIGINT", hook="off")

    assert code == -signal.SIGINT
    assert "finished" not in lines, "something drained a registry that asked not to be"


def test_turned_off_sigterm_cuts_the_job_off(tmp_path):
    code, lines = run(tmp_path, end="SIGTERM", hook="off")

    assert code == -signal.SIGTERM
    assert "finished" not in lines, "something drained a registry that asked not to be"


# -- a copy made by fork() ----------------------------------------------------------------------

FORKED = r"""
import os, signal, sys
from datetime import timedelta
from pathlib import Path
from sqlalchemy import MetaData
from db_scheduler.task.schedule import FixedDelay
from ekbatan import DataSource
from ekbatan.config import DataSourceConfig
from ekbatan.distributedjobs import DistributedJob, job_registry
from ekbatan.schema import scheduled_tasks_table

log, db = Path(sys.argv[1]), sys.argv[2]

import logging
logging.basicConfig(filename=str(log), level=logging.INFO, format="%(process)d %(message)s")
logging.getLogger("db_scheduler").setLevel(logging.WARNING)

source = DataSource.from_config(DataSourceConfig(url=f"sqlite:///{db}"))
metadata = MetaData()
scheduled_tasks_table(metadata)
metadata.create_all(source.sqlalchemy_engine)

class Idle(DistributedJob):
    def name(self): return "idle"
    def schedule(self): return FixedDelay(timedelta(hours=1))
    def execute(self, ctx): pass

jobs = job_registry().data_source(source).with_job(Idle()).build()
jobs.start()

child = os.fork()
if child == 0:
    # multiprocessing's Pool.terminate() and gunicorn's arbiter stop their workers like this.
    signal.raise_signal(signal.SIGTERM)
    os._exit(0)  # not reached: the child dies of the signal
_, status = os.waitpid(child, 0)
died_of = os.WTERMSIG(status) if os.WIFSIGNALED(status) else None
with open(log, "a") as f:
    f.write(f"child {child} ended: signal {died_of}\n")
jobs.stop()
"""


def test_a_copy_made_by_fork_leaves_the_parents_registry_alone(tmp_path):
    """The child inherits the handler and the parent's registry, but none of its threads: the
    registry is the parent's to stop. The child just dies of SIGTERM, as it would have."""
    script = tmp_path / "forked.py"
    script.write_text(FORKED)
    log = tmp_path / "run.log"
    log.write_text("")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    done = subprocess.run(
        [sys.executable, str(script), str(log), str(tmp_path / "jobs.db")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr

    lines = log.read_text().splitlines()
    ended = [line for line in lines if line.startswith("child ")]
    assert ended and ended[0].endswith(f"signal {int(signal.SIGTERM)}"), lines
    child = ended[0].split()[1]
    assert not [line for line in lines if line.startswith(f"{child} ")], (
        f"the forked child stopped the parent's registry: {lines}"
    )
    assert sum(line.endswith(" JobRegistry stopped") for line in lines) == 1, lines


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
def source(registry):
    return registry.default.primary


@pytest.fixture
def exit_hooks(monkeypatch):
    """How many hooks the framework has on the interpreter's exit list right now.

    Counted at the source rather than with `exit_hooks()`: before Python 3.14 that count
    keeps a slot for every hook ever unregistered, so on 3.13 a registry that took its hook off
    still looked as if it had left it on. The real `register`/`unregister` still run.
    """
    live: list[object] = []
    register, unregister = atexit.register, atexit.unregister

    def counting_register(func, *args, **kwargs):
        live.append(func)
        return register(func, *args, **kwargs)

    def counting_unregister(func):
        live[:] = [f for f in live if f != func]
        return unregister(func)

    monkeypatch.setattr(atexit, "register", counting_register)
    monkeypatch.setattr(atexit, "unregister", counting_unregister)
    return lambda: len(live)


def test_starting_adds_one_entry_and_stopping_takes_it_off(source, exit_hooks):
    before = exit_hooks()
    jobs = (
        job_registry()
        .data_source(source)
        .with_job(NeverDue())
        .poll_interval(timedelta(seconds=1))
        .build()
    )

    jobs.start()
    assert exit_hooks() == before + 1

    jobs.stop()
    assert exit_hooks() == before, "a stopped registry was left on the list"


def test_building_alone_adds_nothing(source, exit_hooks):
    """Only a started registry has anything to drain."""
    before = exit_hooks()

    job_registry().data_source(source).with_job(NeverDue()).build()

    assert exit_hooks() == before


def test_turned_off_it_is_never_on_the_list(source, exit_hooks):
    before = exit_hooks()
    jobs = (
        job_registry()
        .data_source(source)
        .with_job(NeverDue())
        .poll_interval(timedelta(seconds=1))
        .register_shutdown_hook(False)
        .build()
    )
    jobs.start()
    try:
        assert exit_hooks() == before
    finally:
        jobs.stop()


def test_a_manual_registry_is_never_on_the_list(source, exit_hooks):
    """A test that starts one and forgets it must not have it drained, against a database the
    suite has already torn down, as the interpreter exits."""
    before = exit_hooks()
    jobs = job_registry().data_source(source).with_job(NeverDue()).build_manual()
    jobs.start()
    try:
        assert exit_hooks() == before
    finally:
        jobs.stop()
