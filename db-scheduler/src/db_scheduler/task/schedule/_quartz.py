"""`CronStyle.QUARTZ`, read exactly as Java db-scheduler reads it, then handed to croniter.

Java db-scheduler reads a QUARTZ pattern with cron-utils 9.2.1 (`CronType.QUARTZ`). croniter has
no Quartz mode: it numbers the days of the week from Sunday = 0, where Quartz has Sunday = 1, and
it lacks `L-3` and `LW`. So a Quartz pattern is never given to croniter as it is. This module
reads it the way cron-utils does - a port of its parser, its validation and its value
generators, check for check - and writes out croniter patterns that mean the same thing.

The reference is cron-utils, not the Quartz Scheduler: where the two disagree, cron-utils is what
a Java service sharing the table actually runs. That includes readings that surprise:

- a range that wraps (`22-2`, `NOV-FEB`) is only its first value, and nothing inside a list;
- in a day-of-month list, `L`, `L-n` and `LW` are dropped, and `nW` means plain `n`;
- `nW` that falls on a Sunday at the end of the month stays on that Sunday;
- `L` alone in day-of-week, and `L-n` there, mean Saturday.

A pattern cron-utils accepts but would throw on while computing a time - `31W` once a shorter
month is in range, `6-2/2` - is refused here up front, as is anything that is not printable
ASCII. `docs/PORTING.md` lists these. `docs/QUARTZ-CONVERTER-LEDGER.md` has how it was checked:
every rule below is pinned by tests against the output of cron-utils itself.

What croniter receives is deliberately plain: explicit lists for seconds, minutes, hours, months
and years, so croniter never interprets a range or a step; explicit days for the day-of-month
specials, computed here; weekday names, `NAME#k` and `L<n>` for day-of-week. The other day field
is always `*`, so croniter's day-of-month/day-of-week OR rule never applies.
"""

from __future__ import annotations

import calendar
import contextlib
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

__all__ = ["QuartzCron", "QuartzError", "compile_quartz"]


class QuartzError(ValueError):
    """Why a QUARTZ pattern was refused."""


class _JavaThrows(QuartzError):
    """cron-utils accepts the pattern but throws while computing a time from it."""


class _NoValue(Exception):
    """cron-utils' `NoSuchValueException`: a generator has no further value."""


# --- what cron-utils parses into --------------------------------------------------------------

# The members of cron-utils' `SpecialChar`, by name: `FieldParser.map` turns a range endpoint
# spelled like one of them into that special instead of a number.
_SPECIAL_NAMES = ("LW", "L", "W", "HASH", "QUESTION_MARK", "NONE")
_DEFAULT = -1  # cron-utils' "not given" for `On.time` and `On.nth`


@dataclass(frozen=True)
class _Always:
    pass


@dataclass(frozen=True)
class _Question:
    pass


@dataclass(frozen=True)
class _On:
    time: int
    special: str = "NONE"  # NONE, L, W, LW, HASH, QUESTION_MARK
    nth: int = _DEFAULT


@dataclass(frozen=True)
class _Between:
    start: int | str  # a number, or a special's name
    end: int | str


@dataclass(frozen=True)
class _Every:
    expression: _Always | _On | _Between
    period: int


@dataclass(frozen=True)
class _And:
    items: tuple[_Expression, ...]


type _Expression = _Always | _Question | _On | _Between | _Every | _And


@dataclass(frozen=True)
class _Field:
    """One field's constraints, as cron-utils' QUARTZ definition gives them."""

    name: str
    low: int
    high: int
    names: dict[str, int]
    specials: frozenset[str]  # beyond NONE: which of L, W, LW, HASH, QUESTION_MARK it takes
    strict_range: bool = False


