# Ledger: parity round 2 - differences and Draculas left after the fixes

Asked on 2026-10-18: "one more round of investigation, to find out the differences and possible
dracula differences of the python and java". Round 1 is [`BEHAVIOUR-PARITY-LEDGER.md`](BEHAVIOUR-PARITY-LEDGER.md)
and its fixes [`PARITY-FIX-LEDGER.md`](PARITY-FIX-LEDGER.md).

## What this round does differently

- **Investigation only.** Nothing in `src/`, `tests/` or the examples changes. The user decides.
- **Every finding carries a verdict**, so the user reads only what needs them:
  - **fix** - a real difference a Java developer would trip on, cheap and safe to match;
  - **leave** - harmless, an accident of Java's or Flyway's code, or a Python limit;
  - **needs you** - a real trade-off, or anything that would replace a dependency or rewrite a
    subsystem (never done unattended).
- **Proven by running both ports**, as round 1: a finding without a run is marked `read`.
- **Decided questions are not re-raised**: the migrator stays on yoyo, and migration items 13-16,
  38-44 and 77-84 stay as they are; JSON is Java's camelCase; ids sort as Java's; the Redis lock is
  Redisson's format; a missing schema is created by hand.

## Areas

| # | Area | Findings | State |
|---|---|---|---|
| R0 | Round 1's probes rerun on today's `main`: did the fixes hold, and did merging them break anything? | [R0](parity-round-2/R0-rerun.md) | done: 1,526 keys - 174 newly match Java, 0 regressions needing action, suite 2,649 passed |
| N1 | Config files and wiring, against Java's Spring, Quarkus and Micronaut properties | [N1](parity-round-2/N1-config-wiring.md) | done: 24 checked - 10 same, 2 fix, 10 leave, 0 needs you |
| N2 | Code generation: `codegen`, `schemagen`, builders, against the annotation processor and Java's generated code | [N2](parity-round-2/N2-codegen.md) | done: 26 checked - 7 same, 6 fix, 13 leave, 0 needs you |
| N3 | Observability: log lines, metrics, spans - names, levels, attributes | [N3](parity-round-2/N3-observability.md) | done: 29 checked - 14 same, 6 fix, 8 leave, 1 needs you |
| N4 | Public API shape, errors, the testing helper (`ActionSpec`) | [N4](parity-round-2/N4-api-errors-testing.md) | done: 24 checked - 10 same, 4 fix, 10 leave, 0 needs you |
| N5 | Combinations the fixes now meet: nested transactions with actions, events and jobs; sharded flows end to end; JSON and timestamps through MySQL and MariaDB | [N5](parity-round-2/N5-combinations.md) | done: 25 checked - 19 same, 0 fix, 6 leave, 0 needs you |
| N6 | What the docs and website promise, checked by running it | [N6](parity-round-2/N6-docs-claims.md) | done: about 220 claims - 35 doc fixes, 4 leave, 1 needs you (a pool bug) |

## Totals

| Area | Checked | Fix | Leave | Needs you |
|---|---|---|---|---|
| R0 rerun | 1,526 keys | 0 | 1 | 0 |
| N1 config | 24 | 2 | 10 | 0 |
| N2 codegen | 26 | 6 | 13 | 0 |
| N3 observability | 29 | 6 | 8 | 1 |
| N4 API, errors, testing | 24 | 4 | 10 | 0 |
| N5 combinations | 25 | 0 | 6 | 0 |
| N6 docs claims | about 220 | 35 | 4 | 1 |

R0: 174 round-1 differences now match Java, no fix broke, and the suite passed on every database
(2,649). N5: the round-1 fixes work together. **Needs you**: log message wording (N3), and the
pool's pre-opened connections skipping `pool_pre_ping` (N6 - a real bug from `EKB-0305`).

The pool finding was resolved by undoing `EKB-0305` (items 28-29) in `EKB-0318`, by the user's
decision: SQLAlchemy's standard pool, with its own `pool_pre_ping`, is back.
