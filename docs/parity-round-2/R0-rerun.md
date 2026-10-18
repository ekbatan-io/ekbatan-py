# R0: round 1's probes rerun on today's `main`

Scope: every round-1 twin probe except F (migrations, decided) was run again on ekbatan-py `main`
at `bccbb9e` (after EKB-0262..0276, 0290..0314) and compared, key by key, with Java's saved output
(Java has not changed since round 1) and with round 1's Python output (taken from `b1b2b44`, the
scan commit). Run: A (registry, repository on PostgreSQL/MySQL/MariaDB, sharded routing), B
(actions), C (pure config logic, and transactions/pools on a PostgreSQL container on 58132), D
(events), E (locks on PostgreSQL/MySQL/MariaDB/Redis on 58143/58106/58107/58179, MariaDB with
`--plugin-load-add=metadata_lock_info`; jobs; bounded waits; and the cross-port run, Java and
Python contending on the same containers - the only Java run, in the R0 worktree, since it is a
joint run), G (clock, codegen, ids, JSON, JSON formats, model). The full suite was run once on
every database. Copies of the probes, every output and the three-way compare script are in
`parity-probes/round2-R0/` (`compare.py`, `*.cmp.txt`; round 1's outputs in `r1-python-out/`).
Ports in the copies were moved to 581xx; nothing else in them changed but the repo-root path.

## Per area

"fixed" = differed from Java in round 1, same now. "regressed" = same as Java in round 1, differs
now. "still" = differed in round 1 and still differs (round 1 already reported these; no verdict).

| Area | Keys | same both times | fixed | regressed | still |
|---|---|---|---|---|---|
| A repository | 409 | 220 | 48 | 0 | 141 |
| B actions | 200 | 155 | 12 | 0 | 33 |
| C pure config | 153 | 105 | 6 | 0 | 42 |
| C transactions, pools | 67 | 49 | 9 | 0 | 9 |
| D events | 144 | 91 | 38 | 0 | 15 |
| E locks | 240 | 200 | 32 | 0 | 8 |
| E jobs | 47 | 43 | 0 | 0 | 4 |
| E bounded waits | 12 | 11 | 1 | 0 | 0 |
| E cross-port (Java and Python on one lock / job table) | 31 | 29 | 2 (+4 new keys, all as Java) | 0 | 0 |
| G clock | 20 | 14 | 0 | 0 | 6 |
| G codegen | 9 | 4 | 0 | 0 | 5 |
| G ids | 64 | 35 | 4 | 0 | 25 |
| G JSON | 50 | 19 | 21 | **2** | 8 |
| G model | 80 | 46 | 1 | 0 | 33 |
| G JSON formats (new in EKB-0290, no round-1 output) | 50009 values | - | - | 0 | 16 values (recorded limit) |
| **Total (PROBE keys)** | **1526** | **1021** | **174** | **2** | **329** |

Notes on the counts:
- A "still" includes 21 null-argument rows whose Python error changed from `AttributeError` (or
  no error) to `ValidationError` with Java's message; only the class name still differs from
  Java's `NullPointerException`. The messages are now Java's (counted under fixed: `*.null.msg`).
- B: `s2.action_params` counted as fixed: it differs from Java only by a random wallet id.
- D: `x.boundary.exact` changed (due date now Java's 09:50) but still `EXPIRED` where Java
  `SUCCEEDED` - round-1 row 16, left as it is by decision. `x.step6` (Python ran one step more)
  is gone, as in Java.
- E: `pg.backend_down.type` now `LockAcquisitionError` vs Java's `LockAcquisitionException` -
  the same error under Python's naming.
- E cross-port: the Redis lock now works both ways: in round 1 Java got `RedisException` on a
  key Python held; now Java and Python each see the other's hold, and an unlock wakes a waiter in
  the other port under 3 s. Shared job table: no overlapping runs, as round 1.
- G: the codegen and model "still" rows now raise `ValidationError: <field> cannot be null` where
  they raised `TypeError`; Java raises `NullPointerException: <field> cannot be null`.

Fixes holding, by commit (all re-proved by this run): timestamps as aware UTC (EKB-0294,
48 A keys and `model.createdDate`); null-argument messages (EKB-0304); camelCase params and
payloads, SQL NULL for an absent payload (EKB-0290, 0291; B s1/s2/s4/s5, 21 G JSON keys); action
clock and default persister (EKB-0302); nested transactions and per-manager transactions
(EKB-0300, 0301; C `tx.nested.*`, `tx.two_managers_same_shard.*`, B s43); pool filled to size and
retried until timeout (EKB-0305); unknown-shard policy parsing (EKB-0303); the event pipeline
(EKB-0270..0275, 38 D keys); lock leases, close, lock_timeout reset, MySQL wait, Redisson format
(EKB-0262..0267, 0311; 32 E keys and cross-port); id order (EKB-0293).

## Regressions (lines that matched Java in round 1 and differ now)

| # | Difference (plain, one line) | Java | Python | Verdict | Why (one line) | Evidence |
|---|---|---|---|---|---|---|
| R0-1 | A decimal sent as a JSON *number* is read back exact in Python, cut in Java | `1.50` -> `1.5`; `1234...890.123456789` -> `123456789012345680000000000000` | `1.50` kept; every digit kept | leave | Java loses digits through a double; Python's exactness is EKB-0290's deliberate choice, recorded in DECISIONS-TO-REVIEW ("every JSON number read as a Decimal") and in round 1 row 82 | `G-domain-model/json.now.out` keys `in.amountNumber`, `in.amountHugeNumber`; cause proved by running the probe on `51aad9b` (round-1 values) and `3e65642` (EKB-0290, today's values) from `r0-bisect` exports |

One more change inside lines that already differed (not counted as a regression): reading back an
`OffsetDateTime`-style value (`atOffset`, written as `2025-01-01T02:00:00+02:00`), Python now keeps
the `+02:00` offset (since EKB-0290, proved the same way). It is the same instant; Java's probe
prints `toInstant()`, so it cannot show which offset Java keeps (Jackson's default adjusts to UTC:
`read`, not run). Leave: nobody compares offsets of a decoded payload field, and the instant is
equal. In the same lines `sci` (`1E+3`) now reads as `Decimal("1E+3")` - part of R0-1.

No `fix` and no `needs you` findings.

## Full suite, every database

`uv run --no-sync pytest -q -p no:cacheprovider --dialects=all` on `bccbb9e` (SQLite, PostgreSQL,
MySQL, MariaDB): exit 0; counted from the progress lines, 2649 passed, 59 skipped, 0 failed,
0 errors. Log: `parity-probes/round2-R0/suite-all-dialects.log`.

## Totals

1526 checked, 1021 same as Java in both rounds, 174 fixed and holding, 2 regressed (one cause,
R0-1, deliberate), 329 still differing (round 1 already reported them).
Verdicts: 0 fix, 1 leave, 0 needs you.
