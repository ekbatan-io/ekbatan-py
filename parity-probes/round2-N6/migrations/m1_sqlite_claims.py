"""N6 / migrations: run the claims of docs/database/migrations.md on SQLite, one PROBE line each."""
from __future__ import annotations

import io, logging, sqlite3, tempfile
from pathlib import Path

from ekbatan.config import DataSourceConfig, ShardingConfig
from ekbatan import migrations as M
from ekbatan.migrations import (YoyoMigrator, baseline, info, migrate, repair, validate_scripts,
                                emit_ddl, HISTORY_TABLE)


def probe(k, v):
    print(f"PROBE {k}: {v}", flush=True)


def err(fn):
    try:
        r = fn()
        return f"ok {r!r}"[:300]
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"[:400]


def layout(db: Path):
    return ShardingConfig.single(f"sqlite:///{db}")


def tables(db):
    c = sqlite3.connect(db)
    try:
        return sorted(r[0] for r in c.execute("select name from sqlite_master where type in ('table','view')"))
    finally:
        c.close()


def write(d: Path, files: dict[str, str]):
    for name, body in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)


root = Path(tempfile.mkdtemp())
probe("history_table_name", HISTORY_TABLE)

# --- files: the documented layout, sub-dir, .SQL, R__, post-apply ---------------------------
d = root / "m1"; db = root / "m1.db"
write(d, {
    "V0001__create_wallets.sql": "CREATE TABLE wallets (id INTEGER PRIMARY KEY, owner_id INTEGER, balance INTEGER);",
    "V0002__add_owner_index.SQL": "CREATE INDEX ix_owner ON wallets (owner_id);",
    "2026/V0003__backfill_currency.sql": "ALTER TABLE wallets ADD COLUMN currency TEXT; UPDATE wallets SET currency='EUR';",
    "R__wallet_totals.sql": "DROP VIEW IF EXISTS wallet_totals; CREATE VIEW wallet_totals AS SELECT owner_id, SUM(balance) AS total FROM wallets GROUP BY owner_id;",
    "post-apply.sql": "CREATE TABLE IF NOT EXISTS hook_runs (n INTEGER); INSERT INTO hook_runs VALUES (1);",
    "__init__.py": "", "README": "x",
})
res = migrate(layout(db), d)
probe("result_type", type(res[0]).__name__ + " fields=" + ",".join(res[0].__slots__))
probe("result_repr", repr(res[0])[:400])
probe("tables_after_first", tables(db))
c = sqlite3.connect(db)
probe("history_rows", c.execute(f"select installed_rank, version, description, type, script, success from {HISTORY_TABLE} order by installed_rank").fetchall())
probe("history_columns", [r[1] for r in c.execute(f"pragma table_info({HISTORY_TABLE})")])
probe("hook_runs_after_1", c.execute("select count(*) from hook_runs").fetchone()[0]); c.close()
migrate(layout(db), d)  # nothing pending
c = sqlite3.connect(db)
probe("hook_runs_after_2_nothing_pending (doc: post-apply runs every migrate)", c.execute("select count(*) from hook_runs").fetchone()[0])
probe("hook_recorded_in_history", c.execute(f"select count(*) from {HISTORY_TABLE} where script like 'post-apply%'").fetchone()[0]); c.close()
probe("lockfile_beside_db", sorted(p.name for p in root.iterdir() if p.name.startswith("m1.db")))

