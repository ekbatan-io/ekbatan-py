# Behaviour parity, area A: the repository

**Scope.** Every built-in read and write of Java's `AbstractRepository` (`ekbatan-core`
`repository/`, `repository/exception/`, `RepositoryRegistry`) against Python's
`src/ekbatan/repository.py`, settled by running the same scenario in both ports
(2026-10-17). Covered: `add`/`addAll` and their `NoResult` forms, `update`/`updateAll` (the
PostgreSQL `VALUES` path and the MySQL/MariaDB join path), versions and stale writes, soft
delete, every read (`findById`, `getById`, `findAllByIds`, `findAll`, `findOneWhere`,
`findAllWhere`, `existsById`, `existsWhere`, `count`, `countWhere`), what each write returns,
inside and outside a transaction, sharded routing, the unsharded-repository guard, the
unknown-shard policy, timestamps and session time zone, null input, and error types. Every
unsharded scenario ran on PostgreSQL 17, MySQL 8.4 and MariaDB 11.4 with Java's own `dummies`
table (same DDL in both ports); sharded scenarios ran on two PostgreSQL databases.

Probes and outputs: [`parity-probes/A-repository/`](../../parity-probes/A-repository/) -
`ProbeRepoBase.java` + `ProbeRepo{Pg,Mysql,Mariadb}Test.java` / `probe_repo.py`,
`ProbeShardedPgTest.java` / `probe_sharded.py`, `ProbeRegistryTest.java` / `probe_registry.py`;
`java.out`, `python.out`, `java-vs-python.diff`. The probe column gives the `PROBE` key (prefix
`pg.`/`mysql.`/`mariadb.`, `shard.` or `registry.`).

Error class names differ by convention everywhere (`StaleRecordException` / `StaleRecordError`,
Dracula 57); a row is `same` when both raise the matching error at the same point.

## Table

