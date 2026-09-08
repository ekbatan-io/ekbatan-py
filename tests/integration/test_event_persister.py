"""The outbox row, written directly by the persister rather than through an action.

Java's `BaseSingleTableJsonEventPersisterTest`, which runs three times - Pg, MySQL, MariaDB - and
here runs once over `--dialects`. Going through the persister rather than the executor is the
point: it is the seam an application replaces, so what it writes and what it refuses is the part
that has to hold when somebody supplies their own.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from ekbatan.action.outbox import OutboxRecord, SingleTableJsonEventPersister
from ekbatan.domain import ModelEvent
from ekbatan.errors import ValidationError
from ekbatan.sharding import DEFAULT_SHARD
from fixtures.schema import EVENTS

STARTED = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
COMPLETED = datetime(2026, 1, 15, 10, 0, 1, tzinfo=UTC)


@dataclass(frozen=True, kw_only=True)
class Params:
    action: str
    priority: int


@dataclass(frozen=True, kw_only=True)
class ItemCreated(ModelEvent):
    description: str
    amount: Decimal = Decimal("0")


@dataclass(frozen=True, kw_only=True)
class ItemRenamed(ModelEvent):
    to: str


@dataclass(frozen=True, kw_only=True)
class ScalarPayload(ModelEvent):
    """A handler's own `payload()` override gone wrong - Java's `ScalarPayloadEvent`."""

    def payload(self):  # type: ignore[override]
        return "not an object"


@pytest.fixture
def persister():
    return SingleTableJsonEventPersister(EVENTS)


#: `None` is a value under test here, so it cannot double as "use the default".
_DEFAULT = object()


@pytest.fixture
def write(persister, registry):
    """Persist one record on the default shard, in a transaction, as the executor would."""

    def go(
        *events,
        action="CreateItemAction",
        params=_DEFAULT,
        action_id=None,
        namespace="com.example.finance",
    ):
        record = OutboxRecord(
            namespace=namespace,
            action_id=action_id or uuid.uuid4(),
            action_name=action,
            action_params=Params(action="create", priority=5) if params is _DEFAULT else params,
            started_date=STARTED,
            completion_date=COMPLETED,
            events=list(events),
            shard=DEFAULT_SHARD,
        )
        with registry.default.transaction() as tx:
            persister.persist(tx.connection, record)
        return record

    return go


def rows(registry):
    with registry.default.connection(readonly=True) as conn:
        return [
            dict(r._mapping) for r in conn.execute(select(EVENTS).order_by(EVENTS.c.event_date))
        ]


def about(event: ModelEvent, model_id: str = "", model_type: str = "Item") -> ModelEvent:
    """What `ChangePersister` does before handing an event over."""
    from dataclasses import replace

    return replace(event, model_id=model_id or str(uuid.uuid4()), model_name=model_type)


# -- what it writes -----------------------------------------------------------------------------


def test_one_event_carries_the_whole_action_context(write, registry):
    """Java's `should_persist_single_event_with_denormalized_action_context`. One row is a
    complete audit record without a join - that is what the denormalisation buys."""
    model_id = str(uuid.uuid4())
    record = write(about(ItemCreated(description="wallet created"), model_id))

    (row,) = rows(registry)
    assert row["namespace"] == "com.example.finance"
    assert row["action_name"] == "CreateItemAction"
    assert row["action_id"] == record.action_id
    assert row["action_params"] == {"action": "create", "priority": 5}
    assert row["model_id"] == model_id
    assert row["model_type"] == "Item"
    assert row["event_type"] == "ItemCreated"
    assert row["payload"]["description"] == "wallet created"
    assert row["delivered"] is False


def test_several_events_share_one_action_context_and_get_their_own_rows(write, registry):
    """Java's `should_persist_multiple_events_with_shared_action_context`, asserted set-wise as
    Java asserts it - the rows of one invocation have no promised order."""
    record = write(
        about(ItemCreated(description="event one")),
        about(ItemCreated(description="event two")),
        action="BatchAction",
    )

    written = rows(registry)
    assert len(written) == 2
    assert {r["action_id"] for r in written} == {record.action_id}
    assert {r["action_name"] for r in written} == {"BatchAction"}
    assert {r["event_type"] for r in written} == {"ItemCreated"}
    assert len({r["id"] for r in written}) == 2, "each event is its own row"
    assert {r["payload"]["description"] for r in written} == {"event one", "event two"}


def test_different_event_types_in_one_invocation(write, registry):
    """Java's `should_persist_mixed_event_types`. Each row routes on its own `event_type`."""
    write(
        about(ItemCreated(description="made")),
        about(ItemRenamed(to="renamed")),
        action="MixedAction",
    )

    written = rows(registry)
    assert {r["event_type"] for r in written} == {"ItemCreated", "ItemRenamed"}
    by_type = {r["event_type"]: r for r in written}
    assert by_type["ItemCreated"]["payload"]["description"] == "made"
    assert by_type["ItemRenamed"]["payload"]["to"] == "renamed"


