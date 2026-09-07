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


# -- what Java's IdTest and TypedValueTest pin -------------------------------------------------


def test_an_id_can_be_parsed_from_its_string_form():
    """Java's `Id.of(Class, String)`, which is how an id comes back off the wire."""
    original = PurseId.generate(MEXICO)

    assert PurseId.of(str(original)) == original
    assert PurseId.of(original.value) == original

    with pytest.raises(ValidationError, match="is not a uuid"):
        PurseId.of("not-a-uuid")


def test_an_id_prints_as_its_uuid_and_nothing_else():
    """Java's `Id.toString()` overrides TypedValue's to return the bare uuid.

    The persister writes `str(model.id)` into the outbox's `model_id` column, so anything
    fancier here would change what a downstream consumer correlates on.
    """
    wallet_id = PurseId.random()

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

    assert hash(PurseId(shared)) == hash(PurseId(shared))
    assert PurseId(shared) != PurseId(uuid4())
    assert len({PurseId(shared), PurseId(shared)}) == 1


def test_comparing_ids_delegates_to_the_uuid():
    """Java's `compareTo`, which is what orders a batched write."""
    low = PurseId(UUID("00000000-0000-0000-0000-000000000001"))
    high = PurseId(UUID("00000000-0000-0000-0000-000000000002"))

    assert low < high
    assert high > low
    assert not low < low
    assert sorted([high, low]) == [low, high]


# -- the ids the framework mints for its own rows ----------------------------------------------


def test_an_ordered_uuid_is_a_version_7_uuid_that_never_repeats_or_goes_back():
    """What makes `(event_date, id)` a total order on the outbox.

    Every row of one unit of work carries that unit's completion instant, so the id is the only
    thing separating them. A `sharded_uuid` will not do: its timestamp is milliseconds and its
    low bits are random, and a unit of work writes all its rows inside one millisecond.
    """
    import uuid as uuid_mod
    from itertools import pairwise

    from ekbatan.sharding import ordered_uuid

    minted = [ordered_uuid() for _ in range(20000)]

    assert {u.version for u in minted} == {7}
    assert {u.variant for u in minted} == {uuid_mod.RFC_4122}
    assert all(a < b for a, b in pairwise(minted)), "strictly increasing, with no ties"
    assert len(set(minted)) == len(minted)


def test_a_sharded_uuid_deliberately_makes_no_such_promise():
    """The contrast, so the reason `ordered_uuid` exists stays visible.

    Two sharded ids minted in one millisecond differ only in random bits, so their order is
    whatever the random bits happened to be. That is fine for a wallet id and not fine for a
    row whose position in a stream is the point.
    """
    from ekbatan.sharding import sharded_uuid

    same_ms = [sharded_uuid(MEXICO, at_ms=1_700_000_000_000) for _ in range(200)]

    assert sorted(same_ms) != same_ms or len(set(same_ms)) < 200, (
        "if this ever holds by luck, it is luck - the ordering is not promised"
    )


def test_ordered_uuids_stay_ordered_across_threads():
    """One process, several workers, one stream. The lock is what makes that true."""
    import threading

    from ekbatan.sharding import ordered_uuid

    minted: list = []
    guard = threading.Lock()
    start = threading.Barrier(8)

    def mint() -> None:
        start.wait()
        mine = [ordered_uuid() for _ in range(2000)]
        with guard:
            minted.extend(mine)

    threads = [threading.Thread(target=mint) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(minted)) == 16000, "no two workers ever got the same id"
