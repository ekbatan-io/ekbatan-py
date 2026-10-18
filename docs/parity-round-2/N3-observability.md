# N3 - Observability: log lines, spans, metrics

**Scope and what was run.** The same scenarios ran in both ports with an in-memory OpenTelemetry
SDK (spans and metrics) and every log record captured (logger, level, text, exception attached or
not): an action that succeeds, with and without a principal; one retry then success; perform
fails; persist fails (rollback); retries run out; cross-shard allowed, refused, and partly
committed; an unknown shard falling back to the default; direct repository writes; a lock wait
that times out, a hold limit that runs out, a lock release that fails (backend killed); fan-out;
a handling round with a passing, a failing and an `Error`-throwing handler; pre-flight and
post-failure expiry; a job registry starting, running an ok and a failing job, and stopping; the
migrator on two shards. Both ran on PostgreSQL 17 with login `probe` and a marker password, every
logger at DEBUG (Java: TRACE), and every log record, span and metric searched for the marker.
Probes and outputs: [`parity-probes/round2-N3/`](../../parity-probes/round2-N3/) (`obs_*` actions,
transactions, locks, migrator; `events_*` events, jobs, metrics; `redis_metric_*`). Logger names
were matched in `EKB-0248`: every Python line seen in these runs was written under the same logger
name as Java's matching line. Not raised again: the migrator's success lines (decided, item 84),
how a shard prints (`0:0`, Dracula 66), Java's "No username" warning (round 1, C84), a missing
handler not counting as a failure (Dracula 73).

