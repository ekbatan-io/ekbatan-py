package io.ekbatan.core.test.repository;

import static io.ekbatan.core.test.model.Dummy.createDummy;
import static java.util.UUID.randomUUID;

import io.ekbatan.core.persistence.TransactionManager;
import io.ekbatan.core.repository.AbstractRepository;
import io.ekbatan.core.shard.DatabaseRegistry;
import io.ekbatan.core.test.model.Dummy;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Arrays;
import java.util.Currency;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.function.Consumer;
import org.jooq.DSLContext;
import java.util.regex.Pattern;
import org.jooq.Condition;
import org.jooq.impl.DSL;
import org.jooq.impl.SQLDataType;

/** Parity probe (area A). Temporary; prints PROBE lines only. */
public abstract class ProbeRepoBase {

    protected abstract String d();

    protected abstract AbstractRepository<Dummy, ?, ?, UUID> repo();

    protected abstract DatabaseRegistry registry();

    protected abstract TransactionManager tm();

    /** The statement that sets a non-UTC session zone, scoped to the transaction where possible. */
    protected abstract String setZoneSql();

    static final Pattern UUIDP =
            Pattern.compile("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");
    static final Currency EUR = Currency.getInstance("EUR");
    static final Instant T0 = Instant.parse("2024-01-02T03:04:05.123456Z");

    protected void p(String k, Object v) {
        System.out.println("PROBE " + d() + "." + k + ": " + norm(String.valueOf(v)));
    }

    static String norm(String s) {
        if (s == null) return "null";
        var first = s.split("\n")[0];
        return UUIDP.matcher(first).replaceAll("<uuid>");
    }

    protected void err(String k, Callable<?> r) {
        try {
            var v = r.call();
            p(k, "no error -> " + v);
        } catch (Throwable t) {
            Throwable c = t;
            if (c.getClass() == RuntimeException.class && c.getCause() != null) c = c.getCause();
            p(k + ".err", c.getClass().getSimpleName());
            p(k + ".msg", c.getMessage());
        }
    }

    protected void errClass(String k, Callable<?> r) {
        try {
            r.call();
            p(k, "no error");
        } catch (Throwable t) {
            Throwable c = t;
            if (c.getClass() == RuntimeException.class && c.getCause() != null) c = c.getCause();
            p(k + ".err", c.getClass().getSimpleName());
        }
    }

    protected Dummy fresh(UUID owner) {
        return createDummy(owner, EUR, BigDecimal.TEN, T0).build();
    }

    protected Dummy fresh() {
        return fresh(randomUUID());
    }

    protected static UUID id(Dummy x) {
        return x.id.getValue();
    }

    protected static Condition owner(UUID o) {
        return DSL.field(DSL.name("owner_id"), SQLDataType.UUID).eq(o);
    }

    /** Count on a separate pooled connection, outside any transaction. */
    protected long otherConn(UUID id) {
        var ctx = registry().primary.get(registry().defaultShard);
        return ctx.fetchCount(DSL.table(DSL.name("dummies")), DSL.field(DSL.name("id"), SQLDataType.UUID).eq(id));
    }

    public void runAll() {
        writes();
        updates();
        reads();
        transactions();
        zone();
    }

