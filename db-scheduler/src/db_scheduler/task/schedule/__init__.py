"""When a task runs next.

A port of `com.github.kagkarlsson.scheduler.task.schedule`. The string forms - `FIXED_DELAY|10s`,
`DAILY|12:00,13:00|Europe/Rome`, `-` - are the same, because a schedule persisted by one port is
parsed by the other.
"""

from __future__ import annotations

import enum
import logging
import os
import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime, time, timedelta
from datetime import tzinfo as datetime_tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .. import ExecutionComplete

__all__ = [
    "NEVER",
    "CompositeParser",
    "CronSchedule",
    "CronStyle",
    "Daily",
    "DailyParser",
    "DisabledSchedule",
    "DisabledScheduleParser",
    "FixedDelay",
    "FixedDelayParser",
    "Parser",
    "PersistentCronSchedule",
    "RegexBasedParser",
    "Schedule",
    "Schedules",
    "UnrecognizableSchedule",
    "system_default_zone",
]

LOG = logging.getLogger("db_scheduler.task.schedule")

def system_default_zone() -> datetime_tzinfo:
    """What Java's `ZoneId.systemDefault()` resolves to, as closely as Python can.

    `Daily()` and `CronSchedule()` take this when no zone is given, because that is what
    db-scheduler does - and a schedule that resolved a different zone from the Java instance
    sharing its table would fire at a different hour.

    Wanted here is a *named* zone, not the current offset: `datetime.astimezone()` would hand
    back `UTC+01:00`, which is right until the clocks go back and then silently an hour out. So:
    `TZ` if it names one, otherwise whatever `/etc/localtime` points at, and only then the
    fixed offset - which on a container, where both these are absent and the clock is UTC, is
    UTC anyway.
    """
    named = os.environ.get("TZ")
    if named:
        try:
            return ZoneInfo(named)
        except (ZoneInfoNotFoundError, ValueError):
            LOG.warning("TZ names a zone this platform does not have: %s", named)

    try:
        link = os.readlink("/etc/localtime")
    except OSError:
        pass
    else:
        _, _, name = link.partition("zoneinfo/")
        if name:
            try:
                return ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):  # pragma: no cover - platform-specific
                LOG.warning("/etc/localtime points at a zone this platform cannot load: %s", name)

    offset = datetime.now().astimezone().tzinfo  # pragma: no cover - platform-specific
    return offset if offset is not None else UTC  # pragma: no cover - astimezone always sets one


class _SystemDefaultZone(datetime_tzinfo):
    """Stands for "no zone was given", which is not the same as "None was given".

    Java says this with two constructor overloads: one defaults to the system zone, the other
    rejects null. Python has one signature, so it needs a default that is distinguishable from
    `None` - and it has to be a `tzinfo` so the parameter can still be typed as one.
    """

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None


_SYSTEM_ZONE = _SystemDefaultZone()


def _resolve_zone(zone: datetime_tzinfo) -> datetime_tzinfo:
    if isinstance(zone, _SystemDefaultZone):
        return system_default_zone()
    if zone is None:
        raise ValueError("zone cannot be None")
    return zone


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
        """No: the next time depends on when the last run *finished*, not on the clock.

        This is what schedule-on-startup keys off. A deterministic schedule that answers
        differently means somebody edited it, so the row moves; a fixed delay always answers
        differently, so its row only ever moves *earlier* - otherwise a service restarting more
        often than its delay would push the execution away on every boot and never run it.
        """
        return False

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

    def __init__(self, *times: time, zone: datetime_tzinfo = _SYSTEM_ZONE) -> None:
        zone = _resolve_zone(zone)
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
        # Zones are compared by id, as `ZoneId.equals` does. `timezone.utc` and
        # `ZoneInfo("UTC")` are different objects for the same zone, and a schedule that came
        # back from JSON must still equal the one that went in.
        return (
            isinstance(other, Daily)
            and other.times == self.times
            and str(other.zone) == str(self.zone)
        )

    def __hash__(self) -> int:
        return hash(("Daily", self.times, str(self.zone)))

    def __repr__(self) -> str:
        shown = ",".join(t.strftime("%H:%M") for t in self.times)
        return f"Daily(times={shown}, zone={self.zone})"


