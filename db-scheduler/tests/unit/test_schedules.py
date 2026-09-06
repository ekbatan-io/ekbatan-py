"""Schedules: when a task runs next.

Ported scenario for scenario from the Java suite - `DailyTest`, `FixedDelayTest`, `CronTest`,
`SchedulesTest`, `DailyParserTest`, `FixedDelayParserTest`, `CompositeParserTest`,
`UnrecognizableScheduleTest` - because the whole point of a schedule is that it agrees with the
other implementation about what "next" means.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from datetime import tzinfo as datetime_tzinfo
from zoneinfo import ZoneInfo

import pytest

from db_scheduler.task import ExecutionComplete
from db_scheduler.task.schedule import (
    NEVER,
    Daily,
    DisabledSchedule,
    FixedDelay,
    Schedules,
    UnrecognizableSchedule,
)

ROME = ZoneInfo("Europe/Rome")

HOUR_0, HOUR_1, HOUR_2, HOUR_3 = time(0, 0), time(1, 0), time(2, 0), time(3, 0)
HOUR_6, HOUR_7, HOUR_8 = time(6, 0), time(7, 0), time(8, 0)
HOUR_12, HOUR_23 = time(12, 0), time(23, 0)


def at(day: date, clock_time: time, zone: datetime_tzinfo = UTC) -> datetime:
    return datetime.combine(day, clock_time, tzinfo=zone)


def complete(done: datetime) -> ExecutionComplete:
    """The simulated completion a schedule is asked about."""
    return ExecutionComplete.simulated_success(done)


# ------------------------------------------------------------------ FixedDelay


class TestFixedDelay:
    def test_next_is_the_delay_after_completion(self) -> None:
        done = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        assert FixedDelay.of(timedelta(seconds=10)).next_execution_time(complete(done)) == done + timedelta(seconds=10)

    def test_the_first_run_is_now_not_now_plus_the_delay(self) -> None:
        """A fixed-delay task starts immediately; the delay is between runs, not before the
        first one."""
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        assert FixedDelay.of(timedelta(hours=1)).initial_execution_time(now) == now

    def test_the_convenience_constructors_agree(self) -> None:
        assert FixedDelay.of_millis(1500).duration == timedelta(milliseconds=1500)
        assert FixedDelay.of_seconds(10).duration == timedelta(seconds=10)
        assert FixedDelay.of_minutes(2).duration == timedelta(minutes=2)
        assert FixedDelay.of_hours(3).duration == timedelta(hours=3)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_non_positive_delay_is_refused(self, bad: int) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            FixedDelay.of_seconds(bad)

    def test_it_is_not_deterministic(self) -> None:
        """The next time is relative to when the last run finished, not to the clock - so two
        instances asking at the same moment can get different answers."""
        assert FixedDelay.of_seconds(1).is_deterministic is False

    def test_equality_and_hashing(self) -> None:
        assert FixedDelay.of_seconds(10) == FixedDelay.of_seconds(10)
        assert FixedDelay.of_seconds(10) != FixedDelay.of_seconds(11)
        assert len({FixedDelay.of_seconds(10), FixedDelay.of_seconds(10)}) == 1


# ------------------------------------------------------------------ Daily


class TestDaily:
    def test_empty_times_are_refused(self) -> None:
        with pytest.raises(ValueError, match="times cannot be empty"):
            Daily()

    def test_a_null_zone_is_refused(self) -> None:
        with pytest.raises(ValueError, match="zone cannot be None"):
            Daily(HOUR_8, zone=None)  # type: ignore[arg-type]

    def test_next_date(self) -> None:
        today = datetime.now(UTC).date()
        tomorrow = today + timedelta(days=1)

        assert Daily(HOUR_8, zone=UTC).next_execution_time(complete(at(today, HOUR_0))) == at(today, HOUR_8)
        assert Daily(HOUR_8, zone=UTC).next_execution_time(complete(at(today, HOUR_8))) == at(tomorrow, HOUR_8)
        assert Daily(HOUR_8, HOUR_12, zone=UTC).next_execution_time(complete(at(today, HOUR_0))) == at(today, HOUR_8)
        assert Daily(HOUR_8, HOUR_12, zone=UTC).next_execution_time(complete(at(today, HOUR_8))) == at(today, HOUR_12)
        assert Daily(HOUR_8, HOUR_12, zone=UTC).next_execution_time(complete(at(today, HOUR_12))) == at(tomorrow, HOUR_8)

    def test_times_are_sorted_however_they_were_given(self) -> None:
        today = datetime.now(UTC).date()
        assert Daily(HOUR_12, HOUR_8, zone=UTC).next_execution_time(complete(at(today, HOUR_0))) == at(today, HOUR_8)

    def test_during_standard_time(self) -> None:
        """Rome is UTC+1 in December, so 08:00 local is 07:00 UTC."""
        day = date(2020, 12, 12)
        assert Daily(HOUR_8, zone=ROME).next_execution_time(complete(at(day, HOUR_0))) == at(day, HOUR_7)

    def test_during_daylight_saving_time(self) -> None:
        """Rome is UTC+2 in June, so 08:00 local is 06:00 UTC."""
        day = date(2020, 6, 12)
        assert Daily(HOUR_8, zone=ROME).next_execution_time(complete(at(day, HOUR_0))) == at(day, HOUR_6)

    def test_it_is_deterministic(self) -> None:
        assert Daily(HOUR_8).is_deterministic is True

    def test_equality_ignores_the_order_times_were_given_in(self) -> None:
        assert Daily(HOUR_8, HOUR_12, zone=UTC) == Daily(HOUR_12, HOUR_8, zone=UTC)
        assert Daily(HOUR_8, zone=UTC) != Daily(HOUR_8, zone=ROME)
        assert len({Daily(HOUR_8, zone=UTC), Daily(HOUR_8, zone=UTC)}) == 1


# ------------------------------------------------------------------ Cron


# `CronSchedule` has a Java test file of its own - `CronTest.java` - and so has a Python one:
# `test_cron.py`. Only its place in the parsing and schedule contract is checked from here.


# ------------------------------------------------------------------ Disabled


class TestDisabledSchedule:
    def test_it_reports_disabled_and_refuses_everything_else(self) -> None:
        schedule = DisabledSchedule()
        assert schedule.is_disabled is True
        for call in (
            lambda: schedule.next_execution_time(complete(datetime.now(UTC))),
            lambda: schedule.is_deterministic,
            lambda: schedule.initial_execution_time(datetime.now(UTC)),
        ):
            with pytest.raises(NotImplementedError, match="does not support"):
                call()


# ------------------------------------------------------------------ parsing


class TestScheduleParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("FIXED_DELAY|10s", FixedDelay.of_seconds(10)),
            ("FIXED_DELAY|1s", FixedDelay.of_seconds(1)),
            ("DAILY|12:00", Daily(HOUR_12)),
            ("DAILY|12:00,13:00", Daily(time(12, 0), time(13, 0))),
            ("DAILY|12:00|Europe/Rome", Daily(HOUR_12, zone=ROME)),
            ("DAILY|10:00,14:00|Europe/Rome", Daily(time(10, 0), time(14, 0), zone=ROME)),
        ],
    )
    def test_recognised(self, text: str, expected: object) -> None:
        assert Schedules.parse_schedule(text) == expected

    def test_the_dash_parses_to_a_disabled_schedule(self) -> None:
        assert Schedules.parse_schedule("-").is_disabled is True

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "DAILY|",
            "DAILY|1200",
            "DAILY|12:00;13:00",
            "DAILY|12:00,13:00,",
            "DAILY|UTC",
            "DAILY|UTC1200",
            "DAILY|UTC1200|",
            "DAILY|UTC|UTC12:00,13:00",
            "DAILY|UTC|UTC|12:00,13:00",
            "DAILY|WRONG|12:00,13:00",
            "DAILY||12:00,13:00",
            "FIXED_DELAY|10",
            "FIXED_DELAY|s",
            "FIXED_DELAY|",
            "NONSENSE",
        ],
    )
    def test_unrecognised(self, text: str) -> None:
        with pytest.raises(UnrecognizableSchedule) as raised:
            Schedules.parse_schedule(text)
        assert "Parsable examples" in str(raised.value), "the error shows what would have worked"

    def test_an_unknown_zone_is_refused(self) -> None:
        with pytest.raises(UnrecognizableSchedule):
            Schedules.parse_schedule("DAILY|12:00|Nowhere/Nothing")


# ------------------------------------------------------------------ the contract


class TestScheduleContract:
    def test_never_is_the_shared_far_future_sentinel(self) -> None:
        """A cron whose pattern yields nothing further reschedules to NEVER rather than
        failing. The value is part of the cross-language contract - a row written by one port
        is read by the other."""
        assert datetime(2500, 1, 1, 12, 0, tzinfo=UTC) == NEVER

    def test_the_initial_time_of_a_non_fixed_delay_schedule_comes_from_a_simulated_success(
        self,
    ) -> None:
        now = datetime(2026, 1, 1, 6, 0, tzinfo=UTC)
        assert Daily(HOUR_8, zone=UTC).initial_execution_time(now) == at(now.date(), HOUR_8)
