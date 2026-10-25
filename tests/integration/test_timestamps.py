"""Time is stored and read exactly as it is given. The framework converts nothing.

No zone is added, removed or moved on the way to the database or on the way back: a value goes
to the driver as the model, the row or the clock held it, and comes back as the driver returns
it. Whether that is right is the application's choice - the default clock gives UTC with no zone
attached, which is what a zone-less column holds.

Each check captures what was handed to SQLAlchemy and what reached the driver, so "as is" is
seen, not inferred from a round trip.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import event, insert, select, text

from ekbatan.ids import ShardedId
from fixtures.actions import (
    WalletCreateAction,
)
from fixtures.domain import Wallet
from fixtures.executing import run
from fixtures.repositories import WalletRepository
from fixtures.schema import EVENTS, WALLETS, in_sql

#: Each test runs on the sync API and on `ekbatan.aio` - see tests/modes.py.
MODES = ("sync", "async")

#: Naive UTC with microseconds - what `SystemClock().instant()` gives.
PRECISE = datetime(2026, 1, 1, 10, 0, 0, 123456)
#: An aware value that is not UTC.
TEHRAN = datetime(2026, 1, 1, 13, 30, 0, 654321, tzinfo=timezone(timedelta(hours=3, minutes=30)))


@contextmanager
def captured(registry: Any) -> Iterator[list[tuple[Any, Any]]]:
    """Every statement's parameters as handed to SQLAlchemy, and as handed to the driver."""
    engine = registry.default.primary.sqlalchemy_engine
    sync = getattr(engine, "sync_engine", engine)
    seen: list[tuple[Any, Any]] = []

    def hook(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool):
        seen.append((context.compiled_parameters, parameters))

    event.listen(sync, "before_cursor_execute", hook)
    try:
        yield seen
    finally:
        event.remove(sync, "before_cursor_execute", hook)


def _flat(parameters: Any) -> list[Any]:
    if isinstance(parameters, dict):
        return list(parameters.values())
    if isinstance(parameters, (list, tuple)):
        return [v for p in parameters for v in _flat(p)]
    return [parameters]


def _handed(seen: list[tuple[Any, Any]], value: datetime) -> tuple[bool, bool]:
    """Whether this very object was handed to SQLAlchemy, and to the driver."""
    to_sqlalchemy = any(v is value for compiled, _ in seen for v in _flat(compiled))
    to_driver = any(v is value for _, sent in seen for v in _flat(sent))
    return to_sqlalchemy, to_driver


def _raw(registry: Any, column: str, table: str, row_id: uuid.UUID) -> Any:
    """The column as the driver returns it, with no SQLAlchemy type in the way."""
    with registry.default.connection(readonly=True) as conn:
        rows = conn.execute(text(f"SELECT id, {column} FROM {table}")).all()
    for found_id, value in rows:
        if str(found_id).replace("-", "") == row_id.hex:
            return value
    raise AssertionError(f"no row {row_id} in {table}")


def _wallet(created: datetime) -> Wallet:
    return Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal("1"),
        created_date=created,
    )


def test_a_naive_time_reaches_the_driver_as_it_is_and_comes_back_equal(registry, clock, dialect):
    """Naive UTC to the microsecond, through a model and its repository, on every database."""
    wallet_repository = WalletRepository(Wallet, WALLETS, registry, clock=clock)

    with captured(registry) as seen:
        written = wallet_repository.add(_wallet(PRECISE))

    to_sqlalchemy, to_driver = _handed(seen, PRECISE)
    assert to_sqlalchemy, "the repository hands SQLAlchemy the model's own value"
    if dialect != "sqlite":  # SQLAlchemy writes SQLite's DATETIME as text; see the next test
        assert to_driver, "and SQLAlchemy hands it to the driver untouched"

    read = wallet_repository.get_by_id(written.id)
    assert read.created_date == PRECISE
    assert read.created_date.tzinfo is None
    assert read.created_date.microsecond == 123456


def test_on_sqlite_sqlalchemy_writes_the_digits_as_given(registry, clock, dialect):
    """SQLite has no time type: SQLAlchemy stores the value's own digits as text."""
    if dialect != "sqlite":
        return
    wallet_repository = WalletRepository(Wallet, WALLETS, registry, clock=clock)
    with captured(registry) as seen:
        wallet_repository.add(_wallet(PRECISE))
    sent = [v for _, params in seen for v in _flat(params)]
    assert "2026-01-01 10:00:00.123456" in sent


