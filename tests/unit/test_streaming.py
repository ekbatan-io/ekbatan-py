"""The outbox row as a consumer outside this process reads it.

Java's `ActionEventTest`, which exists because the published type could not be deserialized at
all - no `@JsonCreator`, no no-arg constructor, final fields, and no `-parameters` in the build,
so Jackson refused with "no Creators, like default constructor, exist" while the constructor's own
javadoc claimed Jackson used it. Nothing verified the claim, which is how it survived publication.

Half of those cases have no counterpart here: a frozen dataclass has no construction hazard to
verify. What does carry over is the round trip, the sentinel, and the timestamp precision.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from ekbatan._json import canonical, dumps
from ekbatan.streaming import ActionEvent

PRECISE = datetime(2026, 8, 2, 10, 15, 30, 123456, tzinfo=UTC)


def a_row(**overrides) -> dict:
    row = {
        "id": uuid.uuid4(),
        "namespace": "com.example.finance",
        "action_id": uuid.uuid4(),
        "action_name": "DepositMoney",
        "action_params": {"amount": "50.00"},
        "started_date": PRECISE,
        "completion_date": datetime(2026, 8, 2, 10, 15, 31, tzinfo=UTC),
        "model_id": str(uuid.uuid4()),
        "model_type": "Wallet",
        "event_type": "WalletMoneyDeposited",
        "payload": {"amount": "50.00", "new_balance": "150.00"},
        "event_date": datetime(2026, 8, 2, 10, 15, 31, tzinfo=UTC),
        "delivered": False,
    }
    return row | overrides


def test_a_row_becomes_an_event_with_every_field_in_its_own_place():
    """Java's `an_action_event_round_trips_through_jackson` and
    `every_setter_lands_in_its_own_field`, which are one thing here."""
    row = a_row()

    event = ActionEvent.from_row(row)

    for name, expected in row.items():
        assert getattr(event, name) == expected, name


def test_the_pairs_java_needed_a_builder_for_cannot_be_transposed():
    """Java's `the_transposable_pairs_land_in_their_own_fields`.

    Its thirteen-argument constructor put `modelType` next to `eventType` and `startedDate` next
    to `completionDate` - adjacent same-typed pairs that transpose silently and produce an event
    on the wrong topic. Its builder exists to remove that. Keyword-only arguments remove it by
    construction, so this asserts the shape rather than the guard.
    """
    started = datetime(2020, 1, 1, tzinfo=UTC)
    completed = datetime(2030, 1, 1, tzinfo=UTC)

    event = ActionEvent.from_row(
        a_row(
            model_type="Wallet",
            event_type="WalletMoneyDeposited",
            started_date=started,
            completion_date=completed,
        )
    )

    assert event.model_type == "Wallet"
    assert event.event_type == "WalletMoneyDeposited"
    assert event.started_date == started
    assert event.completion_date == completed

    with pytest.raises(TypeError):
        ActionEvent("id", "namespace")  # type: ignore[call-arg]


def test_a_sentinel_row_becomes_an_event_with_nothing_to_route_on():
    """Java's `a_sentinel_event_round_trips`. Four fields are legitimately absent, so a type
    that refused them would refuse valid traffic."""
    event = ActionEvent.from_row(
        a_row(model_id=None, model_type=None, event_type=None, payload=None)
    )

    assert event.is_sentinel
    assert (event.model_id, event.model_type, event.event_type, event.payload) == (
        None,
        None,
        None,
        None,
    )
    assert event.action_name == "DepositMoney", "the action context is still there"
    assert event.action_params == {"amount": "50.00"}


def test_timestamps_keep_their_microseconds():
    """Java's `timestamps_keep_their_precision`. The columns store microseconds; a consumer that
    loses them cannot order two events written 300 microseconds apart."""
    event = ActionEvent.from_row(a_row())

    assert event.started_date == PRECISE
    assert event.started_date.microsecond == 123456


def test_an_absent_optional_field_is_none_rather_than_an_error():
    """Java's `an_unset_field_stays_null_rather_than_failing`."""
    row = a_row()
    for absent in ("model_id", "model_type", "event_type", "payload", "delivered"):
        del row[absent]

    event = ActionEvent.from_row(row)

    assert event.model_id is None
    assert event.delivered is False, "and the one with a default takes it"


def test_a_mapping_that_is_not_an_outbox_row_says_what_is_missing():
    """Not a Java case. Java's builder silently produces an object with null fields; here the
    row either is one or is named as not being one."""
    with pytest.raises(ValueError, match="not an outbox row: missing"):
        ActionEvent.from_row({"id": uuid.uuid4(), "namespace": "x"})


# -- the shapes a connector puts it in ----------------------------------------------------------


def test_json_columns_arriving_as_strings_can_be_undone():
    """A Debezium topic carries JSON columns as strings under some converters and as nested
    objects under others. Which one you get is a property of the pipeline, so it is asked for
    rather than guessed - Java leaves the whole mapping to the consumer for the same reason."""
    row = a_row()
    row["action_params"] = json.dumps(row["action_params"])
    row["payload"] = json.dumps(row["payload"])

    event = ActionEvent.from_message(row, json_columns_are_strings=True)

    assert event.action_params == {"amount": "50.00"}
    assert event.payload["new_balance"] == "150.00"


def test_epoch_microsecond_timestamps_can_be_undone():
    """Debezium's default for a `timestamp` column is bare epoch microseconds."""
    row = a_row()
    row["started_date"] = int(PRECISE.timestamp() * 1_000_000)

    event = ActionEvent.from_message(row, timestamps_are_epoch_micros=True)

    assert event.started_date == PRECISE


def test_uuids_arriving_as_strings_are_always_undone():
    """Unlike the other two, this one is not ambiguous: the column is a uuid, so a string in it
    is always a rendering of one."""
    row = a_row()
    row["id"] = str(row["id"])
    row["action_id"] = str(row["action_id"])

    event = ActionEvent.from_message(row)

    assert isinstance(event.id, uuid.UUID)
    assert isinstance(event.action_id, uuid.UUID)


def test_neither_conversion_happens_unless_it_is_asked_for():
    """Passing the wrong one is louder than guessing wrongly."""
    row = a_row()
    row["action_params"] = json.dumps(row["action_params"])

    event = ActionEvent.from_message(row)

    assert isinstance(event.action_params, str), "left exactly as the pipeline delivered it"


def test_what_the_framework_writes_is_what_this_reads():
    """The two halves meeting: `canonical` renders the payload the outbox stores, and this reads
    the row back. If they disagreed, the framework's own output would not parse."""
    payload = canonical({"amount": "50.00"})
    event = ActionEvent.from_row(a_row(payload=payload))

    assert event.payload == {"amount": "50.00"}
    assert json.loads(dumps(event.payload)) == {"amount": "50.00"}