| # | Behaviour | Java | Python | Verdict | Probe |
|---|---|---|---|---|---|
| 1 | `add` on PG / MariaDB returns the stored row, no events, version 1 | fresh row, 0 events | same | same | `add.*` |
| 2 | `add`, `addAll`, `update` on **MySQL**: what comes back | the object passed in (`update`: its `nextVersion()`), events kept | a rebuilt object, no events | differs (D28) | `mysql.add.same_instance`, `*.events_on_result` |
| 3 | `updateAll` (2+ rows) on **MariaDB**: what comes back | the objects passed in, `nextVersion()`, events kept | rebuilt, no events | differs | `mariadb.updateAll.two.events_on_result` |
| 4 | `update` (one row) on MariaDB returns a fresh row | fresh, 0 events | same | same | `mariadb.update.events_on_result` |
| 5 | `updateAll` on **PG**: order of the returned list | database order (not the input order) | input order | differs | `pg.updateAll.two.order_kept` |
| 6 | `addAll` returns rows in input order (all dialects) | yes | yes | same | `addAll.two.order_kept` |
| 7 | `addNoResult` writes and returns nothing | yes | yes | same | `addNoResult.row_exists` |
| 8 | Empty list to `addAll` / `updateAll` / `addAllNoResult` | empty result, no SQL | same | same | `addAll.empty.size`, `updateAll.empty.size` |
| 9 | `null` collection to `addAll` / `updateAll` / `addAllNoResult` | `NullPointerException` "Models collection cannot be null" | same message, as `ValidationError` (fixed in EKB-0304) | same | `addAll.null`, `updateAll.null_collection`, `addAllNoResult.null` |
| 10 | `null` model to `add` / `addNoResult` / `update`, or inside `addAll` | `NullPointerException` "Model cannot be null" / "Model in collection cannot be null"; nothing written | same messages, as `ValidationError`; nothing written (fixed in EKB-0304) | same | `add.null`, `update.null`, `addAll.null_element*` |
| 11 | Duplicate id on `add` or inside a batch | driver error passed through (`IntegrityConstraintViolationException`) | driver error passed through (`IntegrityError`) | same | `add.duplicate_id`, `addAll.duplicate_id_in_batch` |
| 12 | `update` returns version + 1 and the row holds it | 2, 2 | 2, 2 | same | `update.version`, `update.db_version` |
| 13 | `update` leaves `updated_date` and `created_date` as they were | yes | yes | same | `update.updated_date` |
| 14 | Stale version on `update` / `updateNoResult` | `StaleRecordException`, row unchanged | `StaleRecordError`, row unchanged | same | `update.stale*` |
| 15 | Stale message names the row | "Model Dummy[id=..., version=2] was concurrently modified or not found" | "expected to update 1 Wallet row(s) but matched 0; ..." - no id, no version | differs | `update.stale.msg` |
| 16 | `update` of a row that does not exist | stale error (not "not found") | same | same | `update.missing*` |
| 17 | `update` of a soft-deleted row is allowed (writes do not filter state) | succeeds | succeeds | same | `update.soft_deleted_row` |
| 18 | Soft delete (`state = DELETED` via `update`) hides the row from `findById` | yes | yes | same | `delete.*` |
| 19 | `updateAll` / `updateAllNoResult` with one stale row, outside a transaction | raises; the other row stays written | same | same | `updateAll.one_stale*` |
| 20 | `updateAll` with the same row twice in one batch | stale error, row moved once | same | same | `updateAll.same_row_twice*` |
| 21 | `updateAll` where a nullable column is null in every row | works | works | same | `updateAll.null_column_every_row` |
| 22 | `updateAll` with one stale row inside a transaction | everything rolled back | same | same | `tx.updateAll_one_stale.*` |
| 23 | `findById` of a missing / soft-deleted row | empty | `None` | same | `findById.missing`, `delete.findById_present` |
| 24 | `getById` of a missing / soft-deleted row | `EntityNotFoundException`, names type and id | `EntityNotFoundError`, names type and id | same | `getById.*` |
| 25 | `existsById`: live / deleted / missing | true / false / false | same | same | `existsById.*` |
| 26 | `findAllByIds`: empty input, duplicates, missing and deleted ids | empty; duplicates once; missing and deleted left out | same | same | `findAllByIds.empty`, `findAllByIds.dupes_missing_deleted` |
| 27 | `findAllByIds(null)` | empty list | `TypeError: 'NoneType' object is not iterable` | differs | `findAllByIds.null` |
| 28 | `findAllWhere` / `countWhere` / `existsWhere` leave out soft-deleted rows | yes | yes | same | `*Where.two_live_one_deleted`, `*.deleted_only` |
| 29 | `findOneWhere` with two matches on one shard | refuses (`TooManyRowsException` from jOOQ) | refuses (`ValidationError`) | same | `findOneWhere.two_matches` |
| 30 | `findOneWhere` with none / one / only a deleted match | empty / found / empty | same | same | `findOneWhere.*` |
| 31 | `count` and `findAll` leave out soft-deleted rows and agree | yes | yes | same | `count.*`, `findAll.*` |
| 32 | Built-in reads inside an open transaction see its uncommitted rows (bug 8, `EKB-0259`) | no - all seven reads | no - all seven reads | same | `tx.*_sees_own_insert` |
| 33 | Another connection sees an uncommitted insert; sees it after commit | no; yes | no; yes | same | `tx.other_connection_sees_insert`, `tx.after_commit_visible` |
| 34 | Inside a transaction: `update` returns the new version; a read still sees the old one | 2; 1 | 2; 1 | same | `tx.update_returns_version`, `tx.getById_after_own_update_version` |
| 35 | Rollback undoes `add`, `update`, `addAll` made in the transaction | yes | yes | same | `tx.rollback.*` |
| 36 | A write outside a transaction is visible to other connections at once | yes | yes | same | `notx.other_connection_sees_insert` |
| 37 | Timestamps read back: zone, and equal to what was written | `Instant`, equal | naive `datetime`, `!=` the aware value written | differs (D43) | `add.created_date`, `find.created_date_equals_input` |
| 38 | Microseconds kept (all dialects) | `.123456` | `.123456` | same | `find.created_date` |
| 39 | Non-UTC session zone in a transaction does not shift `created_date` (`add`, `update`, `updateAll`; all dialects) | unshifted | unshifted | same | `zone.*` |
| 40 | What leaves a transaction block when it throws | `RuntimeException` wrapping the real one | the real exception | differs (area C) | `shard.tx_on_A.outer_exception` |
| 41 | Sharded `add` goes to the shard in the id; `findById` / `existsById` read there | yes | yes | same | `shard.add_B.*`, `shard.findById_B` |
| 42 | An id naming an unregistered shard: `add` / `findById` use the default, with a warning | yes | yes | same | `shard.add_unregistered.on_A`, `shard.findById_unregistered` |
| 43 | Cross-shard `addAll` / `addAllNoResult` / `updateAll` refused, nothing written | yes | yes | same | `shard.*_cross*` |
| 44 | Which two shards the cross-shard error names | first in the batch, then the first that differs | always the lower shard first; the text suggests `allow_cross_shard=True`, which a repository does not take | differs | `shard.addAll_cross.B_then_A.msg` |
| 45 | Batch of default + unregistered allowed; registered + unregistered refused | yes | yes | same | `shard.addAll_*_unregistered` |
| 46 | `update` on a sharded row goes to its shard | yes | yes | same | `shard.update_on_B.version` |
| 47 | `count` / `findAll` / `*Where` / `findOneWhere` read every shard | yes | yes | same | `shard.*_across` |
| 48 | `findAllByIds` across shards | groups by shard, finds all | same | same | `shard.findAllByIds_across` |
| 49 | `findOneWhere` with one match on each of two shards | first shard's row, no error | same | same | `shard.findOneWhere_one_match_per_shard` |
| 50 | Unsharded repository on a sharded registry: reads only the default shard, writes there even for an id naming shard B, mixed batch allowed | yes | yes | same | `shard.unsharded.*` |
| 51 | Strategy that cannot route by id: `findById` / `getById` / `existsById` / `findAllByIds` refused | `UnsupportedOperationException` | `UnsupportedError` | same | `shard.nonid.find*`, `shard.nonid.getById` |
| 52 | Same strategy: `add` routes by the model, `findAll` reads every shard | yes | yes | same | `shard.nonid.add_routes_by_model.on_B`, `shard.nonid.findAll_scatters` |
| 53 | Writing a shard-B row while a transaction is open on A | commits on B by itself; survives A's rollback | same | same | `shard.tx_on_A.write_to_B_survives_A_rollback` |
| 54 | Writing a shard-A row inside A's transaction joins it | rolled back with it | same | same | `shard.tx_on_A.write_to_A_rolled_back` |
| 55 | Unknown-shard policy FAIL: `add` / `findById` / `findAllByIds` on an unregistered shard | refused (`IllegalArgumentException`) | refused (`ValidationError`) | same | `shard.fail_policy.*` |
| 56 | Two different repositories registered for one model | last one wins | refused (`ValidationError`) | differs (D61) | `registry.two_repos_one_model` |
| 57 | The same repository registered twice | fine | fine | same | `registry.same_repo_twice` |
| 58 | Looking up a model with no repository | `null` | raises `ValidationError` naming it | differs | `registry.lookup_unregistered` |
| 59 | Nanosecond `Instant` truncated to microseconds when a model is built | yes | - | read: Python's `datetime` cannot hold nanoseconds, so there is no input to run | - |
| 60 | `updateNoResult` matching more than one row | `IllegalStateException` | `EkbatanError` | read: needs a non-unique id, which the primary key forbids in both | - |
| 61 | Repository refuses a table without `version` / `state` (Model: `created_date` / `updated_date`) | `NullPointerException` at construction | `ValidationError` at construction, also checks column types | read: Java needs a generated jOOQ table missing the column | - |
| 62 | Read method names, `Optional` vs `None`, raw vs typed id key | - | - | read: API shape (D6, D8) | - |
| 63 | Connection accessors `db` / `readonlyDb` / `txDb` / `txDbElseDb` | protected methods | public `with` blocks | read: API shape (D10); what they do is run in rows 32-36, 53-54 | - |

