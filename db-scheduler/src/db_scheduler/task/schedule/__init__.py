"""When a task runs next.

A port of `com.github.kagkarlsson.scheduler.task.schedule`. The string forms - `FIXED_DELAY|10s`,
`DAILY|12:00,13:00|Europe/Rome`, `-` - are the same, because a schedule persisted by one port is
parsed by the other.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime, time, timedelta
from datetime import tzinfo as datetime_tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .. import ExecutionComplete  # noqa: TID252 - the model this package is about

__all__ = [
    "NEVER",
    "CompositeParser",
    "CronSchedule",
    "Daily",
    "DailyParser",
    "DisabledSchedule",
    "DisabledScheduleParser",
    "FixedDelay",
    "FixedDelayParser",
    "Parser",
    "Schedule",
    "Schedules",
    "UnrecognizableSchedule",
]

#: Where a schedule that will never fire again is parked. Part of the cross-language contract:
#: a row written by one port carries this value and is read by the other.
NEVER = datetime(2500, 1, 1, 12, 0, tzinfo=UTC)


class Schedule(ABC):
    """When to run next, given how the last run ended."""

    @abstractmethod
    def next_execution_time(self, completion: ExecutionComplete) -> datetime: ...

    def initial_execution_time(self, now: datetime) -> datetime:
        """The first time this schedule fires. Asked through a completion that never happened."""
        return self.next_execution_time(ExecutionComplete.simulated_success(now))

    @property
    @abstractmethod
    def is_deterministic(self) -> bool:
        """Whether the next time can be derived from the schedule alone.

        A deterministic schedule can have its next execution recomputed at any point and get
        the same answer, which is what lets a persistent schedule be validated against the row
        it was stored in. Cron is not deterministic here: the pattern can be edited between
        runs.
        """

    @property
    def is_disabled(self) -> bool:
        return False


class FixedDelay(Schedule):
    """A fixed gap between the end of one run and the start of the next.

    Note it measures from *completion*, not from the scheduled time - so a run that takes an
    hour does not produce a burst of catch-up runs afterwards.
    """

    __slots__ = ("duration",)

    def __init__(self, duration: timedelta) -> None:
        if duration is None:
            raise ValueError("duration cannot be None")
        self.duration = duration

    @classmethod
    def of(cls, duration: timedelta) -> FixedDelay:
        return cls(duration)

    @classmethod
    def of_millis(cls, millis: int) -> FixedDelay:
        _positive(millis)
        return cls(timedelta(milliseconds=millis))

    @classmethod
    def of_seconds(cls, seconds: int) -> FixedDelay:
        _positive(seconds)
        return cls(timedelta(seconds=seconds))

    @classmethod
    def of_minutes(cls, minutes: int) -> FixedDelay:
        _positive(minutes)
        return cls(timedelta(minutes=minutes))

    @classmethod
    def of_hours(cls, hours: int) -> FixedDelay:
        _positive(hours)
        return cls(timedelta(hours=hours))

    def next_execution_time(self, completion: ExecutionComplete) -> datetime:
        return completion.time_done + self.duration

    def initial_execution_time(self, now: datetime) -> datetime:
        """Now, not now-plus-the-delay: the delay sits between runs, not before the first."""
        return now

    @property
    def is_deterministic(self) -> bool:
        return True

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FixedDelay) and other.duration == self.duration

    def __hash__(self) -> int:
        return hash(("FixedDelay", self.duration))

    def __repr__(self) -> str:
        return f"FixedDelay(duration={self.duration})"


class Daily(Schedule):
    """One or more times of day, in a named zone.

    The zone is the point: `Daily(time(8, 0), zone=ROME)` means eight in Rome, which is 07:00
    UTC in winter and 06:00 in summer. Storing the local time and the zone rather than an offset
    is what keeps that right across a daylight-saving change.
    """

    __slots__ = ("times", "zone")

    def __init__(self, *times: time, zone: datetime_tzinfo = UTC) -> None:
        if zone is None:
            raise ValueError("zone cannot be None")
        if not times:
            raise ValueError("times cannot be empty")
        self.times = tuple(sorted(times))
        self.zone = zone

    def next_execution_time(self, completion: ExecutionComplete) -> datetime:
        done = completion.time_done.astimezone(self.zone)
        today = done.date()
        for candidate_time in self.times:
            candidate = datetime.combine(today, candidate_time, tzinfo=self.zone)
            if candidate > done:
                return candidate.astimezone(UTC)
        tomorrow = today + timedelta(days=1)
        return datetime.combine(tomorrow, self.times[0], tzinfo=self.zone).astimezone(UTC)

    @property
    def is_deterministic(self) -> bool:
        return True

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Daily) and other.times == self.times and other.zone == self.zone

    def __hash__(self) -> int:
        return hash(("Daily", self.times, str(self.zone)))

    def __repr__(self) -> str:
        shown = ",".join(t.strftime("%H:%M") for t in self.times)
        return f"Daily(times={shown}, zone={self.zone})"


class CronSchedule(Schedule):
    """A cron pattern, in a named zone. `-` means disabled."""

    DISABLED = "-"

    __slots__ = ("_iterator_factory", "pattern", "zone")

    def __init__(self, pattern: str, zone: datetime_tzinfo = UTC) -> None:
        if zone is None:
            raise ValueError("zone cannot be None")
        self.pattern = pattern
        self.zone = zone
        if pattern != self.DISABLED:
            from croniter import CroniterBadCronError, croniter

            if not croniter.is_valid(pattern):
                raise ValueError(f"invalid cron pattern: {pattern!r}")
            try:
                croniter(pattern)
            except CroniterBadCronError as exc:  # pragma: no cover - is_valid covers it
                raise ValueError(f"invalid cron pattern: {pattern!r}") from exc

    def next_execution_time(self, completion: ExecutionComplete) -> datetime:
        self._refuse_if_disabled()
        from croniter import croniter

        last = completion.time_done.astimezone(self.zone)
        following: datetime = croniter(self.pattern, last).get_next(datetime)
        if following is None:  # pragma: no cover - croniter always yields for a valid pattern
            return NEVER
        return following.astimezone(UTC)

    @property
    def is_deterministic(self) -> bool:
        self._refuse_if_disabled()
        return False

    @property
    def is_disabled(self) -> bool:
        return self.pattern == self.DISABLED

    def _refuse_if_disabled(self) -> None:
        if self.is_disabled:
            raise NotImplementedError(
                "a disabled CronSchedule does not support any operation other than "
                "is_disabled. This appears to be a bug."
            )

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, CronSchedule)
            and other.pattern == self.pattern
            and other.zone == self.zone
        )

    def __hash__(self) -> int:
        return hash(("CronSchedule", self.pattern, str(self.zone)))

    def __repr__(self) -> str:
        return f"CronSchedule(pattern={self.pattern!r}, zone={self.zone})"


class DisabledSchedule(Schedule):
    """A schedule that answers only one question, and refuses the rest.

    It exists so a recurring task can be switched off in configuration without deleting it, and
    it refuses loudly rather than returning a plausible time, because a disabled task that
    quietly kept running would be worse than an exception.
    """

    __slots__ = ()

    def next_execution_time(self, completion: ExecutionComplete) -> datetime:
        raise self._unsupported()

    def initial_execution_time(self, now: datetime) -> datetime:
        raise self._unsupported()

    @property
    def is_deterministic(self) -> bool:
        raise self._unsupported()

    @property
    def is_disabled(self) -> bool:
        return True

    @staticmethod
    def _unsupported() -> NotImplementedError:
        return NotImplementedError(
            "DisabledSchedule does not support any other operations than 'is_disabled'. "
            "This appears to be a bug."
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, DisabledSchedule)

    def __hash__(self) -> int:
        return hash("DisabledSchedule")

    def __repr__(self) -> str:
        return "DisabledSchedule()"


# ------------------------------------------------------------------ parsing


class UnrecognizableSchedule(ValueError):
    def __init__(self, given: str, examples: list[str] | None = None) -> None:
        message = f"Unrecognized schedule: {given!r}"
        if examples:
            message += f". Parsable examples: {examples}"
        super().__init__(message)
        self.given = given
        self.examples = examples or []


class Parser(ABC):
    """Turns a string into a Schedule, or declines."""

    @abstractmethod
    def parse(self, text: str) -> Schedule | None: ...

    @abstractmethod
    def examples(self) -> list[str]: ...


class FixedDelayParser(Parser):
    PATTERN = re.compile(r"^FIXED_DELAY\|(\d+)s$")

    def parse(self, text: str) -> Schedule | None:
        found = self.PATTERN.match(text)
        return FixedDelay.of_seconds(int(found.group(1))) if found else None

    def examples(self) -> list[str]:
        return ["FIXED_DELAY|10s"]


class DailyParser(Parser):
    PATTERN = re.compile(r"^DAILY\|((\d{2}:\d{2})(,\d{2}:\d{2})*)(\|(.+))?$")

    def parse(self, text: str) -> Schedule | None:
        found = self.PATTERN.match(text)
        if not found:
            return None
        times = [time.fromisoformat(part) for part in found.group(1).split(",")]
        zone_name = found.group(5)
        if zone_name is None:
            return Daily(*times)
        try:
            zone = ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError):
            # A zone the platform does not know is a misconfiguration, not a different
            # schedule - so decline rather than silently falling back to UTC and running the
            # nightly report at the wrong hour.
            return None
        return Daily(*times, zone=zone)

    def examples(self) -> list[str]:
        return ["DAILY|12:00", "DAILY|12:00,13:30", "DAILY|12:00|Europe/Rome"]


class DisabledScheduleParser(Parser):
    def parse(self, text: str) -> Schedule | None:
        return DisabledSchedule() if text == "-" else None

    def examples(self) -> list[str]:
        return ["-"]


class CompositeParser(Parser):
    """Tries each parser in turn. The first that recognises the string wins."""

    __slots__ = ("_parsers",)

    def __init__(self, *parsers: Parser) -> None:
        if not parsers:
            raise ValueError("at least one parser is required")
        self._parsers = parsers

    @classmethod
    def of(cls, *parsers: Parser) -> CompositeParser:
        return cls(*parsers)

    def parse(self, text: str) -> Schedule | None:
        for parser in self._parsers:
            found = parser.parse(text)
            if found is not None:
                return found
        return None

    def examples(self) -> list[str]:
        return [example for parser in self._parsers for example in parser.examples()]


class Schedules:
    """The factory, matching db-scheduler's `Schedules`."""

    _PARSER = CompositeParser.of(FixedDelayParser(), DailyParser(), DisabledScheduleParser())

    @staticmethod
    def daily(*times: time, zone: datetime_tzinfo = UTC) -> Daily:
        return Daily(*times, zone=zone)

    @staticmethod
    def fixed_delay(delay: timedelta) -> FixedDelay:
        return FixedDelay.of(delay)

    @staticmethod
    def cron(pattern: str, zone: datetime_tzinfo = UTC) -> CronSchedule:
        return CronSchedule(pattern, zone=zone)

    @classmethod
    def parse(cls, text: str) -> Schedule:
        """Parse, or raise showing what would have worked."""
        found = cls._PARSER.parse(text)
        if found is None:
            raise UnrecognizableSchedule(text, cls._PARSER.examples())
        return found


def _positive(amount: int) -> None:
    if amount <= 0:
        raise ValueError("argument must be greater than 0")
