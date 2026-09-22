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

from ekbatan.errors import EkbatanError
from ekbatan.repository import Repository
from fixtures.domain import Wallet, WalletId
from fixtures.schema import WALLETS


def test_a_repository_that_has_not_written_the_mapping_refuses(registry, clock):
    wallets = Repository(Wallet, WALLETS, registry, clock=clock)

    with pytest.raises(EkbatanError, match="has not been written"):
        wallets.to_row(object())  # type: ignore[arg-type]

    with pytest.raises(EkbatanError, match="has not been written"):
        wallets.from_row({})


def test_the_refusal_names_both_halves_and_how_to_write_them(registry, clock):
    wallets = Repository(Wallet, WALLETS, registry, clock=clock)

    with pytest.raises(EkbatanError) as raised:
        wallets.from_row({})

    message = str(raised.value)
    assert "columns_by_field_name" in message
    assert "model_by_field_name" in message
    assert "abstract" in message, "it should say what Java does"


def test_asking_for_the_by_name_mapping_is_a_line_you_write(registry, clock):
    """What every repository in this repository now says, and what it used to inherit."""

    class Written(Repository[Wallet, WalletId]):
        def to_row(self, model):
            return self.columns_by_field_name(model)

        def from_row(self, row):
            return self.model_by_field_name(row)

    wallets = Written(Wallet, WALLETS, registry, clock=clock)
    wallet = a_wallet()

    wallets.add(wallet)
    assert wallets.get(wallet.id).balance == wallet.balance


def a_wallet() -> Wallet:
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import uuid4

    return Wallet.open(
        id=WalletId.random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1.00"),
        created_date=datetime(2026, 1, 1, tzinfo=UTC),
    )
