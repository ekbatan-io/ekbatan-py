"""Record what Java db-scheduler computes for QUARTZ patterns, for `tests/unit/test_quartz_oracle.py`.

Runs every case through `QuartzOracle` (cron-utils 9.2.1, `CronType.QUARTZ`, chained as
db-scheduler chains it) and writes the answers to `tests/unit/quartz_cases/*.jsonl.gz`, so the
tests need no Java. Five suites:

- catalogue - `quartz_cases/catalogue.txt`, written by hand, one feature per section, each
  pattern from many starts (fractions, leap days, month ends, 1971, 2099);
- zones     - the same kind of patterns in zones with odd offsets, away from clock changes;
- sweeps    - every value, range and step of every field, one field at a time;
- calendar  - every day-of-month and day-of-week special, over a full 28-year calendar cycle;
- fuzz      - random patterns from a grammar that mixes valid and broken pieces, seeded.

And `probes`: for every pattern above, whether Java throws while computing from the first of some
month - the evidence behind refusing a pattern because Java would throw on it.

    uv run python scripts/quartz-oracle/generate_fixtures.py           # write
    uv run python scripts/quartz-oracle/generate_fixtures.py --check   # compare with what is written

Each case records Java's outcome: refused (`ERR`), throws while computing (`RUNERR`, with the
times it gave before), or the times (`OK`; `END` when cron-utils ran out). Times are kept as a
digest plus the first one, which is enough to compare exactly and small enough to commit.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORACLE = HERE / "run.sh"
CASES = HERE.parent.parent / "tests" / "unit" / "quartz_cases"

# --- starts ----------------------------------------------------------------------------------

CATALOGUE_STARTS = [
    "2026-10-12T13:00:00Z",  # a Monday afternoon
    "2026-10-12T12:00:00.300Z",  # a fraction, on a second many patterns match
    "2026-10-12T12:00:00Z",  # exactly on a match
    "2027-02-26T23:59:59Z",  # end of a short February
    "2028-02-27T12:00:00Z",  # into a leap day
    "2024-02-29T12:00:00.999Z",  # on a leap day, with a fraction
    "2026-12-31T23:59:58Z",  # the year rolls over
    "2029-06-30T00:00:00Z",
    "2030-03-01T00:00:00Z",
    "2031-08-15T06:30:30.250Z",
    "2026-03-31T12:00:00Z",  # a 31st, into a 30-day month
    "2027-05-31T00:00:00Z",
    "2028-12-31T12:00:00Z",
    "2098-12-31T23:00:00Z",  # runs out at 2099
    "2099-12-31T23:59:59Z",
    "1971-01-01T00:00:00Z",  # the 100-year limit
    "1985-06-15T00:00:00Z",
    *[f"2026-11-0{d}T00:00:00Z" for d in range(2, 9)],  # one start on each weekday
]
CATALOGUE_COUNT = 12

ZONES = ["Europe/Amsterdam", "America/New_York", "Asia/Kolkata", "Asia/Tehran",
         "Australia/Lord_Howe", "Pacific/Chatham", "America/St_Johns", "Asia/Kathmandu"]
# Mid-winter and mid-summer, so that six runs of a daily pattern never meet a clock change.
ZONE_STARTS = ["2026-01-10T10:20:30.400Z", "2026-07-10T03:00:00Z", "2027-01-15T22:00:00Z"]
ZONE_COUNT = 6

# 1970 too: a pattern that only runs in years already past never reaches the point where Java
# throws, from a start today - and the tests want to see the throw before they accept a refusal.
FUZZ_STARTS = ["2026-10-12T13:00:00Z", "2028-02-27T12:00:00.300Z", "2098-12-30T00:00:00Z",
               "1970-01-01T00:00:00Z"]
FUZZ_COUNT = 6
FUZZ_PATTERNS = 8000


# --- the oracle ------------------------------------------------------------------------------


def canonical(iso: str) -> str:
    """A Java ISO time as the UTC instant both sides print: microseconds, `Z`."""
    return datetime.fromisoformat(iso).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def digest(times: list[str]) -> str:
    return hashlib.sha256("\n".join(times).encode()).hexdigest()[:20]


def escape(pattern: str) -> str:
    return pattern.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def run_oracle(rows: list[tuple[str, str, str, int]]) -> list[dict]:
    text = "".join(f"{escape(p)}\t{z}\t{s}\t{n}\n" for p, z, s, n in rows)
    lines = subprocess.run(
        [str(ORACLE)], input=text, capture_output=True, text=True, check=True
    ).stdout.split("\n")
    out = []
    for line in lines[: len(rows)]:
        fields = line.split("\t")
        # Only the exception's class is kept: its message is not stable from run to run (the
        # JVM drops messages of exceptions it throws often, and `||` parts run in parallel).
        if fields[0] == "ERR":
            out.append({"java": "ERR", "why": fields[1]})
            continue
        if fields[0] == "RUNERR":
            times = [canonical(t) for t in fields[2:]]
            out.append({"java": "RUNERR", "why": fields[1].split(":")[0], "n": len(times),
                        "digest": digest(times)})
            continue
        times = [canonical(t) for t in fields[1:]]
        out.append({"java": fields[0], "n": len(times), "digest": digest(times),
                    "first": times[0] if times else None})
    return out


# --- suites ----------------------------------------------------------------------------------


def catalogue() -> list[tuple[str, str, str, int]]:
    patterns = []
    for line in (CASES / "catalogue.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            patterns.append(line.rstrip("\n"))
    return [(p, "UTC", s, CATALOGUE_COUNT) for p in patterns for s in CATALOGUE_STARTS]


ZONE_PATTERNS = [
    "0 0 12 * * ?", "0 30 9 ? * MON-FRI", "0 15 10 ? * 6L", "0 0 12 L * ?", "0 0 12 LW * ?",
    "0 0 12 15W * ?", "0 0 18 ? * 2#1", "0 0 12 1 JAN,JUL ?", "0 */20 9-17 * * ?",
    "30 45 11 L-2 * ?", "0 0 12 ? * 1", "0 0 12 ? * 7", "0 0 12 1 * ? 2026", "0 0 12 ? * L",
    "0 0 20 1/5 * ?", "0 0 9 ? * 2-6/2", "0 0 12 ? * MON || 0 0 13 ? * TUE",
]


def zones() -> list[tuple[str, str, str, int]]:
    return [(p, z, s, ZONE_COUNT) for p in ZONE_PATTERNS for z in ZONES for s in ZONE_STARTS]


def sweeps() -> list[tuple[str, str, str, int]]:
    """Each field on its own, inside a window that one run of the field fits in."""
    rows: list[tuple[str, str, str, int]] = []

    def forms(low: int, high: int, names: list[str] = ()) -> list[str]:
        values = list(range(low, high + 1))
        out = [str(v) for v in [low - 1, high + 1, *values]]
        out += [f"{a}-{b}" for a in values for b in values]
        out += [f"{a}/{b}" for a in values for b in range(high - low + 3)]
        out += ["*", "*/1", "/5", "*/0", f"*/{high - low + 1}", f"*/{high - low + 2}"]
        rng = random.Random(f"{low}-{high}")
        for _ in range(400):
            a, b = rng.choice(values), rng.choice(values)
            out.append(f"{a}-{b}/{rng.randint(1, high - low + 1)}")
        for _ in range(300):
            k = rng.randint(2, 4)
            out.append(",".join(rng.choice([str(rng.choice(values)),
                                            f"{rng.choice(values)}-{rng.choice(values)}",
                                            f"{rng.choice(values)}/{rng.randint(1, 9)}"])
                                for _ in range(k)))
        out += names
        return out

    # seconds in full; minutes run through the same code with the same range, so a sample
    rows += [(f"{f} 0 0 1 1 ? 2030", "UTC", "2029-12-31T23:59:59Z", 61) for f in forms(0, 59)]
    minutes = forms(0, 59)
    rows += [(f"0 {f} 0 1 1 ? 2030", "UTC", "2029-12-31T23:59:59Z", 61)
             for f in random.Random("minutes").sample(minutes, 900)]
    rows += [(f"0 0 {f} 1 1 ? 2030", "UTC", "2029-12-31T23:59:59Z", 25) for f in forms(0, 23)]
    months = ["JAN", "FEB", "DEC", "jan", "JAN-MAR", "NOV-FEB", "JAN/3", "MAR-DEC/2", "FEB,AUG",
              "JUN-JUN", "MAR-FEB"]
    rows += [(f"0 0 0 1 {f} ? 2030", "UTC", "2029-12-31T23:59:59Z", 13)
             for f in forms(1, 12, months)]
    rng = random.Random("years")
    years = ["1969", "1970", "2099", "2100", "*", "2026", "2026-2030", "2030-2026", "2026/4",
             "*/10", "1970/130", "1970/131", "2026,2028,2031", "2026-2040/3", "2040-2026/3",
             "2026-2030,2050", "2099-2099", "*/1", "/7", "2026/0"]
    years += [f"{rng.randint(1965, 2105)}-{rng.randint(1965, 2105)}" for _ in range(150)]
    years += [f"{rng.randint(1970, 2099)}/{rng.randint(1, 40)}" for _ in range(150)]
    rows += [(f"0 0 0 1 1 ? {f}", "UTC", "1970-01-01T00:00:00Z", 131) for f in years]
    # day of month, plain: from 20 January 2028, through a leap February and into the next
    # year's short one for the sparser patterns
    rows += [(f"0 0 0 {f} * ?", "UTC", "2028-01-20T00:00:00Z", 60) for f in forms(1, 31)]
    # day of week, plain: twelve weeks
    days = ["SUN", "MON", "SAT", "mon", "MON-FRI", "FRI-MON", "SAT-SUN", "SUN-SAT", "MON/2",
            "MON-FRI/2", "FRI-MON/2", "TUE,THU", "SUN,SAT"]
    rows += [(f"0 0 0 ? * {f}", "UTC", "2026-10-12T00:00:00Z", 90) for f in forms(1, 7, days)]
    return rows


def calendar_suite() -> list[tuple[str, str, str, int]]:
    """The specials, over 2026-2053: a full cycle of weekdays and leap years."""
    specials = ["L", "LW", "15L", "L-0", "L-32"]
    specials += [f"L-{n}" for n in range(1, 32)]
    specials += [f"{n}W" for n in range(33)]
    specials += [f"{n}L" for n in (1, 15, 28, 31)]
    specials += ["LW,15", "L,1", "L-3,15W", "15W,16W", "1W-5", "W", "LW5", "5LW", "1WW", "LWLW"]
    rows = [(f"0 0 12 {f} * ?", "UTC", "2026-01-01T00:00:00Z", 400) for f in specials]
    # a W that never meets a short month: only long months, or only leap Februaries
    rows += [(f"0 0 12 {n}W {m} ?", "UTC", "2026-01-01T00:00:00Z", 400)
             for n in (29, 30, 31) for m in ("1,3,5,7,8,10,12", "1", "4,6,9,11", "2")]
    rows += [(f"0 0 12 29W 2 ? {y}", "UTC", "2026-01-01T00:00:00Z", 30)
             for y in ("2028", "2028,2032,2036", "2028/4", "2027")]
    weekdays = [f"{d}#{k}" for d in range(9) for k in range(9)]
    weekdays += [f"{d}L" for d in range(9)]
    weekdays += ["L", "L-1", "L-7", "L-8", "MONL", "SATL", "SUNL", "MON#2", "SUN#5", "FRI#3,MON#1",
                 "6L,2", "6L,6#1", "2#1,2#3,2#5", "1-7", "L,2", "?,2", "6#3#2", "2/3,6L"]
    rows += [(f"0 0 12 ? * {f}", "UTC", "2026-01-01T00:00:00Z", 400) for f in weekdays]
    return rows


# --- fuzz ------------------------------------------------------------------------------------

GARBAGE = ["", "-", "/", ",", "#", "X", "5-", "-5", "/5", "5/", "1-5/2/3", "1--5", "1-5-7", "*/",
           "**", "?*", "L/2", "5-L", "L-", "1-?", "+5", "05", "007", "1,,2", ",1", "5#", "#5",
           "99999999999", "1-5/", "MONDAY", "M", "?5", "5?", "L5", "5LL", "W5", "5W5", "LWLW"]


def fuzz_field(rng: random.Random, low: int, high: int, names: list[str], extra: list[str]) -> str:
    def value() -> str:
        roll = rng.random()
        if roll < 0.03:
            return str(rng.choice([low - 1, high + 1, 0, 60, 100]))
        if names and roll < 0.25:
            name = rng.choice(names)
            return name.lower() if rng.random() < 0.2 else name
        return str(rng.randint(low, high))

    def item() -> str:
        roll = rng.random()
        if roll < 0.30:
            return value()
        if roll < 0.45:
            return f"{value()}-{value()}"
        if roll < 0.55:
            return f"{value()}/{rng.randint(1, high - low + 1) if rng.random() < 0.9 else 0}"
        if roll < 0.62:
            return f"*/{rng.randint(1, high - low + 1) if rng.random() < 0.9 else 0}"
        if roll < 0.70:
            return f"{value()}-{value()}/{rng.randint(1, 9)}"
        if roll < 0.75:
            return "*"
        if extra and roll < 0.95:
            return rng.choice(extra)()
        if roll < 0.97:
            return value()
        return rng.choice(GARBAGE)

    roll = rng.random()
    if roll < 0.2:
        return "*"
    if roll < 0.8:
        return item()
    return ",".join(item() for _ in range(rng.randint(2, 4)))


def fuzz_pattern(rng: random.Random) -> str:
    day_names = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
    month_names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV",
                   "DEC"]
    dom_extra = [lambda: "L", lambda: f"L-{rng.randint(0, 32)}", lambda: f"{rng.randint(0, 32)}W",
                 lambda: "LW", lambda: f"{rng.randint(1, 31)}L", lambda: "?"]
    dow_extra = [lambda: "L", lambda: f"{rng.randint(0, 8)}L", lambda: f"{rng.randint(0, 8)}#{rng.randint(0, 7)}",
                 lambda: f"{rng.choice(day_names)}#{rng.randint(1, 6)}", lambda: f"{rng.choice(day_names)}L",
                 lambda: f"L-{rng.randint(0, 8)}", lambda: "?"]
    fields = [
        fuzz_field(rng, 0, 59, [], []) if rng.random() < 0.5 else str(rng.randint(0, 59)),
        fuzz_field(rng, 0, 59, [], []) if rng.random() < 0.5 else str(rng.randint(0, 59)),
        fuzz_field(rng, 0, 23, [], []),
        fuzz_field(rng, 1, 31, [], dom_extra),
        fuzz_field(rng, 1, 12, month_names, []),
        fuzz_field(rng, 1, 7, day_names, dow_extra),
    ]
    roll = rng.random()
    if roll < 0.92:  # mostly well-formed: exactly one of the day fields is ?
        if rng.random() < 0.5:
            fields[3] = "?"
        else:
            fields[5] = "?"
    if rng.random() < 0.25:
        fields.append(rng.choice(["2026", "2026-2030", "*", "2027/2", "2026,2028", "2030-2026",
                                  "1969", "2100", "2027-2099/5", str(rng.randint(1965, 2105))]))
    if rng.random() < 0.03:
        fields = fields[: rng.choice([5, 8])] if rng.random() < 0.5 else [*fields, "0"]
    separator = rng.choice([" "] * 12 + ["  ", "\t", " \t "])
    pattern = separator.join(fields)
    if rng.random() < 0.05:
        pattern = rng.choice([" ", "  "]) + pattern + rng.choice(["", " ", "\t"])
    if rng.random() < 0.04:
        pattern = pattern + " || " + fuzz_pattern(rng)
    if rng.random() < 0.02:
        words = pattern.split(" ")
        i = rng.randrange(len(words))
        words[i] = f"{words[i]}|{rng.choice(['0', '30', '*', 'MON', '5'])}"
        pattern = " ".join(words)
    return pattern


def fuzz() -> list[tuple[str, str, str, int]]:
    rng = random.Random(20261016)
    patterns = []
    seen = set()
    while len(patterns) < FUZZ_PATTERNS:
        pattern = fuzz_pattern(rng)
        if pattern not in seen and "\n" not in pattern:
            seen.add(pattern)
            patterns.append(pattern)
    return [(p, "UTC", s, FUZZ_COUNT) for p in patterns for s in FUZZ_STARTS]


# The first of every month of a common year, and a leap February: from one of these, a pattern
# Java throws on meets the month it throws in.
PROBE_STARTS = [f"2027-{m:02d}-01T00:00:00Z" for m in range(1, 13)] + ["2028-02-01T00:00:00Z"]


def probes() -> list[str]:
    """For every pattern of every suite: does Java throw while computing, from some start?

    The tests hold a Python refusal of the "Java throws" kind to this, and an accepted pattern
    to its opposite. First from the PROBE_STARTS; then, for a pattern Python refuses that way and
    Java has not yet thrown on, from the first of every month 1970-2099 - a pattern pinned to
    2026, or one that runs every second and so never leaves the day it starts in, only meets the
    month Java throws in from a start in or just before it. Python only says where to look;
    whether Java throws is Java's answer.
    """
    sys.path.insert(0, str(HERE.parent.parent / "src"))
    from db_scheduler.task.schedule._quartz import QuartzError, compile_quartz

    patterns = sorted({row[0] for name in SUITES for row in SUITES[name]()})
    rows = [(p, "UTC", s, 3) for p in patterns for s in PROBE_STARTS]
    throws: dict[str, bool] = dict.fromkeys(patterns, False)
    for (pattern, *_), answer in zip(rows, run_oracle(rows), strict=True):
        throws[pattern] |= answer["java"] == "RUNERR"

    def refused_as_throws(pattern: str) -> bool:
        try:
            compile_quartz(pattern)
        except QuartzError as exc:
            return "throws when computing" in str(exc)
        return False

    every_month = [f"{y}-{m:02d}-01T00:00:00Z" for y in range(1970, 2100) for m in range(1, 13)]
    unproven = [p for p in patterns if not throws[p] and refused_as_throws(p)]
    rows = [(p, "UTC", s, 2) for p in unproven for s in every_month]
    for (pattern, *_), answer in zip(rows, run_oracle(rows), strict=True):
        throws[pattern] |= answer["java"] == "RUNERR"
    return [json.dumps({"pattern": p, "throws": throws[p]}, sort_keys=True) for p in patterns]


SUITES = {"catalogue": catalogue, "zones": zones, "sweeps": sweeps, "calendar": calendar_suite,
          "fuzz": fuzz}


def build(name: str) -> list[str]:
    if name == "probes":
        return probes()
    rows = SUITES[name]()
    lines = []
    for (pattern, zone, start, count), answer in zip(rows, run_oracle(rows), strict=True):
        lines.append(json.dumps({"pattern": pattern, "zone": zone, "start": start,
                                 "count": count, **answer}, sort_keys=True))
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="compare, do not write")
    parser.add_argument("suites", nargs="*", default=[*SUITES, "probes"])
    args = parser.parse_args()
    status = 0
    for name in args.suites:
        lines = build(name)
        target = CASES / f"{name}.jsonl.gz"
        content = ("\n".join(lines) + "\n").encode()
        if args.check:
            current = gzip.decompress(target.read_bytes()) if target.exists() else b""
            same = current == content
            print(f"{name}: {len(lines)} cases, {'same' if same else 'DIFFERENT'}")
            status |= not same
        else:
            # mtime=0: the same cases give the same bytes, so git sees no change.
            target.write_bytes(gzip.compress(content, mtime=0))
            print(f"{name}: {len(lines)} cases -> {target.relative_to(HERE.parent.parent)}")
    return status


if __name__ == "__main__":
    sys.exit(main())
