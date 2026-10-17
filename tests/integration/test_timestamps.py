"""Timestamps come back as Java's ``Instant`` does: aware UTC, equal to what was written.

The columns stay zone-less and hold the UTC wall-clock time, which is what Java writes into them
(measured on PostgreSQL, MySQL and MariaDB: Java's values never shift between write and read).
Only what Python hands back changed: an aware ``datetime`` in UTC, so a value read back equals
the value written and compares with ``clock.now()`` instead of raising ``TypeError``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import insert, select, text

from ekbatan.ids import ShardedId
from fixtures.domain import Wallet
from fixtures.schema import EVENTS

PLUS_TWO = timezone(timedelta(hours=2))


def test_a_model_read_back_equals_what_was_written_and_compares_with_the_clock(registry, clock):
    from fixtures.repositories import WalletRepository
    from fixtures.schema import WALLETS

    wallets = WalletRepository(Wallet, WALLETS, registry, clock=clock)
    written = wallets.add(
        Wallet.open(
            id=ShardedId[Wallet].random(),
            owner_id=uuid4(),
            currency="EUR",
            balance=Decimal("1"),
            # Whole seconds: the fixture table is a plain DATETIME, which MySQL keeps to the second.
            created_date=datetime(2026, 1, 1, 12, 0, 0, tzinfo=PLUS_TWO),
        )
    )
    read = wallets.get(written.id)

    assert read.created_date == written.created_date
    assert read.created_date == datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    assert read.created_date.tzinfo is UTC
    assert read.created_date < clock.now() + timedelta(hours=1), "no TypeError"


def test_the_column_holds_the_utc_wall_clock_time_as_java_writes_it(registry):
    """A +02:00 value is stored as its UTC time, with no zone: what Java's `InstantConverter`
    stores, so either port reads the other's rows as the same instant."""
    at = datetime(2026, 1, 1, 12, 30, 0, 250000, tzinfo=PLUS_TWO)
    row_id = uuid.uuid4()
    with registry.default.transaction() as conn:
        conn.execute(
            insert(EVENTS).values(
                id=row_id,
                namespace="n",
                action_id=uuid.uuid4(),
                action_name="A",
                action_params={},
                started_date=at,
                completion_date=at,
                event_date=at,
                delivered=False,
            )
        )
    with registry.default.connection(readonly=True) as conn:
        stored = conn.execute(text(f"SELECT started_date FROM {EVENTS.fullname}")).scalar_one()
        read = conn.execute(select(EVENTS.c.started_date)).scalar_one()

    if isinstance(stored, str):  # SQLite keeps text
        stored = datetime.fromisoformat(stored)
    assert stored == datetime(2026, 1, 1, 10, 30, 0, 250000), "UTC, no zone, to the microsecond"
    assert read == at, "read back as the same instant"
    assert read.tzinfo is UTC
