"""`CronSchedule` and `CronStyle`, ported from `CronTest.java` test for test.

The cron style is the whole game here. db-scheduler's default is SPRING53, whose patterns put
**seconds first** - so `0 0 12 * * ?` is noon, not the 12th of the month. Handing that pattern
to croniter unstyled parses it as `minute hour day month weekday second`, which is a schedule,
just not the one anybody asked for.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from db_scheduler.task import ExecutionComplete, TaskInstance
from db_scheduler.task.helper import ScheduleAndData
from db_scheduler.task.schedule import (
    NEVER,
    CronSchedule,
    CronStyle,
    PersistentCronSchedule,
    Schedule,
    Schedules,
    system_default_zone,
)

LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")
SYSTEM = system_default_zone()


def complete(time_done: datetime) -> ExecutionComplete:
    return ExecutionComplete.simulated_success(time_done)


def next_time(schedule: Schedule, time_done: datetime) -> datetime:
    return schedule.next_execution_time(complete(time_done))


class TestValidation:
    def test_it_validates_the_pattern(self) -> None:
        with pytest.raises(ValueError):
            CronSchedule("asdf asdf asdf")

    def test_it_validates_the_zone(self) -> None:
        with pytest.raises(ValueError):
            CronSchedule("0 * * * * ?", zone=None)  # type: ignore[arg-type]


class TestNextExecutionTime:
    """`should_generate_next_date_correctly`, in all three styles."""

    TIME_DONE = datetime(2000, 1, 1, 12, 0, tzinfo=SYSTEM)

    @pytest.mark.parametrize(
        ("pattern", "expected"),
        [
            ("0 * * * * ?", timedelta(minutes=1)),
            ("0 0 * * * ?", timedelta(hours=1)),
            ("0 0 12 * * ?", timedelta(days=1)),
        ],
    )
    def test_the_default_style_is_spring53(self, pattern: str, expected: timedelta) -> None:
        assert next_time(CronSchedule(pattern), self.TIME_DONE) == self.TIME_DONE + expected

    def test_the_default_style_over_a_month_and_a_year(self) -> None:
        assert next_time(CronSchedule("0 0 12 1 * ?"), self.TIME_DONE) == datetime(
            2000, 2, 1, 12, 0, tzinfo=SYSTEM
        )
        assert next_time(CronSchedule("0 0 12 1 1 ?"), self.TIME_DONE) == datetime(
            2001, 1, 1, 12, 0, tzinfo=SYSTEM
        )

    @pytest.mark.parametrize(
        ("pattern", "expected"),
        [
            ("0 * * * * ? *", timedelta(minutes=1)),
            ("0 0 * * * ? *", timedelta(hours=1)),
            ("0 0 12 * * ? *", timedelta(days=1)),
        ],
    )
    def test_quartz_takes_a_seventh_field(self, pattern: str, expected: timedelta) -> None:
        schedule = CronSchedule(pattern, zone=SYSTEM, cron_style=CronStyle.QUARTZ)
        assert next_time(schedule, self.TIME_DONE) == self.TIME_DONE + expected

    def test_quartz_pins_the_year(self) -> None:
        schedule = CronSchedule("0 0 12 1 1 ? 2005", zone=SYSTEM, cron_style=CronStyle.QUARTZ)
        assert next_time(schedule, self.TIME_DONE) == datetime(2005, 1, 1, 12, 0, tzinfo=SYSTEM)

    @pytest.mark.parametrize(
        ("pattern", "expected"),
        [
            ("* * * * *", timedelta(minutes=1)),
            ("0 * * * *", timedelta(hours=1)),
            ("0 12 * * *", timedelta(days=1)),
        ],
    )
    def test_unix_has_five_fields_and_no_seconds(
        self, pattern: str, expected: timedelta
    ) -> None:
        schedule = CronSchedule(pattern, zone=SYSTEM, cron_style=CronStyle.UNIX)
        assert next_time(schedule, self.TIME_DONE) == self.TIME_DONE + expected

    def test_unix_over_a_month_and_a_year(self) -> None:
        month = CronSchedule("0 12 1 * *", zone=SYSTEM, cron_style=CronStyle.UNIX)
        year = CronSchedule("0 12 1 1 *", zone=SYSTEM, cron_style=CronStyle.UNIX)
        assert next_time(month, self.TIME_DONE) == datetime(2000, 2, 1, 12, 0, tzinfo=SYSTEM)
        assert next_time(year, self.TIME_DONE) == datetime(2001, 1, 1, 12, 0, tzinfo=SYSTEM)


class TestTimeZone:
    def test_it_takes_the_zone_into_account(self) -> None:
        """British Summer Time 2019 ends at 2am on Sunday the 27th of October.

        Noon to noon is 25 hours across that boundary, and getting it right is the entire reason
        a schedule stores a zone rather than an offset.
        """
        time_done = datetime(2019, 10, 26, 12, 0, tzinfo=LONDON)

        # Midday on the 27th - which is 25 hours later, not 24. Asserted as a wall-clock time
        # rather than `time_done + timedelta(hours=25)`, because adding a timedelta to an aware
        # datetime in Python moves the wall clock and not the instant, so that sum is 13:00.
        assert next_time(Schedules.cron("0 0 12 * * ?", LONDON), time_done) == datetime(
            2019, 10, 27, 12, 0, tzinfo=LONDON
        )
        assert time_done.astimezone(UTC) + timedelta(hours=25) == datetime(
            2019, 10, 27, 12, 0, tzinfo=LONDON
        )

    def test_the_zone_wins_even_when_the_completion_is_in_another(self) -> None:
        # 11:00 UTC is 12:00 BST.
        time_done = datetime(2019, 10, 26, 11, 0, tzinfo=UTC)

        assert next_time(Schedules.cron("0 0 12 * * ?", LONDON), time_done) == time_done + timedelta(
            hours=25
        ) == datetime(2019, 10, 27, 12, 0, tzinfo=LONDON)

    def test_two_times_a_day_in_new_york(self) -> None:
        # Midday UTC is 07:00 in New York, so the next fire is 13:05 that same day.
        midday_utc = datetime(2000, 1, 1, 12, 0, tzinfo=UTC)

        assert next_time(
            Schedules.cron("0 05 13,20 * * ?", NEW_YORK), midday_utc
        ) == datetime(2000, 1, 1, 13, 5, tzinfo=NEW_YORK)


class TestDisabled:
    def test_a_dash_marks_it_disabled(self) -> None:
        assert Schedules.cron("-").is_disabled
        assert not Schedules.cron("0 * * * * ?").is_disabled

    def test_a_disabled_schedule_refuses_to_be_asked_for_a_time(self) -> None:
        with pytest.raises(NotImplementedError):
            next_time(Schedules.cron("-"), datetime(2000, 1, 1, tzinfo=UTC))


class TestNoFurtherExecutionTime:
    def test_a_pattern_that_never_matches_parks_at_never_rather_than_hanging(self) -> None:
        """The 29th of February that is also the first Monday: no such date, ever.

        db-scheduler logs an error and returns NEVER rather than propagating, because a single
        unsatisfiable pattern must not take the poll loop down with it.
        """
        schedule = CronSchedule("0 0 0 29 2 MON#1", zone=SYSTEM, cron_style=CronStyle.SPRING53)

        assert next_time(schedule, datetime(2000, 1, 1, 12, 0, tzinfo=UTC)) == NEVER


class TestEqualityAndSurface:
    def test_equal_when_pattern_zone_and_style_match(self) -> None:
        assert CronSchedule("* * * * *", zone=SYSTEM, cron_style=CronStyle.UNIX) == CronSchedule(
            "* * * * *", zone=SYSTEM, cron_style=CronStyle.UNIX
        )

    def test_the_pattern_is_part_of_it(self) -> None:
        assert CronSchedule("1 * * * *", zone=SYSTEM, cron_style=CronStyle.UNIX) != CronSchedule(
            "* * * * *", zone=SYSTEM, cron_style=CronStyle.UNIX
        )

    def test_the_zone_is_part_of_it(self) -> None:
        assert CronSchedule("1 * * * *", zone=LONDON, cron_style=CronStyle.UNIX) != CronSchedule(
            "1 * * * *", zone=NEW_YORK, cron_style=CronStyle.UNIX
        )

    def test_the_style_is_part_of_it(self) -> None:
        assert CronSchedule(
            "* * * * * *", zone=SYSTEM, cron_style=CronStyle.SPRING53
        ) != CronSchedule("* * * * * *", zone=SYSTEM, cron_style=CronStyle.SPRING)

    def test_hashing_matches_equality(self) -> None:
        one = CronSchedule("* * * * *", zone=SYSTEM, cron_style=CronStyle.UNIX)
        same = CronSchedule("* * * * *", zone=SYSTEM, cron_style=CronStyle.UNIX)
        other = CronSchedule("* * * * * *", zone=SYSTEM, cron_style=CronStyle.SPRING53)
        assert len({one, same, other}) == 2

    def test_a_cron_schedule_is_deterministic(self) -> None:
        """True, as in Java - the same pattern gives the same times on every instance."""
        assert CronSchedule("0 * * * * ?").is_deterministic

    def test_it_exposes_what_it_was_built_with(self) -> None:
        schedule = CronSchedule("0 * * * * ?", zone=LONDON, cron_style=CronStyle.SPRING53)
        assert schedule.pattern == "0 * * * * ?"
        assert schedule.zone == LONDON
        assert schedule.cron_style is CronStyle.SPRING53

    def test_repr_names_the_three(self) -> None:
        shown = repr(CronSchedule("0 * * * * ?", zone=LONDON))
        assert "0 * * * * ?" in shown
        assert "SPRING53" in shown
        assert "Europe/London" in shown


class TestCronStyle:
    def test_the_five_styles(self) -> None:
        assert [style.name for style in CronStyle] == [
            "CRON4J",
            "QUARTZ",
            "UNIX",
            "SPRING",
            "SPRING53",
        ]

    def test_the_default_is_spring53(self) -> None:
        assert CronSchedule("0 * * * * ?").cron_style is CronStyle.SPRING53

    def test_none_falls_back_to_spring53(self) -> None:
        """Java's constructor takes `cronStyle != null ? cronStyle : SPRING53`."""
        assert CronSchedule("0 * * * * ?", cron_style=None).cron_style is CronStyle.SPRING53


