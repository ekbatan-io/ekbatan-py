# Area D: events (local event handler)

Scope: the local event handler in both ports. That means fan-out (events to per-handler
notification rows), the handling job (calling handlers, retries, backoff, expiry), the handler
registry, the two event repositories, the config object, and the job defaults. Java:
`ekbatan-events/local-event-handler/src/main/java`. Python: `src/ekbatan/events/`. Every row was
settled by running the same scenario in both ports against PostgreSQL 17 (Testcontainers), with a
fixed clock at `2026-01-01 10:00:00` UTC. Events and notification rows were inserted with the same
SQL on both sides, so the rows the jobs read are byte-for-byte the same. MySQL and MariaDB were
not run. Run on 2026-10-17.

Probes: [`parity-probes/D-events/`](../../parity-probes/D-events/). Java:
`ParityDProbeTest.java` (it ran as a temporary test in the PG local-event-handler integration
project, in a throwaway copy of the Java repo). Python: `probe_events.py`. Outputs: `java.out`
and `python.out` (sorted `PROBE` lines). The Probe column below gives the `PROBE` key prefix.

| # | Behaviour | Java | Python | Verdict | Probe |
|---|---|---|---|---|---|
| 1 | Fan-out picks undelivered events of a subscribed type, oldest first; unsubscribed and sentinel (no type) rows stay undelivered | yes | yes | same | `f.delivered.*`, `c.fanout_took_oldest` |
| 2 | One notification per (event, handler), written on the event's own shard | 6 on shard 0, 2 on shard 1 | same | same | `f.s0.*`, `f.s1.*` |
| 3 | The notification copies the event's columns exactly | 0 mismatches | 0 | same | `f.copied_mismatch` |
| 4 | New notification: `PENDING`, attempts 0, created/updated = fan-out clock | yes | yes | same | `f.s0.*` |
| 5 | New notification's `next_retry_at` | the event's `event_date` | the fan-out time | differs | `f.s0.*`, `f.late.e3_row` |
| 6 | Fan-out run again, or an event flipped back to undelivered, adds no duplicate rows | 6 rows stay 6 | same | same | `f.round2`, `f.refanout.*` |
| 7 | A handler registered after its events exist still gets them | yes | yes | same | `f.late.*` |
| 8 | Envelope: decoded event, event id, action id/name/params, dates, model id/type; an unknown payload field is ignored | all equal | all equal | same | `f.received.*`, `f.after.s0.e6.*` |
| 9 | Envelope tells the handler which attempt this is | no such field | `attempt` 1..8 | read | `b.attempt_seen_by_handler` (Java's envelope has no field to run against; Dracula 76) |
| 10 | Success: `SUCCEEDED`, attempts +1, updated = handling time | yes | yes | same | `f.after.*`, `e.round1.ok` |
| 11 | Backoff after each failure; nothing runs one second early | 30, 60, 120, 240, 300, 300 s (cap 5 min) | same | same | `b.step*` |
| 12 | Handler throws an ordinary exception | `FAILED`, attempts 1, +30 s | same | same | `e.round1.fail` |
| 13 | Handler throws an `Error` (Java) / a `BaseException` (Python); a sibling's success is still recorded | `FAILED`, sibling `SUCCEEDED` | same | same | `e.round1.error`, `e.round1.ok` |
| 14 | Row whose handler is no longer registered | `FAILED`, attempts 1, 2, 3; backoff grows | `PENDING`, attempts 0; retry every 30 s | differs | `e.round*.ghost` |
| 15 | Rows queued under an old name are drained by the handler that lists it as an alias; new rows use the new name | yes | yes | same | `e.alias.*` |
| 16 | Row exactly at `event_date + retention` when claimed | handler called, `SUCCEEDED` | `EXPIRED`, handler not called | differs | `x.boundary.exact` |
| 17 | Row past the retention window when claimed | `EXPIRED`, attempts unchanged, handler not called | same | same | `x.boundary.one_second_older` |
| 18 | When a failing row expires | as soon as the next retry would land past the deadline (5th failure at +450 s) | only once the deadline has passed (still `FAILED` at +450 s, `EXPIRED` at +750 s) | differs | `x.step*`, `x.near_deadline.*` |
| 19 | Event whose `event_date` is later than the clock | not delivered until `event_date` | delivered at once | differs | `c.future_event.*` |
| 20 | Handling order when an older event is fanned out after a newer one | older event first | fan-out order (newer first) | differs | `c.order.*` |
| 21 | One outcome write fails (a trigger refuses `FAILED`) while a sibling succeeded | sibling stays `SUCCEEDED`; handler called once | whole batch rolled back; sibling back to `PENDING`, its handler called again | differs | `t.outcome_write_failed.*` |
| 22 | `findUndelivered` inside an open transaction on the shard | does not see the transaction's own rows (0) | sees them (1) | differs | `t.find_undelivered_*`, `t.undelivered_after_mark_in_same_tx` |
| 23 | `findDue` inside an open transaction on the shard | does not see them (0) | sees them (1) | differs | `t.find_due_sees_own_uncommitted` |
| 24 | `markDelivered` / inserts join the open transaction and roll back with it | yes | yes | same | `t.mark_delivered_rolled_back`, `t.after_rollback_events` |
| 25 | Where the reads go when the shard has a replica (replica empty, primary has the rows) | `findUndelivered` replica (fan-out finds nothing); `findDue` primary (handler called) | the other way round: fan-out works, handling finds nothing | differs | `r.*` |
| 26 | Handlers in a batch, and shards, run at the same time | 5 at once (3 + 2 over two shards) | 5 | same | `c.max_concurrent_handlers` |
| 27 | Default batch sizes (no size given) | fan-out 200, handling 100 | same | same | `c.default_*_batch` |
| 28 | Default job names | `ekbatan-event-fanout`, `ekbatan-event-handling` | same | same | `j.*.name` |
| 29 | Hand-built handling job's schedule | `FixedDelay` (runs) | `DisabledSchedule` (never runs) | differs | `j.handling.schedule_default` (Dracula 11) |
| 30 | Config defaults | every knob empty, `handling.enabled` false | same | same | `j.config.defaults_empty`, `j.config.handling_enabled_default` |
| 31 | Config with batch size 0, poll delay 0, negative retention | accepted (checked later, by the job) | refused when the config is built | differs | `j.config.*` |
| 32 | Job checks: blank name, batch size 0, no clock | blank name ok; batch 0 refused; no clock refused | blank name refused; batch 0 accepted by `EventFanout`; no clock means the system clock | differs | `j.job.*` |
| 33 | Registry refusals: same handler twice, duplicate name, alias equal to a name, alias equal to own name, blank alias/name, two event classes with one simple name | refused | refused, same messages | same | `g.*` (EKB-0251 re-confirmed) |
| 34 | Order of the registry's handled event types | registration order | sorted | differs | `g.handled_event_types` |
| 35 | Subscribers in registration order; unknown type gives none; alias lookup; unknown name gives nothing | yes | yes | same | `g.subscribers_*`, `g.lookup_*` |
| 36 | Fan-out with no handlers registered does nothing | yes | yes | same | `j.empty_registry_fanout.any` |
| 37 | A round reports whether it found work | true / false | same | same | `*.any` |

## Differences

Most surprising first.

### 25. The replica is read at the opposite stage
- **What:** with a replica configured, Java's fan-out reads new events from the replica and the
  handling job reads due rows from the primary. Python does the reverse. With a lagging replica,
  Java delays fan-out; Python delays delivery, and a replica that has not yet seen a row marked
  `SUCCEEDED` can hand it to the handler again.
- **Java:** `repository/EventEntityRepository.java:141` (`readonlyDb`), `repository/EventNotificationRepository.java:223` (`db`)
- **Python:** `events/repository.py:86` (`find_undelivered`, open transaction or primary), `events/repository.py:131` (`find_due`, `_readonly_db`)
- **Match Java?** yes, small: swap the two connection choices. It needs a read that never joins
  the open transaction (see 22).
- **Surprise:** high

### 29. A hand-built handling job never runs (known: Dracula 11)
- **What:** Java's `EventHandlingJob` always has a `FixedDelay` schedule; the on/off switch lives in
  the DI wiring. Python's job reads `handling.enabled` (default false) and returns
  `DisabledSchedule`.
- **Java:** `job/EventHandlingJob.java:159`
- **Python:** `events/jobs.py:186`, `events/jobs.py:206`
- **Match Java?** yes, small: default a hand-built job to on.
- **Surprise:** high

### 22, 23. Event repository reads join the open transaction
- **What:** inside an open transaction on the shard, Python's `find_undelivered` and `find_due`
  see that transaction's uncommitted rows. Java's do not: `findUndelivered` uses the replica
  connection and `findDue` a plain primary connection, both outside the transaction. Same class of
  difference as bug 8. Python's `_readonly_db` docstring says "Never the open transaction", but it
  calls `connection(readonly=True)`, which joins it.
- **Java:** `repository/EventEntityRepository.java:141`, `repository/EventNotificationRepository.java:223`
- **Python:** `events/repository.py:86`, `events/repository.py:54-56`, `persistence.py:229`
- **Match Java?** yes, moderate: needs a connection path that skips the open transaction. Python's
  fan-out reads and marks delivered in one transaction on purpose, so matching changes that too.
- **Surprise:** medium

### 21. Outcome writes are one transaction in Python, separate statements in Java
- **What:** after a batch, Java writes each outcome group as its own auto-committed `UPDATE`; Python
  writes them all in one transaction. When one write fails, Java keeps the ones already written;
  Python rolls back all, so a handler that already succeeded is called again next round.
- **Java:** `job/EventHandlingJob.java:287-293`
- **Python:** `events/dispatch.py:183`
- **Match Java?** yes, small: drop the transaction and write in Java's order. It is a trade
  (Python's is all-or-nothing on purpose).
- **Surprise:** medium

### 5, 19, 20. A new notification is due at the fan-out time, not the event time
- **What:** Java sets `next_retry_at` to the event's `event_date`; Python to the fan-out clock.
  Effects seen: an event dated in the future is delivered at once in Python but waits in Java,
  and an older event fanned out late is handled first in Java but last in Python.
- **Java:** `job/EventFanoutJob.java:173`
- **Python:** `events/fanout.py:131`
- **Match Java?** yes, one line.
- **Surprise:** medium

### 18. A failing row expires later in Python
- **What:** Java expires a failed row as soon as its next retry would land past
  `event_date + retention`. Python marks it `FAILED` with a retry past the deadline and expires it
  at that retry, without calling the handler. Same number of handler calls; the row sits as
  `FAILED` longer, ends as a pre-flight expiry, and the "expired after failure" metric and log
  are not used.
- **Java:** `job/EventHandlingJob.java:274-277`
- **Python:** `events/dispatch.py:240`
- **Match Java?** yes, a few lines.
- **Surprise:** medium

### 16. Exactly at the retention edge
- **What:** a row claimed at exactly `event_date + retention` is delivered in Java (`isAfter` is
  strict) and expired in Python (`<=`).
- **Java:** `job/EventHandlingJob.java:215`
- **Python:** `events/dispatch.py:278`
- **Match Java?** yes, one character.
- **Surprise:** low

### 14. A missing handler is not a failed attempt (known: Dracula 73)
- **What:** Java counts it as a failure (attempts go up, backoff grows, it expires the usual way).
  Python leaves it `PENDING` with attempts 0, so the backoff stays at 30 s for good.
- **Java:** `job/EventHandlingJob.java:441`
- **Python:** `events/dispatch.py:218-226`
- **Match Java?** yes, small: raise inside the delivery as Java does.
- **Surprise:** low

### 31, 32. Config and job checks run at different times
- **What:** Python's config refuses batch size 0, poll delay 0 and negative durations when it is
  built; Java's config accepts them, and only the job builder refuses some. Java's job accepts a
  blank name and refuses a missing clock; Python refuses a blank name and uses the system clock.
  Python's `EventFanout(batch_size=0)` is accepted and would fan out nothing.
- **Java:** `config/LocalEventHandlerConfig.java:155`, `job/EventFanoutJob.java:70-77`
- **Python:** `events/config.py:66-77`, `events/jobs.py:344`, `events/fanout.py:68`
- **Match Java?** partly; the Python checks are stricter on purpose.
- **Surprise:** low

### 34. Handled event types come back sorted
- **What:** Java returns them in registration order, Python sorted. Only used in an `IN (...)` list,
  so nothing downstream changes.
- **Java:** `EventHandlerRegistry.java:67`
- **Python:** `events/handler.py:171`
- **Match Java?** yes, trivial.
- **Surprise:** low

**Totals:** 37 behaviours: 22 same, 14 differ, 1 read.
