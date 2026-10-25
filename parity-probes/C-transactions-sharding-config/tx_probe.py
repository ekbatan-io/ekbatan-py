"""Parity scan area C against a real PostgreSQL: the Python twin of ParityCTxProbeTest.java.

Uses the container the scan started (port read from the scratch file, or PARITY_C_PORT).
"""

from __future__ import annotations

import getpass
import logging
import os
import sys
import threading
import time
import traceback
from pathlib import Path

import psycopg
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ekbatan.config import DataSourceConfig, ShardGroupConfig, ShardingConfig, ShardMemberConfig  # noqa: E402
from ekbatan.ids import ShardedId  # noqa: E402
from ekbatan.persistence import DatabaseRegistry, TransactionManager, DataSource  # noqa: E402
from ekbatan.sharding import DEFAULT_SHARD, EmbeddedBitsShardingStrategy, ShardIdentifier  # noqa: E402
from tests.fixtures.domain import Wallet  # noqa: E402
from tests.fixtures.repositories import WalletRepository  # noqa: E402
from tests.fixtures.schema import WALLETS  # noqa: E402

MARKER = "s3cret-PROBE-marker"
PORT = int(
    os.environ.get("PARITY_C_PORT")
    or Path(
        "/private/tmp/claude-501/-Users-mike-workspace-ekbatan/73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/pgport"
    )
    .read_text()
    .strip()
)

LOGS: list[str] = []
ALL: list[str] = []


class Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        line = f"{record.levelname} {record.name} - {record.getMessage()}"
        if record.exc_info:
            line += " " + "".join(traceback.format_exception(*record.exc_info))
        LOGS.append(line)
        ALL.append(line)


root = logging.getLogger()
root.setLevel(logging.DEBUG)
root.addHandler(Capture())
for name in ("sqlalchemy.engine", "sqlalchemy.pool", "sqlalchemy.dialects"):
    logging.getLogger(name).setLevel(logging.DEBUG)


def p(key: str, value: object) -> None:
    print(f"PROBE {key}: {value}", flush=True)


def yn(b: bool) -> str:
    return "yes" if b else "no"


def url(db: str) -> str:
    return f"postgresql+psycopg://localhost:{PORT}/{db}"


def cfg(db: str, pool: int, app: str) -> DataSourceConfig:
    return DataSourceConfig(
        url=url(db),
        username="probe",
        password=MARKER,
        pool_size=pool,
        data_source_properties={"application_name": app},
    )


def tm(db: str, pool: int, app: str) -> TransactionManager:
    return TransactionManager(DEFAULT_SHARD, DataSource.from_config(cfg(db, pool, app)))


def side() -> psycopg.Connection:
    return psycopg.connect(
        host="localhost", port=PORT, user="probe", password=MARKER, dbname="testdb", autocommit=True
    )


def count(sql: str) -> int:
    with side() as c:
        return c.execute(sql).fetchone()[0]


def rows(i: int) -> int:
    return count(f"select count(*) from tx_probe where id = {i}")


def pid(conn) -> int:
    return conn.execute(text("select pg_backend_pid()")).scalar()


def db(conn) -> str:
    return conn.execute(text("select current_database()")).scalar()


def logs(needle: str) -> int:
    return sum(needle in line for line in LOGS)


def chain(e: BaseException) -> str:
    return "".join(traceback.format_exception(e)) + repr(e)


# ------------------------------------------------------------ transactions