_DAY_NAMES = {name: n for n, name in enumerate(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"), 1)}
_MONTH_NAMES = {name: n for n, name in enumerate(calendar.month_abbr[1:], 1)}
_MONTH_NAMES = {name.upper(): n for name, n in _MONTH_NAMES.items()}

_SECOND = _Field("second", 0, 59, {}, frozenset())
_MINUTE = _Field("minute", 0, 59, {}, frozenset())
_HOUR = _Field("hour", 0, 23, {}, frozenset())
_DAY_OF_MONTH = _Field("day-of-month", 1, 31, {}, frozenset({"L", "W", "LW", "QUESTION_MARK"}))
_MONTH = _Field("month", 1, 12, _MONTH_NAMES, frozenset())
_DAY_OF_WEEK = _Field("day-of-week", 1, 7, _DAY_NAMES, frozenset({"L", "HASH", "QUESTION_MARK"}))
_YEAR = _Field("year", 1970, 2099, {}, frozenset(), strict_range=True)
_FIELDS = (_SECOND, _MINUTE, _HOUR, _DAY_OF_MONTH, _MONTH, _DAY_OF_WEEK, _YEAR)


# --- Java's string handling, where Python's differs -------------------------------------------

_JAVA_WHITESPACE = re.compile(r"[ \t\n\x0b\f\r]+")  # Java's `\s`: ASCII only
_ALLOWED = re.compile(r"[ -~\t\n\x0b\f\r]*")  # printable ASCII and Java's whitespace
_JAVA_INT = re.compile(r"[+-]?[0-9]+")


def _java_split(text: str, separator: str) -> list[str]:
    """`String.split` with a literal separator: no match keeps the input; trailing empties go."""
    if separator not in text:
        return [text]
    parts = text.split(separator)
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def _java_split_whitespace(text: str) -> list[str]:
    parts = _JAVA_WHITESPACE.split(text)
    if len(parts) == 1:
        return parts
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def _java_trim(text: str) -> str:
    """`String.trim`: drops every character up to and including the space, at both ends."""
    return text.strip("".join(chr(c) for c in range(33)))


def _parse_int(text: str) -> int:
    """`Integer.parseInt`, minus its non-ASCII digits (refused up front)."""
    if not _JAVA_INT.fullmatch(text):
        raise QuartzError(f"not a number: {text!r}")
    value = int(text)
    if not -(2**31) <= value < 2**31:
        raise QuartzError(f"number out of range: {text!r}")
    return value


# --- the parser: cron-utils' CronParser, CronParserField and FieldParser ----------------------


def _parse(expression: str) -> list[tuple[_Expression, ...]]:
    """`CronParser.parse`: one tuple of seven fields per part (`||` and `|` make several)."""
    replaced = _java_trim(_JAVA_WHITESPACE.sub(" ", expression))
    if not replaced:
        raise QuartzError("empty pattern")
    if expression.startswith("@"):
        raise QuartzError("nicknames such as @daily are not supported by QUARTZ")
    if "||" in expression:
        return [fields for part in _java_split(expression, "||") for fields in _parse(part)]
    if "|" in expression:
        tokens = _java_split_whitespace(expression)
        count = max((len(_java_split(token, "|")) for token in tokens), default=0)
        parts = []
        for j in range(count):
            words = []
            for token in tokens:
                if "|" in token:
                    pieces = _java_split(token, "|")
                    if j >= len(pieces):
                        # Java: ArrayIndexOutOfBoundsException, out of the constructor.
                        raise QuartzError(f"{token!r} has fewer |-alternatives than another field")
                    words.append(pieces[j])
                else:
                    words.append(token)
            parts.append(_java_trim(" ".join(words)))
        return [fields for part in parts for fields in _parse(part)]
    words = replaced.upper().split(" ")
    if any(word.endswith(",") for word in words):
        raise QuartzError("a field ends with a comma")
    if len(words) not in (6, 7):
        raise QuartzError(f"{len(words)} fields; QUARTZ takes 6, or 7 with a year")
    fields = tuple(_parse_field(field, word) for field, word in zip(_FIELDS, words, strict=False))
    for field, parsed in zip(_FIELDS, fields, strict=False):
        _validate(field, parsed)
    if len(fields) == 6:
        fields = (*fields, _Always())
    day_of_month, day_of_week = fields[3], fields[5]
    # `CronConstraintsFactory.ensureEitherDayOfWeekOrDayOfMonth`: exactly one of them is `?`.
    if isinstance(day_of_month, _Question) == isinstance(day_of_week, _Question):
        raise QuartzError("exactly one of day-of-month and day-of-week must be ?")
    return [fields]


def _parse_field(field: _Field, text: str) -> _Expression:
    """`CronParserField.parse`: a day-of-week name before `L` becomes its number first."""
    if field is _DAY_OF_WEEK and text.endswith("L") and text[:-1] in field.names:
        text = f"{field.names[text[:-1]]}L"
    return _parse_expression(field, text)


def _parse_expression(field: _Field, text: str) -> _Expression:
    """`FieldParser.parse`."""
    if not any(c in text for c in "/-,"):
        if "?" in text and "QUESTION_MARK" not in field.specials:
            raise QuartzError(f"? is not allowed in the {field.name} field")
        return _no_specials(field, text)
    items = _java_split(text, ",")
    if len(items) > 1:
        return _And(tuple(_parse_expression(field, item) for item in items))
    halves = _java_split(text, "-")
    if "-" in text and len(halves) != 2:
        raise QuartzError(f"not a range: {text!r}")
    if halves[0] == "L":
        return _parse_l(field, "L", _to_int(field, halves[1]))
    if len(halves) > 1:
        return _parse_between(field, halves)
    return _parse_slash(field, text, _java_split(text, "/"))


def _parse_between(field: _Field, halves: list[str]) -> _Expression:
    if not halves[0] or not halves[1]:
        raise QuartzError(f"not a range: {'-'.join(halves)!r}")
    if "/" in halves[1]:
        step = _java_split(halves[1], "/")
        if len(step) < 2:
            raise QuartzError(f"a step is missing: {'-'.join(halves)!r}")
        # Java reads only the first step: `1-5/2/3` is `1-5/2`.
        return _Every(_Between(_map(field, halves[0]), _map(field, step[0])), _to_int(field, step[1]))
    return _Between(_map(field, halves[0]), _map(field, halves[1]))


def _parse_slash(field: _Field, text: str, parts: list[str]) -> _Expression:
    if len(parts) == 1:
        raise QuartzError(f"a step is missing: {text!r}")
    if len(parts) != 2:
        raise QuartzError(f"not a step: {text!r}")
    start, step = parts
    if start.strip() == "*" and step == "1":
        return _no_specials(field, start)
    if start.strip() in ("*", ""):
        return _Every(_Always(), _parse_int(step))
    return _Every(_On(_to_int(field, start)), _parse_int(step))


def _no_specials(field: _Field, text: str) -> _Expression:
    if text == "*":
        return _Always()
    if text == "?":
        return _Question()
    return _parse_on(field, text)


def _parse_on(field: _Field, text: str) -> _On:
    if "#" in text:
        if "HASH" not in field.specials:
            raise QuartzError(f"# is not allowed in the {field.name} field")
        parts = _java_split(text, "#")
        if len(parts) < 2:
            raise QuartzError(f"not an n#k: {text!r}")
        nth = _to_int(field, parts[1])
        if not parts[0]:
            raise QuartzError(f"not an n#k: {text!r}")
        # Java reads only the first `#`: `6#3#2` is `6#3`.
        return _On(_to_int(field, parts[0]), "HASH", nth)
    if "LW" in text:
        if text.replace("LW", ""):
            raise QuartzError(f"expected LW: {text!r}")
        return _On(_DEFAULT, "LW")
    if re.search(r"[0-9]L", text) or text == "L":
        return _parse_l(field, text, _DEFAULT)
    if re.search(r"[0-9]W", text):
        return _On(_to_int(field, text.replace("W", "")), "W")
    return _On(_to_int(field, text))


def _parse_l(field: _Field, text: str, days_before: int) -> _On:
    rest = text.replace("L", "")
    return _On(_to_int(field, rest) if rest else _DEFAULT, "L", days_before)


def _to_int(field: _Field, text: str) -> int:
    """`mapToIntegerFieldValue`: a name of this field, or a number."""
    if text in field.names:
        return field.names[text]
    return _parse_int(text)


def _map(field: _Field, text: str) -> int | str:
    """`FieldParser.map`, for range endpoints: a special's name stays a special."""
    if text in _SPECIAL_NAMES:
        return text
    return _to_int(field, text)


# --- validation: cron-utils' ValidationFieldExpressionVisitor ---------------------------------


def _validate(field: _Field, expression: _Expression) -> None:
    match expression:
        case _And(items):
            for item in items:
                _validate(field, item)
        case _Between(start, end):
            for end_point in (start, end):
                if isinstance(end_point, int):
                    _in_range(field, end_point)
            for end_point in (start, end):
                if isinstance(end_point, str) and end_point not in field.specials:
                    raise QuartzError(f"{end_point} is not allowed in the {field.name} field")
            if any(isinstance(p, str) and p != "L" for p in (start, end)):
                raise QuartzError("no special characters allowed in a range, except L")
            if (
                field.strict_range
                and isinstance(start, int)
                and isinstance(end, int)
                and start > end
            ):
                raise QuartzError(f"invalid range: {start}-{end}")
        case _Every(inner, period):
            _validate(field, inner)
            if not (0 < period <= field.high - field.low + 1 and period <= field.high):
                raise QuartzError(f"step {period} out of range for the {field.name} field")
        case _On(time, special, nth):
            if special not in ("NONE", "QUESTION_MARK") and special not in field.specials:
                raise QuartzError(f"{special} is not allowed in the {field.name} field")
            if time != _DEFAULT:
                _in_range(field, time)
            if nth != _DEFAULT:
                _in_range(field, nth)


def _in_range(field: _Field, value: int) -> None:
    if not field.low <= value <= field.high:
        raise QuartzError(f"{value} not in range [{field.low}, {field.high}] for {field.name}")


# --- value generators: cron-utils' FieldValueGenerator family ---------------------------------


class _Generator:
    """`FieldValueGenerator`: `next` past a reference, `matches`, and candidates in a window."""

    def next(self, reference: int) -> int:
        raise NotImplementedError

    def matches(self, value: int) -> bool:
        raise NotImplementedError

    def inside(self, start: int, end: int) -> list[int]:
        """`generateCandidatesNotIncludingIntervalExtremes`."""
        raise NotImplementedError

    def candidates(self, start: int, end: int) -> list[int]:
        """`generateCandidates`."""
        found = set(self.inside(start, end))
        if self.matches(start):
            found.add(start)
        if self.matches(end):
            found.add(end)
        return sorted(found)

    def _walk(self, start: int, end: int) -> list[int]:
        values = []
        try:
            reference = self.next(start)
            while reference < end:
                values.append(reference)
                reference = self.next(reference)
        except _NoValue:
            pass
        return values


def _integer(value: int | str) -> int:
    """`BetweenFieldValueGenerator.map`: a special at a range end throws."""
    if isinstance(value, str):
        raise _JavaThrows(f"{value} as the end of a range")
    return value


class _AlwaysGenerator(_Generator):
    def __init__(self, field: _Field) -> None:
        self.field = field

    def next(self, reference: int) -> int:
        if reference + 1 <= self.field.high:
            return reference + 1
        raise _NoValue

    def matches(self, value: int) -> bool:
        return self.field is _DAY_OF_WEEK or self.field.low <= value <= self.field.high

    def inside(self, start: int, end: int) -> list[int]:
        return list(range(start + 1, end))


class _OnGenerator(_Generator):
    """`OnFieldValueGenerator`: only the time counts, whatever special the `On` carries."""

    def __init__(self, on: _On) -> None:
        self.time = on.time

    def next(self, reference: int) -> int:
        if self.time <= reference:
            raise _NoValue
        return self.time

    def matches(self, value: int) -> bool:
        return self.time == value

    def inside(self, start: int, end: int) -> list[int]:
        return [self.time] if start < self.time < end else []


class _BetweenGenerator(_Generator):
    def __init__(self, between: _Between) -> None:
        self.between = between

    def next(self, reference: int) -> int:
        start, end = _integer(self.between.start), _integer(self.between.end)
        candidate = reference + 1
        while candidate < start:
            candidate += 1
        if candidate > end:
            raise _NoValue
        return candidate

    def matches(self, value: int) -> bool:
        return _integer(self.between.start) <= value <= _integer(self.between.end)

    def inside(self, start: int, end: int) -> list[int]:
        values: list[int] = []
        low, high = _integer(self.between.start), _integer(self.between.end)
        range_start, range_end = start, end
        if start <= high and low <= end:
            if high < end:
                range_end = high
            if low > start:
                range_start = low
            try:
                if range_start != start:
                    values.append(range_start)
                reference = self.next(range_start)
                while reference < range_end:
                    values.append(reference)
                    reference = self.next(reference)
                if range_end != end:
                    values.append(reference)
            except _NoValue:
                pass
        return values


class _EveryGenerator(_Generator):
    def __init__(self, every: _Every, field: _Field) -> None:
        self.period = every.period
        inner = every.expression
        if isinstance(inner, _Between):
            self.start = max(field.low, _integer(inner.start))
            self.end = min(field.high, _integer(inner.end))
        elif isinstance(inner, _On):
            self.start, self.end = inner.time, field.high
        else:
            self.start, self.end = field.low, field.high
        self.offset = inner.time if isinstance(inner, _On) else self.start

    def next(self, reference: int) -> int:
        if reference >= self.end:
            raise _NoValue
        if reference >= self.offset:
            following = reference + (self.period - (reference - self.offset) % self.period)
        else:
            following = self.offset
        if following < self.start:
            return self.start
        if following > self.end:
            raise _NoValue
        return following

    def matches(self, value: int) -> bool:
        return (
            value >= self.offset
            and (value - self.offset) % self.period == 0
            and self.start <= value <= self.end
        )

    def inside(self, start: int, end: int) -> list[int]:
        values = []
        try:
            if start < self.offset < end:
                values.append(self.offset)
            reference = self.next(start)
            while reference < end:
                if reference != self.offset:
                    values.append(reference)
                reference = self.next(reference)
        except _NoValue:
            pass
        return values


class _AndGenerator(_Generator):
    def __init__(self, conjunction: _And, field: _Field) -> None:
        self.items = [_list_item_generator(item, field) for item in conjunction.items]

    def next(self, reference: int) -> int:
        found = []
        for item in self.items:
            with contextlib.suppress(_NoValue):
                found.append(item.next(reference))
        found = sorted(value for value in found if value >= 0)
        if not found:
            raise _NoValue
        return found[0]

    def matches(self, value: int) -> bool:
        return any(item.matches(value) for item in self.items)

    def inside(self, start: int, end: int) -> list[int]:
        return self._walk(start, end)


def _list_item_generator(item: _Expression, field: _Field) -> _Generator:
    """`AndFieldValueGenerator.createCandidateGeneratorInstance`."""
    match item:
        case _Always():
            return _AlwaysGenerator(field)
        case _Between():
            return _BetweenGenerator(item)
        case _Every():
            return _EveryGenerator(item, field)
        case _On():
            return _OnGenerator(item)
    raise _JavaThrows(f"{type(item).__name__.strip('_')} inside a list")


def _generator(expression: _Expression, field: _Field) -> _Generator:
    """`FieldValueGeneratorFactory.forCronField`."""
    match expression:
        case _Always():
            return _AlwaysGenerator(field)
        case _And():
            return _AndGenerator(expression, field)
        case _Between():
            return _BetweenGenerator(expression)
        case _Every():
            return _EveryGenerator(expression, field)
        case _On(special="NONE"):
            return _OnGenerator(expression)
        case _On():
            raise _JavaThrows(f"{expression.special} in the {field.name} field")
    return _NullGenerator()


class _NullGenerator(_Generator):
    def next(self, reference: int) -> int:
        raise _NoValue

    def matches(self, value: int) -> bool:
        return False

    def inside(self, start: int, end: int) -> list[int]:
        return []


def _time_values(expression: _Expression, field: _Field) -> list[int]:
    """What `ExecutionTimeBuilder` builds a `TimeNode` from; an empty one is refused there."""
    values = _generator(expression, field).candidates(field.low, field.high)
    if not values:
        raise QuartzError(f"the {field.name} field matches no value")
    return values


def _year_values(expression: _Expression) -> list[int]:
    """The years the search stops in: those the generator matches, in 1970-2099.

    cron-utils checks a year with `generateCandidates(y, y)`, which is `isMatch(y)`, and moves
    to the next with `generateNextValue`. Both are ported; the two agree for every expression the
    year field accepts, which the tests check.
    """
    generator = _generator(expression, _YEAR)
    return [year for year in range(_YEAR.low, _YEAR.high + 1) if generator.matches(year)]


# --- day of month ----------------------------------------------------------------------------


def _month_length(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _special_day(on: _On, year: int, month: int) -> int | None:
    """`OnDayOfMonthValueGenerator`: the one day `L`, `L-n`, `nW` or `LW` picks, if any."""
    length = _month_length(year, month)
    match on.special:
        case "L":
            value = length - max(on.nth, 0)
        case "W":
            if on.time > length:
                raise _JavaThrows(f"{on.time}W in a month of {length} days")
            weekday = calendar.weekday(year, month, on.time)  # Monday = 0
            if weekday == 5:
                value = 3 if on.time == 1 else on.time - 1
            elif weekday == 6 and on.time + 1 <= length:
                value = on.time + 1
            else:
                value = on.time
        case "LW":
            overshoot = calendar.weekday(year, month, length) + 1 - 5
            value = length - overshoot if overshoot > 0 else length
        case _:
            return None
    return value if 1 <= value <= length else None


def _days_of_month(
    expression: _Expression, years: list[int], months: list[int]
) -> dict[tuple[int, int], tuple[int, ...]]:
    """{(year, month): days} for every month the search can stop in."""
    if isinstance(expression, _On) and expression.special != "NONE":
        days: dict[tuple[int, int], tuple[int, ...]] = {}
        for year in years:
            for month in months:
                day = _special_day(expression, year, month)
                days[year, month] = (day,) if day is not None else ()
        return days
    by_length = _plain_days(expression)
    return {
        (year, month): by_length[_month_length(year, month)] for year in years for month in months
    }


def _plain_days(expression: _Expression) -> dict[int, tuple[int, ...]]:
    """{month length: days} for a day-of-month field without a special: `generateCandidates(1,
    length)`, which can only depend on the month through its length."""
    generator = _generator(expression, _DAY_OF_MONTH)
    return {length: tuple(generator.candidates(1, length)) for length in (28, 29, 30, 31)}


# --- day of week -----------------------------------------------------------------------------

_CRONITER_DAY = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")  # by Quartz number - 1


@dataclass(frozen=True)
class _Weekdays:
    """A day-of-week field as croniter can say it: plain days, `n#k` items, `nL` items."""

    plain: frozenset[int] = frozenset()  # Quartz numbers, 1 = Sunday
    nth: frozenset[tuple[int, int]] = frozenset()
    last: frozenset[int] = frozenset()

    def __or__(self, other: _Weekdays) -> _Weekdays:
        return _Weekdays(self.plain | other.plain, self.nth | other.nth, self.last | other.last)


_SATURDAY = 7


def _weekdays(expression: _Expression) -> _Weekdays:
    """`FieldValueGeneratorFactory.createDayOfWeekValueGeneratorInstance`, per expression kind."""
    match expression:
        case _On(time, "NONE"):
            return _Weekdays(plain=frozenset({time}))
        case _On(time, "L") if time == _DEFAULT:
            return _Weekdays(plain=frozenset({_SATURDAY}))  # "L by itself simply means SAT"
        case _On(time, "L"):
            return _Weekdays(last=frozenset({time}))
        case _On(time, "HASH", nth):
            return _Weekdays(nth=frozenset({(time, nth)}))
        case _On():
            return _Weekdays()
        case _Between(start, end):
            if isinstance(start, str) or isinstance(end, str):
                raise _JavaThrows("L as the end of a day-of-week range")
            if end >= start:
                return _Weekdays(plain=frozenset(range(start, end + 1)))
            return _Weekdays(plain=frozenset([*range(start, 8), *range(1, end + 1)]))
        case _Every():
            generator = _EveryGenerator(expression, _DAY_OF_WEEK)
            days = frozenset(range(generator.start, generator.end + 1, expression.period))
            if not days:
                raise _JavaThrows("a day-of-week step that matches no day")
            return _Weekdays(plain=days)
        case _And(items):
            found = _Weekdays()
            for item in items:
                found |= _weekdays(item)
            return found
        case _Always():
            return _Weekdays(plain=frozenset(range(1, 8)))
    return _Weekdays()  # `?` in a list: cron-utils' NullFieldValueGenerator, which adds nothing


# --- the croniter patterns -------------------------------------------------------------------


def _join(values: list[int] | tuple[int, ...], everything: range) -> str:
    return "*" if list(values) == list(everything) else ",".join(map(str, values))


def _years(values: list[int] | tuple[int, ...]) -> str:
    """The year field. croniter reads every year from 1970 to 2099 - written out or as a range -
    as `*`, and runs on past 2099 where cron-utils stops; `_Part.closest` cuts that off."""
    return _join(values, range(_YEAR.low, _YEAR.high + 1))


class _Part:
    """One Quartz pattern without `|`: the croniter patterns whose union it is.

    The croniter objects are built once and reused: building one is most of the cost, and a
    `W` or `LW` pattern becomes some thirty of them. croniter keeps its position inside the
    object, so the reuse is under a lock.
    """

    __slots__ = ("_iterators", "_lock", "patterns")

    def __init__(self, patterns: tuple[str, ...]) -> None:
        # Imported here, never at module level - as `CronSchedule` always did. croniter calls
        # `platform.architecture()` on import, which runs `file` as a subprocess; importing it
        # with the module would do that in every process that imports ekbatan, and under gevent
        # one subprocess is enough to stop `multiprocessing` from seeing its children exit.
        from croniter import croniter

        self.patterns = patterns
        self._iterators = [
            croniter(pattern, _EPOCH, second_at_beginning=True, max_years_between_matches=130)
            for pattern in patterns
        ]
        self._lock = threading.Lock()

    def closest(self, start: datetime) -> datetime | None:
        """cron-utils' `nextClosestMatch`: the earliest match at or after `start`.

        A start that matches is returned as it is, fraction and all. The 100-year limit is
        cron-utils' own.
        """
        from croniter import CroniterBadDateError

        whole = start.replace(microsecond=0)
        before = _shift(whole, -1)
        found = []
        with self._lock:
            for iterator in self._iterators:
                with contextlib.suppress(CroniterBadDateError):
                    found.append(iterator.get_next(datetime, start_time=before))
        if not found:
            return None
        earliest = min(found)
        if earliest.year > _YEAR.high:
            return None
        if earliest == whole:
            return start
        if earliest.year - start.year > 100:
            return None
        return earliest

    def next_after(self, start: datetime) -> datetime | None:
        """cron-utils' `SingleExecutionTime.nextExecution`."""
        following = self.closest(start)
        if following is not None and following == start:
            following = self.closest(_shift(start, 1))
        return following


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _shift(moment: datetime, seconds: int) -> datetime:
    """Plus or minus whole seconds of real time, as Java's `plusSeconds`, kept in the zone."""
    return (moment.astimezone(UTC) + timedelta(seconds=seconds)).astimezone(moment.tzinfo)


def _part(fields: tuple[_Expression, ...]) -> _Part:
    second, minute, hour, day_of_month, month, day_of_week, year = fields
    seconds = _join(_time_values(second, _SECOND), range(60))
    minutes = _join(_time_values(minute, _MINUTE), range(60))
    hours = _join(_time_values(hour, _HOUR), range(24))
    month_values = _time_values(month, _MONTH)
    years = _year_values(year)
    clock = f"{seconds} {minutes} {hours}"
    patterns: list[str] = []
    if isinstance(day_of_month, _Question):
        weekdays = _weekdays(day_of_week)
        tail = f"{_join(month_values, range(1, 13))}"
        year_text = _years(years)
        if years:
            if weekdays.plain:
                names = ",".join(_CRONITER_DAY[d - 1] for d in sorted(weekdays.plain))
                if len(weekdays.plain) == 7:
                    names = "*"
                patterns.append(f"{clock} * {tail} {names} {year_text}")
            for day, nth in sorted(weekdays.nth):
                if nth <= 5:  # a sixth or seventh such weekday never exists
                    patterns.append(f"{clock} * {tail} {_CRONITER_DAY[day - 1]}#{nth} {year_text}")
            for day in sorted(weekdays.last):
                patterns.append(f"{clock} * {tail} L{day - 1} {year_text}")
    elif years:
        months = _join(month_values, range(1, 13))
        if not (isinstance(day_of_month, _On) and day_of_month.special != "NONE"):
            by_length = _plain_days(day_of_month)
            every = by_length[31]
            if all(by_length[n] == tuple(d for d in every if d <= n) for n in (28, 29, 30)):
                # The same days in every month, as far as each month goes: one pattern.
                if every:
                    patterns.append(
                        f"{clock} {_join(every, range(1, 32))} {months} * {_years(years)}"
                    )
                return _Part(tuple(patterns))
        days = _days_of_month(day_of_month, years, month_values)
        # Group the months by (days, years) so that the patterns stay few and short.
        by_month: dict[tuple[tuple[int, ...], tuple[int, ...]], list[int]] = {}
        for month_value in month_values:
            by_days: dict[tuple[int, ...], list[int]] = {}
            for year_value in years:
                by_days.setdefault(days[year_value, month_value], []).append(year_value)
            for day_tuple, year_list in by_days.items():
                if day_tuple:
                    by_month.setdefault((day_tuple, tuple(year_list)), []).append(month_value)
        for (day_tuple, year_tuple), month_list in by_month.items():
            patterns.append(
                f"{clock} {_join(day_tuple, range(1, 32))} {_join(month_list, range(1, 13))} * "
                f"{_years(year_tuple)}"
            )
    return _Part(tuple(patterns))


# --- the public face -------------------------------------------------------------------------


@dataclass(frozen=True)
class QuartzCron:
    """A QUARTZ pattern, compiled: the parts of a `||` or `|`, each a union of croniter patterns."""

    parts: tuple[_Part, ...]

    @property
    def croniter_patterns(self) -> tuple[tuple[str, ...], ...]:
        """What croniter is given, per part. For tests and for reading, not for parsing back."""
        return tuple(part.patterns for part in self.parts)

    def next_after(self, start: datetime) -> datetime | None:
        """The next time after `start`, an aware datetime in the schedule's zone; None if none.

        cron-utils' `CompositeExecutionTime`: the earliest of the parts, each computed on its
        own - which is not quite the same as the union, because each part keeps or drops the
        start's fraction by its own rule.
        """
        found = [t for t in (part.next_after(start) for part in self.parts) if t is not None]
        return min(found) if found else None


def compile_quartz(pattern: str) -> QuartzCron:
    """Read `pattern` as cron-utils' QUARTZ does; raise `QuartzError` where it would not run."""
    if not _ALLOWED.fullmatch(pattern):
        raise QuartzError("only printable ASCII is accepted")
    try:
        return QuartzCron(tuple(_part(fields) for fields in _parse(pattern)))
    except _JavaThrows as exc:
        raise QuartzError(
            f"Java's cron-utils accepts this but throws when computing a time ({exc}), "
            "so it is refused here up front"
        ) from exc
