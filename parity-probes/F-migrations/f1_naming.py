"""PARITY-PROBE F1: file naming, ordering, what is accepted and refused. Twin of
ParityFNamingProbeTest.java."""

from common import SQL, Captured, cfg, fresh, history, msg, probe, query, tables

from ekbatan.migrations import YoyoMigrator

FOLDERS = [
    "accept",
    "dup_v1_v1.0",
    "dup_v1_v01",
    "dup_repeatable",
    "lowercase_v",
    "misnamed",
    "repeatable_with_version",
    "undo",
    "conf",
    "callback",
    "post_apply",
    "python_file",
    "non_utf8",
    "long_description",
    "placeholder",
    "placeholder_builtin",
    "hidden",
    "empty",
    "delimiter_mysql",
]


def run(dialect: str, folder: str) -> None:
    db = "f1_" + folder.replace(".", "_")
    key = f"{dialect}.{folder}"
    fresh(dialect, db)
    with Captured() as log:
        try:
            r = YoyoMigrator.migrate_data_source(cfg(dialect, db), str(SQL / folder))
            probe(key + ".outcome", f"OK applied={r.migrations_executed}")
            rows = [row.split("|") for row in history(dialect, db)]
            probe(
                key + ".versions",
                [row[1] if row[1] != "null" else "R:" + row[2] for row in rows],
            )
        except Exception as e:
            probe(key + ".outcome", "FAIL " + msg(e))
        probe(key + ".warnings", log.warnings())
    probe(key + ".tables", tables(dialect, db))
    if folder.startswith("placeholder"):
        try:
            probe(key + ".t_ph", query(dialect, db, "SELECT v FROM t_ph"))
        except Exception:
            probe(key + ".t_ph", "none")
    probe(key + ".history", history(dialect, db))


for dialect in ("pg", "mysql"):
    for folder in FOLDERS:
        if folder == "delimiter_mysql" and dialect != "mysql":
            continue
        run(dialect, folder)
