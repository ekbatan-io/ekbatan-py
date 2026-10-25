"""Area A twin of ProbeShardedPgTest.java: routing on a two-shard PostgreSQL registry.

Java uses two containers, this uses two databases in one container; routing is the same.

    uv run --no-sync python parity-probes/A-repository/probe_sharded.py
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from common import DUMMIES, Out, database, fresh, jbool, manager, started
from sqlalchemy import func, select

from ekbatan.ids import ShardedId
from ekbatan.persistence import DatabaseRegistry
from ekbatan.sharding import EmbeddedBitsShardingStrategy, ShardIdentifier, sharded_uuid
from fixtures.domain import Wallet
from fixtures.repositories import WalletRepository

A, B, U = ShardIdentifier(0, 0), ShardIdentifier(1, 0), ShardIdentifier(2, 0)
ONE = Decimal("1")


class NonIdStrategy:
    """Routes every model to B by its content, and cannot route by id."""

    uses_shard_aware_id = False

    def shard_for_id(self, key):
        return None

    def shard_for(self, persistable):
        return B


def on(shard, owner=None) -> Wallet:
    return fresh(owner, id=ShardedId[Wallet](sharded_uuid(shard))).without_events()


def main() -> None:
    backend = started("postgres")
    try:
        url_a = database(backend, "shard_a")
        url_b = database(backend, "shard_b")
        tm_a, tm_b = manager(A, url_a), manager(B, url_b)
        reg = DatabaseRegistry.builder().with_default_database(tm_a).with_database(tm_b).build()
        s = WalletRepository(Wallet, DUMMIES, reg, sharding=EmbeddedBitsShardingStrategy())
        n = WalletRepository(Wallet, DUMMIES, reg)
        o = Out("shard")

        def count_on(shard, key) -> int:
            with reg.manager(shard).primary.connect() as c:
                return c.execute(
                    select(func.count()).select_from(DUMMIES).where(DUMMIES.c.id == key.value)
                ).scalar_one()

        dB = on(B)
        s.add(dB)
        o.p("add_B.on_B", count_on(B, dB.id))
        o.p("add_B.on_A", count_on(A, dB.id))
        o.p("findById_B", jbool(s.find_by_id(dB.id) is not None))
        o.p("existsById_B", jbool(s.exists_by_id(dB.id)))
        dU = on(U)
        s.add(dU)
        o.p("add_unregistered.on_A", count_on(A, dU.id))
        o.p("findById_unregistered", jbool(s.find_by_id(dU.id) is not None))

        dA, dB2 = on(A), on(B)
        o.err("addAll_cross.A_then_B", lambda: s.add_all([dA, dB2]))
        o.err("addAll_cross.B_then_A", lambda: s.add_all([dB2, dA]))
        o.p("addAll_cross.nothing_written", count_on(A, dA.id) + count_on(B, dB2.id))
        o.err("addAllNoResult_cross", lambda: s.add_all_no_result([dA, dB2]))
        o.err("addAll_registered_and_unregistered", lambda: len(s.add_all([on(B), on(U)])))
        o.err("addAll_default_and_unregistered", lambda: len(s.add_all([on(A), on(U)])))
        s.add(dA)
        s.add(dB2)
        o.err(
            "updateAll_cross",
            lambda: s.update_all([s.get_by_id(dA.id).deposit(ONE), s.get_by_id(dB2.id).deposit(ONE)]),
        )
        dB3 = s.add(on(B))
        o.p("update_on_B.version", s.update(s.get_by_id(dB3.id).deposit(ONE)).version)

        o.p("count_across", s.count())
        o.p("findAll_across", len(s.find_all()))
        owner_b = uuid4()
        s.add(on(B, owner_b))
        cond = DUMMIES.c.owner_id == owner_b
        o.p("findOneWhere_across", jbool(s.find_one_where(cond) is not None))
        o.p("existsWhere_across", jbool(s.exists_where(cond)))
        o.p("countWhere_across", s.count_where(cond))
        o.p("findAllWhere_across", len(s.find_all_where(cond)))
        o.p("findAllByIds_across", len(s.find_all_by_ids([dB.id, dA.id, dU.id, sharded_uuid(B)])))

        owner_both = uuid4()
        s.add(on(A, owner_both))
        s.add(on(B, owner_both))
        o.err(
            "findOneWhere_one_match_per_shard",
            lambda: jbool(s.find_one_where(DUMMIES.c.owner_id == owner_both) is not None),
        )

        with tm_a.primary.connect() as c:
            a_rows = c.execute(select(func.count()).select_from(DUMMIES)).scalar_one()
        o.p("unsharded.count_equals_A_rows", jbool(n.count() == a_rows))
        o.p("unsharded.findAll_equals_A_rows", jbool(len(n.find_all()) == a_rows))
        dB5 = on(B)
        n.add(dB5)
        o.p("unsharded.add_B_id.on_A", count_on(A, dB5.id))
        o.p("unsharded.add_B_id.on_B", count_on(B, dB5.id))
        o.p("unsharded.findById_row_on_B", jbool(n.find_by_id(dB.id) is not None))
        o.err("unsharded.addAll_mixed_ids", lambda: len(n.add_all([on(A), on(B)])))

        x = WalletRepository(Wallet, DUMMIES, reg, sharding=NonIdStrategy())
        o.err("nonid.findById", lambda: x.find_by_id(uuid4()))
        o.err("nonid.existsById", lambda: x.exists_by_id(uuid4()))
        o.err("nonid.findAllByIds", lambda: x.find_all_by_ids([uuid4()]))
        o.err("nonid.getById", lambda: x.get_by_id(uuid4()))
        dX = on(A)
        x.add(dX)
        o.p("nonid.add_routes_by_model.on_B", count_on(B, dX.id))
        o.p("nonid.findAll_scatters", jbool(len(x.find_all()) == len(s.find_all())))

        dB6 = on(B)
        try:
            with tm_a.transaction():
                s.add(dB6)
                raise RuntimeError("boom")
        except Exception as e:  # noqa: BLE001
            o.p("tx_on_A.outer_exception", type(e).__name__)
        o.p("tx_on_A.write_to_B_survives_A_rollback", count_on(B, dB6.id))

        dA7 = on(A)
        try:
            with tm_a.transaction():
                s.add(dA7)
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        o.p("tx_on_A.write_to_A_rolled_back", jbool(count_on(A, dA7.id) == 0))

        reg_fail = (
            DatabaseRegistry.builder()
            .with_default_database(tm_a)
            .with_database(tm_b)
            .with_unknown_shard_policy("fail")
            .build()
        )
        sf = WalletRepository(Wallet, DUMMIES, reg_fail, sharding=EmbeddedBitsShardingStrategy())
        o.err("fail_policy.add_unregistered", lambda: sf.add(on(U)))
        o.err("fail_policy.findById_unregistered", lambda: sf.find_by_id(sharded_uuid(U)))
        o.err("fail_policy.findAllByIds_unregistered", lambda: sf.find_all_by_ids([sharded_uuid(U)]))
        reg.close()
    finally:
        backend.stop()


if __name__ == "__main__":
    main()