## Differences

**37. Timestamps come back without a zone** (Dracula 43). A model's `created_date` /
`updated_date` read back is a naive `datetime`; compared with the aware `datetime` that was
written (`T0 = ...tzinfo=UTC`), `==` is `False` and `<` raises. Java's `Instant` equals what was
written. Java `domain/Model.java:84-86`; Python `domain.py:654`, `clock.py:132`. Could match:
partly - keep the value aware in memory and drop the zone only on the way to the column; touches
the model, the mapper and every test that compares dates. Surprise: **high**.

**9. A `null` collection is accepted silently.** **Fixed** (`EKB-0304`): refused with Java's
message. `add_all(None)`, `update_all(None)` and
`add_all_no_result(None)` return `[]` or nothing - the `if not models` check treats `None` as
empty. Java throws `NullPointerException` "Models collection cannot be null", so a caller's bug
shows. Java `repository/AbstractRepository.java:381, 426, 535, 572`; Python `repository.py:725,
753, 793, 817`. Could match: yes, one `None` check per method. Surprise: **medium**.

**2. On MySQL, `add` / `addAll` / `update` return a rebuilt object** (Dracula 28). Java returns
the object passed in (for `update`, its `nextVersion()`), still carrying its events; Python
rebuilds it through `from_row(to_row(...))`, with no events. Java
`repository/AbstractRepository.java:357-360, 408-411, 468-482`; Python `repository.py:744-745,
859-866`. Could match: yes, but it brings back the risk the rebuild exists for (a second
`update` writing the same events again). Surprise: low.

