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
from sqlalchemy import MetaData, create_engine, select

from ekbatan.action.outbox import EventEntity, SingleTableJsonEventPersister
from ekbatan.domain import ModelEvent
from ekbatan.errors import ValidationError
from ekbatan.persistence import DatabaseRegistry, TransactionManager
from ekbatan.schema import outbox_table
from ekbatan.sharding import DEFAULT_SHARD


@dataclass(frozen=True, kw_only=True)
class TestEvent(ModelEvent["Model"]):
    __test__ = False  # not a pytest class

    amount: int = 1


def persist(engine, table, *events: ModelEvent) -> None:
    """One unit of work's events, written inside a transaction as the executor writes them."""
    registry = (
        DatabaseRegistry.builder()
        .with_database(TransactionManager(DEFAULT_SHARD, engine, dialect="sqlite"))
        .build()
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    with registry.default.transaction():
        SingleTableJsonEventPersister(registry, table).persist_action_events(
            "test_namespace", "TestAction", now, now, {}, list(events), DEFAULT_SHARD, uuid.uuid4()
        )


@pytest.fixture
def written():
    """Persist a record and hand back the rows it wrote."""
    metadata = MetaData()
    table = outbox_table(metadata)
    engine = create_engine("sqlite://")
    metadata.create_all(engine)

    def write(*events: ModelEvent):
        persist(engine, table, *events)
        with engine.connect() as conn:
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
    engine = create_engine("sqlite://")
    metadata.create_all(engine)

    persist(engine, table, TestEvent(model_id="model-1"))
    with engine.begin() as conn:
        conn.execute(table.update().values(delivered=True))

    with engine.connect() as conn:
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
