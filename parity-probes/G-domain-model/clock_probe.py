"""Area G twin probe: clocks. Twin: ClockParityProbeTest.java (Java's Clock.systemUTC() and
ekbatan-test-support's VirtualClock).

Updated for EKB-0426 (`now()` renamed `instant()`) and EKB-0384 (no conversion: the default
clock is naive UTC, `to_storage` is gone). `clock.python.out` is the run recorded when the probe
was written; the re-run of 2026-10-22 is `clock.python.rerun-2026-10-22.out`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from ekbatan.clock import SystemClock, VirtualClock


def p(key, value):
    print(f"PROBE {key}: {value}")


def err(fn):
    try:
        return f"OK {fn()}"
    except Exception as e:  # noqa: BLE001
        return f"ERR {type(e).__name__}: {e}"


def iso(d):
    if d.tzinfo is not None:
        d = d.astimezone(UTC).replace(tzinfo=None)
    return d.isoformat() + "Z"


T0 = datetime(2025, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)


def utc_now():
    """The wall clock as the default clock gives it since EKB-0384: UTC digits, no zone."""
    return datetime.now(UTC).replace(tzinfo=None)


def since(d, other):
    """Seconds between two readings, each compared in its own sort (naive or aware)."""
    if (d.tzinfo is None) != (other.tzinfo is None):
        other = other.replace(tzinfo=None) if d.tzinfo is None else other.replace(tzinfo=UTC)
    return abs((d - other).total_seconds())

sysc = SystemClock()
reads = [sysc.instant() for _ in range(2000)]
p("system.maxFractionDigits", 6)  # datetime holds microseconds, never more
p("system.subMicroNonZero", "n/a (datetime holds microseconds)")
p("system.nearWallClock", since(reads[0], utc_now()) < 1)
p("system.zoneUtc", "naive UTC digits (EKB-0384)" if reads[0].tzinfo is None else reads[0].utcoffset() == timedelta(0))

v = VirtualClock()
a = v.instant()
b = v.instant()
p("virtual.default.realTime", since(a, utc_now()) < 1)
p("virtual.default.advance", err(lambda: v.advance(timedelta(seconds=1))))
v.pause_at(T0)
p("virtual.pauseAt.read", iso(v.instant()))
p("virtual.pauseAt.twoReadsEqual", v.instant() == v.instant())
v.advance(timedelta(milliseconds=1500))
p("virtual.advance1500ms", iso(v.instant()))
v.advance(timedelta(microseconds=1))
p("virtual.advance1us", iso(v.instant()))
p("virtual.advanceNegative", err(lambda: (v.advance(timedelta(seconds=-10)), iso(v.instant()))[1]))
v.pause_at(datetime(2025, 1, 1, 2, 0, tzinfo=timezone(timedelta(hours=2))))
p("virtual.pauseAt.offsetInput", iso(v.instant()))
v.resume_from(T0)
r = v.instant()
p("virtual.resumeFrom.nearAnchor", since(r, T0) < 1)
p("virtual.resumeFrom.advance", err(lambda: v.advance(timedelta(seconds=1))))
v.pause()
x = v.instant()
p("virtual.pause.frozen", x == v.instant())
v.resume()
p("virtual.resume.realTime", since(v.instant(), utc_now()) < 1)

# "start the clock at an instant": Python VirtualClock(instant); Java has no such constructor,
# the nearest is new VirtualClock(Clock.fixed(instant, UTC)).
c = VirtualClock(T0)
p("virtual.constructedAtInstant.read", iso(c.instant()))
p("virtual.constructedAtInstant.advance", err(lambda: (c.advance(timedelta(seconds=1)), iso(c.instant()))[1]))
p("virtual.nanosInstant", "n/a (datetime holds microseconds)")
p("toStorage.plus2", "n/a (to_storage removed by EKB-0384: the framework converts no time)")
