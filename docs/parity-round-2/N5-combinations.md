# N5: combinations the round-1 fixes now meet

**Scope and what was run.** Round 1's fixes (nested transactions, events, the pool, JSON and
timestamps, sharding) were each tested alone; here they were run together, end to end, in both
ports against the same real databases (PostgreSQL 17, MySQL 9.4, MariaDB 11.8, fixed ports
58601-58603, lock waits bounded at 4 s so nothing hangs). Both ports used the very tables Java's
`local-event-handler` integration tests migrate (`eventlog.events`, `eventlog.event_notifications`,
`widgets`, `scheduled_tasks`), and a Python `Widget` shaped as Java's. Four scenarios: **(a)**
an action, repository calls, an event handler, a distributed job and a keyed lock, each inside or
around a caller's transaction - what commits, what is visible, what blocks (all three databases);
**(b)** a sharded flow on two PostgreSQL databases - action on shard 1, its outbox row, fan-out,
a failing handler retried, the default-shard fallback and the `fail` policy; **(c)** decimals,
dates, times, bytes, uuids and typed ids in params and payloads, written by one port and read
(fan-out + handling + repository) by the other, on all three databases; **(d)** a whole app
(registry, executor, keyed locks, job registry running fan-out and handling) started and stopped
with the right password and with a wrong one - times, connections, threads. Every row below was
run in both ports; Java ran as a temporary JUnit test in the throwaway worktree (deleted since).
Probes and outputs: [`parity-probes/round2-N5/`](../../parity-probes/round2-N5/).

**Result: the fixes hold together.** Every combination behaves the same in both ports on all three
databases. The six differences left are all `leave`: two were already decided (Java reads JSON
numbers through a double; Java cannot read a typed id back), the rest are wording, an error type,
and timing noise.

## Differences

| # | Difference (plain, one line) | Java | Python | Verdict | Why (one line) | Evidence |
|---|---|---|---|---|---|---|
| 1 | Reading a decimal from a payload or params: Java loses digits and scale, Python keeps them | `10.50` -> `10.5`; `12345678901234567890.123456789` -> `12345678901234567000` (PG), `1.2345678901234567E+19` (MariaDB); `1E-7` -> `1.0E-7` | exact on PG and MariaDB, whatever the column holds | leave | already decided (`EKB-0290`, "Java reads through a double"); the stored text is identical, so this is Java's reader, not the data | `c.*.java-read.out` vs `c.*.python-read.out`: `event.amount/big/tiny` |
| 2 | A typed id (`ShardedId`) in action params: Java cannot read it back, from either port's row | `InvalidDefinitionException: Cannot construct instance of ShardedId (no Creators)` | reads it | leave | already decided (J: "Java cannot read either back"); Java writes it the same as Python | `c.*.read_other.params.decoded` |
| 3 | The unknown-shard `fail` refusal names the setting in each port's spelling | `Shard ShardIdentifier(0, 5) is not registered and unknownShardPolicy is FAIL; registered: [...]` | `shard 0:5 is not registered and unknown_shard_policy is 'fail'; registered: ['0:0', '0:1']` | leave | same point, same outcome, nothing written; the message names the key the reader actually configured | `b.pg.*.out`: `b3.error_message` |
| 4 | Pool used up (an action inside a caller's transaction on a pool of 1): the error type and text | `RuntimeException: Failed to acquire connection` <- Hikari "Connection is not available, request timed out after 30009ms" | SQLAlchemy `TimeoutError: QueuePool limit of size 1 overflow 0 reached, connection timed out, timeout 30.00` | leave | both wait the full 30 s and name the cause; a new type would break every `except TimeoutError` (the round-1 pool decision's reasoning) | `a.pg.*.out`: `a5.*` |
| 5 | Stopping an app whose password is wrong takes a while, a little longer in Python | 11.0 s, 15.5 s, 11.0 s (3 runs) | 20.0 s, 20.0 s, 20.0 s (3 runs) | leave | in both, stop waits out a connection attempt already in flight (bounded by the 30 s pool timeout); with a good password both stop at once | `d.pg.*.out`, `n5-d-*.out`: `d2.stop_took`; `d3.pg.python.out` |
| 6 | Threads left after a clean stop | none of the app's; the JDK's shared virtual-thread carriers (`ForkJoinPool-#-worker-#`, `Read-Poller`...) stay | none at all | leave | the leftovers are the JVM's, started by db-scheduler's virtual threads, not Ekbatan's | `d1.threads_left_after_stop` |

