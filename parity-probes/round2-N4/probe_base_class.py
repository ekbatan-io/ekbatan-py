"""Round 2, area N4: which failures the framework raises on purpose fall outside EkbatanError.

Python only: Java has no common base (everything is a RuntimeException), so there is nothing to
twin. No database needed.

Run: cd ekbatan-py && uv run --no-sync python parity-probes/round2-N4/probe_base_class.py
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from ekbatan.action.action import Action  # noqa: E402
from ekbatan.clock import SystemClock  # noqa: E402
from ekbatan.errors import EkbatanError  # noqa: E402
from ekbatan.events.notification import backoff  # noqa: E402
from ekbatan.repository import ModelRepository  # noqa: E402
from ekbatan.streaming import ActionEvent  # noqa: E402
from ekbatan.testing import run_action  # noqa: E402
from fixtures.domain import Wallet  # noqa: E402


def p(key, value):
    print(f"PROBE {key}: {value}", flush=True)


def check(key, fn):
    try:
        fn()
        p(key, "no error")
    except BaseException as e:  # noqa: BLE001
        p(key, f"{type(e).__name__} caught_by_EkbatanError={isinstance(e, EkbatanError)}")


class NoMapping(ModelRepository[Wallet, object]):
    pass


class NoPerform(Action[None, None]):
    pass


check("repository_without_to_row_from_row", lambda: NoMapping(None, None, None))
check("builder_unknown_field_setter", lambda: Wallet.builder().no_such_field(1))
check("perform_not_overridden", lambda: run_action(None, NoPerform(SystemClock()), None))
check("backoff_zero_attempts", lambda: backoff(0, timedelta(minutes=5)))
check("streaming_from_row_missing_columns", lambda: ActionEvent.from_row({"id": 1}))
check("plan_outside_execution", lambda: NoPerform(SystemClock()).plan)
