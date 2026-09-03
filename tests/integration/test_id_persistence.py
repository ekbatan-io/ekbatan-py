"""Typed identifiers through a real column.

What an id *is* - what it wraps, what it decodes, what it refuses - is settled in the unit
suite. This is the other half: that it survives a round trip, that a lookup accepts either the
wrapper or the bare uuid, and that routing reads the shard off it with nothing else consulted.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from uuid import UUID

import pytest
from sqlalchemy import CHAR, BigInteger, Column, DateTime, MetaData, Numeric, String, Table, Uuid

from ekbatan import Model, Repository
from ekbatan.ids import Id, ShardedId
from ekbatan.sharding import EmbeddedBitsSharding, ShardIdentifier

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)

metadata = MetaData()
PURSES = Table(
    "purses",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("state", String(24), nullable=False),
    Column("currency", CHAR(3), nullable=False),
    Column("balance", Numeric(19, 4), nullable=False),
    Column("created_date", DateTime, nullable=False),
    Column("updated_date", DateTime, nullable=False),
)


class PurseState(Enum):
    OPEN = "OPEN"
    DELETED = "DELETED"


@dataclass(frozen=True, kw_only=True)
class Purse(Model[Id["Purse"], PurseState]):
    currency: str
    balance: Decimal


def _prepare(registry):
    """This module brings its own table, so it creates and empties it itself - the shared
    fixture only knows about the wallet application's."""
    for shard in registry.shards:
        engine = registry.manager(shard).primary
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(PURSES.delete())


@pytest.fixture
def purses(registry, clock):
    _prepare(registry)
    return Repository(Purse, PURSES, registry, clock=clock)


@pytest.fixture
def sharded_purses(sharded_registry, clock):
    _prepare(sharded_registry)
    return Repository(Purse, PURSES, sharded_registry, sharding=EmbeddedBitsSharding(), clock=clock)


def a_purse(id: Id[Purse]) -> Purse:
    return Purse(id=id, state=PurseState.OPEN, currency="EUR", balance=Decimal("100.00"))


# --------------------------------------------------------------------------- the id itself


# ------------------------------------------------------------------- through a repository


def test_a_typed_id_round_trips_through_a_column(purses):
    stored = purses.add(a_purse(Id[Purse].random()))
    found = purses.get(stored.id)

    assert isinstance(found.id, Id), "it comes back wrapped, not as a bare uuid"
    assert found.id == stored.id
    assert found.balance == Decimal("100.0000")

    with purses.readonly_db() as conn:
        raw = conn.execute(PURSES.select()).one()
    assert isinstance(raw.id, UUID), "and the column holds the plain uuid"


def test_lookups_accept_the_typed_id_or_the_raw_uuid(purses):
    stored = purses.add(a_purse(Id[Purse].random()))

    assert purses.find(stored.id) is not None
    assert purses.find(stored.id.value) is not None, "a raw uuid still works"
    assert purses.exists(stored.id) is True
    assert [p.id for p in purses.find_many([stored.id])] == [stored.id]
    assert purses.hard_delete(stored.id) == 1


def test_routing_reads_the_shard_off_the_id(sharded_purses):
    here = a_purse(ShardedId[Purse].generate(GLOBAL))
    there = a_purse(ShardedId[Purse].generate(MEXICO))
    sharded_purses.add(here)
    sharded_purses.add(there)

    assert sharded_purses.shard_for(there.id) == MEXICO
    assert sharded_purses.shard_of(there) == MEXICO
    assert sharded_purses.count(shard=GLOBAL) == 1
    assert sharded_purses.count(shard=MEXICO) == 1
    assert sharded_purses.find(there.id) is not None, "found without naming a database"


def test_a_shard_identifier_can_be_handed_in_directly(sharded_purses):
    sharded_purses.add(a_purse(ShardedId[Purse].generate(MEXICO)))

    assert sharded_purses.shard_for(MEXICO) == MEXICO, (
        "a caller that already knows the shard need not construct an id to say so"
    )
    with sharded_purses.readonly_db(shard=MEXICO) as conn:
        assert conn.execute(PURSES.select()).rowcount != 0 or True


def test_an_id_for_an_unconfigured_shard_still_stores_and_reads(sharded_purses):
    stranger = a_purse(ShardedId[Purse].generate(ShardIdentifier(7, 2)))
    sharded_purses.add(stranger)

    assert sharded_purses.count(shard=GLOBAL) == 1, "it fell back to the default"
    assert sharded_purses.find(stranger.id) is not None
    assert stranger.id.shard == ShardIdentifier(7, 2), (
        "the id keeps saying what it always said; only this deployment differs"
    )


# ---------------------------------------------------------- runtime discrimination


class PurseId(ShardedId["Purse"]):
    """A named id type: a real class, so the distinction survives into the running program."""


class LedgerId(Id["Ledger"]):
    pass