No `fix`, no `needs you`.

## Same in both ports (run on PostgreSQL, MySQL and MariaDB unless marked)

What a Java developer can rely on in the Python port, as measured:

- **a1** An action run inside a caller's transaction commits on its own: the caller rolls back,
  the action's row and outbox row stay, the caller's own row is gone.
- **a2** An action that throws inside a caller's transaction writes nothing; the caller catches it
  and its own row still commits; the message reaches the caller unchanged.
- **a3** A repository write inside a caller's transaction joins it (invisible outside until
  commit). Inside it, the built-in `find` does **not** see that uncommitted row, `tx_db_else_db`
  does; an action run inside it does not see it either; a nested `transaction()` does not; after
  the nested block the caller's transaction is current again.
- **a4** The caller holds a row lock, then runs an action that updates the same row: the action
  blocks on its own caller, fails at the database's lock timeout (4 s here), is not retried, and
  the caller's transaction is still usable and commits. On PostgreSQL's default (no lock timeout)
  both would wait for ever (`read`: not run, by design of the probe).
- **a5** (PG) The same on a pool of one: the action waits the full 30 s pool timeout, then fails;
  nothing written.
- **a6** An event handler opening its own transaction around an action, failing after it: no
  transaction is open when the handler starts; its own transaction rolls back but the action
  inside has committed, so the retry (after Java's 30 s backoff) runs it again - two widgets, one
  side row; notification `FAILED/1` then `SUCCEEDED/2`.
- **a7** A distributed job doing the same: no open transaction at start; the action commits, the
  job's own row rolls back; db-scheduler records one failure.
- **a8** A keyed lock held around nested work: re-entrant on the same thread inside the
  transaction, refused to another thread (500 ms wait), granted after release; the work commits.
- **b1** (PG, 2 databases) An action on shard 1: widget and outbox row only in shard 1's database;
  fan-out writes the notification there and marks the event delivered there; the failing handler's
  notification is `FAILED/1` due in 30 s, then `SUCCEEDED/2`.
- **b2** (PG) An unknown shard (0:5) under the default policy: everything lands in the default
  database, the id keeps shard bits 0:5, `find` by that id finds it, it is fanned out and handled
  from the default shard.
- **b3** (PG) Under `fail`: the write and the read are both refused; nothing written anywhere.
- **c1** The stored JSON (params and payload) is identical, byte for byte, whichever port wrote it,
  on all three databases: PostgreSQL's `jsonb` reorders keys, MySQL keeps fractions as doubles
  (`10.50` -> `10.5`, the long decimal -> `1.2345678901234567e19`), MariaDB keeps the text.
- **c2** Timestamps: both store the UTC wall time to the microsecond, the same text in all five
  columns; each port reads the other's back as the same instant
  (`2026-03-29T01:30:00.123456Z`), through the envelope and through the repository.
- **c3** On MySQL both ports read back the same rounded numbers (the database's doubles).
- **c4** Dates, `LocalDateTime`, `LocalTime`, bytes (base64), uuids and `0.1` as a double read
  back equal across the ports; a handler in one port receives the other port's events by event
  type name, with `modelId` and `modelType` intact.
- **d1** (PG) A healthy app: building and starting it return at once, the pools fill in
  the background to 11 connections (5 + 3 + 3) within 2 s, an action reaches its handler within
  3 s, stop is immediate, 0 connections left.
- **d2** (PG) A wrong password: building returns at once; `start()` throws after 90 s, naming "password authentication failed";
  an action fails after 30 s with the same cause; the wrong password appears nowhere (checked with
  a marker in every output and log).

## Totals

25 checked: 19 same, 0 fix, 6 leave, 0 needs you.