def transactions() -> None:
    m = tm("testdb", 5, "parity-c")
    registry = DatabaseRegistry({DEFAULT_SHARD: m})

    during = []
    with m.transaction() as t:
        t.execute(text("insert into tx_probe values (1, 'commit')"))
        during.append(rows(1))
    p("tx.uncommitted.visible_to_other_connection", yn(during[0] == 1))
    p("tx.commit.visible_after", yn(rows(1) == 1))

    boom = RuntimeError("boom")
    try:
        with m.transaction() as t:
            t.execute(text("insert into tx_probe values (2, 'rollback')"))
            raise boom
    except RuntimeError as e:
        p("tx.rollback.runtime.propagated_same_object", yn(e is boom))
    p("tx.rollback.runtime.row_kept", yn(rows(2) == 1))
    # Python has one form; the "checked form" keys repeat the same block
    try:
        with m.transaction() as t:
            t.execute(text("insert into tx_probe values (4, 'rollback-checked-form')"))
            raise boom
    except RuntimeError as e:
        p("tx.rollback.runtime.checked_form_same_object", yn(e is boom))
    p("tx.rollback.runtime.checked_form_row_kept", yn(rows(4) == 1))

    class Fatal(BaseException):
        pass

    fatal = Fatal("fatal")
    try:
        with m.transaction() as t:
            t.execute(text("insert into tx_probe values (3, 'error')"))
            raise fatal
    except BaseException as e:  # noqa: BLE001
        p("tx.rollback.base_error.propagated_same_object", yn(e is fatal))
    p("tx.rollback.base_error.row_kept", yn(rows(3) == 1))

    checked = OSError("checked")
    try:
        with m.transaction():
            raise checked
    except OSError as e:
        p("tx.checked.unchecked_form", "unchanged" if e is checked else "wrapped")
        p("tx.checked.checked_form", "unchanged" if e is checked else "wrapped")

    p("tx.current.outside", "present" if m.current() is not None else "empty")
    inside = {}
    with m.transaction() as t:
        inside["in"] = "present" if m.current() is not None else "empty"
        inside["same"] = yn(m.current() is t.connection)
        th = threading.Thread(
            target=lambda: inside.__setitem__("thread", "present" if m.current() is not None else "empty")
        )
        th.start()
        th.join()
    p("tx.current.inside", inside["in"])
    p("tx.current.is_block_context", inside["same"])
    p("tx.current.other_thread_inside", inside["thread"])
    p("tx.current.after", "present" if m.current() is not None else "empty")

    with m.connection() as c:
        c.execute(text("insert into tx_probe values (10, 'autocommit')"))
    p("tx.autocommit.outside_visible_immediately", yn(rows(10) == 1))
    try:
        with m.connection() as c:
            c.execute(text("insert into tx_probe values (11, 'first')"))
            c.execute(text("insert into tx_probe values (11, 'duplicate')"))
    except Exception:  # noqa: BLE001
        pass
    p("tx.autocommit.first_statement_kept_after_second_fails", yn(rows(11) == 1))

    LOGS.clear()
    try:
        with m.transaction():
            raise ValueError("x")
    except ValueError:
        pass
    p("tx.log.rollback_warn_lines", logs("rolled back"))
    p("tx.log.rollback_warn_text", next((x for x in LOGS if "rolled back" in x), "none"))
    registry.close()


# ------------------------------------------------------------ nesting


def nesting() -> None:
    m = tm("testdb", 5, "parity-c")
    res = {}
    with m.transaction() as outer:
        outer.execute(text("insert into tx_probe values (20, 'outer')"))
        outer_pid = pid(outer.connection)
        with m.transaction() as inner:
            res["same"] = yn(pid(inner.connection) == outer_pid)
            res["sees"] = yn(inner.execute(text("select count(*) from tx_probe where id = 20")).scalar() == 1)
        res["after"] = yn(pid(m.current()) == outer_pid)
    p("tx.nested.same_connection", res["same"])
    p("tx.nested.inner_sees_outer_uncommitted", res["sees"])
    p("tx.nested.current_after_inner_is_outer", res["after"])

    try:
        with m.transaction() as outer:
            outer.execute(text("insert into tx_probe values (21, 'outer')"))
            with m.transaction() as inner:
                inner.execute(text("insert into tx_probe values (22, 'inner')"))
            raise RuntimeError("outer fails")
    except RuntimeError:
        pass
    p("tx.nested.inner_row_kept_after_outer_rollback", yn(rows(22) == 1))
    p("tx.nested.outer_row_kept_after_outer_rollback", yn(rows(21) == 1))

    with m.transaction() as outer:
        outer.execute(text("insert into tx_probe values (23, 'outer')"))
        try:
            with m.transaction() as inner:
                inner.execute(text("insert into tx_probe values (24, 'inner')"))
                raise RuntimeError("inner fails")
        except RuntimeError:
            pass
    p("tx.nested.inner_failure_caught.inner_row_kept", yn(rows(24) == 1))
    p("tx.nested.inner_failure_caught.outer_row_kept", yn(rows(23) == 1))

    with m.transaction() as outer:
        outer.execute(text("update tx_probe set note = 'outer' where id = 500"))
        try:
            with m.transaction() as inner:
                inner.execute(text("set local lock_timeout = '1s'"))
                inner.execute(text("update tx_probe set note = 'inner' where id = 500"))
            res["lock"] = "ok"
        except Exception as e:  # noqa: BLE001
            res["lock"] = "blocked (lock timeout)" if "lock timeout" in str(e) else "error"
    p("tx.nested.inner_updates_row_outer_locked", res["lock"])

    m2 = tm("replicadb", 2, "parity-c")
    with m.transaction():
        res["m2cur"] = "present" if m2.current() is not None else "empty"
        with m2.transaction() as other:
            res["m2db"] = db(other.connection)
    p("tx.two_managers_same_shard.second_current_inside_first", res["m2cur"])
    p("tx.two_managers_same_shard.second_tx_database", res["m2db"])
    m.close()
    m2.close()


