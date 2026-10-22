"""An application's own `json_column()` keeps the `datetime` it is given.

Persistence writes what it is given. A `dict` holding a `datetime`, written to a JSON column of
the application's own, goes in as it is - no zone for a naive one, its offset (`Z` at zero) for
an aware one - and nothing refuses it. The refusal of a `datetime` with no declared kind belongs
to event payloads and action params alone (`test_time_kinds_events`), whose JSON a Java reader
takes by the declared kind. Both modes, every database.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, select

import ekbatan.persistence as sync_persistence
from ekbatan.schema import json_column
from fixtures.logins import data_source

MODES = ("sync", "async")

TEHRAN = timezone(timedelta(hours=3, minutes=30))

NOTES = Table(
    "json_column_notes",
    MetaData(),
    Column("id", Integer, primary_key=True, autoincrement=False),
    Column("body", json_column()),
)


@pytest.fixture
def notes(registry: Any, backend: Any) -> Iterator[Table]:
    # Through the module: an async run rebinds this module's own names to their twins.
    engine = sync_persistence.engine_for(data_source(backend.url("ekbatan_main")))
    try:
        NOTES.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(NOTES.delete())
        yield NOTES
    finally:
        engine.dispose()


def test_a_datetime_in_a_dict_is_written_as_it_is_given(registry, notes):
    body = {
        "naive": datetime(2026, 3, 1, 9, 30, 0, 120000),
        "utc": datetime(2026, 3, 1, 9, 30, tzinfo=UTC),
        "tehran": datetime(2026, 3, 1, 13, 0, 0, 120000, TEHRAN),
        "list": [datetime(2026, 3, 1, 9, 30)],
    }
    with registry.default.connection() as conn:
        conn.execute(notes.insert().values(id=1, body=body))
    with registry.default.connection(readonly=True) as conn:
        (read,) = conn.execute(select(notes.c.body)).scalars().all()

    assert read == {
        "naive": "2026-03-01T09:30:00.12",
        "utc": "2026-03-01T09:30:00Z",
        "tehran": "2026-03-01T13:00:00.12+03:30",
        "list": ["2026-03-01T09:30:00"],
    }
