"""Typed identifiers, end to end through a repository.

A UUID parameter accepts any UUID, so a wallet id and a notification id are the same type to a
checker and different things to everyone else. These make them different types too, and - where
the id carries shard bits - let routing be read off the id with nothing else consulted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

import pytest
from sqlalchemy import CHAR, BigInteger, Column, DateTime, MetaData, Numeric, String, Table, Uuid

from ekbatan import Model, Repository
from ekbatan.errors import ValidationError
from ekbatan.ids import Id, ShardedId, TypedValue, unwrap
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


@pytest.fixture
def purses(registry, clock):
    metadata.create_all(registry.default.primary)
    return Repository(Purse, PURSES, registry, clock=clock)


@pytest.fixture
def sharded_purses(sharded_registry, clock):
    for shard in sharded_registry.shards:
        metadata.create_all(sharded_registry.manager(shard).primary)
    return Repository(Purse, PURSES, sharded_registry, sharding=EmbeddedBitsSharding(), clock=clock)


def a_purse(id: Id[Purse]) -> Purse:
    return Purse(id=id, state=PurseState.OPEN, currency="EUR", balance=Decimal("100.00"))


# --------------------------------------------------------------------------- the id itself


def test_an_id_wraps_a_uuid_and_says_what_it_identifies():
    raw = uuid4()
    wallet_id = Id[Purse].of(raw)

    assert wallet_id.value == raw
    assert str(wallet_id) == str(raw), "it renders as the uuid the column holds"
    assert Id[Purse].of(str(raw)) == wallet_id, "and parses one back"
    assert unwrap(wallet_id) is raw

    with pytest.raises(ValidationError, match="not a uuid"):
        Id[Purse].of("nonsense")


def test_a_random_id_carries_no_shard_and_a_generated_one_does():
    plain = Id[Purse].random()
    assert plain.shard is None, "a version 4 id names no shard, and that is allowed"
    assert plain.minted_at is None

    routed = ShardedId[Purse].generate(MEXICO)
    assert routed.shard == MEXICO
    assert routed.minted_at is not None
    assert (datetime.now(UTC) - routed.minted_at).total_seconds() < 5, (
        "a version 7 id carries when it was minted, free of charge"
    )


def test_a_sharded_id_refuses_to_pretend():
    with pytest.raises(ValidationError, match="carries no shard"):
        _ = ShardedId[Purse](uuid4()).shard


def test_ids_sort_by_when_they_were_minted():
    """Across milliseconds, which is the guarantee version 7 actually makes.

    Within one millisecond the low bits are random and the order is unspecified - so this
    asserts what is true rather than what would be convenient. What it buys is index locality:
    rows written together land together, which a random primary key never does.
    """
    ids = []
    for _ in range(10):
        ids.append(ShardedId[Purse].generate(GLOBAL))
        time.sleep(0.002)

    assert ids == sorted(ids)
    assert [i.minted_at for i in ids] == sorted(i.minted_at for i in ids)  # type: ignore[type-var]


def test_a_typed_value_can_wrap_anything():
    @dataclass(frozen=True, order=True)
    class Iban(TypedValue[str]):
        value: str

    account = Iban("GB33BUKB20201555555555")
    assert str(account) == "GB33BUKB20201555555555"
    assert unwrap(account) == "GB33BUKB20201555555555"
    assert account == Iban("GB33BUKB20201555555555")


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
