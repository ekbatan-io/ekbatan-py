# Ledger: the Quartz converter

Asked on 2026-10-16: in `CronStyle.QUARTZ`, turn the pattern into one croniter reads **exactly as
Java does** - not just the day-of-week off-by-one. "Many many tests"; the conversion must be very
reliable.

**The reference** is what Java db-scheduler runs: cron-utils 9.2.1, `CronType.QUARTZ`,
`ExecutionTime.forCron(cron).nextExecution(lastDone)` (`CronSchedule.java`). Not the Quartz
Scheduler itself - where cron-utils and Quartz disagree, cron-utils wins, because that is Java.

**The rule:** a pattern the converter cannot make croniter read exactly as cron-utils does is
refused with a clear error. Never guessed.

## Steps

| # | Step | State |
|---|---|---|
| 1 | Oracle: a Java program on cron-utils 9.2.1 that prints the next N times (or the error) for a list of patterns, starts and zones | done - `scripts/quartz-oracle/` |
| 2 | Survey cron-utils QUARTZ: what each field accepts and means, by running probes | done - below |
| 3 | Survey croniter: which Quartz features it reads, and how, by running probes | done - no `L-n`, no `LW`, no mixing `#`/`L` with plain days; its `W` differs at month ends |
| 4 | Design: the mapping per feature, and the list of what must be refused | done - below |
| 5 | Build the converter; wire it into `CronSchedule` for QUARTZ | done - `task/schedule/_quartz.py` |
| 6 | Tests: hand-written catalogue per feature, exhaustive sweeps per field, grammar fuzz, many start times; all against the oracle's recorded output | done - below |
| 7 | Prove the tests bite: break the converter on purpose, see them fail | done - below |
| 8 | Docs: `PORTING.md`, the Dracula row, DECISIONS | done |
| 9 | Full suite, lint, commit | |

## Findings

### How cron-utils reads a QUARTZ pattern (read in its source, confirmed by the oracle)

- Exactly one of day-of-month and day-of-week is `?`. Both `*`, both `?`, or both set: refused.
- Day-of-week 1-7 is SUN-SAT; names SUN-SAT; 0 and 8 refused.
- Seconds, minutes, hours, months, years are plain sets of values, built once.
- **A range that wraps** (`22-2`, `50-10`, `28-3`, `NOV-FEB`) means only its first value. Inside a
  list it means nothing (`22-2,5` is 5). Day-of-week ranges do wrap properly (`FRI-MON`).
  A year range that wraps is refused.
- **In a day-of-month list**, `L`, `L-n` and `LW` are dropped, and `nW` means plain `n`:
  `L,15` is the 15th only; `15W,1` is the 15th and the 1st, weekday or not.
- Alone in day-of-month: `L` is the last day; `L-n` is n days before it, and nothing when that is
  before the 1st (`L-31` never runs); `nL` is the same as `L`; `LW` is the last weekday.
- `nW` alone: a Saturday moves to Friday (the 1st moves to Monday the 3rd); a Sunday moves to
  Monday, **except on the last day of the month, where it stays Sunday**. When the month is
  shorter than n, Java **throws while computing** (`31W` in November).
- Day-of-week `L` alone means Saturday, every week; so does `L-n`. `nL` is the last such weekday
  of the month; `n#k` the k-th, never when there is none (`6#6`).
- A day-of-week range with a step that wraps (`6-2/2`) throws while computing.
- A list in day-of-week is a true union, `#` and `L` items included.
- `||` joins whole patterns, and `|` inside fields expands into several; either way the next time
  is the earliest of the parts, each part computed on its own.
- Leftovers Java ignores: `1-5/2/3` is `1-5/2`, `6#3#2` is `6#3`, `LWLW` is `LW`, `5LL` is `5L`.
- **The search**: the next time is the earliest whole second at or after the start that matches;
  if that is the start itself, the earliest one at or after start + 1 second - and if the start
  had a fraction and start + 1 second matches, **the fraction is kept** (12:00:00.300 every
  second gives 12:00:01.300). Real completion times have fractions, so this matters.
- Nothing after 2099. Nothing more than 100 years after the start.

### Design

- Parse by porting cron-utils' parser and validation line by line (the same splits, the same
  order of checks), so that what Java accepts and refuses, Python accepts and refuses.
- Seconds, minutes, hours, months, years: port cron-utils' value generators, and hand croniter
  the resulting explicit list. croniter never interprets a range or a step.
- Day-of-month specials (`L`, `L-n`, `W`, `LW`): compute the day for every month 1970-2099 with
  the ported rule, and group into patterns of explicit days, months and years. croniter's own
  `L` and `W` are never used.
