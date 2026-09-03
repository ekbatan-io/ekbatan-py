"""Identifiers, in isolation - what they wrap, what they decode, and what they refuse.

Whether one survives a column is a different question, and lives in the integration suite.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import uuid4

import pytest

from ekbatan import Model
from ekbatan.errors import ValidationError
from ekbatan.ids import Id, ShardedId, TypedValue, unwrap
from ekbatan.sharding import ShardIdentifier

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)


class PurseState(Enum):
    OPEN = "OPEN"
    DELETED = "DELETED"


@dataclass(frozen=True, kw_only=True)
class Purse(Model[Id["Purse"], PurseState]):
    currency: str
    balance: Decimal


class PurseId(ShardedId["Purse"]):
    """A named id type: a real class, so the distinction survives into the running program."""


class LedgerId(Id["Ledger"]):
    pass


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


def test_named_id_types_are_different_at_runtime_not_only_to_a_checker():
    raw = uuid4()

    assert PurseId(raw) != LedgerId(raw), (
        "a generic parameter is erased at runtime; a subclass is not"
    )
    assert PurseId(raw) == PurseId(raw)
    assert len({PurseId(raw), LedgerId(raw)}) == 2, "and they do not collide in a set either"
    assert PurseId(raw) != raw, "nor with the bare uuid"


def test_a_generic_alias_cannot_do_the_same():
    """Stated so nobody is surprised by it later."""
    raw = uuid4()

    class A:
        pass

    class B:
        pass

    assert Id[A](raw) == Id[B](raw), (
        "Python erases the parameter exactly as Java erases its own - use a named subclass "
        "when you want the interpreter to check too"
    )


def test_a_named_id_keeps_every_factory():
    routed = PurseId.generate(MEXICO)
    assert isinstance(routed, PurseId)
    assert routed.shard == MEXICO
    assert routed.minted_at is not None
    assert isinstance(PurseId.of(str(routed.value)), PurseId)


def test_a_sharded_id_with_no_shard_named_goes_to_the_default():
    """The inherited random() would mint a version 4 uuid, and .shard would then have to raise
    on an id this class had just produced."""
    assert PurseId.random().shard == GLOBAL


def test_a_named_id_still_matches_its_base_class():
    raw = uuid4()
    assert PurseId(raw) == ShardedId["Purse"](raw), (
        "a row read back is rebuilt as whatever its field declares, so the family has to agree"
    )
    assert isinstance(PurseId(raw), Id)
