# Ledger: fixing differences 1-44 of the behaviour parity scan

Asked on 2026-10-17, after [the scan](BEHAVIOUR-PARITY-LEDGER.md): "can you fix all 1 to 44 in a
professional and un-attended manner ... regarding 6, i like the thing to be compatible with java,
so camel case ... just make sane decisions, just make no draculas, be an agent of sanity".

The numbers are the ones in the importance table printed on 2026-10-17 (1-16 high, 17-44 medium).

## The rule

**Match Java.** Java is the reference, and a Python service must be able to share a database, an
outbox, a lock and a migration history with a Java service. Two exceptions, each recorded in
[`DECISIONS-TO-REVIEW.md`](DECISIONS-TO-REVIEW.md) rather than asked:

- **Python cannot do it** (for example, interrupting a thread). Then the closest honest
  behaviour, documented as a limit, not dressed up as a feature.
- **Java's behaviour is a bug** (for example, Flyway hanging on its own lock). Then Python does
  what Java means to do, and the Java bug goes under "Noticed in the Java, not acted on" for the
  user - the Java repository is the user's to change.

ekbatan-py is not released yet, so no compatibility shim is kept for the old Python formats.

## Every fix is proven the same way

1. A test that fails on the old code and passes on the new one.
2. The parity probe for that row rerun in both ports: the Python `PROBE` lines now match Java's.
3. The full suite on every database, lint, and the examples it touches.
4. Docs and website together; the Dracula row struck through with the commit; the scan's area
   file updated.

## Groups

Run in parallel, each in its own worktree of this repository, then merged onto `main` in order.

| Group | Items | Commits | Ledger |
|---|---|---|---|
| L - locks and jobs | 2, 30, 31, 32, 33, 34, 35 | `EKB-0263`.. | [L](parity-fix/L-locks-jobs.md) |
| E - events | 11, 12, 17, 18, 19, 20 | `EKB-0270`.. | [E](parity-fix/E-events.md) |
| M - migrations | 13, 14, 15, 16, 37, 38, 39, 40, 41, 42, 43, 44 | `EKB-0277`.. | [M](parity-fix/M-migrations.md) |
| J - JSON, domain, timestamps | 6, 7, 8, 9, 10, 21, 24, 25, 26 | `EKB-0290`.. | [J](parity-fix/J-json-domain.md) |
| T - transactions, actions, config, repository | 3, 4, 5, 22, 23, 27, 28, 29, 36 | `EKB-0300`.. | [T](parity-fix/T-transactions-config.md) |

Item 1 (a lock lease closed twice) is done: `EKB-0262`.

## State

| Group | State |
|---|---|
| L | done, merged |
| E | done, merged |
| M | done, merged |
| J | running |
| T | running |
