"""PARITY-PROBE F6: one database, both ports in turn. One step per call; cross.sh runs them in
order with the Java steps (ParityFCrossProbeTest) between them.

    python f6_cross.py <step>

Rewritten for parity fix M, once the migrator wrote Flyway's history: the steps are the turns
of the goal - Java migrates V1-V2, Python V3, Java V4, and the reverse - then both validate and
both migrate again, applying nothing. The scan's first version of this probe (the two ports
refusing each other's databases) is in git history.
"""

import sys
import time
from pathlib import Path

from common import SQL, _s, cfg, fresh, history, msg, probe, query, tables

from ekbatan.migrations import FlywayMigrator

DIALECTS = ["pg", "mysql", "maria"]
START = Path(
    "/private/tmp/claude-501/-Users-mike-workspace-ekbatan/"
    "73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/parityF-start"
)


def show(k: str, d: str, db: str) -> None:
    probe(k + ".tables", tables(d, db))
    probe(k + ".history", history(d, db))


def migrate(k: str, d: str, db: str, folder: str) -> None:
    try:
        r = FlywayMigrator.migrate_data_source(cfg(d, db), str(SQL / folder))
        probe(
            k + ".outcome",
            f"OK applied={r.migrations_executed} initial={_s(r.initial_schema_version)} "
            f"target={_s(r.target_schema_version)}",
        )
    except Exception as e:
        probe(k + ".outcome", "FAIL " + msg(e))
    show(k, d, db)


def validate(k: str, d: str, db: str, folder: str) -> None:
    try:
        FlywayMigrator.builder().with_data_source(cfg(d, db)).locations(
            str(SQL / folder)
        ).validate()
        probe(k + ".validate", "OK")
    except Exception as e:
        probe(k + ".validate", "FAIL " + msg(e))


def at_start(offset: float) -> None:
    at = int(START.read_text().strip()) / 1000 + offset
    while time.time() < at:
        time.sleep(0.02)


step = sys.argv[1]
for d in DIALECTS:
    if step == "a2":
        migrate(f"cross.{d}.a2_python_migrates_v3", d, "f6_a", "turns/b")
    elif step == "a4":
        validate(f"cross.{d}.a4_python", d, "f6_a", "turns/c")
        migrate(f"cross.{d}.a4_python_migrates_again", d, "f6_a", "turns/c")
    elif step == "b1":
        fresh(d, "f6_b")
        migrate(f"cross.{d}.b1_python_migrates_v1_v2", d, "f6_b", "turns/a")
        probe(
            f"cross.{d}.b1_installed_on_is_a_timestamp",
            query(
                d,
                "f6_b",
                "SELECT data_type FROM information_schema.columns WHERE table_name = "
                "'flyway_schema_history' AND column_name = 'installed_on' AND table_schema = "
                + ("current_schema()" if d == "pg" else "DATABASE()"),
            ),
        )
    elif step == "b3":
        migrate(f"cross.{d}.b3_python_migrates_v4", d, "f6_b", "turns/c")
    elif step == "b5":
        validate(f"cross.{d}.b5_python", d, "f6_b", "turns/c")
        migrate(f"cross.{d}.b5_python_migrates_again", d, "f6_b", "turns/c")
    elif step == "c0":
        fresh(d, "f6_c")
    elif step == "c1":
        at_start({"pg": 0, "mysql": 15, "maria": 30}[d])
        began = time.time()
        folder = "lock_pg" if d == "pg" else "lock_mysql"
        try:
            r = FlywayMigrator.migrate_data_source(cfg(d, "f6_c"), str(SQL / folder))
            probe(f"cross.{d}.c1_python_outcome", f"OK applied={r.migrations_executed}")
        except Exception as e:
            probe(f"cross.{d}.c1_python_outcome", "FAIL " + msg(e))
        probe(f"cross.{d}.c1_python_took_s", round(time.time() - began))
    elif step == "c2":
        show(f"cross.{d}.c2_after_both_at_once", d, "f6_c")
    elif step == "e0" and d == "pg":
        fresh(d, "f6_e")
    elif step in ("e1", "e1b") and d == "pg":
        # Python builds a concurrent index while Java waits on the lock: no one hangs.
        at_start(0)
        began = time.time()
        try:
            r = FlywayMigrator.migrate_data_source(
                cfg(d, "f6_e"), str(SQL / "cic_pg")
            )
            probe(f"cross.pg.{step}_python_outcome", f"OK applied={r.migrations_executed}")
        except Exception as e:
            probe(f"cross.pg.{step}_python_outcome", "FAIL " + msg(e))
        probe(f"cross.pg.{step}_python_took_under_30s", _s(time.time() - began < 30))
    elif step == "e2" and d == "pg":
        show("cross.pg.e2_after", d, "f6_e")
        probe(
            "cross.pg.e2_index",
            query(d, "f6_e", "SELECT indexname FROM pg_indexes WHERE indexname = 'cic_idx'"),
        )
