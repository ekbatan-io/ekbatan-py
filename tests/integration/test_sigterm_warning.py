"""Who has SIGTERM, in real processes: a bare worker's registry takes it, a uvicorn service's
leaves it to uvicorn, and the warning is left for a worker that turned the hook off.

The unit tests prove the rule. These prove the fact the rule rests on - that by the time a
FastAPI lifespan starts the registry, uvicorn's own SIGTERM handler is already installed - by
serving an app with uvicorn on a free port and reading what it logged.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

WARNING = "Nothing in this process handles SIGTERM"
TOOK = "Nothing else in this process handles SIGTERM, so the JobRegistry does"

JOBS = r"""
import logging, sys
from datetime import timedelta
from sqlalchemy import MetaData
from db_scheduler.task.schedule import FixedDelay
from ekbatan import DataSource
from ekbatan.config import DataSourceConfig
from ekbatan.distributedjobs import DistributedJob, job_registry
from ekbatan.schema import scheduled_tasks_table

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
logging.getLogger("db_scheduler").setLevel(logging.WARNING)

class Idle(DistributedJob):
    def name(self): return "idle"
    def schedule(self): return FixedDelay(timedelta(hours=1))
    def execute(self, ctx): pass

def build(db, hook=None):
    source = DataSource.from_config(DataSourceConfig(url=f"sqlite:///{db}"))
    metadata = MetaData()
    scheduled_tasks_table(metadata)
    metadata.create_all(source.sqlalchemy_engine)
    builder = job_registry().data_source(source).with_job(Idle())
    if hook is not None:
        builder = builder.register_shutdown_hook(hook)
    return builder.build()
"""

WORKER = r"""
import sys
from jobs import build
jobs = build(sys.argv[1], hook=None if sys.argv[2] == "default" else sys.argv[2] == "on")
jobs.start()
jobs.stop()
"""

SERVICE = r"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from jobs import build

@asynccontextmanager
async def lifespan(app):
    jobs = build(os.environ["JOBS_DB"])
    jobs.start()
    yield
    jobs.stop()

app = FastAPI(lifespan=lifespan)
"""


def write_scripts(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "jobs.py").write_text(JOBS)
    (tmp_path / "worker.py").write_text(WORKER)
    (tmp_path / "service.py").write_text(SERVICE)
    return {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(tmp_path), str(ROOT / "src")]),
        "JOBS_DB": str(tmp_path / "jobs.db"),
    }


def run_worker(tmp_path: Path, *, hook: str) -> str:
    env = write_scripts(tmp_path)
    done = subprocess.run(
        [sys.executable, str(tmp_path / "worker.py"), env["JOBS_DB"], hook],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "Starting JobRegistry" in done.stderr, done.stderr
    return done.stderr


def a_free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_bare_worker_takes_sigterm_by_default_and_is_not_warned(tmp_path):
    output = run_worker(tmp_path, hook="default")

    assert output.count(TOOK) == 1, output
    assert WARNING not in output, output


def test_a_bare_worker_with_the_hook_off_is_warned(tmp_path):
    output = run_worker(tmp_path, hook="off")

    assert output.count(WARNING) == 1, output
    assert TOOK not in output, output


def test_a_uvicorn_service_starting_jobs_in_its_lifespan_is_not(tmp_path):
    # uvicorn and FastAPI are the `examples` group, which CI's fast job installs.
    pytest.importorskip("uvicorn")
    pytest.importorskip("fastapi")
    env = write_scripts(tmp_path)
    log = tmp_path / "uvicorn.log"
    with log.open("w") as sink:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "service:app",
                "--app-dir",
                str(tmp_path),
                "--port",
                str(a_free_port()),
            ],
            env=env,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 60
            while "Application startup complete" not in log.read_text():
                assert server.poll() is None, f"uvicorn died early:\n{log.read_text()}"
                assert time.monotonic() < deadline, f"uvicorn never started:\n{log.read_text()}"
                time.sleep(0.1)
        finally:
            server.send_signal(signal.SIGTERM)
            server.wait(timeout=30)

    output = log.read_text()
    assert "Starting JobRegistry" in output, "the lifespan never started the jobs:\n" + output
    assert "JobRegistry stopped" in output, "the lifespan never stopped them:\n" + output
    assert WARNING not in output, output
    assert TOOK not in output, "the registry took SIGTERM from uvicorn:\n" + output
