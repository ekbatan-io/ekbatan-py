"""PARITY-PROBE F5: location forms, schemas, and a script migration. PostgreSQL.
Twin of ParityFLocationsProbeTest.java. The same location strings are handed over as given."""

from pathlib import Path

from common import PW, SQL, Captured, cfg, execute, fresh, msg, probe, query, tables, url

from ekbatan.config import DataSourceConfig
from ekbatan.migrations import HISTORY_TABLE as HISTORY, FlywayMigrator


def loc(key: str, *locations: str) -> None:
    db = "f5_loc"
    fresh("pg", db)
    with Captured() as log:
        try:
            r = FlywayMigrator.migrate_data_source(cfg("pg", db), *locations)
            probe(f"loc.{key}.outcome", f"OK applied={r.migrations_executed}")
        except Exception as e:
            probe(f"loc.{key}.outcome", "FAIL " + msg(e))
        probe(f"loc.{key}.warnings", log.warnings())
    probe(f"loc.{key}.tables", tables("pg", db))


def schema(key: str, precreate: bool) -> None:
    db = "f5_schema"
    fresh("pg", db)
    if precreate:
        execute("pg", db, "CREATE SCHEMA app")
    config = DataSourceConfig(
        url=url("pg", db, "?options=-csearch_path%3Dapp"), username="postgres", password=PW
    )
    try:
        r = FlywayMigrator.migrate_data_source(config, str(SQL / "quick"))
        probe(f"schema.{key}.outcome", f"OK applied={r.migrations_executed}")
    except Exception as e:
        probe(f"schema.{key}.outcome", "FAIL " + msg(e))
    probe(
        f"schema.{key}.schemas",
        query(
            "pg",
            db,
            "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname <> "
            "'information_schema' ORDER BY nspname",
        ),
    )
    probe(
        f"schema.{key}.tables",
        query(
            "pg",
            db,
            "SELECT table_schema || '.' || table_name FROM information_schema.tables WHERE "
            "table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY 1",
        ),
    )
    try:
        probe(
            f"schema.{key}.history",
            query(
                "pg",
                db,
                "SELECT installed_rank, version, description, type, script FROM "
                f"app.{HISTORY} ORDER BY installed_rank",
            ),
        )
    except Exception:
        probe(f"schema.{key}.history", "none in app")


quick = str(SQL / "quick")
loc("filesystem_prefix", "filesystem:" + quick)
loc("plain_absolute_path", quick)
loc("classpath_prefix", "classpath:db/parityF")
loc("no_location")
loc("missing_folder", "filesystem:" + str(SQL / "no_such_folder"))
loc("a_file_not_a_folder", "filesystem:" + quick + "/V1__quick.sql")
loc("two_locations_same_version", "filesystem:" + quick, "filesystem:" + str(SQL / "hidden"))

loc("two_locations_same_version_native_form", quick, str(SQL / "hidden"))

# A watchdog: after 30 s, a CREATE INDEX CONCURRENTLY still waiting is reported and cancelled.
import threading

done = threading.Event()


def watchdog() -> None:
    if done.wait(30):
        return
    stuck = query(
        "pg",
        "postgres",
        "SELECT pid || ' ' || wait_event FROM pg_stat_activity WHERE query LIKE "
        "'CREATE INDEX CONCURRENTLY%' AND state = 'active'",
    )
    probe(
        "loc.concurrent_index_no_marker.still_waiting_after_30s",
        "yes, on " + stuck[0].split(" ")[1] if stuck else "no",
    )
    for row in stuck:
        query("pg", "postgres", f"SELECT pg_terminate_backend({row.split(' ')[0]})")


w = threading.Thread(target=watchdog)
w.start()
loc("concurrent_index_no_marker", str(SQL / "concurrent_index_pg"))
done.set()
w.join()

schema("missing_schema", False)
schema("existing_schema", True)

marker = Path(
    "/private/tmp/claude-501/-Users-mike-workspace-ekbatan/"
    "73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/parityF-script-ran"
)
marker.unlink(missing_ok=True)
loc("script_migration", str(SQL / "script_migration"))
probe("loc.script_migration.script_ran", "true" if marker.exists() else "false")
marker.unlink(missing_ok=True)
