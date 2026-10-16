"""Importing the scheduler runs nothing outside the process.

croniter calls `platform.architecture()` on import, which on some platforms runs `file` as a
subprocess - and under gevent a single subprocess stops `multiprocessing` from seeing its
children exit (`EKB-0246` imported croniter at module level and broke ekbatan's gevent run that
way). So croniter is imported where a cron is first used, never when the package is.
"""

from __future__ import annotations

import subprocess
import sys


def test_importing_the_schedules_does_not_import_croniter() -> None:
    code = (
        "import sys, db_scheduler, db_scheduler.task.schedule\n"
        "assert 'croniter' not in sys.modules, 'croniter imported with the package'\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