# ------------------------------------------------------------ failures


def failures() -> None:
    m = tm("testdb", 2, "parity-c")
    LOGS.clear()
    got = None
    try:
        with m.transaction() as t:
            t.execute(text("insert into tx_probe values (30, 'doomed')"))
            mine = pid(t.connection)
            with side() as c:
                c.execute(f"select pg_terminate_backend({mine})")
            raise RuntimeError("boom")
    except RuntimeError as e:
        got = str(e)
    p("tx.failed_rollback.propagated", got)
    p("tx.failed_rollback.discard_logged", yn(logs("rollback failed") > 0))
    p("tx.failed_rollback.row_kept", yn(rows(30) == 1))
    try:
        with m.transaction() as t:
            t.execute(text("insert into tx_probe values (31, 'after')"))
        p("tx.failed_rollback.next_tx_ok", yn(rows(31) == 1))
    except Exception as e:  # noqa: BLE001
        p("tx.failed_rollback.next_tx_ok", f"no: {e}")

    sqlstate = "none"
    try:
        with m.transaction() as t:
            t.execute(text("insert into tx_deferred values (1)"))
            t.execute(text("insert into tx_deferred values (1)"))
    except Exception as e:  # noqa: BLE001
        sqlstate = getattr(getattr(e, "orig", None), "sqlstate", None) or "none"
    p("tx.commit_failure.sqlstate", sqlstate)
    p("tx.commit_failure.rows", count("select count(*) from tx_deferred"))
    try:
        with m.transaction() as t:
            t.execute(text("insert into tx_probe values (32, 'after-commit-failure')"))
        p("tx.commit_failure.next_tx_ok", yn(rows(32) == 1))
    except Exception:  # noqa: BLE001
        p("tx.commit_failure.next_tx_ok", "no")
    m.close()


# ------------------------------------------------------------ replica


def replica() -> None:
    member = ShardMemberConfig(
        member=0,
        configs={
            "primaryConfig": cfg("testdb", 3, "parity-c"),
            "secondaryConfig": cfg("replicadb", 3, "parity-c"),
        },
    )
    layout = ShardingConfig(
        default_shard=DEFAULT_SHARD, groups=(ShardGroupConfig(group=0, name="g", members=(member,)),)
    )
    registry = DatabaseRegistry.from_config(layout)
    m = registry.default
    repo = WalletRepository(Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy())
    with m.replica.connect() as c:
        p("rep.secondary_database", db(c))
    with repo.readonly_db() as c:
        p("rep.repo_readonly_database", db(c))
    res = {}
    with m.transaction() as t:
        t.execute(text("insert into tx_probe values (40, 'in-tx')"))
        tx_pid = pid(t.connection)
        with repo.readonly_db() as c:
            res["ro"] = db(c)
        with m.connection(readonly=True) as c:
            res["mro"] = db(c)
        with repo.db() as c:
            res["dbsame"] = yn(pid(c) == tx_pid)
            res["dbsees"] = yn(c.execute(text("select count(*) from tx_probe where id = 40")).scalar() == 1)
        with repo.tx_db_else_db() as c:
            res["else"] = yn(pid(c) == tx_pid)
        res["txdb"] = "present" if repo.tx_db() is not None else "empty"
    p("rep.repo_readonly_inside_tx_database", res["ro"])
    p("rep.manager_level_readonly_inside_tx_database", res["mro"] + " (manager.connection(readonly=True))")
    p("rep.repo_db_inside_tx_same_connection", res["dbsame"])
    p("rep.repo_db_inside_tx_sees_uncommitted", res["dbsees"])
    p("rep.repo_txDbElseDb_inside_tx_same_connection", res["else"])
    p("rep.repo_txDb_inside_tx", res["txdb"])
    p("rep.repo_txDb_outside_tx", "present" if repo.tx_db() is not None else "empty")
    with repo.tx_db_else_db() as c:
        c.execute(text("insert into tx_probe values (41, 'tx-else-outside')"))
    p("rep.repo_txDbElseDb_outside_autocommits", yn(rows(41) == 1))
    registry.close()

    no_replica = ShardingConfig.single(url("testdb"), username="probe", password=MARKER, pool_size=3)
    r2 = DatabaseRegistry.from_config(no_replica)
    with r2.default.replica.connect() as c:
        p("rep.no_replica.secondary_database", db(c))
    r2.close()


# ------------------------------------------------------------ accessors


