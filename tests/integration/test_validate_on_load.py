"""A model's `validate()` runs on every row read back, not only on the ones a program builds.

`from_record` makes the model like any other code does, so the row a repository hands back has
passed the built-in checks and then `validate()`. A row the model would refuse - written by an
older version of the program, by hand, by another service - is refused where it is read, with
the model's own message, rather than travelling on as a wallet with a negative balance.

Each row is spoiled with plain SQL behind the repository's back, then read through it: one row,
many rows, every read path, a model and an entity. Both modes, every database.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import update

from ekbatan.errors import ValidationError
from ekbatan.ids import Id, ShardedId
from fixtures.domain import NOW, Notification, Wallet
from fixtures.repositories import NotificationRepository, WalletRepository
from fixtures.schema import NOTIFICATIONS, WALLETS

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")


@pytest.fixture
def wallet_repository(registry, clock):
    return WalletRepository(Wallet, WALLETS, registry, clock=clock)


@pytest.fixture
def notes(registry, clock):
    return NotificationRepository(Notification, NOTIFICATIONS, registry, clock=clock)


def a_wallet(balance: str = "10") -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal(balance),
        created_date=NOW,
    )


def spoil(registry, statement) -> None:
    """Write behind the repository's back, as another program would."""
    engine = registry.default.primary.sqlalchemy_engine
    with engine.begin() as conn:
        conn.execute(statement)


def test_a_row_the_model_refuses_is_refused_where_it_is_read(registry, wallet_repository):
    kept = wallet_repository.add(a_wallet())
    spoiled = wallet_repository.add(a_wallet())
    assert wallet_repository.get(spoiled.id).balance == Decimal(10), "a good row reads back"

    spoil(
        registry,
        update(WALLETS).where(WALLETS.c.id == spoiled.id.value).values(balance=Decimal("-5")),
    )

    with pytest.raises(ValidationError, match="balance cannot be negative"):
        wallet_repository.get(spoiled.id)
    with pytest.raises(ValidationError, match="balance cannot be negative"):
        wallet_repository.find_many([kept.id, spoiled.id])
    assert wallet_repository.get(kept.id).balance == Decimal(10), "only the bad row is refused"


def test_every_read_path_checks_the_rows_it_makes(registry, wallet_repository):
    stored = wallet_repository.add(a_wallet())
    spoil(
        registry,
        update(WALLETS).where(WALLETS.c.id == stored.id.value).values(balance=Decimal("-1")),
    )
    for read in (
        lambda: wallet_repository.find(stored.id),
        lambda: wallet_repository.find_where(WALLETS.c.id == stored.id.value),
        lambda: wallet_repository.find_one_where(WALLETS.c.id == stored.id.value),
        lambda: wallet_repository.all(),
    ):
        with pytest.raises(ValidationError, match="balance cannot be negative"):
            read()


def test_an_entity_s_validate_runs_on_load(registry, notes, wallet_repository):
    wallet = wallet_repository.add(a_wallet())
    note = notes.add(
        Notification.create(
            id=Id[Notification].random(), wallet_id=wallet.id, kind="email", recipient="ada@x"
        )
    )
    assert notes.get(note.id).recipient == "ada@x"

    spoil(
        registry,
        update(NOTIFICATIONS).where(NOTIFICATIONS.c.id == note.id.value).values(recipient=" "),
    )

    with pytest.raises(ValidationError, match="recipient cannot be blank"):
        notes.get(note.id)