| # | Difference (plain, one line) | Java | Python | Verdict | Why (one line) | Evidence |
|---|---|---|---|---|---|---|
| 1 | A failing event handler is logged without its stack trace, and without the event id | WARN `Handler 'fail-handler' threw RuntimeException while processing event <event id> (attempts=0): handler boom` + exception | WARN `handler 'fail-handler' failed on attempt 1 (RuntimeError: handler boom)`, no exception | fix | the operator debugging a broken handler has no traceback to read | `events.*` `e2_handle_round.log.*` |
| 2 | Discarded notifications: one ERROR per row in Python, one per handler in Java, different text and id | ERROR `DISCARDED 2 event notification(s) for handler 'ok-handler' - each was already past the retention window ... (PT24H). These events will never be delivered. Example event: <event id> (attempts=0).` | ERROR per row: `notification <notification id> for handler 'ok-handler' was discarded: WidgetCreatedEvent was already past its retention window ...` | fix | after an outage Python writes thousands of ERROR lines; an alert on `DISCARDED` never fires | `e3_expired_preflight.log.*`, `e4_expired_postfailure.log.*` |
| 3 | A partial cross-shard commit is logged at CRITICAL with no stack trace | ERROR `CRITICAL: Cross-shard action ... PARTIALLY COMMITTED! ...` + exception | CRITICAL `... PARTIALLY COMMITTED: ...`, no exception | fix | an alert matching level ERROR misses it, and the cause's traceback is lost | `obs.*` `a8_partial_commit.log.*` |
| 4 | A failed action with no retry has no `ekbatan.action.retry.count` on its span | `retry.count=0` on every execute span | missing when the first attempt fails | fix | a dashboard filtering on `retry.count` loses every failed action | `a3_*`, `a4_*`, `a8_*` `.span.*` |
| 5 | A failed lock release does not name the key | ERROR `Failed to release advisory lock for key order-3; evicting connection` + exception | ERROR `failed to release a database lock; discarding its session` + exception | fix | the operator cannot tell which lock may still be held | `l3_release_fails.log.0` |
| 6 | Lists in log lines and one span attribute print with Python quotes | `[ok-job, failing-job]`, `committed_shards=[ShardIdentifier(0, 0)]` | `['ok-job', 'failing-job']`, `committed_shards=['0:0']` | fix | a `repr` of a list leaking into text meant for people | `j1_start.log.0`, `a6_*`/`a8_*`/`a9_*` |
| 7 | Nearly every log message is worded differently (case, words, values) | `Executing X [principal=a]`, `Transaction rolled back [shard=(0,0)]: E`, `Retrying X (retry 1/1) due to E: m`, `Shard ... is not registered` | `executing X [principal=a]`, `transaction rolled back [shard=0:0]: E`, `retrying X (1/1) after E: m`, `shard ... is not registered` | needs you | levels and loggers match; a Java log search or alert written on the text misses most Python lines | every `.log.*` key |
| 8 | A failed span's status text starts with the exception type | `ERROR:perform boom` | `ERROR:RuntimeError: perform boom` | leave | OpenTelemetry Python's own default; the type is also on the exception event | `a3_perform_fails.span.*` |
| 9 | The exception event carries `exception.escaped=False` and the Python class path | `exception.type=java.lang.IllegalStateException` | `exception.type=RuntimeError`, `exception.escaped=False` | leave | written by the OpenTelemetry SDK, not by Ekbatan | same |
| 10 | Three DEBUG lines Java writes are missing or later in Python | `Resolved 1 entity types across 1 shards ...`, `Persisted events for action X [actionEventId=...]`, `Persisting ...` before the writes | none, none, `persisting ...` after the writes (so a failed write has none) | leave | DEBUG only; nobody alerts on them | `a1_success.log.*`, `a4_*.log.*` |
| 11 | A refused cross-shard action gets one ERROR in Python, two in Java | `X rejected: spans 2 shards [...] but cross-shard is disabled` then `X failed: CrossShardException: ...` | only `X failed: CrossShardError: ...`, which names both shards | leave | the one line carries the same facts | `a7_cross_shard_rejected.log.*` |
| 12 | Python adds a WARN per failed notification saying when it retries | none | `notification <id> for handler 'x' will be retried in 0:00:30` | leave | more information, not less; one extra line per failure | `e2_handle_round.log.4-7` |
| 13 | A handler raising a non-`Exception` is a WARN in Python; Java's `Error` is an ERROR with stack trace | ERROR `Handler 'error-handler' threw Error AssertionError ...` + exception | WARN `handler 'error-handler' failed on attempt 1 (Fatal: error boom)` | leave | Python's non-`Exception` errors are `KeyboardInterrupt` and `SystemExit`; row 1's fix adds the traceback anyway | `e2_handle_round.log.*` |
| 14 | Python has one metric Java lacks, under the events meter's scope | no metric in `ekbatan-keyed-lock-redis` | `ekbatan.locks.release_failures` `{release}`, scope `io.ekbatan.events.localeventhandler` | leave | an extra counter harms no dashboard; the odd scope is cosmetic | `redis_metric.python.out` |
| 15 | The scheduler's stop line says a different wait | `Will wait up to 2xPT30M.` | `Will wait up to 0:30:00.` | leave | db-scheduler's own line; the single wait is Dracula 35, decided | `j3_stop.log.2` |
| 16 | Logger names of every line seen | Java class names | the same names | same | `EKB-0248` holds | all `.log.*` |
| 17 | Span names, scope `io.ekbatan.core`, kind, parent/child tree | execute > perform, persist > transaction > repository, event.persist | the same, in the same order | same | | all `.span.*` |
| 18 | Span attributes on success, cross-shard and partial commit (keys) | as listed | the same keys and values (shard text aside, rows 6 and 7) | same | | `a1_*`, `a6_*`, `a8_*` |
| 19 | Retry span event and `retry.count` after a retry | `retry{retry.count=1, retry.exception=StaleRecordException}` | the same | same | | `a2_*`, `a5_*` |
| 20 | The repository span only on the no-result batch writes | `addAllNoResult` yes, `addAll` no | the same | same | | `a10_*`, `a11_*` |
| 21 | Metrics: all five instruments | `ekbatan.events.handled`, `.handler.duration`, `.delivery.lag`, `.fanned_out`, `.notifications_created` | the same names, units, types, descriptions, scope, temporality, attributes, values and bucket count | same | | `metric.*` |
| 22 | Fan-out and handling spans | one `ekbatan.transaction` for fan-out, none for handling | the same | same | | `e*.spans` |
| 23 | Log levels and exception attachment of action, retry, rollback, unknown-shard, hold-limit and lock-release lines | INFO / WARN / ERROR as Java | the same level and attachment (row 3 aside) | same | | all `.log.*` |
| 24 | Unknown shard WARN written twice per action | twice | twice | same | | `a9_unknown_shard.log.*` |
| 25 | A lock wait that times out writes nothing | nothing | nothing | same | | `l1_wait_times_out.*` |
| 26 | Job lines: started, finished, failed with exception, starting and stopping the registry | as Java | the same text and level (row 6 aside) | same | | `j1_*`, `j3_*` |
| 27 | db-scheduler's own lines (start, initial execution, unhandled exception, shutdown) | as listed | the same text from the Python port (row 15 aside) | same | | `j1_*`, `j3_*` |
| 28 | Marker password in any framework log line | 0 (3 hits, all testcontainers' docker client creating the container) | 0 | same | | `secret.*` |
| 29 | Marker password in any span or metric | 0 | 0 | same | | `secret.*` |

## Fix and needs-you details

**1. Handler failure without traceback or event id.** Java
`EventHandlingJob.java:396-404` passes the exception and names the event id and stored attempts.
Python `events/dispatch.py:255-261` logs the handler and attempt only, with no `exc_info`. Cost:
add `exc_info=error` and the row's `event_id`; one line, one test.

**2. Discarded notifications, grouped.** Java `EventHandlingJob.java:307-308`, `:326-342`
(`logDiscarded`): one ERROR per handler per batch, with the count, the retention, one example
event id and its attempts. Python `events/dispatch.py:224-230` (pre-flight) and `:286-291`
(post-failure) log one line per row, with the notification id. Cost: collect expired rows during
`_drain_shard` and log once per handler after the writes, with Java's text; small, plus test
updates.

**3. Partial commit at CRITICAL without traceback.** Java `ActionExecutor.java:302-311`: ERROR,
exception attached. Python `action/executor.py:411-419`: `LOG.critical`, no `exc_info`. Cost:
`LOG.error(..., exc_info=error)`; one line.

**4. `retry.count` missing on a failed first attempt.** Java `Retry.java:31` sets it at the start
of every attempt (0 first). Python `action/executor.py:320` and `:332` set it only on a retry and
on success. Cost: set `{"ekbatan.action.retry.count": retried}` at the top of each attempt; one
line.

**5. Lock release failure without the key.** Java `PostgresKeyedLockProvider.java:207` (MySQL
`:217`, MariaDB `:212`) name the key. Python `locks/sql.py:140`: the payload is `(hashed, conn)`
(`:129`), so the key is not at hand. Cost: carry the key in the payload and put it in the message;
small.

**6. Lists printed with Python quotes.** Java prints `List.toString()`: `[a, b]`. Python
`distributedjobs/__init__.py:171` passes the list itself; `action/executor.py:384-388`, `:407`,
`:412` and `persistence.py:359` pass lists of strings, so they print `['0:0', '1:0']`. Cost: join
with `", "` inside brackets; four places. The shard text itself (`0:0` against
`ShardIdentifier(0, 0)`) is Dracula 66 and stays apart.

> **Fixed (`EKB-0317`)**, with items 1, 2, 3, 5 and 6, which are the same lines: every Ekbatan log
> line now has Java's text, level and attached exception. The probes now match 75 of 107 Java lines
> exactly (2 before); the rest differ only in language-specific exception names, the error
> messages themselves, handler order, db-scheduler's own lines and the migrator's (decided).

**7. Message wording (needs you).** `EKB-0248` matched logger names and deliberately left the texts
("messages differ in places"); that was a call made while building, not the user's. Java's texts
are in the files named above plus `TransactionManager.java:169-200`, `Retry.java:40-46`,
`DatabaseRegistry.java:116-120`, `KeyedReentrantHolder.java:154`, `ChangePersister.java:83`; the
Python ones in `action/executor.py`, `persistence.py:176-205`, `:358-363`, `locks/_holder.py:99`,
`action/persister.py:87`. Matching them is mechanical (about twenty strings), plus the tests that
assert on the old texts. The open question is only whether log text counts as something a Java
team relies on (log searches, alerts); the shard inside the text would still print as Dracula 66
says unless that changes too.

## Totals

29 checked: 14 same, 6 fix, 8 leave, 1 needs you.
