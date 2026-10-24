"""The persister on its own: what it writes, in what order, on whose connection.

Reachable as a unit because it is a class. Inline in the executor, every one of these
assertions needed a whole action executed around it to get at.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from ekbatan import ChangePersister, RepositoryRegistry
from ekbatan.action.outbox import SingleTableJsonEventPersister
from ekbatan.action.plan import PersistableChanges
from ekbatan.errors import ValidationError
from ekbatan.ids import Id, ShardedId
from fixtures.domain import Notification, Wallet
from fixtures.schema import EVENTS

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")

NOW = datetime(2026, 1, 1, 10, 0, 0)


@pytest.fixture
def persister(registry, wallet_repository):
    return ChangePersister(
        RepositoryRegistry([wallet_repository]), SingleTableJsonEventPersister(registry)
    )


def a_wallet(balance: str = "100.00") -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal(balance),
        created_date=NOW,
    )


def write(persister, registry, shard, changes):
    with registry.manager(shard).transaction() as tx:
        persister.persist(
            tx,
            changes,
            namespace="example.wallet",
            action_name="SomeAction",
            params={"k": "v"},
            started=NOW,
            completed=NOW,
            action_id=uuid.uuid4(),
        )


def test_it_writes_the_rows_and_then_their_events(persister, registry, wallet_repository):
    wallet = a_wallet()
    staged = PersistableChanges(additions={wallet.id: wallet})

    write(persister, registry, registry.default_shard, {Wallet: staged})

    assert wallet_repository.get(wallet.id).balance == Decimal("100.0000")
    with registry.default.connection(readonly=True) as conn:
        rows = list(conn.execute(select(EVENTS.c.model_id, EVENTS.c.event_type)))
    assert [r.event_type for r in rows] == ["WalletCreatedEvent"]
    assert rows[0].model_id == str(wallet.id)


def test_a_unit_of_work_that_staged_nothing_still_leaves_a_row(persister, registry):
    write(persister, registry, registry.default_shard, {})

    with registry.default.connection(readonly=True) as conn:
        rows = list(conn.execute(select(EVENTS.c.action_name, EVENTS.c.event_type)))
    assert [(r.action_name, r.event_type) for r in rows] == [("SomeAction", None)]


def test_it_opens_no_transaction_of_its_own(persister, registry, wallet_repository):
    """Handed a connection, it writes on it - so the caller's rollback takes everything."""
    wallet = a_wallet()
    try:
        with registry.manager(registry.default_shard).transaction() as tx:
            persister.persist(
                tx,
                {Wallet: PersistableChanges(additions={wallet.id: wallet})},
                namespace="example.wallet",
                action_name="SomeAction",
                params={},
                started=NOW,
                completed=NOW,
                action_id=uuid.uuid4(),
            )
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert wallet_repository.find(wallet.id) is None
    with registry.default.connection(readonly=True) as conn:
        assert conn.execute(select(func.count()).select_from(EVENTS)).scalar_one() == 0


def test_an_entity_is_written_and_emits_nothing(registry, notification_repository):
    persister = ChangePersister(
        RepositoryRegistry([notification_repository]), SingleTableJsonEventPersister(registry)
    )
    note = Notification.create(
        wallet_id=ShardedId[Wallet].random(),
        kind="X",
        recipient="ada@x",
        id=Id[Notification].random(),
    )

    write(
        persister,
        registry,
        registry.default_shard,
        {Notification: PersistableChanges(additions={note.id: note})},
    )

    assert notification_repository.get(note.id).recipient == "ada@x"
    with registry.default.connection(readonly=True) as conn:
        rows = list(conn.execute(select(EVENTS.c.event_type)))
    assert [r.event_type for r in rows] == [None], "an entity's changes are not events"


def test_it_names_the_type_it_has_no_repository_for(persister, registry):
    note = Notification.create(
        wallet_id=ShardedId[Wallet].random(),
        kind="X",
        recipient="ada@x",
        id=Id[Notification].random(),
    )

    with pytest.raises(ValidationError, match="no repository is registered for Notification"):
        write(
            persister,
            registry,
            registry.default_shard,
            {Notification: PersistableChanges(additions={note.id: note})},
        )


def test_it_refuses_to_be_built_with_no_repositories(registry):
    with pytest.raises(ValidationError, match="at least one repository"):
        ChangePersister(RepositoryRegistry(), SingleTableJsonEventPersister(registry))
