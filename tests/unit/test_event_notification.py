"""The notification row's own refusals, and its round trip through a table row.

Java's `EventNotification` constructor checks, one for one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from ekbatan.errors import ValidationError
from ekbatan.events.notification import EventNotification, EventNotificationState

NOW = datetime(2026, 3, 1, 12, 0)


def a_notification():
    return (
        EventNotification.builder()
        .id(uuid.uuid4())
        .event_id(uuid.uuid4())
        .handler_name("audit")
        .state(EventNotificationState.PENDING)
        .attempts(0)
        .next_retry_at(NOW)
        .created_date(NOW)
        .updated_date(NOW)
        .namespace("example.wallet")
        .action_id(uuid.uuid4())
        .action_name("CreateWalletAction")
        .action_params({})
        .started_date(NOW)
        .completion_date(NOW)
        .event_type("WalletCreatedEvent")
        .event_date(NOW)
    )


def test_model_id_type_and_payload_may_be_left_unset():
    built = a_notification().build()
    assert (built.model_id, built.model_type, built.payload) == (None, None, None)


@pytest.mark.parametrize("field", ["handler_name", "event_type"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_handler_name_or_event_type_is_refused(field, blank):
    with pytest.raises(ValidationError, match=f"{field} cannot be blank"):
        getattr(a_notification(), field)(blank).build()


def test_a_negative_attempt_count_is_refused():
    with pytest.raises(ValidationError, match="attempts cannot be negative"):
        a_notification().attempts(-1).build()


def test_a_notification_names_what_it_is_missing():
    with pytest.raises(ValidationError, match="id, event_id, handler_name, state"):
        EventNotification.builder().build()


def test_the_state_is_stored_by_name_and_read_back_as_the_enum():
    built = a_notification().state(EventNotificationState.FAILED).build()

    row = built.to_row()
    assert row["state"] == "FAILED"
    assert EventNotification.from_row(SimpleNamespace(**row)).state is EventNotificationState.FAILED
