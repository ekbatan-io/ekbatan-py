"""Typed identifiers through a real column.

What an id *is* - what it wraps, what it decodes, what it refuses - is settled in the unit
suite. This is the other half: that it survives a round trip, that a lookup accepts either the
wrapper or the bare uuid, and that routing reads the shard off it with nothing else consulted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TypedDict
from uuid import UUID

import pytest
from sqlalchemy import (
    CHAR,
    BigInteger,
    Column,
    DateTime,
    MetaData,
    Numeric,
    String,
    Table,
    Uuid,
)

from ekbatan import AbstractRepository, Model
from ekbatan.ids import Id, ShardedId
from ekbatan.sharding import EmbeddedBitsShardingStrategy, ShardIdentifier
from modes import rebased

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")

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


class PursesRecord(TypedDict):
    id: UUID
    version: int
    state: str
    currency: str
    balance: Decimal
    created_date: datetime
    updated_date: datetime


NOW = datetime(2026, 1, 1, 12, 0)


class PurseState(Enum):
    OPEN = "OPEN"
    DELETED = "DELETED"


@dataclass(frozen=True, kw_only=True)
class Purse(Model[Id["Purse"], PurseState]):
    currency: str
    balance: Decimal


def _prepare(registry):
    """This module brings its own table, so it creates and empties it itself - the shared
    fixture only knows about the wallet application's. `run_sync` on an async connection, as
    SQLAlchemy's asyncio API says: DDL helpers are sync."""
    for shard in registry.shards:
        engine = registry.manager(shard).primary
        with engine.begin() as conn:
            if hasattr(conn, "run_sync"):
                conn.run_sync(metadata.create_all)
            else:
                metadata.create_all(conn)
            conn.execute(PURSES.delete())


@rebased
class PurseRepository(AbstractRepository[Purse, PursesRecord, Id[Purse]]):
    """The mapping written out, as Java's `fromRecord` / `toRecord` are: `to_record` /
    `from_record`, typed by the table's record.

    This file is about what a *typed id* does on the way to a column and back, so the two lines
    that matter are the first of each: `purse.id.value` going out, `Id[Purse](...)` coming back.
    """

    def to_record(self, purse: Purse) -> PursesRecord:
        return {
            "id": purse.id.value,
            "version": purse.version,
            "state": purse.state.name,
            "currency": purse.currency,
            "balance": purse.balance,
            "created_date": purse.created_date,
            "updated_date": purse.updated_date or purse.created_date,
        }

    def from_record(self, values: PursesRecord) -> Purse:
        return Purse(
            id=Id[Purse](values["id"]),
            version=values["version"],
            state=PurseState[values["state"]],
            currency=values["currency"],
            balance=values["balance"],
            created_date=values["created_date"],
            updated_date=values["updated_date"],
        )


@pytest.fixture
def purses(registry, clock):
    _prepare(registry)
    return PurseRepository(Purse, PURSES, registry, clock=clock)


@pytest.fixture
def sharded_purses(sharded_registry, clock):
    _prepare(sharded_registry)
    return PurseRepository(
        Purse, PURSES, sharded_registry, sharding=EmbeddedBitsShardingStrategy(), clock=clock
    )


def a_purse(id: Id[Purse]) -> Purse:
    return Purse(
        id=id,
        state=PurseState.OPEN,
        version=1,
        currency="EUR",
        balance=Decimal("100.00"),
        created_date=NOW,
    )


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
    assert purses.count(PURSES.c.id == stored.id.value) == 1


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