def test_an_invocation_with_no_events_still_writes_one_row(write, registry):
    """Java's `should_persist_sentinel_row_for_zero_events`. Without it, "nothing happened" and
    "nothing was recorded" are indistinguishable afterwards."""
    write(action="NoOpAction")

    (row,) = rows(registry)
    assert (row["model_id"], row["model_type"], row["event_type"], row["payload"]) == (
        None,
        None,
        None,
        None,
    )
    assert row["action_name"] == "NoOpAction"
    assert row["action_params"] == {"action": "create", "priority": 5}
    assert row["started_date"] is not None and row["completion_date"] is not None


def test_the_namespace_is_on_every_row(write, registry):
    """Java's `should_persist_namespace_on_every_row`. It is what lets two deployments share an
    eventlog table without either reading the other's traffic."""
    write(about(ItemCreated(description="a")), about(ItemCreated(description="b")))
    write(namespace="com.example.other")

    assert {r["namespace"] for r in rows(registry)} == {
        "com.example.finance",
        "com.example.other",
    }


def test_the_rows_of_one_invocation_can_be_found_by_its_action_id(write, registry):
    """Java's `should_find_events_by_action_id`. The correlation a consumer groups on."""
    first = write(about(ItemCreated(description="a")), about(ItemCreated(description="b")))
    write(about(ItemCreated(description="unrelated")))

    theirs = [r for r in rows(registry) if r["action_id"] == first.action_id]

    assert len(theirs) == 2
    assert {r["payload"]["description"] for r in theirs} == {"a", "b"}


def test_the_timestamps_are_the_ones_the_invocation_was_given(write, registry):
    write(about(ItemCreated(description="a")))

    (row,) = rows(registry)
    assert row["started_date"] == STARTED.replace(tzinfo=None)
    assert row["completion_date"] == COMPLETED.replace(tzinfo=None)
    assert row["event_date"] == COMPLETED.replace(tzinfo=None), "the event's own time"


# -- what it refuses ----------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["a string", [1, 2, 3], 42])
def test_action_params_that_are_not_an_object_are_refused(write, bad):
    """Java's `should_reject_scalar_action_params` and `should_reject_list_action_params`.

    A JSON column takes a bare scalar quite happily, so nothing fails at write time - it fails at
    the consumer, days later, doing `params["amount"]` on a string.
    """
    with pytest.raises(ValidationError, match="action_params must be a JSON object"):
        write(about(ItemCreated(description="a")), params=bad)


def test_a_parameterless_action_writes_an_empty_object_not_a_null(write, registry):
    """Where this parts company with Java's `should_reject_null_action_params`.

    There an action always takes a params object, so a null reaching the check is a mistake.
    Here `execute(SomeAction(), None)` is how a parameterless action is invoked and is common,
    so the column gets `{}` - which a reader typed against `Mapping` can still use. Written as
    JSON `null`, as it was, every parameterless action left a row a typed reader cannot read.
    """
    write(about(ItemCreated(description="a")), params=None)

    (row,) = rows(registry)
    assert row["action_params"] == {}


def test_an_event_whose_payload_is_not_an_object_is_refused(write):
    """Java's `should_reject_an_event_that_does_not_serialize_to_a_json_object`, and its comment
    says why it is a named error: it used to surface as a bare ClassCastException naming Jackson
    node classes, from inside the persister, with nothing pointing at the event."""
    with pytest.raises(ValidationError, match="payload\\(\\) must give a JSON object"):
        write(about(ScalarPayload()))


def test_two_event_classes_with_one_name_are_refused(write):
    """Java's `should_reject_different_event_classes_with_same_simple_name`.

    A consumer routes and deserialises on the bare name. Stored under one name they are
    indistinguishable downstream, and the failure lands weeks later as a payload that will not
    parse.
    """

    @dataclass(frozen=True, kw_only=True)
    class Colliding(ModelEvent):
        first: str = "a"

    other = Colliding
    del Colliding

    @dataclass(frozen=True, kw_only=True)
    class Colliding(ModelEvent):
        second: str = "b"

    with pytest.raises(ValidationError, match="event type name collision"):
        write(about(other()), about(Colliding()))


def test_a_table_missing_an_outbox_column_is_refused_at_construction():
    """Not a Java case - its persister takes a jOOQ table it declares itself. Here the table is
    supplied, so it is checked once at construction rather than at the first write."""
    from sqlalchemy import Column, Integer, MetaData, Table

    incomplete = Table("nearly", MetaData(), Column("id", Integer, primary_key=True))

    with pytest.raises(ValidationError, match="missing outbox column"):
        SingleTableJsonEventPersister(incomplete)