# CRLF is not a change
(d / "V0001__create_wallets.sql").write_bytes((d / "V0001__create_wallets.sql").read_bytes().replace(b"\n", b"\r\n") + b"\r\n")
probe("crlf_edit_accepted", err(lambda: migrate(layout(db), d) and "migrated"))
(d / "V0001__create_wallets.sql").write_bytes(b"\xef\xbb\xbf" + (d / "V0001__create_wallets.sql").read_bytes())
probe("bom_edit_accepted", err(lambda: migrate(layout(db), d) and "migrated"))
# real edit refused
(d / "V0001__create_wallets.sql").write_text("CREATE TABLE wallets (id INTEGER PRIMARY KEY, owner_id INTEGER, balance INTEGER, x INT);")
probe("real_edit_refused", err(lambda: migrate(layout(db), d)))
(d / "V0001__create_wallets.sql").write_text("CREATE TABLE wallets (id INTEGER PRIMARY KEY, owner_id INTEGER, balance INTEGER);")
# version rewritten V2 -> V0002 / moved dir is not a change
(d / "V0002__add_owner_index.SQL").rename(d / "2026" / "V2__add_owner_index.SQL")
probe("version_respelled_and_moved_accepted", err(lambda: migrate(layout(db), d) and "migrated"))
# description changed refused
(d / "2026" / "V2__add_owner_index.SQL").rename(d / "V2__add_owner_idx.SQL")
probe("description_change_refused", err(lambda: migrate(layout(db), d)))
(d / "V2__add_owner_idx.SQL").rename(d / "V2__add_owner_index.SQL")
# older-than-applied refused; file gone refused
write(d, {"V0001_5__late.sql": "SELECT 1;"})
probe("older_new_refused", err(lambda: migrate(layout(db), d)))
(d / "V0001_5__late.sql").unlink()
(d / "2026" / "V0003__backfill_currency.sql").rename(root / "gone.sql")
probe("gone_file_refused", err(lambda: migrate(layout(db), d)))
(root / "gone.sql").rename(d / "2026" / "V0003__backfill_currency.sql")
# repeatable re-runs when changed, and each run adds a row
(d / "R__wallet_totals.sql").write_text("DROP VIEW IF EXISTS wallet_totals; CREATE VIEW wallet_totals AS SELECT owner_id, SUM(balance)+0 AS total FROM wallets GROUP BY owner_id;")
migrate(layout(db), d)
c = sqlite3.connect(db)
probe("repeatable_rows_after_change", c.execute(f"select count(*) from {HISTORY_TABLE} where version is null").fetchone()[0]); c.close()
# info listing
(shard,) = info(layout(db), d)
for m in shard.migrations:
    probe("info_row", f"{m.category} {m.version} {m.description} {m.state.value}")
probe("info_problems_attr", hasattr(shard, "problems"))
probe("states", [s.value for s in M.MigrationState])

# --- refused names (validate_scripts, no db) -------------------------------------------------
cases = {
    "misnamed": {"V1_create.sql": "SELECT 1;"},
    "dup_version": {"V1__a.sql": "SELECT 1;", "V01__b.sql": "SELECT 1;"},
    "dup_version_1.0": {"V1__a.sql": "SELECT 1;", "V1.0__b.sql": "SELECT 1;"},
    "dup_repeatable_desc": {"R__a_b.sql": "SELECT 1;", "R__a b.sql": "SELECT 1;"},
    "R_with_version": {"R1__x.sql": "SELECT 1;"},
    "long_desc": {"V1__" + "x" * 201 + ".sql": "SELECT 1;"},
    "rollback": {"V1__a.sql": "SELECT 1;", "V1__a.rollback.sql": "SELECT 1;"},
    "python": {"V2__backfill.py": "steps=[]"},
    "callback_afterMigrate": {"afterMigrate.sql": "SELECT 1;"},
    "conf_file": {"V2__x.sql": "SELECT 1;", "V2__x.sql.conf": "executeInTransaction=false"},
    "delimiter": {"V1__a.sql": "DELIMITER //\nSELECT 1//\n"},
    "depends": {"V1__a.sql": "-- depends: V0\nSELECT 1;"},
    "hidden_dir_ignored": {".git/V9__x.sql": "garbage", "V1__a.sql": "SELECT 1;"},
    "version_order_numeric": {"V10__ten.sql": "SELECT 1;", "V2__two.sql": "SELECT 1;"},
}
for name, files in cases.items():
    cd = root / ("c_" + name.replace(".", "_")); cd.mkdir()
    write(cd, files)
    probe(f"validate[{name}]", err(lambda: [s.script for s in validate_scripts(cd)]))
