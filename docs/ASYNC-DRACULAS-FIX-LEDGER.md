# Async draculas - fix ledger

Work list: `docs/ASYNC-DRACULAS.md` (19 rows). Asked on 2026-10-20: fix every leftover or
unforced difference so sync and async differ only where async forces it. Judgement calls are in
`docs/DECISIONS-TO-REVIEW.md` under "async dracula fixes". Started at `ca9321f` (EKB-0388).

Each row is re-checked against current main before it is fixed (the time rule, EKB-0384-0388,
landed after the list was written).

## Rows

| # | what | plan | state | commit |
|---|---|---|---|---|
| 1 | `*Rows` mixins in async repositories | mapping back inside each async repository; async SQLite test as the sync twin's; every doc/website sample | fixed: mapping inlined from each sync twin (verbatim, same position), `*Rows` deleted in 7 async examples x 3 builds; async SQLite test as the sync one; 13 doc/website places rewritten | EKB-0394 |
| 2 | stipend job missing in async REST + SQLite twins | add it, its work awaited on a `HandlerLoop` as `job-worker-uv-async-pg` does | todo | |
| 3 | load caps only in async | add 503 handler, `poolTimeout`, `limit_concurrency` to sync REST, saga, sharded; test the 503 in sync | todo | |
| 4 | sharded keyed lock only in async | add the lock and its test to the sync sharded example | todo | |
| 5 | async `find_one_where`/`exists_where` fail on a down shard sync never reaches | async stops at the first shard in order with the answer; docs; test both modes | fixed: async `_first_answer` asks all shards at once, answers in shard order, cancels the rest; a later broken shard no longer fails it; tested both modes, one shard down | EKB-0392 |
| 6 | shard note on scatter-read errors only in async | add the note in sync | fixed: the note-making moved into `_RepositoryCore._shard_failure`; sync scatter reads add the same note (no "cancelled" clause) | EKB-0392 |
| 7 | async MySQL/MariaDB drop `innodb_lock_wait_timeout` | put it back + its assertion | fixed: async MySQL/MariaDB request pool sets `innodb_lock_wait_timeout=10` again; settings test reads it back through aiomysql (MySQL run: 15 passed) | EKB-0399 |
| 8 | async UTC test weakened | port the Tehran test to the three async twins | todo | |
| 9 | other REST tests dropped in async | port 422, recipient and currency checks | todo | |
| 10 | unused repository helpers on the sync side | keep only what each example uses, both twins the same | fixed: each pair carries what it uses - REST: `find_for_wallet` only (sync lost 6 helpers, async 2); job worker: `find_open_below` + `find_for_wallet` (sync lost 5) | EKB-0397 |
| 11 | `RaiseAlertAction` in `handlers.py` in async REST twins | move back to `actions.py` | fixed: `RaiseAlertAction`/`RaiseAlertParams` back in `actions.py` in the 3 async REST twins, same place as sync | EKB-0395 |
| 12 | lock provider picked from a dict in async | name the provider directly | fixed: each async REST app names its provider (`AsyncPostgresKeyedLockProvider` etc.); setup guide shows async `app.py` per database | EKB-0396 |
| 13 | async READMEs drop shared sections | restore them | todo | |
| 14 | deprecated `testcontainers.postgres` in sync tests | `testcontainers.community.postgres` | fixed: 4 sync tests x 3 builds import `testcontainers.community.postgres` | EKB-0398 |
| 15 | `tm.transaction()` timestamp sample | re-check | already resolved: both tabs write `clock.now()` as is (EKB-0384-0388) | - |
| 16 | config-file sample import path | `from ekbatan import DatabaseRegistry` | fixed: sync tab imports `from ekbatan import DatabaseRegistry`, both copies | EKB-0390 |
| 17 | async locks not exported from `ekbatan.aio` | export them; surface test compares package exports | fixed: async lock classes exported from `ekbatan.aio`; surface test compares package exports both ways | EKB-0393 |
| 18 | job worker README says `stop()` | say `close()` | fixed: sync README and app docstring say `close()` | EKB-0391 |
| 19 | one url, two drivers | kept by choice | todo | |

Side notes from the list: "Fifteen cases" in the REST READMEs (sync has sixteen tests);
the surface test's gaps (row 17 extends it).

## Also asked (time as is)

- (a) "python and java would not share a single db": the `timestamptz` `scheduled_tasks` note
  for a Python worker on Java's table is not a real case - decision marked "not applicable
  (user)"; docs that present one shared database for the scheduler table trimmed.
- (b) event-JSON time without `Z`: user undecided - decision marked "open, user undecided",
  nothing changed.

## Runs

Baseline at `ca9321f`, in a separate checkout (`git worktree` under `/private/tmp`), so edits
here cannot touch it.

| suite | before | after |
|---|---|---|
| unit | | |
| `pytest --dialects=all` | | |
| `make test-psycopg-async` | | |

## Log