**3. On MariaDB, `updateAll` returns a rebuilt object.** New, and not in Dracula 28 (which names
MySQL only). Java's batch update treats MariaDB like MySQL and returns `nextVersion()` of each
input, events kept - while Java's single-row `update` on MariaDB returns a fresh row (row 4), so
Java itself differs between the two. Python rebuilds in both. Java
`repository/AbstractRepository.java:546-556`; Python `repository.py:859-866`. Could match: yes,
same trade as row 2. Surprise: low.

**5. On PostgreSQL, `updateAll` returns rows in a different order.** Java returns them in the
order `UPDATE ... RETURNING` gives (here, table order: the batch `[x2, x1]` came back `[x1, x2]`);
Python puts them back in input order. MySQL and MariaDB keep input order in both. Java
`repository/AbstractRepository.java:558-559`; Python `repository.py:867-870`. Could match: yes,
by dropping the reorder - but Python's order is the one a caller can rely on; Java could adopt
it instead. Surprise: low.

**10. A `null` model fails with `AttributeError`.** **Fixed** (`EKB-0304`): refused first, with
Java's messages. Java checks first and says "Model cannot be
null" / "Model in collection cannot be null"; Python fails on the first attribute it reads
(`'NoneType' object has no attribute 'id'` / `'next_version'`). Nothing is written in either.
Java `repository/AbstractRepository.java:353, 374, 393, 457`; Python `repository.py:702, 729,
820`. Could match: yes, a check at the top of each method. Surprise: low.

**27. `findAllByIds(null)` raises.** Java returns an empty list for `null`; Python raises
`TypeError`. Java `repository/AbstractRepository.java:716-718`; Python `repository.py:585`.
Could match: yes, one line. Surprise: low.

**15. The single-row stale message names no row.** Java: "Model Dummy[id=..., version=2] was
concurrently modified or not found". Python: "expected to update 1 Wallet row(s) but matched 0;
one was concurrently modified or is gone" - the id and the version read are missing, so a log
line cannot say which row lost the race. Batch messages carry counts only in both. Java
`repository/AbstractRepository.java:477-479, 493-495, 526-528`; Python `repository.py:851-855`.
Could match: yes, add id and version when the batch is one row. Surprise: low.

**44. The cross-shard error names the shards in sorted order, and gives the wrong hint.** Java
names the first shard of the batch, then the first that differs (`[B, A]` gives "started on
(1, 0) ... (0, 0)"); Python sorts, so it always says "started on 0:0 and then reached 1:0",
whatever the batch order. Its text also says "Pass allow_cross_shard=True", which is an executor
option a repository call cannot take. Java `repository/AbstractRepository.java:865-880`; Python
`repository.py:348-354`, `errors.py:73-80`. Could match: yes - keep first-seen order, and a
repository-specific message. Surprise: low.

**40. A transaction block hands back the real exception.** Java's `inTransaction` wraps whatever
the block throws - a `StaleRecordException` included - in a `RuntimeException`; Python's
`transaction()` re-raises the original. Belongs to area C; recorded here because repository
errors reach the caller this way. Java `persistence/TransactionManager.java:137-156`; Python
`persistence.py:194`. Could match: yes, but wrapping would only make Python worse to use.
Surprise: low.

**56. Two repositories for one model are refused** (Dracula 61). Java's registry keeps the last;
Python's raises. Java `repository/RepositoryRegistry.java:72`; Python `repository.py:1237-1249`.
Could match: yes; it is a deliberate choice. Surprise: low.

**58. Looking up an unregistered model raises.** Java's `repository(Class)` returns `null`;
Python's raises `ValidationError` naming the model and what is registered. Java
`repository/RepositoryRegistry.java:45`; Python `repository.py:1251-1267`. Could match: yes, by
returning `None`; the docstring explains why it does not. Surprise: low.

## Totals

63 behaviours: 46 same, 12 differs, 5 read. Since fixed by group T (`EKB-0304`): rows 9 and 10.
