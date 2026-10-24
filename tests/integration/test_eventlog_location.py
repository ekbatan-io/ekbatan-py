"""Where the two event tables are, on each database - Dracula 14, the user's option 2.

**PostgreSQL:** ``eventlog.events`` and ``eventlog.event_notifications``, in a schema of their own
in each database - exactly Java's. **MySQL and MariaDB:** in the application's own database,
because there a schema is a database, and one ``eventlog`` database would be shared by every
database on the server. **SQLite:** in its one file. Nothing passes the tables: the executor's
default persister, the fan-out and the dispatch name them themselves.

Each test writes through the framework, then asks the database itself, in hand-written SQL - which
no ``schema_translate_map`` touches - where the rows are.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from ekbatan import DEFAULT_SHARD, DatabaseRegistry, TransactionManager
from ekbatan.action.outbox import SingleTableJsonEventPersister
from fixtures.actions import CreateWalletAction, CreateWalletParams
from fixtures.domain import WalletCreatedEvent
from fixtures.executing import run
from fixtures.handlers import RecordingHandler


def count(registry, sql: str) -> int:
    with registry.default.connection(readonly=True) as conn:
        return int(conn.execute(text(sql)).scalar())


def a_wallet_created(executor) -> None:
    run(
        executor,
        CreateWalletAction(executor.clock),
        CreateWalletParams(owner_id=uuid4(), currency="EUR", initial_balance=Decimal("1")),
    )


def test_an_actions_event_row_is_where_the_database_keeps_eventlog(executor, registry):
    a_wallet_created(executor)

    if registry.default.dialect == "postgres":
        assert count(registry, "SELECT count(*) FROM eventlog.events") == 1
        assert (
            count(
                registry,
                "SELECT count(*) FROM information_schema.tables"
                " WHERE table_name = 'events' AND table_schema <> 'eventlog'",
            )
            == 0
        ), "the only events table is eventlog's"
    else:
        assert count(registry, "SELECT count(*) FROM events") == 1
    if registry.default.dialect in ("mysql", "mariadb"):
        assert (
            count(
                registry,
                "SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'eventlog'",
            )
            == 0
        ), "no eventlog database on the server: it would be shared by every database on it"


def test_fanned_out_notifications_are_beside_the_events(executor, pipeline, registry):
    fanout, _ = pipeline(RecordingHandler("eventlog-reader", WalletCreatedEvent))
    a_wallet_created(executor)

    assert fanout.drain_once() == 1
    table = (
        "eventlog.event_notifications"
        if registry.default.dialect == "postgres"
        else "event_notifications"
    )
    assert count(registry, f"SELECT count(*) FROM {table}") == 1


def test_a_registry_on_an_engine_built_by_hand_finds_them_too(backend, registry):
    """An engine `engine_for` did not build carries no mapping; every connection ekbatan opens
    adds it, so a registry over such an engine still writes where the framework reads."""
    if backend.dialect == "postgres":
        pytest.skip("eventlog is a schema of its own on PostgreSQL: there is nothing to map")
    raw = create_engine(backend.url("ekbatan_main"))  # no execution options: built by hand
    try:
        hand = (
            DatabaseRegistry.builder()
            .with_default_database(TransactionManager(DEFAULT_SHARD, raw))
            .build()
        )
        now = datetime(2026, 10, 24, 12, 0)
        with hand.default.transaction():
            SingleTableJsonEventPersister(hand).persist_action_events(
                "test.eventlog", "HandBuilt", now, now, {}, [], DEFAULT_SHARD, uuid.uuid4()
            )
        with raw.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM events")).scalar() == 1
    finally:
        raw.dispose()
