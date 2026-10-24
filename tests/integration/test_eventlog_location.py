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

from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

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