def test_an_aware_time_is_handed_to_the_driver_unchanged(registry, clock, dialect):
    """A +03:30 value is not moved to UTC or stripped by the framework. What the database does
    with it is the driver's and the database's business - here it is only observed."""
    wallet_repository = WalletRepository(Wallet, WALLETS, registry, clock=clock)
    model = _wallet(TEHRAN)
    assert model.created_date is TEHRAN, "the model keeps it as given"

    failure: Exception | None = None
    with captured(registry) as seen:
        try:
            written = wallet_repository.add(model)
        except Exception as error:  # a driver may refuse an aware value for a zone-less column
            failure = error

    to_sqlalchemy, to_driver = _handed(seen, TEHRAN)
    assert to_sqlalchemy, "handed to SQLAlchemy exactly as given"
    if dialect != "sqlite":
        assert to_driver, "and on to the driver exactly as given"
    if failure is not None:
        assert not type(failure).__module__.startswith("ekbatan"), f"the driver's: {failure!r}"
        return

    read = wallet_repository.get_by_id(written.id)
    raw = _raw(registry, "created_date", WALLETS.fullname, written.id.value)
    if isinstance(raw, str):
        raw = datetime.fromisoformat(raw)
    assert read.created_date == raw, "read back exactly as the driver returns it"


def test_the_outbox_columns_round_trip_a_naive_time_to_the_microsecond(registry):
    """The framework's own `timestamp_column()`, written and read with no help from a model."""
    with captured(registry) as seen, registry.default.transaction() as conn:
        conn.execute(
            insert(EVENTS).values(
                id=uuid.uuid4(),
                namespace="n",
                action_id=uuid.uuid4(),
                action_name="A",
                action_params={},
                started_date=PRECISE,
                completion_date=PRECISE,
                event_date=PRECISE,
                delivered=False,
            )
        )
    assert _handed(seen, PRECISE)[0]
    with registry.default.connection(readonly=True) as conn:
        read = conn.execute(select(EVENTS.c.started_date)).scalar_one()
        events = in_sql(EVENTS, registry.default.dialect)
        stored = conn.execute(text(f"SELECT started_date FROM {events}")).scalar_one()

    if isinstance(stored, str):  # SQLite keeps text
        stored = datetime.fromisoformat(stored)
    assert stored == PRECISE, "stored as given, to the microsecond"
    assert read == PRECISE and read.tzinfo is None, "read back as stored, no zone added"


def test_an_action_writes_the_clocks_own_value_to_the_outbox(executor, registry, clock, outbox):
    """The executor stamps `started_date` and `completion_date` from the clock, unchanged."""
    clock.pause_at(PRECISE)
    with captured(registry) as seen:
        run(
            executor,
            WalletCreateAction(executor.clock),
            WalletCreateAction.Params(
                owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1")
            ),
        )

    rows = [c for compiled, _ in seen for c in compiled if "event_date" in c]
    assert rows, "the outbox insert was seen"
    for c in rows:
        for name in ("started_date", "completion_date", "event_date"):
            assert c[name] is PRECISE, (name, c[name])
    row = next(r for r in outbox() if r["action_name"] == "WalletCreateAction")
    assert row["started_date"] == row["completion_date"] == row["event_date"] == PRECISE
    assert row["started_date"].tzinfo is None


def test_an_aware_clock_reaches_the_driver_unchanged(executor, registry, clock, dialect):
    """A clock of your own that gives +03:30 is not moved to UTC by the executor or the persister.
    asyncpg refuses such a value for a zone-less column - its refusal, not ours."""
    clock.pause_at(TEHRAN)
    with captured(registry) as seen:
        try:
            run(
                executor,
                WalletCreateAction(executor.clock),
                WalletCreateAction.Params(
                    owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1")
                ),
            )
        except Exception as error:
            assert not type(error).__module__.startswith("ekbatan"), repr(error)
    to_sqlalchemy, to_driver = _handed(seen, TEHRAN)
    assert to_sqlalchemy
    if dialect != "sqlite":
        assert to_driver
