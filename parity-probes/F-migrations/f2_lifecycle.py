"""PARITY-PROBE F2: the history table, re-runs, edits, failures, baseline - per dialect.
Twin of ParityFLifecycleProbeTest.java."""

from common import (
    HISTORY,
    SQL,
    Captured,
    cfg,
    columns,
    execute,
    fresh,
    history,
    msg,
    probe,
    query,
    tables,
)

from ekbatan.migrations import FlywayMigrator


def schema(dialect: str) -> str:
    return "current_schema()" if dialect == "pg" else "DATABASE()"


def step(dialect: str, db: str, key: str, folder: str) -> None:
    with Captured() as log:
        try:
            r = FlywayMigrator.migrate_data_source(cfg(dialect, db), str(SQL / "life" / folder))
            probe(
                key + ".outcome",
                f"OK applied={r.migrations_executed} initial={r.initial_schema_version} "
                f"target={r.target_schema_version}".replace("None", "null"),
            )
            probe(key + ".result_warnings", list(r.warnings))
        except Exception as e:
            probe(key + ".outcome", "FAIL " + msg(e))
        probe(key + ".log_warnings", log.warnings())
    probe(key + ".tables", tables(dialect, db))
    probe(key + ".history", history(dialect, db))


def life(dialect: str) -> None:
    db = "f2_life"
    k = f"{dialect}.life."
    fresh(dialect, db)
    step(dialect, db, k + "a_first", "base")
    probe(
        k + "a_first.history_tables",
        query(
            dialect,
            db,
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE "
            f"'%schema_history' AND table_schema = {schema(dialect)}",
        ),
    )
    probe(k + "a_first.columns", columns(dialect, db))
    probe(
        k + "a_first.indexes",
        query(
            dialect,
            db,
            f"SELECT indexname || ' ' || indexdef FROM pg_indexes WHERE tablename = '{HISTORY}' "
            "ORDER BY indexname",
        )
        if dialect == "pg"
        else query(
            dialect,
            db,
            "SELECT index_name, column_name FROM information_schema.statistics WHERE "
            f"table_schema = DATABASE() AND table_name = '{HISTORY}' ORDER BY index_name",
        ),
    )
    probe(
        k + "a_first.installed_on_type",
        query(
            dialect,
            db,
            f"SELECT data_type FROM information_schema.columns WHERE table_name = '{HISTORY}' "
            f"AND column_name = 'installed_on' AND table_schema = {schema(dialect)}",
        ),
    )
    step(dialect, db, k + "b_again", "base")
    step(dialect, db, k + "c_edited", "edited_v1")
    step(dialect, db, k + "d_crlf", "crlf_v1")
    step(dialect, db, k + "e_joined", "joined_v1")
    step(dialect, db, k + "f_renamed", "renamed_v1")
    step(dialect, db, k + "g_respelled", "respelled_v1")
    step(dialect, db, k + "h_out_of_order", "out_of_order")
    step(dialect, db, k + "i_missing", "missing_v2")
    step(dialect, db, k + "j_repeatable_changed", "repeatable_changed")
    step(dialect, db, k + "k_failing", "failing")
    step(dialect, db, k + "l_fixed", "fixed")
    step(dialect, db, k + "m_fixed_again", "fixed")


def future(dialect: str) -> None:
    db = "f2_future"
    fresh(dialect, db)
    step(dialect, db, f"{dialect}.future.a_newer_release", "future")
    step(dialect, db, f"{dialect}.future.b_older_release", "base")


def non_empty(dialect: str) -> None:
    db = "f2_nonempty"
    fresh(dialect, db)
    execute(dialect, db, "CREATE TABLE existing (id INT)")
    step(dialect, db, f"{dialect}.nonempty", "base")


def baseline(dialect: str) -> None:
    db = "f2_baseline"
    k = f"{dialect}.baseline"
    fresh(dialect, db)
    execute(dialect, db, "CREATE TABLE life_a (id INT PRIMARY KEY, name VARCHAR(20))")
    try:
        builder = (
            FlywayMigrator.builder()
            .with_data_source(cfg(dialect, db))
            .locations(str(SQL / "life" / "base"))
        )
        builder.baseline("1")
        r = builder.migrate()[0].migrate_result
        probe(
            k + ".outcome",
            f"OK applied={r.migrations_executed} initial={r.initial_schema_version} "
            f"target={r.target_schema_version}".replace("None", "null"),
        )
    except Exception as e:
        probe(k + ".outcome", "FAIL " + msg(e))
    probe(k + ".tables", tables(dialect, db))
    probe(k + ".history", history(dialect, db))


for dialect in ("pg", "mysql", "maria"):
    life(dialect)
    future(dialect)
    non_empty(dialect)
    baseline(dialect)