    void writes() {
        var a = fresh();
        var added = repo().add(a);
        p("add.same_instance", added == a);
        p("add.events_on_input", a.events.size());
        p("add.events_on_result", added.events.size());
        p("add.version", added.version);
        p("add.created_date", added.createdDate);
        p("add.updated_date", added.updatedDate);
        var f = repo().findById(id(a)).orElseThrow();
        p("find.created_date", f.createdDate);
        p("find.created_date_equals_input", f.createdDate.equals(T0));
        p("find.events", f.events.size());
        p("find.balance", f.balance.toPlainString());

        var b = fresh();
        repo().addNoResult(b);
        p("addNoResult.row_exists", repo().existsById(id(b)));

        p("addAll.empty.size", repo().addAll(List.of()).size());
        var c1 = fresh();
        var c2 = fresh();
        var res = repo().addAll(List.of(c1, c2));
        p("addAll.two.size", res.size());
        p("addAll.two.order_kept", res.get(0).id.equals(c1.id) && res.get(1).id.equals(c2.id));
        p("addAll.two.same_instances", res.get(0) == c1);
        p("addAll.two.events_on_result", res.get(0).events.size());
        var c3 = fresh();
        var r1 = repo().addAll(List.of(c3));
        p("addAll.one.same_instance", r1.get(0) == c3);

        err("addAll.null", () -> repo().addAll(null));
        var c4 = fresh();
        err("addAll.null_element", () -> repo().addAll(Arrays.asList(c4, null)));
        p("addAll.null_element.first_row_written", repo().existsById(id(c4)));
        err("add.null", () -> repo().add(null));
        err("addNoResult.null", () -> {
            repo().addNoResult(null);
            return null;
        });
        err("addAllNoResult.empty", () -> {
            repo().addAllNoResult(List.of());
            return "ok";
        });
        err("addAllNoResult.null", () -> {
            repo().addAllNoResult(null);
            return "ok";
        });
        errClass("add.duplicate_id", () -> repo().add(a));
        errClass("addAll.duplicate_id_in_batch", () -> repo().addAll(List.of(fresh(), a)));
    }

    void updates() {
        var a = repo().add(fresh());
        var u = repo().update(repo().getById(id(a)).deposit(BigDecimal.ONE));
        p("update.version", u.version);
        p("update.events_on_result", u.events.size());
        p("update.db_version", repo().getById(id(a)).version);
        p("update.balance", u.balance.stripTrailingZeros().toPlainString());
        p("update.updated_date", u.updatedDate);
        p("update.created_date", u.createdDate);

        var stale = repo().getById(id(a)); // version 2
        repo().update(stale.deposit(BigDecimal.ONE)); // db -> 3
        err("update.stale", () -> repo().update(stale.deposit(BigDecimal.ONE)));
        err("updateNoResult.stale", () -> {
            repo().updateNoResult(stale.deposit(BigDecimal.ONE));
            return null;
        });
        p("update.stale.db_version_after", repo().getById(id(a)).version);

        var ghost = fresh();
        err("update.missing", () -> repo().update(ghost));
        err("updateNoResult.missing", () -> {
            repo().updateNoResult(ghost);
            return null;
        });

        var cur = repo().getById(id(a));
        repo().updateNoResult(cur.deposit(BigDecimal.ONE));
        p("updateNoResult.db_version", repo().getById(id(a)).version);

        err("update.null", () -> repo().update(null));

        // soft delete
        var del = repo().add(fresh());
        var delU = repo().update(repo().getById(id(del)).delete());
        p("delete.returned_state", delU.state);
        p("delete.returned_version", delU.version);
        p("delete.findById_present", repo().findById(id(del)).isPresent());
        err("update.soft_deleted_row", () -> repo().update(delU.deposit(BigDecimal.ONE)).version);

        // updateAll
        p("updateAll.empty.size", repo().updateAll(List.of()).size());
        var x1 = repo().add(fresh());
        var x2 = repo().add(fresh());
        var g1 = repo().getById(id(x1));
        var g2 = repo().getById(id(x2));
        var ua = repo().updateAll(List.of(g2.deposit(BigDecimal.ONE), g1.deposit(BigDecimal.ONE)));
        p("updateAll.two.versions", ua.get(0).version + "," + ua.get(1).version);
        p("updateAll.two.order_kept", ua.get(0).id.equals(x2.id));
        p("updateAll.two.events_on_result", ua.get(0).events.size());
        p("updateAll.two.balance", ua.get(0).balance.stripTrailingZeros().toPlainString());
        var one = repo().updateAll(List.of(repo().getById(id(x1)).deposit(BigDecimal.ONE)));
        p("updateAll.one.version", one.get(0).version);

        // one stale in a batch, outside a transaction
        var s1 = repo().getById(id(x1)); // current
        var s2 = repo().getById(id(x2)); // current
        repo().update(s1.deposit(BigDecimal.ONE)); // s1 now stale
        var s2v = s2.version;
        err("updateAll.one_stale", () -> repo().updateAll(List.of(s1.deposit(BigDecimal.ONE), s2.deposit(BigDecimal.ONE))));
        p("updateAll.one_stale.fresh_row_moved", repo().getById(id(x2)).version > s2v);
        var s3 = repo().getById(id(x2));
        err("updateAllNoResult.one_stale", () -> {
            repo().updateAllNoResult(List.of(s1.deposit(BigDecimal.ONE), s3.deposit(BigDecimal.ONE)));
            return null;
        });
        p("updateAllNoResult.one_stale.fresh_row_moved", repo().getById(id(x2)).version > s3.version);

        // same row twice in one batch
        var y = repo().add(fresh());
        var yy = repo().getById(id(y));
        err("updateAll.same_row_twice", () -> repo().updateAll(
                        List.of(yy.deposit(BigDecimal.ONE), yy.deposit(BigDecimal.TWO)))
                .size());
        p("updateAll.same_row_twice.db_version", repo().getById(id(y)).version);
        var yy2 = repo().getById(id(y));
        err("updateAllNoResult.same_row_twice", () -> {
            repo().updateAllNoResult(List.of(yy2.deposit(BigDecimal.ONE), yy2.deposit(BigDecimal.TWO)));
            return null;
        });

        // all-null nullable column in a batch
        var n1 = repo().add(fresh());
        var n2 = repo().add(fresh());
        err("updateAll.null_column_every_row", () -> repo().updateAll(List.of(
                                repo().getById(id(n1)).deposit(BigDecimal.ONE),
                                repo().getById(id(n2)).deposit(BigDecimal.ONE)))
                .get(0)
                .rewardPoints);
        err("updateAll.null_collection", () -> repo().updateAll(null));
    }

