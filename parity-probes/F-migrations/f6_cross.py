"""PARITY-PROBE F6: one database, both ports in turn. One step per call; cross.sh runs them in
order with the Java steps (ParityFCrossProbeTest) between them.

    python f6_cross.py <step>
"""

import sys
import time
from pathlib import Path

from common import SQL, cfg, fresh, history, msg, probe, query, tables

from ekbatan.migrations import YoyoMigrator

DIALECTS = ["pg", "mysql"]
START = Path(
    "/private/tmp/claude-501/-Users-mike-workspace-ekbatan/"
    "73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/parityF-start"
)


def both(k: str, d: str, db: str) -> None:
    probe(k + ".tables", tables(d, db))
    probe(k + ".flyway_history", history(d, db, "flyway_schema_history"))
    probe(k + ".ekbatan_history", history(d, db))


def migrate(k: str, d: str, db: str, folder: str) -> None:
    try:
        r = YoyoMigrator.migrate_data_source(cfg(d, db), str(SQL / folder))
        probe(
            k + ".outcome",
            f"OK applied={r.migrations_executed} initial={r.initial_schema_version}",
        )
    except Exception as e:
        probe(k + ".outcome", "FAIL " + msg(e))
    both(k, d, db)


step = sys.argv[1]
for d in DIALECTS:
    if step == "a2":
        migrate(f"cross.{d}.a2_python_migrates_base_plus_v3", d, "f6_a", "life/fixed")
    elif step == "a3":
        k = f"cross.{d}.a3_python_baselines_at_2_then_migrates"
        b = YoyoMigrator.builder().with_data_source(cfg(d, "f6_a")).locations(str(SQL / "life/fixed"))
        try:
            b.baseline("2")
            probe(k + ".baseline", "OK")
        except Exception as e:
            probe(k + ".baseline", "FAIL " + msg(e))
        migrate(k, d, "f6_a", "life/fixed")
    elif step == "a5":
        both(f"cross.{d}.a5_final", d, "f6_a")
    elif step == "b1":
        fresh(d, "f6_b")
        migrate(f"cross.{d}.b1_python_migrates_base", d, "f6_b", "life/base")
        probe(
            f"cross.{d}.b1_installed_on_sample",
            query(d, "f6_b", "SELECT installed_on FROM ekbatan_schema_history WHERE installed_rank = 1"),
        )
    elif step == "c0":
        fresh(d, "f6_c")
    elif step == "c1":
        at = int(START.read_text().strip()) / 1000 + (15 if d == "mysql" else 0)
        while time.time() < at:
            time.sleep(0.02)
        try:
            r = YoyoMigrator.migrate_data_source(cfg(d, "f6_c"), str(SQL / f"lock_{d}"))
            probe(f"cross.{d}.c1_python_outcome", f"OK applied={r.migrations_executed}")
        except Exception as e:
            probe(f"cross.{d}.c1_python_outcome", "FAIL " + msg(e))
    elif step == "c2":
        both(f"cross.{d}.c2_after_both_at_once", d, "f6_c")
