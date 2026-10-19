"""The scheduler's clock, serializer and persistence keep a time exactly as given.

The default clock gives the current UTC time with no zone attached. Nothing on the way to or
from the database adds, removes or moves a zone. The guard at the end fails if a conversion
creeps back into the persistence modules. Schedules (`Daily`, cron) are not persistence: to
work out "eight in Rome" they must know which instant a time is, so they read a naive time as
UTC and answer in the same kind - that is checked here too.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta, timezone
from pathlib import Path

from db_scheduler.clock import SettableClock, SystemClock
from db_scheduler.serializer import JsonSerializer
from db_scheduler.task import ExecutionComplete
from db_scheduler.task.schedule import Schedules

NAIVE = datetime(2026, 1, 1, 12, 0, 0, 123456)
TEHRAN = datetime(2026, 1, 1, 15, 30, 0, 654321, tzinfo=timezone(timedelta(hours=3, minutes=30)))
SRC = Path(__file__).resolve().parents[2] / "src" / "db_scheduler"


def test_the_system_clock_is_utc_with_no_zone_attached() -> None:
    reading = SystemClock().now()
    assert reading.tzinfo is None
    assert abs(reading - datetime.now(UTC).replace(tzinfo=None)) < timedelta(seconds=1)


def test_a_settable_clock_keeps_what_it_is_given() -> None:
    assert SettableClock(NAIVE).now() is NAIVE
    assert SettableClock(TEHRAN).now() is TEHRAN
    clock = SettableClock()
    assert clock.now().tzinfo is None, "starts at UTC, no zone attached"
    clock.set(TEHRAN)
    assert clock.now() is TEHRAN


def test_task_data_writes_a_time_as_it_is() -> None:
    serializer = JsonSerializer()
    naive = serializer.serialize({"at": NAIVE})
    aware = serializer.serialize({"at": TEHRAN})
    assert naive is not None and b"2026-01-01T12:00:00.123456" in naive
    assert aware is not None and b"2026-01-01T15:30:00.654321+03:30" in aware


def test_a_daily_schedule_answers_in_the_kind_it_was_asked_in() -> None:
    """Naive in, naive UTC out; aware in, aware out - the same instant either way."""
    daily = Schedules.daily(time(8, 0), zone=UTC)

    def next_after(done: datetime) -> datetime:
        return daily.next_execution_time(ExecutionComplete.simulated_success(done))

    naive = next_after(datetime(2026, 1, 1, 9, 0))
    aware = next_after(datetime(2026, 1, 1, 9, 0, tzinfo=UTC))
    assert naive == datetime(2026, 1, 2, 8, 0) and naive.tzinfo is None
    assert aware == datetime(2026, 1, 2, 8, 0, tzinfo=UTC)


#: What a conversion looks like in source.
CONVERSIONS = re.compile(
    r"astimezone\(|tzinfo\s*=|\btz\s*=|utcfromtimestamp|utcnow|timezone\.utc|datetime\.now\(UTC\)"
)
#: The persistence path and the clock. Schedules do zone arithmetic on purpose (see above).
GUARDED = ("clock.py", "jdbc", "serializer", "scheduled_execution.py", "client.py")
ALLOWED = {
    ("clock.py", "return datetime.now(UTC).replace(tzinfo=None)"),
    ("scheduled_execution.py", "_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)"),
}


def test_no_conversion_in_the_persistence_path() -> None:
    found = []
    for name in GUARDED:
        target = SRC / name
        files = sorted(target.rglob("*.py")) if target.is_dir() else [target]
        for path in files:
            if not path.exists():
                continue
            relative = path.relative_to(SRC).as_posix()
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                code = line.split("#", 1)[0]
                if CONVERSIONS.search(code) and (relative, line.strip()) not in ALLOWED:
                    found.append(f"{relative}:{number}: {line.strip()}")
    assert not found, "the scheduler's persistence must not convert time:\n" + "\n".join(found)


def test_the_allow_list_is_still_true() -> None:
    for relative, line in ALLOWED:
        assert line in (SRC / relative).read_text(), (relative, line)