- Day-of-week: plain weekdays as a list of names; each `#` and `nL` item as its own croniter
  pattern (`FRI#3`, `L5`), because croniter cannot mix them in one field.
- Every pattern has `*` in the other day field, so croniter's day-of-month/day-of-week OR rule
  never comes into play.
- One Quartz pattern becomes one or more croniter patterns; the next time is the earliest of
  them, then the search rules above (fraction, 100 years) are applied on top.

### Decisions (also in `docs/DECISIONS-TO-REVIEW.md`)

1. Where Java computes times, Python computes the same times - including the surprising
   readings above. A Java and a Python scheduler can share one table, so they must agree.
2. Where Java throws while computing (`31W` with short months in range, `6-2/2`, a list of
   wrapping ranges that leaves a field empty), Python refuses the pattern up front.
3. Python refuses a few strings Java accepts: characters outside printable ASCII (Java takes
   non-ASCII digits).

### croniter, found while building

- croniter reads a year field holding every year 1970-2099 - written out or as `1970-2099` - as
  `*`, and then runs on past 2099. So results after 2099 are cut off after croniter.
- croniter's objects keep their position; building one is most of the cost (a `W` pattern
  becomes about thirty). They are built once per pattern and used under a lock: about 0.05 ms a
  run for plain patterns, 2 ms for `LW`.

### Not a Quartz difference: clock changes (Dracula bug 10)

On the day the clocks go forward, a time inside the missing hour runs at 03:00 here and not at
all in Java; on the day they go back, a daily time inside the repeated hour runs twice here and
once in Java. Every style, run through `CronSchedule` for QUARTZ and SPRING53 in Amsterdam and
New York. Left alone, by the user's decision on 2026-10-16: it is croniter's behaviour for every style. The tests keep their zones away from clock-change days.

### The tests

| file | what | size |
|---|---|---|
| `tests/unit/test_quartz.py` | one rule per test, Java's answer written in | 93 tests |
| `tests/unit/test_quartz_oracle.py` | every recorded case, Python against Java | 56,477 cases |
| `tests/unit/quartz_cases/catalogue.txt` | the hand-written patterns, one feature per section | 334 patterns x 24 starts |
| `quartz_cases/sweeps` | every value, range and step of every field | 15,600 patterns |
| `quartz_cases/calendar` | every day special over 2026-2053, 400 runs each | 206 patterns |
| `quartz_cases/fuzz` | random patterns, valid and broken, seeded | 8,000 patterns x 4 starts |
| `quartz_cases/zones` | +05:30, +05:45, +12:45, -03:30, a 30-minute DST, off DST days | 17 x 8 zones x 3 starts |
| `quartz_cases/probes` | per pattern, does Java throw from the first of some month | 24,000 patterns |

Regenerate with `uv run python scripts/quartz-oracle/generate_fixtures.py` (needs a JDK; the two
jars are fetched from Maven Central and checked against their SHA-1). `--check` compares with
what is committed.

Outside the committed suite, two runs of 100,000 more random patterns each (other seeds). The
first, before the month probes existed, flagged four patterns refused as "Java throws" with no
throw on record - all `29W`/`31W` that fire many times a day and so never left their first day;
run from a February start, Java threw on each. The second, with the probes, found nothing.

### Proving the tests bite

Planted bugs, one at a time, each must fail the suite: the original off-by-one, `#` and `nL` off
by one, wrapping ranges that really wrap, `L` or `W` kept in a day-of-month list, `?` in a
day-of-week list throwing, `nW` on a month-end Sunday moving to Friday, `1W` staying on a
Saturday, `L-n` off by one, `LW` on a Sunday, the fraction dropped, no 100-year limit, no 2099
limit, `||` read as one union, both day fields allowed, a step limit off by one, day-of-week `L`
as the last Saturday, a second `#` refused, `31W` accepted, lower-case names refused, a trailing
comma accepted, a wrapping year range accepted, `?` in seconds, an empty time field accepted,
plain day sets taken from 31-day months only.

### Found after the commit: croniter at import time (`EKB-0249`)

`EKB-0246` imported croniter at module level, where `CronSchedule` had always imported it inside
its methods. croniter calls `platform.architecture()` on import, which runs `file` as a
subprocess; under gevent, one subprocess is enough to stop `multiprocessing` from seeing its
children exit, and ekbatan's `make test-gevent` lost three migration-concurrency tests (bisected
to `EKB-0246`; reproduced with nothing but a `subprocess.run(['true'])` under gevent). croniter is
imported lazily again, and `tests/unit/test_import_side_effects.py` keeps it so.
