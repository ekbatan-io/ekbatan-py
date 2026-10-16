"""`CronStyle.QUARTZ`, rule by rule, each with the answer Java db-scheduler gives.

Every expected time below was produced by cron-utils 9.2.1 - Java db-scheduler's cron engine -
through `scripts/quartz-oracle/`. `test_quartz_oracle.py` holds the same comparison for
56,477 more cases; this file is the readable part: one rule per test, the surprising
readings included, because those are the ones a Java service sharing the table also has.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from db_scheduler.task import ExecutionComplete
from db_scheduler.task.schedule import NEVER, CronSchedule, CronStyle
from db_scheduler.task.schedule._quartz import _YEAR, _generator, _parse_expression, compile_quartz

MONDAY = datetime(2026, 10, 12, 13, 0, tzinfo=UTC)  # a Monday afternoon


def at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def runs(pattern: str, start: datetime, count: int = 1) -> list[datetime]:
    """The next `count` times, chained as the scheduler chains them: each from the one before."""
    schedule = CronSchedule(pattern, UTC, CronStyle.QUARTZ)
    found = []
    moment = start
    for _ in range(count):
        moment = schedule.next_execution_time(ExecutionComplete.simulated_success(moment))
        if moment == NEVER:
            break
        found.append(moment)
    return found


def refused(pattern: str) -> str:
    with pytest.raises(ValueError, match="invalid cron pattern") as raised:
        CronSchedule(pattern, UTC, CronStyle.QUARTZ)
    return str(raised.value)


class TestDayOfWeekNumbers:
    """Quartz counts the week from Sunday = 1. croniter counts from Sunday = 0, which is why a
    QUARTZ pattern is never handed to croniter as it is."""

    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            ("1", "2026-10-18"),  # Sunday
            ("2", "2026-10-19"),  # Monday - the bug this module was written for: not Tuesday
            ("3", "2026-10-13"),
            ("4", "2026-10-14"),
            ("5", "2026-10-15"),
            ("6", "2026-10-16"),
            ("7", "2026-10-17"),  # Saturday
        ],
    )
    def test_one_is_sunday(self, day: str, expected: str) -> None:
        assert runs(f"0 0 12 ? * {day}", MONDAY) == [at(f"{expected}T12:00:00Z")]

    @pytest.mark.parametrize(
        ("name", "number"),
        [("SUN", 1), ("MON", 2), ("TUE", 3), ("WED", 4), ("THU", 5), ("FRI", 6), ("SAT", 7)],
    )
    def test_names_mean_what_the_numbers_mean(self, name: str, number: int) -> None:
        assert runs(f"0 0 12 ? * {name}", MONDAY, 3) == runs(f"0 0 12 ? * {number}", MONDAY, 3)

    def test_names_are_read_in_any_case(self) -> None:
        assert runs("0 0 12 ? * mon", MONDAY, 3) == runs("0 0 12 ? * MON", MONDAY, 3)

    @pytest.mark.parametrize("day", ["0", "8"])
    def test_zero_and_eight_are_refused(self, day: str) -> None:
        assert "not in range [1, 7]" in refused(f"0 0 12 ? * {day}")

    def test_a_range(self) -> None:
        friday = MONDAY + timedelta(days=4)
        assert runs("0 0 12 ? * MON-FRI", friday, 2) == [
            at("2026-10-19T12:00:00Z"),
            at("2026-10-20T12:00:00Z"),
        ]

    def test_a_range_that_wraps_over_the_weekend(self) -> None:
        assert runs("0 0 12 ? * FRI-MON", MONDAY, 5) == [
            at("2026-10-16T12:00:00Z"),
            at("2026-10-17T12:00:00Z"),
            at("2026-10-18T12:00:00Z"),
            at("2026-10-19T12:00:00Z"),
            at("2026-10-23T12:00:00Z"),
        ]

    def test_a_step_counts_from_its_start(self) -> None:
        """`2/3`: Monday, then every third day of the Quartz week - 2, 5: Monday, Thursday."""
        assert runs("0 0 12 ? * 2/3", MONDAY, 3) == [
            at("2026-10-15T12:00:00Z"),
            at("2026-10-19T12:00:00Z"),
            at("2026-10-22T12:00:00Z"),
        ]

    def test_a_question_mark_in_a_list_adds_nothing(self) -> None:
        assert runs("0 0 12 ? * 2,?", MONDAY, 2) == runs("0 0 12 ? * 2", MONDAY, 2)


class TestDayOfWeekSpecials:
    def test_hash_is_the_kth_such_day_of_the_month(self) -> None:
        assert runs("0 0 12 ? * 6#3", MONDAY, 2) == [
            at("2026-10-16T12:00:00Z"),
            at("2026-11-20T12:00:00Z"),
        ]

    def test_a_fifth_one_only_in_months_that_have_one(self) -> None:
        assert runs("0 0 12 ? * 2#5", MONDAY, 2) == [
            at("2026-11-30T12:00:00Z"),
            at("2027-03-29T12:00:00Z"),
        ]

    def test_a_sixth_one_never_comes(self) -> None:
        assert runs("0 0 12 ? * 6#6", MONDAY) == []

    def test_java_reads_only_the_first_hash(self) -> None:
        assert runs("0 0 12 ? * 6#3#2", MONDAY) == [at("2026-10-16T12:00:00Z")]

    def test_nl_is_the_last_such_day_of_the_month(self) -> None:
        assert runs("0 0 12 ? * 6L", MONDAY, 2) == [
            at("2026-10-30T12:00:00Z"),
            at("2026-11-27T12:00:00Z"),
        ]

    @pytest.mark.parametrize("pattern", ["0 0 12 ? * L", "0 0 12 ? * L-2"])
    def test_l_alone_means_every_saturday(self, pattern: str) -> None:
        """cron-utils: "L by itself simply means 7 or SAT". `L-n` reads the same."""
        assert runs(pattern, MONDAY, 2) == [at("2026-10-17T12:00:00Z"), at("2026-10-24T12:00:00Z")]

    def test_a_list_is_a_true_union_specials_included(self) -> None:
        assert runs("0 0 12 ? * 6L,2", MONDAY, 3) == [
            at("2026-10-19T12:00:00Z"),
            at("2026-10-26T12:00:00Z"),
            at("2026-10-30T12:00:00Z"),
        ]

    def test_a_wrapping_range_with_a_step_is_refused(self) -> None:
        """cron-utils throws "Collection is empty" the first time it computes a day from it."""
        assert "throws when computing" in refused("0 0 12 ? * 6-2/2")


class TestDayOfMonthSpecials:
    def test_l_is_the_last_day(self) -> None:
        assert runs("0 0 12 L * ?", at("2027-02-01T00:00:00Z"), 2) == [
            at("2027-02-28T12:00:00Z"),
            at("2027-03-31T12:00:00Z"),
        ]

    def test_l_in_february_follows_the_leap_years(self) -> None:
        assert runs("0 0 12 L 2 ?", at("2027-03-01T00:00:00Z"), 2) == [
            at("2028-02-29T12:00:00Z"),
            at("2029-02-28T12:00:00Z"),
        ]

    def test_l_minus_n_counts_back_from_the_last_day(self) -> None:
        assert runs("0 0 12 L-3 * ?", MONDAY, 2) == [
            at("2026-10-28T12:00:00Z"),
            at("2026-11-27T12:00:00Z"),
        ]

    def test_l_minus_n_before_the_first_does_not_run_that_month(self) -> None:
        """`L-30` exists only in 31-day months, where it is the 1st."""
        assert runs("0 0 12 L-30 * ?", MONDAY, 3) == [
            at("2026-12-01T12:00:00Z"),
            at("2027-01-01T12:00:00Z"),
            at("2027-03-01T12:00:00Z"),
        ]

    def test_l_minus_31_never_runs(self) -> None:
        assert runs("0 0 12 L-31 * ?", MONDAY) == []

    def test_nl_in_day_of_month_is_just_l(self) -> None:
        assert runs("0 0 12 15L * ?", MONDAY, 2) == [
            at("2026-10-31T12:00:00Z"),
            at("2026-11-30T12:00:00Z"),
        ]

    def test_lw_is_the_last_weekday(self) -> None:
        """October 2026 ends on a Saturday, so Friday the 30th."""
        assert runs("0 0 12 LW * ?", at("2026-10-01T00:00:00Z"), 3) == [
            at("2026-10-30T12:00:00Z"),
            at("2026-11-30T12:00:00Z"),
            at("2026-12-31T12:00:00Z"),
        ]

    def test_w_moves_a_sunday_to_monday(self) -> None:
        """15 November 2026 is a Sunday."""
        assert runs("0 0 12 15W * ?", at("2026-11-01T00:00:00Z"), 2) == [
            at("2026-11-16T12:00:00Z"),
            at("2026-12-15T12:00:00Z"),
        ]

    def test_1w_on_a_saturday_moves_forward_to_monday_the_3rd(self) -> None:
        """1 August 2026 is a Saturday; the Friday before is in another month."""
        assert runs("0 0 12 1W * ?", at("2026-08-01T00:00:00Z")) == [at("2026-08-03T12:00:00Z")]

    @pytest.mark.parametrize(
        ("pattern", "start", "sunday"),
        [
            ("0 0 12 31W 1,3,5,7,8,10,12 ?", "2025-08-01T00:00:00Z", "2025-08-31T12:00:00Z"),
            ("0 0 12 30W 4,6,9,11 ?", "2025-11-01T00:00:00Z", "2025-11-30T12:00:00Z"),
        ],
    )
    def test_w_on_a_sunday_that_ends_the_month_stays_on_that_sunday(
        self, pattern: str, start: str, sunday: str
    ) -> None:
        """Quartz would move it back to the Friday; cron-utils leaves it - and Java runs it."""
        assert runs(pattern, at(start)) == [at(sunday)]

    def test_w_past_the_end_of_a_month_is_refused(self) -> None:
        """Java takes `31W`, runs it in October, and throws on reaching November."""
        assert "throws when computing" in refused("0 0 12 31W * ?")

    def test_w_past_the_end_is_fine_when_no_short_month_is_in_range(self) -> None:
        assert runs("0 0 12 29W 2 ? 2028", at("2026-01-01T00:00:00Z")) == [
            at("2028-02-29T12:00:00Z")
        ]


class TestReadingsThatSurprise:
    """cron-utils reads these differently from Quartz. Java runs them the cron-utils way, so a
    Python scheduler sharing the table must too."""

    def test_l_in_a_day_of_month_list_is_dropped(self) -> None:
        assert runs("0 0 12 L,15 * ?", MONDAY, 2) == [
            at("2026-10-15T12:00:00Z"),
            at("2026-11-15T12:00:00Z"),
        ]

    def test_w_in_a_day_of_month_list_is_a_plain_day(self) -> None:
        """15 November 2026 is a Sunday, and it runs on it."""
        assert runs("0 0 12 15W,1 * ?", at("2026-11-01T00:00:00Z"), 2) == [
            at("2026-11-01T12:00:00Z"),
            at("2026-11-15T12:00:00Z"),
        ]

    def test_a_wrapping_range_is_only_its_first_value(self) -> None:
        assert runs("0 0 22-2 * * ?", MONDAY, 2) == [
            at("2026-10-12T22:00:00Z"),
            at("2026-10-13T22:00:00Z"),
        ]

    def test_a_wrapping_range_inside_a_list_is_nothing(self) -> None:
        assert runs("0 0 22-2,5 * * ?", MONDAY, 2) == [
            at("2026-10-13T05:00:00Z"),
            at("2026-10-14T05:00:00Z"),
        ]

    def test_a_wrapping_month_range_too(self) -> None:
        assert runs("0 0 12 1 NOV-FEB ?", MONDAY, 2) == [
            at("2026-11-01T12:00:00Z"),
            at("2027-11-01T12:00:00Z"),
        ]

    def test_java_ignores_a_second_step(self) -> None:
        assert runs("1-5/2/3 * * * * ?", MONDAY, 4) == [
            at("2026-10-12T13:00:01Z"),
            at("2026-10-12T13:00:03Z"),
            at("2026-10-12T13:00:05Z"),
            at("2026-10-12T13:01:01Z"),
        ]

    def test_java_reads_lwlw_as_lw(self) -> None:
        assert runs("0 0 12 LWLW * ?", at("2026-10-01T00:00:00Z")) == [at("2026-10-30T12:00:00Z")]


class TestTheSearch:
    """How cron-utils steps from one time to the next - which db-scheduler does from the moment
    a run finished, fraction of a second and all."""

    def test_a_fraction_is_kept_when_the_next_second_matches(self) -> None:
        start = at("2026-10-12T12:00:00.300Z")
        assert runs("* * * * * ?", start, 2) == [
            at("2026-10-12T12:00:01.300Z"),
            at("2026-10-12T12:00:02.300Z"),
        ]

    def test_otherwise_the_next_time_is_a_whole_second(self) -> None:
        assert runs("0 0 12 * * ?", at("2026-10-12T12:00:00.300Z")) == [
            at("2026-10-13T12:00:00Z")
        ]

    def test_a_start_on_a_match_moves_on(self) -> None:
        assert runs("0 0 12 * * ?", at("2026-10-12T12:00:00Z")) == [at("2026-10-13T12:00:00Z")]

    def test_nothing_after_2099(self) -> None:
        assert runs("0 0 12 * * ?", at("2099-12-31T12:00:00Z")) == []
        assert runs("0 0 12 31 12 ? 2099", at("2099-12-31T12:00:00Z")) == []

    def test_nothing_more_than_100_years_ahead(self) -> None:
        start = at("1985-06-15T00:00:00Z")
        assert runs("0 0 12 1 1 ? 2085", start) == [at("2085-01-01T12:00:00Z")]
        assert runs("0 0 12 1 1 ? 2086", start) == []


class TestSeveralPatterns:
    def test_double_bar_takes_the_earliest(self) -> None:
        assert runs("0 0 12 ? * MON || 0 30 18 ? * FRI", MONDAY, 3) == [
            at("2026-10-16T18:30:00Z"),
            at("2026-10-19T12:00:00Z"),
            at("2026-10-23T18:30:00Z"),
        ]

    def test_a_bar_inside_fields_makes_one_pattern_per_column(self) -> None:
        """`12|18 ... MON|FRI` is `12 ... MON` and `18 ... FRI` - not every pairing."""
        assert runs("0 0 12|18 ? * MON|FRI", MONDAY, 3) == [
            at("2026-10-16T18:00:00Z"),
            at("2026-10-19T12:00:00Z"),
            at("2026-10-23T18:00:00Z"),
        ]

    def test_each_part_keeps_or_drops_the_fraction_on_its_own(self) -> None:
        """The every-second part alone would give 12:00:01.300; the other part gives 12:00:01
        whole, and that is earlier. A single pattern matching both would give 12:00:01.300."""
        assert runs("* * * * * ? || 1 0 12 * * ?", at("2026-10-12T12:00:00.300Z")) == [
            at("2026-10-12T12:00:01Z")
        ]


class TestRefused:
    @pytest.mark.parametrize(
        "pattern",
        [
            "0 0 12 * * *",  # both day fields set
            "0 0 12 ? * ?",  # neither
            "0 0 12 1 * ? 2030-2027",  # a year range that wraps
            "0 0 12 * * ?,",  # a trailing comma
            "0 0 12 * *",  # five fields
            "@daily",  # no nicknames in QUARTZ
            "0 0 12 ? * #3",
            "0 0 12 L-0 * ?",
            "5L * * * * ?",  # L outside the day fields
            "0 0 12 6#3 * ?",  # # outside day-of-week
        ],
    )
    def test_where_java_refuses(self, pattern: str) -> None:
        refused(pattern)

    def test_text_that_is_not_printable_ascii(self) -> None:
        """Java would take some of it - non-ASCII digits, say. Nobody means that on purpose."""
        assert "printable ASCII" in refused("0 0 12 ? * ٢")

    def test_the_reason_is_in_the_message(self) -> None:
        assert "exactly one of day-of-month and day-of-week" in refused("0 0 12 * * *")

    def test_it_is_refused_when_built_not_when_run(self) -> None:
        """Java builds a `31W` schedule and throws later, from inside the scheduler loop."""
        with pytest.raises(ValueError, match="31W"):
            CronSchedule("0 0 12 31W * ?", UTC, CronStyle.QUARTZ)


class TestWhatCroniterIsGiven:
    """Explicit values wherever cron-utils' reading and croniter's could differ."""

    @pytest.mark.parametrize(
        ("pattern", "expected"),
        [
            ("0 0 12 ? * 2", ("0 0 12 * * MON *",)),
            ("0 0 12 ? * 1,7", ("0 0 12 * * SUN,SAT *",)),
            ("0 0 12 ? * 6#3", ("0 0 12 * * FRI#3 *",)),
            ("0 0 12 ? * 6L", ("0 0 12 * * L5 *",)),
            ("0 0 12 ? * 6L,2", ("0 0 12 * * MON *", "0 0 12 * * L5 *")),
            ("0 0 22-2 * * ?", ("0 0 22 * * * *",)),
            ("*/15 0 12 1/10 * ? 2027", ("0,15,30,45 0 12 1,11,21,31 * * 2027",)),
            ("0 0 12 1 NOV-FEB ?", ("0 0 12 1 11 * *",)),
        ],
    )
    def test_patterns(self, pattern: str, expected: tuple[str, ...]) -> None:
        assert compile_quartz(pattern).croniter_patterns == (expected,)

    def test_l_becomes_explicit_days_by_month_length(self) -> None:
        (patterns,) = compile_quartz("0 0 12 L * ?").croniter_patterns
        assert sorted(p.split()[3:5] for p in patterns) == [
            ["28", "2"],
            ["29", "2"],
            ["30", "4,6,9,11"],
            ["31", "1,3,5,7,8,10,12"],
        ]

    def test_the_other_day_field_is_always_a_star(self) -> None:
        """So croniter's rule that a set day-of-month and day-of-week mean either never applies."""
        for pattern in ["0 0 12 ? * MON", "0 0 12 15W * ?", "0 0 12 L-3 * ?", "0 0 12 ? * 2#1"]:
            for converted in compile_quartz(pattern).croniter_patterns[0]:
                fields = converted.split()
                assert "*" in (fields[3], fields[5])


class TestYearGenerator:
    """The year search asks a generator "does it match" and "what comes next"; both are ported,
    and they must agree, or the search would skip a year Java stops in."""

    @pytest.mark.parametrize(
        "expression",
        ["*", "2026", "2026-2030", "2026/4", "*/10", "1970/130", "2026,2028,2031",
         "2026-2040/3", "2026-2030,2050", "2099", "2040-2026/3", "/7"],
    )
    def test_matches_and_next_agree(self, expression: str) -> None:
        generator = _generator(_parse_expression(_YEAR, expression), _YEAR)
        matching = [y for y in range(1969, 2101) if generator.matches(y) and 1970 <= y <= 2099]
        stepped = []
        year = 1969
        while True:
            try:
                year = generator.next(year)
            except Exception:
                break
            if year > 2099:
                break
            stepped.append(year)
        assert stepped == matching
