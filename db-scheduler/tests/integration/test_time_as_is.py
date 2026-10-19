"""The repository writes and reads a time exactly as it is given. Nothing converts it.

No zone is added, removed or moved between the clock (or the caller) and the driver, or between
the driver and the `Execution` handed back. What the database does with a value is the
database's business; these checks capture what reached SQLAlchemy and the driver, so "as is"
is seen, not inferred from a round trip.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import event, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repository_contract import an_instance, scheduled

#: Naive UTC with microseconds - what `SystemClock().now()` gives.
PRECISE = datetime(2026, 1, 1, 12, 0, 0, 123456)
#: An aware value that is not UTC.
TEHRAN = datetime(2026, 1, 1, 15, 30, 0, 654321, tzinfo=timezone(timedelta(hours=3, minutes=30)))


@contextmanager
def captured(engine: Any) -> Iterator[list[tuple[Any, Any]]]:
    seen: list[tuple[Any, Any]] = []

    def hook(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        seen.append((context.compiled_parameters, parameters))

    event.listen(engine, "before_cursor_execute", hook)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", hook)


def _flat(parameters: Any) -> list[Any]:
    if isinstance(parameters, dict):
        return list(parameters.values())
    if isinstance(parameters, (list, tuple)):
        return [v for p in parameters for v in _flat(p)]
    return [parameters]


def _handed(seen: list[tuple[Any, Any]], value: datetime) -> tuple[bool, bool]:
    to_sqlalchemy = any(v is value for compiled, _ in seen for v in _flat(compiled))
    to_driver = any(v is value for _, sent in seen for v in _flat(sent))
    return to_sqlalchemy, to_driver


def test_a_naive_time_is_written_as_it_is_and_read_back_equal(
    repository: Any, engine: Any, dialect: str, persist_timestamp_in_utc: bool
) -> None:
    if not persist_timestamp_in_utc:
        return  # a naive time belongs in a zone-less column - the application's choice
    with captured(engine) as seen:
        repository.create_if_not_exists(scheduled(an_instance(), PRECISE))
    to_sqlalchemy, to_driver = _handed(seen, PRECISE)
    assert to_sqlalchemy, "handed to SQLAlchemy as it is"
    if dialect != "sqlite":  # SQLAlchemy writes SQLite's DATETIME as text
        assert to_driver, "and on to the driver as it is"

    found = repository.get_execution("a-task", "an-id")
    assert found is not None
    assert found.execution_time == PRECISE, "to the microsecond"
    assert found.execution_time.tzinfo is None, "no zone added on the way back"


def test_an_aware_time_is_handed_to_the_driver_unchanged(
    repository: Any, engine: Any, table: Any, dialect: str
) -> None:
    with captured(engine) as seen:
        try:
            repository.create_if_not_exists(scheduled(an_instance(), TEHRAN))
        except Exception as error:  # a driver may refuse it; that is the driver's call
            assert not type(error).__module__.startswith("db_scheduler"), repr(error)
            return
    to_sqlalchemy, to_driver = _handed(seen, TEHRAN)
    assert to_sqlalchemy, "not moved to UTC or stripped on the way in"
    if dialect != "sqlite":
        assert to_driver

    found = repository.get_execution("a-task", "an-id")
    with engine.connect() as conn:
        raw = conn.execute(text(f"SELECT execution_time FROM {table.name}")).scalar_one()
    if isinstance(raw, str):
        raw = datetime.fromisoformat(raw)
    assert found is not None and found.execution_time == raw, "read back as the driver returns"


def test_the_clock_s_own_value_is_what_a_pick_writes(
    repository: Any, engine: Any, clock: Any, persist_timestamp_in_utc: bool
) -> None:
    """The heartbeat and the pick are the clock's value, as it is."""
    if not persist_timestamp_in_utc:
        return
    clock.set(PRECISE)
    repository.create_if_not_exists(scheduled(an_instance(), PRECISE))
    execution = repository.get_execution("a-task", "an-id")
    assert execution is not None
    with captured(engine) as seen:
        picked = repository.pick(execution, PRECISE)
    assert picked is not None
    assert _handed(seen, PRECISE)[0], "the clock's own value, handed on as it is"
    assert picked.last_heartbeat == PRECISE and picked.last_heartbeat.tzinfo is None