def accessors() -> None:
    registry = DatabaseRegistry({DEFAULT_SHARD: tm("testdb", 3, "parity-c")})
    repo = WalletRepository(Wallet, WALLETS, registry, sharding=EmbeddedBitsShardingStrategy())
    unknown = ShardIdentifier(5, 5)
    for key, call in (
        ("acc.db_unknown_shard", lambda: repo.db(shard=unknown)),
        ("acc.readonly_unknown_shard", lambda: repo.readonly_db(shard=unknown)),
    ):
        try:
            with call() as c:
                r = db(c)
        except Exception as e:  # noqa: BLE001
            r = f"error: {e}"
        p(key, r)
    try:
        v = repo.tx_db(shard=unknown)
        r = "Optional.empty" if v is None else "present"
    except Exception as e:  # noqa: BLE001
        r = f"error: {e}"
    p("acc.txDb_unknown_shard", r)
    LOGS.clear()
    key = ShardedId.generate(unknown)
    with repo.db(key=key.value) as c:
        p("acc.db_by_id_unknown_shard", db(c))
    p("acc.db_by_id_unknown_shard.warned", yn(logs("is not registered") > 0))
    registry.close()


# ------------------------------------------------------------ pools


def pools() -> None:
    m = tm("testdb", 3, "parity-c-idle")
    with m.transaction() as t:
        t.execute(text("select 1"))
    time.sleep(3)
    p(
        "pool.open_connections_after_one_query",
        count("select count(*) from pg_stat_activity where application_name = 'parity-c-idle'"),
    )
    m.close()

    busy = tm("testdb", 3, "parity-c-max")

    def work() -> None:
        with busy.transaction() as t:
            t.execute(text("select pg_sleep(2)"))

    threads = [threading.Thread(target=work) for _ in range(5)]
    for th in threads:
        th.start()
    time.sleep(1)
    p(
        "pool.max_connections_under_load",
        count(
            "select count(*) from pg_stat_activity where application_name = 'parity-c-max' and state = 'active'"
        ),
    )
    for th in threads:
        th.join()
    p(
        "pool.driver_setting_reaches_server",
        yn(count("select count(*) from pg_stat_activity where application_name = 'parity-c-max'") > 0),
    )
    busy.close()


# ------------------------------------------------------------ redaction


def redaction() -> None:
    ok = tm("testdb", 1, "parity-c-login")
    with ok.transaction() as t:
        p("red.login_with_marker_password", yn(t.execute(text("select 1")).scalar() == 1))
    registry = DatabaseRegistry({DEFAULT_SHARD: ok})
    printed = repr(ok) + repr(ok.primary) + str(ok.primary.url) + repr(ok.primary.url) + repr(registry)
    p("red.printed_forms.marker", yn(MARKER in printed))
    ok.close()

    bad_cfg = DataSourceConfig(url=url("testdb"), username="probe", password=MARKER + "-wrong", pool_size=1)
    bad = TransactionManager(DEFAULT_SHARD, DataSource.from_config(bad_cfg))
    start = time.time()
    t_text = ""
    try:
        with bad.transaction() as t:
            t.execute(text("select 1"))
    except BaseException as e:  # noqa: BLE001
        t_text = chain(e)
    secs = round(time.time() - start)
    p("red.wrong_password.failed", yn(bool(t_text)))
    p("red.wrong_password.seconds_to_fail", f"about {secs}" if secs >= 5 else "under 5")
    p("red.wrong_password.marker_in_error", yn(MARKER in t_text))
    p("red.wrong_password.error_names_auth", yn("password authentication failed" in t_text))
    bad.close()

    no_user = DataSourceConfig(url=url("testdb"), password=MARKER)
    engine = DataSource.from_config(no_user).sqlalchemy_engine
    try:
        with engine.connect():
            t2 = "connected"
    except BaseException as e:  # noqa: BLE001
        t2 = chain(e)
    p("red.no_username.logs_in_as_os_user", yn(f'"{getpass.getuser()}"' in t2))
    p("red.no_username.marker_in_error", yn(MARKER in t2))
    p("red.no_username.warned", yn(any("username" in x.lower() and x.startswith("WARNING") for x in LOGS)))
    engine.dispose()
    p("red.all_captured_logs.marker", yn(any(MARKER in x for x in ALL)))
    p("red.all_captured_logs.count_nonzero", yn(bool(ALL)))
    p("red.all_captured_logs.debug_lines_present", yn(any(x.startswith("DEBUG") for x in ALL)))


def setup() -> None:
    with side() as c:
        c.execute("drop table if exists tx_probe")
        c.execute("create table tx_probe (id int primary key, note text)")
        c.execute("drop table if exists tx_deferred")
        c.execute("create table tx_deferred (id int, constraint u unique (id) deferrable initially deferred)")
        c.execute("insert into tx_probe values (500, 'locked-row')")


if __name__ == "__main__":
    setup()
    transactions()
    nesting()
    failures()
    replica()
    accessors()
    pools()
    redaction()
