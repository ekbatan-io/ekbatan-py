"""Area G twin probe: clocks. Twin: ClockParityProbeTest.java (Java's Clock.systemUTC() and
ekbatan-test-support's VirtualClock)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from ekbatan.clock import SystemClock, VirtualClock, to_storage


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

sysc = SystemClock()
reads = [sysc.now() for _ in range(2000)]
p("system.maxFractionDigits", 6)  # datetime holds microseconds, never more
p("system.subMicroNonZero", "n/a (datetime holds microseconds)")
p("system.nearWallClock", abs((reads[0] - datetime.now(UTC)).total_seconds()) < 1)
p("system.zoneUtc", reads[0].utcoffset() == timedelta(0))

v = VirtualClock()
a = v.now()
b = v.now()
p("virtual.default.realTime", abs((a - datetime.now(UTC)).total_seconds()) < 1)
p("virtual.default.advance", err(lambda: v.advance(timedelta(seconds=1))))
v.pause_at(T0)
p("virtual.pauseAt.read", iso(v.now()))
p("virtual.pauseAt.twoReadsEqual", v.now() == v.now())
v.advance(timedelta(milliseconds=1500))
p("virtual.advance1500ms", iso(v.now()))
v.advance(timedelta(microseconds=1))
p("virtual.advance1us", iso(v.now()))
p("virtual.advanceNegative", err(lambda: (v.advance(timedelta(seconds=-10)), iso(v.now()))[1]))
v.pause_at(datetime(2025, 1, 1, 2, 0, tzinfo=timezone(timedelta(hours=2))))
p("virtual.pauseAt.offsetInput", iso(v.now()))
v.resume_from(T0)
r = v.now()
p("virtual.resumeFrom.nearAnchor", abs((r - T0).total_seconds()) < 1)
p("virtual.resumeFrom.advance", err(lambda: v.advance(timedelta(seconds=1))))
v.pause()
x = v.now()
p("virtual.pause.frozen", x == v.now())
v.resume()
p("virtual.resume.realTime", abs((v.now() - datetime.now(UTC)).total_seconds()) < 1)

# "start the clock at an instant": Python VirtualClock(instant); Java has no such constructor,
# the nearest is new VirtualClock(Clock.fixed(instant, UTC)).
c = VirtualClock(T0)
p("virtual.constructedAtInstant.read", iso(c.now()))
p("virtual.constructedAtInstant.advance", err(lambda: (c.advance(timedelta(seconds=1)), iso(c.now()))[1]))
p("virtual.nanosInstant", "n/a (datetime holds microseconds)")
p("toStorage.plus2", iso(to_storage(datetime(2025, 1, 1, 2, 0, tzinfo=timezone(timedelta(hours=2))))))
