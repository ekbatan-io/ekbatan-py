"""Several processes migrating one database at once - the migrator's lock, on every dialect.

This is where yoyo went wrong on its own, each measured:

- #6: its lock gave up after ten seconds, so every process waiting behind a longer migration
  crashed.
- #7: its lock was a row in a table, so a process killed while holding it left every later
  deploy failing until someone cleared the row by hand.
- #15: it applied migrations on a second connection, apart from the lock's, so when the lock's
  connection died - a network blip, an idle timeout, a proxy - a second process ran the same
  migration at the same time. Every split design ran it twice.

Every test counts rows in a `runs` table that the slow migration inserts into: one row is one
application, so "ran once" is checked, not inferred from who finished first.
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path
from queue import Empty

import pytest

from ekbatan._yoyo import POSTGRES_LOCK_KEY, mysql_lock_name
from ekbatan.config import ShardingConfig
from ekbatan.migrations import HISTORY_TABLE, migrate
from fixtures.migrating import CONTEXT, fresh, query, sleep_sql, start

SERVERS = ("postgres", "mysql", "mariadb")


def slow_migrations(directory: Path, dialect: str, seconds: float) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "V1__runs.sql").write_text("CREATE TABLE runs (n INTEGER);")
    (directory / "V2__slow.sql").write_text(
        f"{sleep_sql(dialect, seconds)}\nINSERT INTO runs VALUES (1);"
    )
    return directory


def runs(url: str) -> int:
    return int(query(url, "SELECT count(*) FROM runs")[0][0])


def lock_is_held(url: str, dialect: str) -> bool:
    if dialect == "postgres":
        return bool(
            query(
                url,
                "SELECT count(*) FROM pg_locks l JOIN pg_database d ON d.oid = l.database "
                "WHERE l.locktype = 'advisory' AND l.granted AND d.datname = current_database()",
            )[0][0]
        )
    if dialect in ("mysql", "mariadb"):
        database = query(url, "SELECT DATABASE()")[0][0]
        return query(url, f"SELECT IS_USED_LOCK('{mysql_lock_name(database)}')")[0][0] is not None
    import fcntl

    with open(url.removeprefix("sqlite:///") + "-ekbatan-migrate.lock", "a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False


def wait_for_the_lock(url: str, dialect: str, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while not lock_is_held(url, dialect):
        assert time.monotonic() < deadline, "nobody took the migration lock"
        time.sleep(0.1)


def drop_the_lock_holders_session(url: str, dialect: str) -> None:
    """What a network blip, an idle-connection killer or a proxy does to a connection."""
    if dialect == "postgres":
        query(
            url,
            "SELECT pg_terminate_backend(l.pid) FROM pg_locks l "
            "JOIN pg_database d ON d.oid = l.database WHERE l.locktype = 'advisory' "
            f"AND l.granted AND l.objid = ({POSTGRES_LOCK_KEY} & 4294967295) "
            "AND d.datname = current_database()",
        )
    else:
        database = query(url, "SELECT DATABASE()")[0][0]
        holder = query(url, f"SELECT IS_USED_LOCK('{mysql_lock_name(database)}')")[0][0]
        query(url, f"KILL {holder}")


def collect(results, count: int, timeout: float) -> list[tuple]:
    got = []
    deadline = time.monotonic() + timeout
    while len(got) < count:
        try:
            got.append(results.get(timeout=max(0.1, deadline - time.monotonic())))
        except Empty:
            pytest.fail(f"only {len(got)} of {count} migrators finished within {timeout}s: {got}")
    return got


def finish(processes, timeout: float = 5) -> None:
    for process in processes:
        process.join(timeout)
        if process.is_alive():
            process.kill()
            pytest.fail("a migrator did not exit")


# -- #6: waiting as long as it takes ---------------------------------------------------------


def test_four_processes_at_once_apply_each_migration_once(backend, request, tmp_path):
    """On the servers the migration takes longer than yoyo's ten-second lock timeout, which is
    what made its waiting processes crash. Here they wait, then find nothing left to do."""
    url = fresh(backend, request)
    seconds = 11 if backend.dialect in SERVERS else 2.5
    location = slow_migrations(tmp_path / "m", backend.dialect, seconds)
    results, go = CONTEXT.Queue(), CONTEXT.Event()

    processes = [start(url, location, results, f"p{i}", go) for i in range(4)]
    go.set()
    outcomes = collect(results, 4, timeout=seconds * 2 + 60)
    finish(processes)

    assert [o[1] for o in outcomes] == ["ok"] * 4, outcomes
    applied = [o[2] for o in outcomes if o[2]]
    assert applied == [["V1__runs.sql", "V2__slow.sql"]], "one process applied, the rest waited"
    assert runs(url) == 1


# -- #7: a process killed while holding the lock ---------------------------------------------


def test_a_process_killed_mid_migration_does_not_block_the_next(backend, request, tmp_path):
    """yoyo's lock row outlived the process and failed every later deploy. A session lock and an
    OS file lock both die with the process."""
    url = fresh(backend, request)
    location = slow_migrations(tmp_path / "m", backend.dialect, 30)
    results = CONTEXT.Queue()

    doomed = start(url, location, results, "doomed")
    wait_for_the_lock(url, backend.dialect)
    os.kill(doomed.pid, signal.SIGKILL)
    doomed.join(10)

    fast = slow_migrations(tmp_path / "fast", backend.dialect, 0)
    began = time.monotonic()
    (result,) = migrate(ShardingConfig.single(url), fast)

    assert time.monotonic() - began < 10, "the lock was let go when the process died"
    assert result.migrate_result.migrations[-1] == "V2__slow.sql"
    assert runs(url) == 1, "the killed attempt left nothing behind"


# -- #15: the lock's session dies mid-migration ----------------------------------------------


def test_when_the_holders_session_is_dropped_the_migration_still_runs_once(
    backend, request, tmp_path
):
    """The failure that made the migrator use one connection. With the lock on its own
    connection, dropping that connection let a waiting process in while the migration was still
    running, and it ran twice. With one connection, the migration dies with its lock."""
    if backend.dialect not in SERVERS:
        pytest.skip("SQLite has no session to drop; a killed process is the test above")
    url = fresh(backend, request)
    location = slow_migrations(tmp_path / "m", backend.dialect, 8)
    results = CONTEXT.Queue()

    first = start(url, location, results, "first")
    wait_for_the_lock(url, backend.dialect)
    second = start(url, location, results, "second")
    time.sleep(1.5)
    drop_the_lock_holders_session(url, backend.dialect)
    outcomes = {o[0]: o for o in collect(results, 2, timeout=90)}
    finish([first, second])

    assert outcomes["first"][1] == "failed", "its connection was dropped"
    assert outcomes["second"][1] == "ok"
    assert outcomes["second"][2] == ["V2__slow.sql"], "the second process applied it"
    assert runs(url) == 1, "once - not once per process"


def test_a_short_idle_timeout_does_not_let_a_second_process_in(backend, request, tmp_path):
    """An idle-connection killer took down a lock connection that sat idle through a migration.
    Here nothing sits idle: the migrating connection is busy, and a waiting one asks every half
    second."""
    if backend.dialect not in SERVERS:
        pytest.skip("SQLite has no idle timeout")
    url = fresh(backend, request)
    database = query(
        url, "SELECT current_database()" if backend.dialect == "postgres" else "SELECT DATABASE()"
    )[0][0]
    location = slow_migrations(tmp_path / "m", backend.dialect, 5)
    if backend.dialect == "postgres":
        query(url, f"ALTER DATABASE {database} SET idle_session_timeout = '1s'")
    else:
        query(url, "SET GLOBAL wait_timeout = 2")
    try:
        results, go = CONTEXT.Queue(), CONTEXT.Event()
        processes = [start(url, location, results, f"p{i}", go) for i in range(2)]
        go.set()
        outcomes = collect(results, 2, timeout=60)
        finish(processes)
    finally:
        if backend.dialect == "postgres":
            query(url, f"ALTER DATABASE {database} RESET idle_session_timeout")
        else:
            query(url, "SET GLOBAL wait_timeout = 28800")

    assert [o[1] for o in outcomes] == ["ok", "ok"], outcomes
    assert runs(url) == 1


# -- #10: CREATE INDEX CONCURRENTLY with processes waiting -----------------------------------


def test_concurrent_index_builds_do_not_wait_on_the_waiters_forever(backend, request, tmp_path):
    """The first version of the lock hung here. A waiting session blocked inside a lock call is
    mid-statement, `CREATE INDEX CONCURRENTLY` waits for every such session, and they wait for
    the lock - nothing moves and nothing reports a deadlock. Waiters now try and sleep, idle
    between attempts, so the index gets built."""
    if backend.dialect != "postgres":
        pytest.skip("CREATE INDEX CONCURRENTLY is PostgreSQL's")
    url = fresh(backend, request)
    location = tmp_path / "m"
    location.mkdir()
    (location / "V1__table.sql").write_text("CREATE TABLE people (id INTEGER, owner TEXT);")
    (location / "V2__slow.sql").write_text("SELECT pg_sleep(2);")
    (location / "V3__index.sql").write_text(
        "-- transactional: false\nCREATE INDEX CONCURRENTLY ix_people_owner ON people (owner);"
    )
    results, go = CONTEXT.Queue(), CONTEXT.Event()

    processes = [start(url, location, results, f"p{i}", go) for i in range(4)]
    go.set()
    outcomes = collect(results, 4, timeout=60)
    finish(processes)

    assert [o[1] for o in outcomes] == ["ok"] * 4, outcomes
    assert {r[0] for r in query(url, "SELECT indexname FROM pg_indexes")} >= {"ix_people_owner"}
    assert [r[0] for r in query(url, f"SELECT script FROM {HISTORY_TABLE} ORDER BY script")] == [
        "V1__table.sql",
        "V2__slow.sql",
        "V3__index.sql",
    ]