    void reads() {
        p("findById.missing", repo().findById(randomUUID()).isPresent());
        err("getById.missing", () -> repo().getById(randomUUID()));
        var live = repo().add(fresh());
        var del = repo().add(fresh());
        repo().update(repo().getById(id(del)).delete());
        err("getById.deleted", () -> repo().getById(id(del)));
        p("existsById.live", repo().existsById(id(live)));
        p("existsById.deleted", repo().existsById(id(del)));
        p("existsById.missing", repo().existsById(randomUUID()));

        p("findAllByIds.empty", repo().findAllByIds(List.of()).size());
        err("findAllByIds.null", () -> repo().findAllByIds(null).size());
        p(
                "findAllByIds.dupes_missing_deleted",
                repo().findAllByIds(List.of(id(live), id(live), randomUUID(), id(del)))
                        .size());

        var o = randomUUID();
        var w1 = repo().add(fresh(o));
        var w2 = repo().add(fresh(o));
        var w3 = repo().add(fresh(o));
        repo().update(repo().getById(id(w3)).delete());
        p("findAllWhere.two_live_one_deleted", repo().findAllWhere(owner(o)).size());
        p("countWhere.two_live_one_deleted", repo().countWhere(owner(o)));
        p("existsWhere.live", repo().existsWhere(owner(o)));
        err("findOneWhere.two_matches", () -> repo().findOneWhere(owner(o)).isPresent());
        p("findOneWhere.none", repo().findOneWhere(owner(randomUUID())).isPresent());
        var o2 = randomUUID();
        var only = repo().add(fresh(o2));
        p("findOneWhere.one", repo().findOneWhere(owner(o2)).map(x -> x.id.equals(only.id)).orElse(false));
        var o3 = randomUUID();
        var dOnly = repo().add(fresh(o3));
        repo().update(repo().getById(id(dOnly)).delete());
        p("findOneWhere.deleted_only", repo().findOneWhere(owner(o3)).isPresent());
        p("existsWhere.deleted_only", repo().existsWhere(owner(o3)));
        p("countWhere.deleted_only", repo().countWhere(owner(o3)));

        var beforeCount = repo().count();
        var beforeAll = repo().findAll().size();
        repo().add(fresh());
        var z = repo().add(fresh());
        repo().update(repo().getById(id(z)).delete());
        p("count.delta_after_one_live_one_deleted", repo().count() - beforeCount);
        p("findAll.delta_after_one_live_one_deleted", repo().findAll().size() - beforeAll);
        p("count.equals_findAll", repo().count() == repo().findAll().size());
    }