cd = root / "multi1"; cd2 = root / "multi2"; cd.mkdir(); cd2.mkdir()
write(cd, {"V1__a.sql": "SELECT 1;"}); write(cd2, {"V1__b.sql": "SELECT 1;"})
probe("validate[dup_across_dirs]", err(lambda: validate_scripts(cd, cd2)))

# --- repeatable order --------------------------------------------------------------------------
probe("description_order", sorted(["ab", "a-b", "a b", "a", "B", "b"], key=M.description_order))

# --- refuse a non-empty db without history; baseline ------------------------------------------
db2 = root / "pre.db"; c = sqlite3.connect(db2); c.execute("create table t (x int)"); c.commit(); c.close()
probe("nonempty_without_history_refused", err(lambda: migrate(layout(db2), cases_dir := (root / "c_version_order_numeric"))))
probe("baseline", err(lambda: baseline(layout(db2), version="3")))
probe("after_baseline_migrate", err(lambda: [r.migrate_result.migrations for r in migrate(layout(db2), root / "c_version_order_numeric")]))
db3 = root / "empty.db"
probe("baseline_empty_refused", err(lambda: baseline(layout(db3), version="3")))
probe("baseline_nonempty_history_refused", err(lambda: baseline(layout(db), version="9")))

# --- failure half-way on SQLite: rolled back whole, next run applies from start ----------------
db4 = root / "fail.db"; d4 = root / "fail"
write(d4, {"V1__a.sql": "CREATE TABLE a (x int);", "V2__b.sql": "CREATE TABLE b (x int); INSERT INTO nope VALUES (1);"})
buf = io.StringIO(); h = logging.StreamHandler(buf); h.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
lg = logging.getLogger("io.ekbatan.flyway.FlywayMigrator"); lg.addHandler(h); lg.setLevel(logging.INFO)
probe("fail_error", err(lambda: migrate(layout(db4), d4)))
for line in buf.getvalue().splitlines():
    if line.startswith(("ERROR", "WARNING")):
        probe("fail_log", line[:300])
probe("fail_tables", tables(db4))
write(d4, {"V2__b.sql": "CREATE TABLE b (x int);"})
probe("fail_next_run", err(lambda: [r.migrate_result.migrations for r in migrate(layout(db4), d4)]))

# --- transactional false ----------------------------------------------------------------------
db5 = root / "nt.db"; d5 = root / "nt"
write(d5, {"V1__a.sql": "-- transactional: false\nCREATE TABLE a (x int);"})
probe("transactional_false", err(lambda: migrate(layout(db5), d5) and tables(db5)))

# --- query results logged at INFO; output_query_results=False ----------------------------------
db6 = root / "q.db"; d6 = root / "q"
write(d6, {"V1__a.sql": "CREATE TABLE a (x int); INSERT INTO a VALUES (42); SELECT x FROM a;"})
buf.truncate(0); buf.seek(0)
migrate(layout(db6), d6)
probe("select_logged", [l for l in buf.getvalue().splitlines() if "42" in l][:3])
db7 = root / "q2.db"; buf.truncate(0); buf.seek(0)
migrate(layout(db7), d6, output_query_results=False)
probe("select_logged_when_off", [l for l in buf.getvalue().splitlines() if "42" in l][:3])

# --- builder and data source ------------------------------------------------------------------
db8 = root / "b.db"
probe("builder", err(lambda: len(YoyoMigrator.builder().with_sharding_config(layout(db8)).locations(d6).output_query_results(False).migrate())))
probe("migrate_data_source", err(lambda: type(YoyoMigrator.migrate_data_source(DataSourceConfig(url=f"sqlite:///{root/'ds.db'}"), d6)).__name__))
probe("targets", err(lambda: [t.label for t in YoyoMigrator.targets(layout(db8))]))

# --- repair removes a failed row (on SQLite nothing is recorded as failed) ---------------------
probe("repair", err(lambda: repair(layout(db4), d4)))
probe("emit_ddl_has_create", "CREATE TABLE" in emit_ddl(__import__('ekbatan.schema', fromlist=['x']).outbox_table(__import__('sqlalchemy').MetaData()).metadata, "postgresql"))
