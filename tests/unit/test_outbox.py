"""A freshly written outbox row is never already delivered.

Java's `EventEntityDeliveredDefaultTest`. It looks like a test of a builder default, and locally
it is. It exists because something outside the module depends on it: the Debezium single-message
transforms publish a row only when `delivered` is false, in the configuration where the connector
has unwrapped Debezium's envelope and there is therefore no `op` field to filter on. There,
`delivered = false` is the only remaining signal that says "this is the insert, not the fanout's
UPDATE".

So if a future change ever makes the persister insert rows already marked delivered - an
optimisation like "no in-process handlers are registered, so mark it up front" is the obvious way
it would happen - those transforms would silently stop publishing anything at all. The failure is
invisible: the connector stays RUNNING and the topic simply stays empty.

**This test is the tripwire. If it fails, do not simply update it** - either keep the invariant,
or change the transforms to stop relying on it and update the unwrapped-records section of the
event-streaming documentation to match.

The transforms are Java and stay Java - they run inside Kafka Connect regardless of which
language wrote the row - which is exactly why the invariant has to hold on this side too.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import MetaData, select

from ekbatan.action.outbox import EventEntity, SingleTableJsonEventPersister
from ekbatan.config import DataSourceConfig
from ekbatan.domain import ModelEvent
from ekbatan.errors import ValidationError
from ekbatan.persistence import DatabaseRegistry, DataSource, TransactionManager
from ekbatan.schema import eventlog_translation, outbox_table
from ekbatan.sharding import DEFAULT_SHARD


@dataclass(frozen=True, kw_only=True)
class TestEvent(ModelEvent["Model"]):
    __test__ = False  # not a pytest class

    amount: int = 1


def in_memory() -> DataSource:
    """An in-memory database. Its pool maps `eventlog` onto it, as every data source does on
    SQLite: the event tables live in the database itself (ekbatan.schema.eventlog_translation)."""
    return DataSource.from_config(DataSourceConfig(url="sqlite://"))


def persist(source: DataSource, *events: ModelEvent) -> None:
    """One unit of work's events, written inside a transaction as the executor writes them."""
    registry = (
        DatabaseRegistry.builder().with_database(TransactionManager(DEFAULT_SHARD, source)).build()
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    with registry.default.transaction():
        SingleTableJsonEventPersister(registry).persist_action_events(
            "test_namespace", "TestAction", now, now, {}, list(events), DEFAULT_SHARD, uuid.uuid4()
        )


@pytest.fixture
def written():
    """Persist a record and hand back the rows it wrote."""
    metadata = MetaData()
    table = outbox_table(metadata)
    source = in_memory()
    metadata.create_all(source.sqlalchemy_engine)

    def write(*events: ModelEvent):
        persist(source, *events)
        with source.sqlalchemy_engine.connect() as conn:
            return [row._mapping for row in conn.execute(select(table))]

    return write


def test_a_newly_persisted_event_row_is_not_marked_delivered(written) -> None:
    """The Debezium transforms treat `delivered = false` as the signal that a row is a new
    event; read this module's docstring before changing it."""
    rows = written(TestEvent(model_id="model-1"))

    assert len(rows) == 1
    assert rows[0]["delivered"] is False


def test_the_sentinel_row_for_an_action_that_emitted_nothing_is_not_delivered_either(
    written,
) -> None:
    """A unit of work that emitted nothing is still recorded, as one row with every event column
    null. It travels the same path as any other row, so it needs the same flag - and it is the
    row most likely to be special-cased by exactly the optimisation this file guards against."""
    rows = written()

    assert len(rows) == 1
    assert rows[0]["event_type"] is None, "the sentinel"
    assert rows[0]["delivered"] is False


def test_every_row_of_a_multi_event_action_is_undelivered(written) -> None:
    """One row per event, and the invariant is per row rather than per action."""
    rows = written(
        TestEvent(model_id="model-1", amount=1),
        TestEvent(model_id="model-2", amount=2),
    )

    assert len(rows) == 2
    assert [row["delivered"] for row in rows] == [False, False]


def test_the_flag_is_still_settable_for_the_fanout_that_owns_it(written) -> None:
    """Not testing the default here - just that the one legitimate writer can still set it, so
    the invariant above is about *who* writes true, not about the flag being immutable. The
    fan-out doing so for real is `tests/integration/test_events.py`."""
    metadata = MetaData()
    table = outbox_table(metadata)
    source = in_memory()
    metadata.create_all(source.sqlalchemy_engine)

    persist(source, TestEvent(model_id="model-1"))
    with source.sqlalchemy_engine.begin() as conn:
        conn.execute(table.update().values(delivered=True))

    with source.sqlalchemy_engine.connect() as conn:
        assert conn.execute(select(table.c.delivered)).scalar() is True


# -- Java's EventEntityDeliveredDefaultTest, on the entity itself --------------------------------


def a_new_entity():
    now = datetime.now(UTC).replace(tzinfo=None)
    return EventEntity.create_event_entity(
        id=uuid.uuid4(),
        namespace="test-namespace",
        action_id=uuid.uuid4(),
        action_name="TestAction",
        action_params={},
        started_date=now,
        completion_date=now,
        model_id="model-1",
        model_type="TestModel",
        event_type="TestEvent",
        payload={},
        event_date=now,
    )


def test_a_new_event_entity_is_not_delivered() -> None:
    assert a_new_entity().build().delivered is False


def test_the_entity_builder_can_still_set_delivered() -> None:
    assert a_new_entity().delivered(True).build().delivered is True


def test_two_event_entities_are_equal_when_their_ids_are() -> None:
    """By id alone, as Java's `equals` and `hashCode` are."""
    entity = a_new_entity().build()
    same_row = a_new_entity().id(entity.id).action_name("SomethingElse").build()

    assert entity == same_row
    assert hash(entity) == hash(same_row)
    assert entity != a_new_entity().build()


@pytest.mark.parametrize("blank", ["", "  "])
def test_an_event_entity_with_a_blank_namespace_is_refused(blank) -> None:
    with pytest.raises(ValidationError, match="namespace cannot be blank"):
        a_new_entity().namespace(blank).build()


def test_an_event_entity_names_what_it_is_missing() -> None:
    with pytest.raises(ValidationError, match="id, namespace, action_id"):
        EventEntity.builder().build()


def test_an_event_entity_round_trips_through_its_row() -> None:
    entity = a_new_entity().build()
    assert EventEntity.from_row(SimpleNamespace(**entity.to_row())).to_row() == entity.to_row()


# -- where the two tables are: fixed, as Java's (Dracula 14) ------------------------------------


def test_the_event_tables_are_named_as_java_names_them() -> None:
    from ekbatan.schema import notifications_table

    metadata = MetaData()
    assert outbox_table(metadata).fullname == "eventlog.events"
    assert notifications_table(metadata).fullname == "eventlog.event_notifications"


def test_eventlog_is_mapped_onto_the_database_only_on_sqlite() -> None:
    """A schema in each database on PostgreSQL and a database of its own on MySQL and MariaDB -
    real on both, so nothing is mapped. SQLite has neither: its connections map it onto the
    file."""
    for dialect in ("postgres", "postgresql", "mysql", "mariadb"):
        assert eventlog_translation(dialect) is None, dialect
    assert eventlog_translation("sqlite") == {"eventlog": None}


def test_emit_ddl_creates_eventlog_first_where_it_is_a_schema_or_a_database() -> None:
    from ekbatan.migrations import emit_ddl
    from ekbatan.schema import notifications_table

    metadata = MetaData()
    outbox_table(metadata)
    notifications_table(metadata)

    postgres = emit_ddl(metadata, "postgres")
    assert postgres.startswith("CREATE SCHEMA IF NOT EXISTS eventlog;\n")
    assert "CREATE TABLE eventlog.events (" in postgres
    assert "CREATE TABLE eventlog.event_notifications (" in postgres
    for dialect in ("mysql", "mariadb"):
        mysql = emit_ddl(metadata, dialect)
        assert mysql.startswith("CREATE DATABASE IF NOT EXISTS eventlog;\n"), dialect
        assert mysql.count("CREATE DATABASE") == 1, dialect
        assert "CREATE TABLE eventlog.events (" in mysql, dialect
        assert "CREATE TABLE eventlog.event_notifications (" in mysql, dialect
    sqlite = emit_ddl(metadata, "sqlite")
    assert "eventlog" not in sqlite
    assert "CREATE TABLE events (" in sqlite
    assert metadata.tables["eventlog.events"].schema == "eventlog", "the caller's metadata is kept"