class CronStyle(enum.Enum):
    """Which cron dialect a pattern is written in.

    Not decoration: the styles disagree about how many fields there are and what the first one
    means. SPRING53 - db-scheduler's default - puts **seconds first**, so `0 0 12 * * ?` is
    noon every day. Read as a UNIX-plus-trailing-seconds pattern, the same string is the 12th of
    the month. One pattern, two schedules, no error either way.
    """

    CRON4J = "CRON4J"
    QUARTZ = "QUARTZ"
    UNIX = "UNIX"
    SPRING = "SPRING"
    SPRING53 = "SPRING53"

    @property
    def seconds_first(self) -> bool:
        """Whether the leading field is seconds. Five-field styles have no seconds at all."""
        return self in (CronStyle.QUARTZ, CronStyle.SPRING, CronStyle.SPRING53)


class CronSchedule(Schedule):
    """A cron pattern, in a named zone. `-` means disabled.

    croniter stands in for cron-utils. It reads all five dialects db-scheduler names, given the
    one flag that says where the seconds live - including Quartz's `?` and its optional seventh
    year field, both of which `croniter.is_valid` rejects even though `croniter` itself parses
    them, which is why validation here is a parse rather than a check.
    """

    DISABLED = "-"

    __slots__ = ("cron_style", "pattern", "zone")

    def __init__(
        self,
        pattern: str,
        zone: datetime_tzinfo = _SYSTEM_ZONE,
        cron_style: CronStyle | None = CronStyle.SPRING53,
    ) -> None:
        self.zone = _resolve_zone(zone)
        # Java: `cronStyle != null ? cronStyle : SPRING53`. A serializer that omits the field
        # hands back null, and the default has to survive that.
        self.cron_style = cron_style if cron_style is not None else CronStyle.SPRING53
        self.pattern = pattern
        self._validate()

    def _validate(self) -> None:
        """Parse the pattern now, so a bad one fails where the task that owns it is still named.

        A disabled schedule is not parsed at all - `-` is not a pattern. Java says the same
        thing by installing a `DisabledScheduleExecutionTime` that throws from every method:
        constructing is fine, asking it anything is not.
        """
        from croniter import CroniterBadCronError, croniter

        if self.is_disabled:
            return
        try:
            croniter(self.pattern, second_at_beginning=self.cron_style.seconds_first)
        except (CroniterBadCronError, ValueError) as exc:
            raise ValueError(f"invalid cron pattern: {self.pattern!r}") from exc

    def next_execution_time(self, completion: ExecutionComplete) -> datetime:
        """The next time after this one finished, read in this schedule's own zone.

        The zone is applied to the *completion* time, not just to the result, so that
        `0 05 13,20 * * ?` in New York means one o'clock in New York however the row was
        written.
        """
        from croniter import CroniterBadDateError, croniter

        if self.is_disabled:
            raise self._unsupported()
        last = completion.time_done.astimezone(self.zone)
        iterator = croniter(
            self.pattern, last, second_at_beginning=self.cron_style.seconds_first
        )
        try:
            following: datetime = iterator.get_next(datetime)
        except CroniterBadDateError:
            # `0 0 0 29 2 MON#1` - the 29th of February that is also the first Monday. No such
            # date exists, and db-scheduler parks it at NEVER rather than letting one
            # unsatisfiable pattern take the poll loop down.
            LOG.error(
                "Cron-pattern did not return any further execution-times. This behavior is "
                'currently not supported by the scheduler. Setting next execution-time to '
                'far-future, aka "NEVER" (%s)',
                NEVER,
            )
            return NEVER
        return following.astimezone(UTC)

    @property
    def is_deterministic(self) -> bool:
        return True

    @property
    def is_disabled(self) -> bool:
        return self.pattern == self.DISABLED

    def _unsupported(self) -> NotImplementedError:
        return NotImplementedError(
            "Schedule is marked as disabled. Method should never be called"
        )

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, CronSchedule)
            and other.pattern == self.pattern
            and str(other.zone) == str(self.zone)
            and other.cron_style is self.cron_style
        )

    def __hash__(self) -> int:
        return hash(("CronSchedule", self.pattern, str(self.zone), self.cron_style))

    def __repr__(self) -> str:
        return (
            f"CronSchedule pattern={self.pattern}, "
            f"cronStyle={self.cron_style.name}, zone={self.zone}"
        )


