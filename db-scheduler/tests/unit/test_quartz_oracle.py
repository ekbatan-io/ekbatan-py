"""`CronStyle.QUARTZ` against what Java db-scheduler computes, case by recorded case.

`quartz_cases/*.jsonl.gz` hold the answers of cron-utils 9.2.1 itself - Java db-scheduler's cron
engine - for 56,477 cases: a hand-written catalogue, every value, range and step of
every field, every day-of-month and day-of-week special over a full 28-year calendar cycle, odd
time zones, and random patterns. They were recorded by `scripts/quartz-oracle/
generate_fixtures.py`, which runs the Java; these tests need none.

For each case the Python side must do what Java does:

- Java refuses the pattern -> Python refuses it, and not with "Java throws later" as the reason.
- Java throws while computing a time from it -> Python refuses it up front, saying so.
- Java gives times -> Python gives the same instants, to the microsecond, chained the same way -
  unless Python refused the pattern because Java throws on it from some other start, which the
  recorded cases must then show.

`probes.jsonl.gz` adds, for every pattern, whether Java throws when started on the first of any
month of a common year or a leap February. A refusal "because Java throws" needs that evidence;
an accepted pattern must have none.
"""

from __future__ import annotations

import gzip
import json
from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from db_scheduler.task.schedule._quartz import QuartzError, compile_quartz

CASES = Path(__file__).parent / "quartz_cases"
THROWS = "throws when computing"

Case = dict[str, Any]


def load(suite: str) -> dict[str, list[Case]]:
    by_pattern: dict[str, list[Case]] = defaultdict(list)
    with gzip.open(CASES / f"{suite}.jsonl.gz", "rt") as lines:
        for line in lines:
            case = json.loads(line)
            by_pattern[case["pattern"]].append(case)
    return dict(by_pattern)


def canonical(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def load_probes() -> dict[str, bool]:
    with gzip.open(CASES / "probes.jsonl.gz", "rt") as lines:
        return {case["pattern"]: case["throws"] for case in map(json.loads, lines)}


PROBES = load_probes()


def run(pattern: str, cases: list[Case], throws: bool | None = None) -> list[str]:
    """Every way the Python side disagrees with the recorded Java answers for one pattern.

    `throws`: whether Java throws while computing from the first of some month (the probes).
    """
    if throws is None:
        throws = PROBES[pattern]
    throws = throws or any(case["java"] == "RUNERR" for case in cases)
    try:
        compiled = compile_quartz(pattern)
        refusal = None
    except QuartzError as exc:
        compiled, refusal = None, str(exc)
    problems = []
    if refusal is not None and THROWS in refusal and not throws:
        problems.append(f"refused as Java-throws, but Java never threw: {refusal}")
    if refusal is None and throws:
        problems.append("Java throws while computing from some month, Python accepts")
    for case in cases:
        where = f"from {case['start']} in {case['zone']}"
        if case["java"] == "ERR":
            if refusal is None:
                problems.append(f"Java refuses ({case['why']}), Python accepts")
            elif THROWS in refusal:
                problems.append(f"Java refuses outright ({case['why']}), Python: {refusal}")
            continue
        if case["java"] == "RUNERR":
            if refusal is None or THROWS not in refusal:
                problems.append(f"Java throws {where} ({case['why']}), Python: {refusal}")
            continue
        if refusal is not None:
            if THROWS not in refusal:
                problems.append(f"Java gives times {where}, Python refuses: {refusal}")
            continue
        assert compiled is not None
        moment: datetime | None = datetime.fromisoformat(case["start"]).astimezone(
            ZoneInfo(case["zone"])
        )
        times = []
        for _ in range(case["count"]):
            assert moment is not None
            moment = compiled.next_after(moment)
            if moment is None:
                break
            times.append(canonical(moment))
        status = "OK" if len(times) == case["count"] else "END"
        digest = sha256("\n".join(times).encode()).hexdigest()[:20]
        if (status, len(times), digest) != (case["java"], case["n"], case["digest"]):
            problems.append(
                f"{where}: Java {case['java']} {case['n']} times starting {case.get('first')}; "
                f"Python {status} {len(times)} times: {times[:4]}"
            )
    return problems


def check(chunk: list[tuple[str, list[Case]]]) -> None:
    failures = []
    for pattern, cases in chunk:
        failures += [f"{pattern!r}: {problem}" for problem in run(pattern, cases)]
    assert not failures, f"{len(failures)} disagreements:\n" + "\n".join(failures[:40])


def chunks(suite: str, size: int) -> list[list[tuple[str, list[Case]]]]:
    items = sorted(load(suite).items())
    return [items[i : i + size] for i in range(0, len(items), size)]


CATALOGUE = sorted(load("catalogue").items())


@pytest.mark.parametrize(("pattern", "cases"), CATALOGUE, ids=[repr(p) for p, _ in CATALOGUE])
def test_the_catalogue(pattern: str, cases: list[Case]) -> None:
    """Hand-written, one feature per section of `catalogue.txt`, from 24 starts each."""
    problems = run(pattern, cases)
    assert not problems, "\n".join(problems[:20])


@pytest.mark.parametrize("chunk", chunks("zones", 1), ids=lambda c: repr(c[0][0]))
def test_zones(chunk: list[tuple[str, list[Case]]]) -> None:
    """Zones with odd offsets (+05:30, +05:45, +12:45, -03:30, a 30-minute DST), off DST days."""
    check(chunk)


@pytest.mark.parametrize("chunk", chunks("sweeps", 150), ids=lambda c: f"{c[0][0]!r}...")
def test_every_value_range_and_step_of_every_field(chunk: list[tuple[str, list[Case]]]) -> None:
    check(chunk)


@pytest.mark.parametrize("chunk", chunks("calendar", 10), ids=lambda c: f"{c[0][0]!r}...")
def test_every_day_special_over_a_full_calendar_cycle(chunk: list[tuple[str, list[Case]]]) -> None:
    """`L`, `L-n`, `nW`, `LW`, `n#k`, `nL`: 400 runs from 2026, past every weekday/leap pairing."""
    check(chunk)


@pytest.mark.parametrize("chunk", chunks("fuzz", 200), ids=lambda c: f"{c[0][0]!r}...")
def test_random_patterns(chunk: list[tuple[str, list[Case]]]) -> None:
    """8000 patterns from a grammar mixing valid and broken pieces, from four starts each."""
    check(chunk)


def test_the_cases_cover_every_outcome() -> None:
    """A guard on the fixtures themselves: each kind of Java answer is well represented."""
    counts: dict[str, int] = defaultdict(int)
    for suite in ("catalogue", "sweeps", "calendar", "fuzz", "zones"):
        for cases in load(suite).values():
            for case in cases:
                counts[case["java"]] += 1
    assert counts["OK"] > 15_000
    assert counts["END"] > 10_000
    assert counts["ERR"] > 10_000
    assert counts["RUNERR"] > 300