class TestPersistentCronSchedule:
    """A cron pattern that travels in the row, and is rebuilt into a schedule on the way out.

    It stores the zone as its *name* rather than a `tzinfo`, which is what makes it survive a
    round trip through JSON and come back meaning the same hour.
    """

    def test_it_builds_a_cron_schedule_from_what_it_stored(self) -> None:
        stored = PersistentCronSchedule("0 0 12 * * ?", LONDON)

        assert stored.schedule == CronSchedule("0 0 12 * * ?", LONDON)

    def test_it_keeps_the_zone_by_name(self) -> None:
        assert PersistentCronSchedule("0 * * * * ?", LONDON).zone_id == "Europe/London"

    def test_the_zone_defaults_to_the_system_one(self) -> None:
        # Compared as a whole schedule rather than by reading `.zone`, because `schedule` is
        # declared as a `Schedule` here exactly as it is in Java.
        assert PersistentCronSchedule("0 * * * * ?").schedule == CronSchedule(
            "0 * * * * ?", SYSTEM
        )

    def test_it_carries_data_alongside(self) -> None:
        assert PersistentCronSchedule("0 * * * * ?", data={"n": 1}).data == {"n": 1}
        assert PersistentCronSchedule("0 * * * * ?", LONDON, {"n": 1}).data == {"n": 1}
        assert PersistentCronSchedule("0 * * * * ?").data is None

    def test_it_is_a_schedule_and_data(self) -> None:
        """Which is what lets `scheduled_according_to_data()` accept it."""
        assert isinstance(PersistentCronSchedule("0 * * * * ?"), ScheduleAndData)

    def test_scheduling_according_to_it_uses_the_stored_pattern(self) -> None:
        schedulable = (
            TaskInstance.Builder[Any]("t", "i")
            .data(PersistentCronSchedule("0 0 12 * * ?", UTC))
            .scheduled_according_to_data()
        )

        assert schedulable.next_execution_time(datetime(2026, 1, 1, 13, 0, tzinfo=UTC)) == datetime(
            2026, 1, 2, 12, 0, tzinfo=UTC
        )

    def test_repr_names_the_pattern(self) -> None:
        assert repr(PersistentCronSchedule("0 * * * * ?")) == (
            "PersistentCronSchedule pattern=0 * * * * ?"
        )
