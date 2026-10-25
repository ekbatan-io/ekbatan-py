"""Area A twin of ProbeRepoBase.java (+ ProbeRepo{Pg,Mysql,Mariadb}Test.java).

    uv run --no-sync python parity-probes/A-repository/probe_repo.py [postgres mysql mariadb]
"""

from __future__ import annotations

import sys
from decimal import Decimal
from uuid import uuid4

from common import (
    DUMMIES,
    T0,
    Out,
    database,
    fresh,
    jbool,
    plain,
    single_registry,
    started,
    ts,
)
from sqlalchemy import func, select, text

from fixtures.domain import Wallet
from fixtures.repositories import WalletRepository

PREFIX = {"postgres": "pg", "mysql": "mysql", "mariadb": "mariadb"}
ZONE_SQL = {
    "postgres": "SET LOCAL TIME ZONE 'America/New_York'",
    "mysql": "SET time_zone = '-05:00'",
    "mariadb": "SET time_zone = '-05:00'",
}
ONE, TWO = Decimal("1"), Decimal("2")


def run(dialect: str) -> None:
    backend = started(dialect)
    try:
        url = database(backend, "probe_a")
        reg = single_registry(url)
        repo = WalletRepository(Wallet, DUMMIES, reg)
        o = Out(PREFIX[dialect])
        tm = reg.default

        def other_conn(key) -> int:
            with tm.primary.connect() as c:
                return c.execute(
                    select(func.count()).select_from(DUMMIES).where(DUMMIES.c.id == key.value)
                ).scalar_one()

        def owner(x):
            return DUMMIES.c.owner_id == x

        # ---------------------------------------------------------------- writes
        a = fresh()
        added = repo.add(a)
        o.p("add.same_instance", jbool(added is a))
        o.p("add.events_on_input", len(a.events))
        o.p("add.events_on_result", len(added.events))
        o.p("add.version", added.version)
        o.p("add.created_date", ts(added.created_date))
        o.p("add.updated_date", ts(added.updated_date))
        f = repo.find_by_id(a.id.value)
        o.p("find.created_date", ts(f.created_date))
        o.p("find.created_date_equals_input", jbool(f.created_date == T0))
        o.p("find.events", len(f.events))
        o.p("find.balance", f.balance)

        b = fresh()
        repo.add_no_result(b)
        o.p("addNoResult.row_exists", jbool(repo.exists_by_id(b.id.value)))

        o.p("addAll.empty.size", len(repo.add_all([])))
        c1, c2 = fresh(), fresh()
        res = repo.add_all([c1, c2])
        o.p("addAll.two.size", len(res))
        o.p("addAll.two.order_kept", jbool(res[0].id == c1.id and res[1].id == c2.id))
        o.p("addAll.two.same_instances", jbool(res[0] is c1))
        o.p("addAll.two.events_on_result", len(res[0].events))
        c3 = fresh()
        r1 = repo.add_all([c3])
        o.p("addAll.one.same_instance", jbool(r1[0] is c3))

        o.err("addAll.null", lambda: repo.add_all(None))
        c4 = fresh()
        o.err("addAll.null_element", lambda: repo.add_all([c4, None]))
        o.p("addAll.null_element.first_row_written", jbool(repo.exists_by_id(c4.id.value)))
        o.err("add.null", lambda: repo.add(None))
        o.err("addNoResult.null", lambda: repo.add_no_result(None))
        o.err("addAllNoResult.empty", lambda: (repo.add_all_no_result([]), "ok")[1])
        o.err("addAllNoResult.null", lambda: (repo.add_all_no_result(None), "ok")[1])
        o.err_class("add.duplicate_id", lambda: repo.add(a))
        o.err_class("addAll.duplicate_id_in_batch", lambda: repo.add_all([fresh(), a]))

        # --------------------------------------------------------------- updates
        a = repo.add(fresh())
        u = repo.update(repo.get_by_id(a.id.value).deposit(ONE))
        o.p("update.version", u.version)
        o.p("update.events_on_result", len(u.events))
        o.p("update.db_version", repo.get_by_id(a.id.value).version)
        o.p("update.balance", plain(u.balance))
        o.p("update.updated_date", ts(u.updated_date))
        o.p("update.created_date", ts(u.created_date))

        stale = repo.get_by_id(a.id.value)
        repo.update(stale.deposit(ONE))
        o.err("update.stale", lambda: repo.update(stale.deposit(ONE)))
        o.err("updateNoResult.stale", lambda: repo.update_no_result(stale.deposit(ONE)))
        o.p("update.stale.db_version_after", repo.get_by_id(a.id.value).version)

        ghost = fresh()
        o.err("update.missing", lambda: repo.update(ghost))
        o.err("updateNoResult.missing", lambda: repo.update_no_result(ghost))

        cur = repo.get_by_id(a.id.value)
        repo.update_no_result(cur.deposit(ONE))
        o.p("updateNoResult.db_version", repo.get_by_id(a.id.value).version)

        o.err("update.null", lambda: repo.update(None))

        d = repo.add(fresh())
        del_u = repo.update(repo.get_by_id(d.id.value).delete())
        o.p("delete.returned_state", del_u.state.name)
        o.p("delete.returned_version", del_u.version)
        o.p("delete.findById_present", jbool(repo.find_by_id(d.id.value) is not None))
        o.err("update.soft_deleted_row", lambda: repo.update(del_u.deposit(ONE)).version)

        o.p("updateAll.empty.size", len(repo.update_all([])))
        x1, x2 = repo.add(fresh()), repo.add(fresh())
        g1, g2 = repo.get_by_id(x1.id.value), repo.get_by_id(x2.id.value)
        ua = repo.update_all([g2.deposit(ONE), g1.deposit(ONE)])
        o.p("updateAll.two.versions", f"{ua[0].version},{ua[1].version}")
        o.p("updateAll.two.order_kept", jbool(ua[0].id == x2.id))
        o.p("updateAll.two.events_on_result", len(ua[0].events))
        o.p("updateAll.two.balance", plain(ua[0].balance))
        one = repo.update_all([repo.get_by_id(x1.id.value).deposit(ONE)])
        o.p("updateAll.one.version", one[0].version)

        s1, s2 = repo.get_by_id(x1.id.value), repo.get_by_id(x2.id.value)
        repo.update(s1.deposit(ONE))
        s2v = s2.version
        o.err("updateAll.one_stale", lambda: repo.update_all([s1.deposit(ONE), s2.deposit(ONE)]))
        o.p("updateAll.one_stale.fresh_row_moved", jbool(repo.get_by_id(x2.id.value).version > s2v))
        s3 = repo.get_by_id(x2.id.value)
        o.err(
            "updateAllNoResult.one_stale",
            lambda: repo.update_all_no_result([s1.deposit(ONE), s3.deposit(ONE)]),
        )
        o.p("updateAllNoResult.one_stale.fresh_row_moved", jbool(repo.get_by_id(x2.id.value).version > s3.version))

        y = repo.add(fresh())
        yy = repo.get_by_id(y.id.value)
        o.err("updateAll.same_row_twice", lambda: len(repo.update_all([yy.deposit(ONE), yy.deposit(TWO)])))
        o.p("updateAll.same_row_twice.db_version", repo.get_by_id(y.id.value).version)
        yy2 = repo.get_by_id(y.id.value)
        o.err(
            "updateAllNoResult.same_row_twice",
            lambda: repo.update_all_no_result([yy2.deposit(ONE), yy2.deposit(TWO)]),
        )

        n1, n2 = repo.add(fresh()), repo.add(fresh())
        o.err(
            "updateAll.null_column_every_row",
            lambda: repo.update_all(
                [repo.get_by_id(n1.id.value).deposit(ONE), repo.get_by_id(n2.id.value).deposit(ONE)]
            )[0].reward_points,
        )
        o.err("updateAll.null_collection", lambda: repo.update_all(None))

        # ----------------------------------------------------------------- reads
        o.p("findById.missing", jbool(repo.find_by_id(uuid4()) is not None))
        o.err("getById.missing", lambda: repo.get_by_id(uuid4()))
        live = repo.add(fresh())
        dl = repo.add(fresh())
        repo.update(repo.get_by_id(dl.id.value).delete())
        o.err("getById.deleted", lambda: repo.get_by_id(dl.id.value))
        o.p("existsById.live", jbool(repo.exists_by_id(live.id.value)))
        o.p("existsById.deleted", jbool(repo.exists_by_id(dl.id.value)))
        o.p("existsById.missing", jbool(repo.exists_by_id(uuid4())))

        o.p("findAllByIds.empty", len(repo.find_all_by_ids([])))
        o.err("findAllByIds.null", lambda: len(repo.find_all_by_ids(None)))
        o.p(
            "findAllByIds.dupes_missing_deleted",
            len(repo.find_all_by_ids([live.id.value, live.id.value, uuid4(), dl.id.value])),
        )

        ow = uuid4()
        repo.add(fresh(ow))
        repo.add(fresh(ow))
        w3 = repo.add(fresh(ow))
        repo.update(repo.get_by_id(w3.id.value).delete())
        o.p("findAllWhere.two_live_one_deleted", len(repo.find_all_where(owner(ow))))
        o.p("countWhere.two_live_one_deleted", repo.count_where(owner(ow)))
        o.p("existsWhere.live", jbool(repo.exists_where(owner(ow))))
        o.err("findOneWhere.two_matches", lambda: jbool(repo.find_one_where(owner(ow)) is not None))
        o.p("findOneWhere.none", jbool(repo.find_one_where(owner(uuid4())) is not None))
        o2 = uuid4()
        only = repo.add(fresh(o2))
        got = repo.find_one_where(owner(o2))
        o.p("findOneWhere.one", jbool(got is not None and got.id == only.id))
        o3 = uuid4()
        d_only = repo.add(fresh(o3))
        repo.update(repo.get_by_id(d_only.id.value).delete())
        o.p("findOneWhere.deleted_only", jbool(repo.find_one_where(owner(o3)) is not None))
        o.p("existsWhere.deleted_only", jbool(repo.exists_where(owner(o3))))
        o.p("countWhere.deleted_only", repo.count_where(owner(o3)))

        before_count = repo.count()
        before_all = len(repo.find_all())
        repo.add(fresh())
        z = repo.add(fresh())
        repo.update(repo.get_by_id(z.id.value).delete())
        o.p("count.delta_after_one_live_one_deleted", repo.count() - before_count)
        o.p("findAll.delta_after_one_live_one_deleted", len(repo.find_all()) - before_all)
        o.p("count.equals_findAll", jbool(repo.count() == len(repo.find_all())))

        # ---------------------------------------------------------- transactions
        n = fresh()
        with tm.transaction():
            repo.add(n)
            o.p("tx.findById_sees_own_insert", jbool(repo.find_by_id(n.id.value) is not None))
            o.p("tx.existsById_sees_own_insert", jbool(repo.exists_by_id(n.id.value)))
            o.p("tx.countWhere_sees_own_insert", repo.count_where(owner(n.owner_id)))
            o.p("tx.findAllByIds_sees_own_insert", len(repo.find_all_by_ids([n.id.value])))
            o.p("tx.findAll_sees_own_insert", jbool(any(x.id == n.id for x in repo.find_all())))
            o.p("tx.findOneWhere_sees_own_insert", jbool(repo.find_one_where(owner(n.owner_id)) is not None))
            o.p("tx.existsWhere_sees_own_insert", jbool(repo.exists_where(owner(n.owner_id))))
            o.p("tx.other_connection_sees_insert", other_conn(n.id))
        o.p("tx.after_commit_visible", jbool(repo.exists_by_id(n.id.value)))

        with tm.transaction():
            nu = repo.update(repo.get_by_id(n.id.value).deposit(ONE))
            o.p("tx.update_returns_version", nu.version)
            o.p("tx.getById_after_own_update_version", repo.get_by_id(n.id.value).version)
        o.p("tx.after_commit_version", repo.get_by_id(n.id.value).version)

        rb = fresh()
        try:
            with tm.transaction():
                repo.add(rb)
                repo.update(repo.get_by_id(n.id.value).deposit(ONE))
                repo.add_all([fresh(), fresh()])
                raise RuntimeError("boom")
        except RuntimeError as e:
            o.p("tx.rollback.exception_seen", type(e).__name__)
        o.p("tx.rollback.insert_gone", jbool(not repo.exists_by_id(rb.id.value)))
        o.p("tx.rollback.update_gone_version", repo.get_by_id(n.id.value).version)

        t1, t2 = repo.add(fresh()), repo.add(fresh())
        st1, st2 = repo.get_by_id(t1.id.value), repo.get_by_id(t2.id.value)
        repo.update(st1.deposit(ONE))
        try:
            with tm.transaction():
                repo.update_all([st1.deposit(ONE), st2.deposit(ONE)])
        except Exception as e:  # noqa: BLE001
            o.p("tx.updateAll_one_stale.exception", type(e).__name__)
        o.p("tx.updateAll_one_stale.fresh_row_version", repo.get_by_id(t2.id.value).version)

        ob = fresh()
        repo.add(ob)
        o.p("notx.other_connection_sees_insert", other_conn(ob.id))

        # ------------------------------------------------------------------ zone
        z1, z2 = repo.add(fresh()), repo.add(fresh())
        z3 = fresh()
        with tm.transaction() as tx:
            tx.execute(text(ZONE_SQL[dialect]))
            repo.add(z3)
            ua = repo.update_all([repo.get_by_id(z1.id.value).deposit(ONE), repo.get_by_id(z2.id.value).deposit(ONE)])
            repo.update(ua[0].deposit(ONE))
        o.p("zone.add_created_date", ts(repo.get_by_id(z3.id.value).created_date))
        o.p("zone.updateAll_created_date", ts(repo.get_by_id(z2.id.value).created_date))
        o.p("zone.update_created_date", ts(repo.get_by_id(z1.id.value).created_date))
        reg.close()
    finally:
        backend.stop()


if __name__ == "__main__":
    for d in sys.argv[1:] or ["postgres", "mysql", "mariadb"]:
        run(d)
