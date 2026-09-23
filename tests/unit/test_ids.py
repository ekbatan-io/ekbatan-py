"""Identifiers, in isolation - what they wrap, what they decode, and what they refuse.

Whether one survives a column is a different question, and lives in the integration suite.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

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


class Ledger:
    """A second model, so two models' ids can be compared - Java's `OtherModel`."""


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


# -- Java's rules, measured on Java's own classes through jshell ---------------------------------


def test_two_models_ids_with_one_uuid_are_equal_as_they_are_in_java():
    """The model is erased at runtime in both languages. Java's `Id.of(Wallet.class, u)` keeps no
    class at all - `equals` compares the uuid - and so does `ShardedId`. Telling the two apart is
    the checker's job, and mypy does it: `ShardedId[Ledger]` is rejected where
    `ShardedId[Purse]` is expected."""
    raw = uuid4()

    assert Id[Purse](raw) == Id[Ledger](raw)
    assert len({Id[Purse](raw), Id[Ledger](raw)}) == 1
    routed = ShardedId[Purse].generate(MEXICO).value
    assert ShardedId[Purse](routed) == ShardedId[Ledger](routed)


def test_a_sharded_id_is_not_an_id():
    """Java's `ShardedId` implements `ShardAwareId` and `ModelId`; it does not extend `Id`."""
    routed = ShardedId[Purse].generate(MEXICO)

    assert not isinstance(routed, Id)
    assert not issubclass(ShardedId, Id)


def test_an_id_and_a_sharded_id_are_never_equal():
    """Java: `Id.equals` compares `getClass()`, `ShardedId.equals` requires a `ShardedId` - so
    neither direction matches, whatever the uuid."""
    raw = ShardedId[Purse].generate(MEXICO).value

    assert Id[Purse](raw) != ShardedId[Purse](raw)
    assert ShardedId[Purse](raw) != Id[Purse](raw)
    assert len({Id[Purse](raw), ShardedId[Purse](raw)}) == 2


def test_an_id_is_not_its_bare_uuid():
    raw = uuid4()

    assert Id[Purse](raw) != raw
    assert ShardedId[Purse](raw) != raw


def test_a_subclass_of_an_id_still_equals_it():
    """So a value an application wrapped in its own subclass compares equal to the same value
    read back from a column, which is rebuilt as the declared type."""

    class Special(Id[Purse]):
        pass

    raw = uuid4()
    assert Special(raw) == Id[Purse](raw)


def test_a_sharded_id_keeps_every_factory_through_the_subscript():
    """`ShardedId[Purse].generate(shard)` is Java's `ShardedId.generate(Purse.class, shard)`."""
    routed = ShardedId[Purse].generate(MEXICO)
    assert type(routed) is ShardedId
    assert routed.shard == MEXICO
    assert routed.minted_at is not None
    assert ShardedId[Purse].of(str(routed.value)) == routed


def test_a_sharded_id_with_no_shard_named_goes_to_the_default():
    """`random()` has no Java counterpart; it is `generate(DEFAULT_SHARD)`, so `.shard` never
    has to raise on an id this class produced itself."""
    assert ShardedId[Purse].random().shard == GLOBAL


# -- what Java's IdTest and TypedValueTest pin -------------------------------------------------


def test_an_id_can_be_parsed_from_its_string_form():
    """Java's `Id.of(Class, String)`, which is how an id comes back off the wire."""
    original = ShardedId[Purse].generate(MEXICO)

    assert ShardedId[Purse].of(str(original)) == original
    assert ShardedId[Purse].of(original.value) == original

    with pytest.raises(ValidationError, match="is not a uuid"):
        ShardedId[Purse].of("not-a-uuid")


def test_an_id_prints_as_its_uuid_and_nothing_else():
    """Java's `Id.toString()` overrides TypedValue's to return the bare uuid.

    The persister writes `str(model.id)` into the outbox's `model_id` column, so anything
    fancier here would change what a downstream consumer correlates on.
    """
    wallet_id = ShardedId[Purse].random()

    assert str(wallet_id) == str(wallet_id.value)
    assert f"{wallet_id}" == str(wallet_id.value)


def test_a_typed_value_prints_bare_and_reprs_with_its_type():
    """Where Python and Java put the type name differ, deliberately.

    Java's `TypedValue.toString()` is `AccountNumber{ABC123}` - one string carrying both. Python
    has two, so `str()` is the value for building strings with and `repr()` carries the type for
    reading in a traceback. Anything else would put `AccountNumber{...}` inside f-strings.
    """

    class AccountNumber(TypedValue[str]):
        pass

    number = AccountNumber("ABC123")

    assert str(number) == "ABC123"
    assert repr(number) == "AccountNumber('ABC123')"


def test_equal_ids_hash_together_and_unequal_ones_do_not():
    shared = uuid4()

    assert hash(ShardedId[Purse](shared)) == hash(ShardedId[Purse](shared))
    assert ShardedId[Purse](shared) != ShardedId[Purse](uuid4())
    assert len({ShardedId[Purse](shared), ShardedId[Purse](shared)}) == 1


def test_comparing_ids_delegates_to_the_uuid():
    """Java's `compareTo`, which is what orders a batched write."""
    low = ShardedId[Purse](UUID("00000000-0000-0000-0000-000000000001"))
    high = ShardedId[Purse](UUID("00000000-0000-0000-0000-000000000002"))

    assert low < high
    assert high > low
    assert not low < low
    assert sorted([high, low]) == [low, high]


# -- the shard coordinate, and the bits it has to fit into -------------------------------------
#
# Java's ShardIdentifierTest, case for case.


def test_the_coordinate_accepts_its_whole_range_and_nothing_outside_it():
    from ekbatan.sharding import MAX_GROUP, MAX_MEMBER, MIN_GROUP, MIN_MEMBER

    assert (MIN_GROUP, MAX_GROUP, MIN_MEMBER, MAX_MEMBER) == (0, 255, 0, 63), (
        "8 bits of group and 6 of member, which is what a sharded uuid reserves"
    )

    lowest = ShardIdentifier(MIN_GROUP, MIN_MEMBER)
    highest = ShardIdentifier(MAX_GROUP, MAX_MEMBER)
    assert (lowest.group, lowest.member) == (0, 0)
    assert (highest.group, highest.member) == (255, 63)

    for group, member, word in [
        (-1, 0, "group"),
        (256, 0, "group"),
        (0, -1, "member"),
        (0, 64, "member"),
    ]:
        with pytest.raises(ValidationError, match=word):
            ShardIdentifier(group, member)


def test_the_widest_coordinate_survives_a_round_trip_through_a_sharded_uuid():
    """The bounds case that would break first if the bit layout were ever mis-shifted: every
    shard bit set, decoded back out of the id."""
    from ekbatan.sharding import MAX_GROUP, MAX_MEMBER, read_shard, sharded_uuid

    widest = ShardIdentifier(MAX_GROUP, MAX_MEMBER)

    assert read_shard(sharded_uuid(widest)) == widest
    assert all(
        read_shard(sharded_uuid(ShardIdentifier(g, m))) == ShardIdentifier(g, m)
        for g in (0, 1, 127, 255)
        for m in (0, 1, 62, 63)
    )