class PersistentCronSchedule:
    """A cron pattern that lives in the row rather than in the code.

    Handed to a task as its data, it makes the schedule editable with an UPDATE: no deploy, no
    restart, and each instance in the cluster picks up the new pattern the next time it reads
    the row.

    The zone is kept as its *name*, not as a `tzinfo`, because this is a value that gets
    serialized - `"Europe/London"` survives the round trip and still means London after the
    clocks change, where a stored offset would not.

    It is a `ScheduleAndData`, registered as one in `task/helper.py`: the two live in different
    packages that would otherwise import each other, which is a knot Java does not have to
    untie.
    """

    __slots__ = ("cron_pattern", "data", "zone_id")

    def __init__(
        self,
        cron_pattern: str,
        zone: datetime_tzinfo = _SYSTEM_ZONE,
        data: object = None,
    ) -> None:
        self.cron_pattern = cron_pattern
        self.zone_id = str(_resolve_zone(zone))
        self.data = data

    @property
    def schedule(self) -> Schedule:
        return CronSchedule(self.cron_pattern, ZoneInfo(self.zone_id))

    def __repr__(self) -> str:
        return f"PersistentCronSchedule pattern={self.cron_pattern}"


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


class RegexBasedParser(Parser):
    """A parser that is one pattern plus what to build from a match.

    The examples are not decoration either: `Schedules.parse_schedule` puts them in the
    exception, so somebody who typed `DAILY|8:00` is shown `DAILY|12:00` rather than told the
    string was unrecognised.
    """

    __slots__ = ("_examples", "_pattern")

    def __init__(self, pattern: re.Pattern[str], examples: list[str]) -> None:
        if pattern is None:
            raise ValueError("pattern cannot be None")
        self._pattern = pattern
        self._examples = list(examples)

    def parse(self, text: str) -> Schedule | None:
        if text is None:
            return None
        found = self._pattern.fullmatch(text)
        return self.matched_schedule(found) if found else None

    def examples(self) -> list[str]:
        return list(self._examples)

    @abstractmethod
    def matched_schedule(self, result: re.Match[str]) -> Schedule | None: ...


class FixedDelayParser(RegexBasedParser):
    PATTERN = re.compile(r"FIXED_DELAY\|(\d+)s")

    def __init__(self) -> None:
        super().__init__(self.PATTERN, ["FIXED_DELAY|10s"])

    def matched_schedule(self, result: re.Match[str]) -> Schedule | None:
        return FixedDelay.of_seconds(int(result.group(1)))


class DailyParser(RegexBasedParser):
    PATTERN = re.compile(r"DAILY\|((\d{2}:\d{2})(,\d{2}:\d{2})*)(\|(.+))?")

    def __init__(self) -> None:
        super().__init__(
            self.PATTERN, ["DAILY|12:00", "DAILY|12:00,13:30", "DAILY|12:00|Europe/Rome"]
        )

    def matched_schedule(self, found: re.Match[str]) -> Schedule | None:
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
    def daily(*times: time, zone: datetime_tzinfo = _SYSTEM_ZONE) -> Daily:
        return Daily(*times, zone=zone)

    @staticmethod
    def fixed_delay(delay: timedelta) -> FixedDelay:
        return FixedDelay.of(delay)

    @staticmethod
    def cron(
        pattern: str,
        zone: datetime_tzinfo = _SYSTEM_ZONE,
        cron_style: CronStyle | None = CronStyle.SPRING53,
    ) -> CronSchedule:
        return CronSchedule(pattern, zone, cron_style)

    #: Nested here as it is in Java, where it is `Schedules.UnrecognizableSchedule`. The
    #: module-level name is the same object, so either import works.
    UnrecognizableSchedule = UnrecognizableSchedule

    @classmethod
    def parse_schedule(cls, text: str) -> Schedule:
        """Parse, or raise showing what would have worked.

        Currently `DAILY|hh:mm,...(|TIME_ZONE)`, `FIXED_DELAY|xxxs` and `-`, exactly as Java
        documents on `parseSchedule`.
        """
        found = cls._PARSER.parse(text)
        if found is None:
            raise UnrecognizableSchedule(text, cls._PARSER.examples())
        return found


def _positive(amount: int) -> None:
    if amount <= 0:
        raise ValueError("argument must be greater than 0")
