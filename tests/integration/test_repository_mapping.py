"""The mapping between a row and a model is written, not guessed.

Java declares `fromRecord` and `toRecord` abstract on `AbstractRepository`, so a repository that
has not written them does not compile. This port defaulted to mapping by field name unless a
repository said otherwise, which is the one piece of magic that was left in the read and write
paths: rename a column without renaming the field and it silently stopped matching.

`EKB-0217` made the default a refusal. The by-name mapping is still one line - a dataclass knows
its own fields - but it is chosen now rather than inherited.
"""

from __future__ import annotations

import pytest

from ekbatan.ids import ShardedId
from ekbatan.repository import AbstractRepository
from fixtures.domain import Wallet
from fixtures.schema import WALLETS, WalletsRecord

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")


def test_a_repository_that_has_not_written_the_mapping_cannot_be_created(registry, clock):
    """Java declares `fromRecord` and `toRecord` abstract, so a repository without them does not
    compile. The nearest thing here is refusing to create it - where the mistake is made, at
    startup, rather than on the first row read or written."""

    class Forgot(AbstractRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
        pass

    with pytest.raises(TypeError, match=r"from_record\(\) and to_record\(\) have not been written"):
        Forgot(Wallet, WALLETS, registry, clock=clock)


def test_one_half_written_names_the_other(registry, clock):
    class HalfDone(AbstractRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
        def to_record(self, model):
            return self.columns_by_field_name(model)

    with pytest.raises(TypeError, match=r"from_record\(\) has not been written"):
        HalfDone(Wallet, WALLETS, registry, clock=clock)


def test_the_refusal_says_how_to_write_both_halves(registry, clock):
    class Forgot(AbstractRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
        pass

    with pytest.raises(TypeError) as raised:
        Forgot(Wallet, WALLETS, registry, clock=clock)

    message = str(raised.value)
    assert "columns_by_field_name" in message
    assert "model_by_field_name" in message
    assert "abstract" in message, "it should say what Java does"
    assert "WalletsRecord" in message, "it should say what the record is"


def test_the_abstract_base_itself_cannot_be_created(registry, clock):
    with pytest.raises(TypeError, match="cannot be created"):
        AbstractRepository(Wallet, WALLETS, registry, clock=clock)


def test_asking_for_the_by_name_mapping_is_a_line_you_write(registry, clock):
    """What every repository in this repository now says, and what it used to inherit."""

    class Written(AbstractRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
        def to_record(self, model):
            return self.columns_by_field_name(model)

        def from_record(self, record):
            return self.model_by_field_name(record)

    wallet_repository = Written(Wallet, WALLETS, registry, clock=clock)
    wallet = a_wallet()

    wallet_repository.add(wallet)
    assert wallet_repository.get_by_id(wallet.id).balance == wallet.balance


def a_wallet() -> Wallet:
    from datetime import datetime
    from decimal import Decimal
    from uuid import uuid4

    return Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        created_date=datetime(2026, 1, 1),
    )