    void transactions() {
        var n = fresh();
        tm().inTransaction((Consumer<DSLContext>) ctx -> {
            repo().add(n);
            p("tx.findById_sees_own_insert", repo().findById(id(n)).isPresent());
            p("tx.existsById_sees_own_insert", repo().existsById(id(n)));
            p("tx.countWhere_sees_own_insert", repo().countWhere(owner(n.ownerId)));
            p("tx.findAllByIds_sees_own_insert", repo().findAllByIds(List.of(id(n))).size());
            p("tx.findAll_sees_own_insert", repo().findAll().stream().anyMatch(x -> x.id.equals(n.id)));
            p("tx.findOneWhere_sees_own_insert", repo().findOneWhere(owner(n.ownerId)).isPresent());
            p("tx.existsWhere_sees_own_insert", repo().existsWhere(owner(n.ownerId)));
            p("tx.other_connection_sees_insert", otherConn(id(n)));
        });
        p("tx.after_commit_visible", repo().existsById(id(n)));

        tm().inTransaction((Consumer<DSLContext>) ctx -> {
            var nu = repo().update(repo().getById(id(n)).deposit(BigDecimal.ONE));
            p("tx.update_returns_version", nu.version);
            p("tx.getById_after_own_update_version", repo().getById(id(n)).version);
        });
        p("tx.after_commit_version", repo().getById(id(n)).version);

        var rb = fresh();
        try {
            tm().inTransaction((Consumer<DSLContext>) ctx -> {
                repo().add(rb);
                repo().update(repo().getById(id(n)).deposit(BigDecimal.ONE));
                repo().addAll(List.of(fresh(), fresh()));
                throw new IllegalStateException("boom");
            });
        } catch (RuntimeException e) {
            p("tx.rollback.exception_seen", (e.getCause() != null ? e.getCause() : e).getClass().getSimpleName());
        }
        p("tx.rollback.insert_gone", !repo().existsById(id(rb)));
        p("tx.rollback.update_gone_version", repo().getById(id(n)).version);

        // stale batch inside a transaction: the good row is rolled back too
        var t1 = repo().add(fresh());
        var t2 = repo().add(fresh());
        var st1 = repo().getById(id(t1));
        var st2 = repo().getById(id(t2));
        repo().update(st1.deposit(BigDecimal.ONE));
        try {
            tm().inTransaction((Consumer<DSLContext>) ctx -> {
                repo().updateAll(List.of(st1.deposit(BigDecimal.ONE), st2.deposit(BigDecimal.ONE)));
            });
        } catch (RuntimeException e) {
            p("tx.updateAll_one_stale.exception", (e.getCause() != null ? e.getCause() : e).getClass().getSimpleName());
        }
        p("tx.updateAll_one_stale.fresh_row_version", repo().getById(id(t2)).version);

        var o = fresh();
        repo().add(o);
        p("notx.other_connection_sees_insert", otherConn(id(o)));
    }

    void zone() {
        var z1 = repo().add(fresh());
        var z2 = repo().add(fresh());
        var z3 = fresh();
        tm().inTransaction((Consumer<DSLContext>) ctx -> {
            ctx.execute(setZoneSql());
            repo().add(z3);
            var ua = repo().updateAll(List.of(
                    repo().getById(id(z1)).deposit(BigDecimal.ONE),
                    repo().getById(id(z2)).deposit(BigDecimal.ONE)));
            repo().update(ua.get(0).deposit(BigDecimal.ONE));
        });
        p("zone.add_created_date", repo().getById(id(z3)).createdDate);
        p("zone.updateAll_created_date", repo().getById(id(z2)).createdDate);
        p("zone.update_created_date", repo().getById(id(z1)).createdDate);
    }
}
