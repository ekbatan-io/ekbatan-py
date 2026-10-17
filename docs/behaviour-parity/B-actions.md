# Area B: actions - behaviour parity, run in both ports

Part of [the behaviour-parity ledger](../BEHAVIOUR-PARITY-LEDGER.md).

**Scope.** The action executor, the action plan, the change persister, the outbox rows
(`eventlog.events`) and the action registry, plus the `ActionSpec` test helper. Every row below
was settled by running the same scenario in both ports on 2026-10-17: a temporary JUnit test in a
throwaway copy of the Java repo (module `postgres-sharded`, so two real PostgreSQL databases,
global `0:0` and mexico `1:0`), and a Python script against two PostgreSQL containers with the
same tables. Both print `PROBE` lines with the same keys. Sources and outputs are in
[`parity-probes/B-actions/`](../../parity-probes/B-actions/): `ParityBActionsProbeTest.java`,
`probe_actions.py`, `java.out`, `python.out`. Exception type names differ by language
(`IllegalStateException` / `ValidationError`, `StaleRecordException` / `StaleRecordError`,
Dracula 57); a row counts as `same` when the same call fails at the same point with the same
effect on the database.

| # | Behaviour | Java | Python | Verdict | Probe |
|---|---|---|---|---|---|
| 1 | `execute` returns what `perform` returned (added row: version 1, its event still attached) | yes | yes | same | s1.result_* |
| 2 | An added row lands at version 1, on the shard its id names, nowhere else | yes | yes | same | s1.wallets_*, s1.db_version |
| 3 | One outbox row per event: namespace, action name (class name), model type, event type, model id | yes | yes | same | s1.row.* |
| 4 | Outbox `id` and `action_id` are random v4 UUIDs | v4 | v4 | same | s1.row.*uuid_version |
| 5 | `delivered` is false on insert | false | false | same | s1.row.delivered |
| 6 | `started <= completion`, `event_date = completion`, stored as UTC | yes | yes | same | s1.row.*, s5.row.* |
| 7 | Timestamps come from the executor's clock, microseconds, no zone | `10:00:00.123456` x3 | same | same | s32.* |
| 8 | `action_params` JSON shape | `{"countryCode": "DE"}` | `{"countryCode": "DE"}` | same (fixed in EKB-0290) | s1.row.action_params, s2.action_params |
| 9 | Event `payload` JSON shape | camelCase, decimals as numbers | camelCase, decimals as numbers | same (fixed in EKB-0290) | s1.row.payload_minus_ids, s2.payload_minus_ids |
| 10 | Update: caller gets the next version; row moves to version+1; `updated_date` untouched | yes | yes | same | s2.* |
| 11 | Events composed before staging all land, in order, under one `action_id` | 2 rows | 2 rows | same | s3.* |
| 12 | Update with no new event still writes one row with the event columns empty | yes | yes | same | s4.outbox_rows, s4.event_type, s4.model_type |
| 13 | How an empty `payload` is stored (no-event row) | SQL `NULL` | SQL `NULL` | same (fixed in EKB-0291) | s4.payload*, s5.row.payload* |
| 14 | Empty plan: one row on the default shard, params `{}`, returns null | yes | yes | same | s5.* |
| 15 | Principal reaches `perform` (null allowed) and is not stored anywhere | yes | yes | same | s1.principal_seen, s40.* |
| 16 | `perform` throws: the same exception object reaches the caller, nothing written, no retry | yes | yes | same | s6.* |
| 17 | `perform` throwing a stale-record error is retried like a commit conflict | 2 attempts | 2 attempts | same | s7.* |
| 18 | Optimistic-lock conflict at commit: one retry after >= 100 ms, then the stale error, nothing written | yes | yes | same | s8.* |
| 19 | `noRetry` / `no_retry`: one attempt | 1 | 1 | same | s9.* |
| 20 | `withRetry` for the same class replaces the default | 4 attempts | 4 attempts | same | s10.attempts |
| 21 | Retry matches the exact class; a subclass is not retried | 3 / 1 | 3 / 1 | same | s11.exact_attempts, s11.subclass_attempts |
| 22 | Adding a retry for another class keeps the default stale-record retry | 2 attempts | 2 attempts | same | s11.default_dropped_when_other_added |
| 23 | The cause chain is followed: a wrapper around a stale error is retried, and the wrapper reaches the caller | yes | yes | same | s12.* |
| 24 | Each attempt gets a fresh plan; the result is the successful attempt's | yes | yes | same | s13.* |
| 25 | A plan touching two shards is refused by default, before anything is written | yes | yes | same | s14.wallets_*, s14.outbox_* |
| 26 | Which shards the cross-shard error names (staged mexico first, then global) | active 1:0, requested 0:0 | active 0:0, requested 1:0 | differs | s14.active_shard, s14.requested_shard |
| 27 | Cross-shard allowed: each shard gets its own rows and outbox rows, one shared `action_id` and start time; shards commit in staging order | yes | yes | same | s15.* |
| 28 | Partial commit, no retry: the first shard stays committed, the failing one rolls back, stale error to the caller | yes | yes | same | s16.* |
| 29 | Partial commit with the default retry: the whole action runs again, so the committed shard gets a second copy | 2 wallets | 2 wallets | same | s17.* |
| 30 | An id naming an unconfigured shard routes the row and its outbox row to the default shard | yes | yes | same | s18.* |
| 31 | An unregistered action is refused before `perform` | refused | refused | same | s19.* |
| 32 | `null` / `None` params | refused at commit, rolled back | stored as `{}`, committed | differs | s20.* |
| 33 | Scalar params refused at commit, rows rolled back | yes | yes | same | s21.* |
| 34 | One row staged twice (add+add, add+update, update+update) refused, not retried, nothing written | yes | yes | same | s23.* |
| 35 | Two event classes with one simple name: the second is refused and rolled back; the first keeps working | yes | yes | same | s26.* |
| 36 | Rows and outbox rows are one transaction: another connection sees nothing mid-commit; a failing event persister rolls the rows back | yes | yes | same | s27.* |
| 37 | A read inside `perform` does not see what the plan staged | false | false | same | s28.* |
| 38 | `execute` inside `perform` commits on its own; the outer plan is untouched; the outer failing does not undo it | yes | yes | same | s29.* |
| 39 | Reaching the plan outside an execution, or from a thread started inside one, is refused | refused | refused | same | s30.*, s39.plan_outside |
| 40 | Two concurrent deposits on one row: one conflicts, is retried, both succeed (3 attempts, balance 12) | yes | yes | same | s31.* |
| 41 | An action built with a different clock from the executor's | accepted | refused at build | differs | s32.action_clock_differs_from_executor_clock |
| 42 | Duplicate primary key: the driver's integrity error reaches the caller, not retried | yes | yes | same | s33.* |
| 43 | Namespace rule (null, blank, hyphen, leading digit, empty segment, trailing dot, newline, non-ASCII refused; `a.b_c`, `_x.y1` accepted) | 11 / 11 | same | same | s34.* |
| 44 | Retry config refuses negative retries and negative delay; 0 retries is fine | yes | yes | same | s35.* |
| 45 | Two instances of one action type registered | second replaces first | refused | differs | s36.* |
| 46 | Executor builder with no event persister | builds the default | refused | differs | s37.* |
| 47 | Executor with an empty repository registry | builds | refused | differs | s38.empty_repository_registry |
| 48 | A staged type with no repository is refused before anything is written | yes | yes | same | s38.unknown_type_exception, s38.outbox |
| 49 | Insert order within one action: additions before updates, each in staging order | yes | yes | same | s42.* |
| 50 | `execute` inside a transaction the caller opened, which then rolls back | action stays committed | action rolled back too | differs | s43.* |
| 51 | `ActionSpec`: a passing chain passes | yes | yes | same | s39.pass_chain |
| 52 | `ActionSpec` failure messages (count, missing event, missing update, no changes, totals, no events, not emitted) | word for word | word for word | same | s39.*_wrong, s39.*missing, s39.no_* |
| 53 | `ActionSpec`: events match subclasses; additions match the exact class | yes | yes | same | s39.emitted_base_class_count, s39.added_base_class |
| 54 | `ActionSpec`: a failing verifier's error propagates; the result verifier gets the result | yes | yes | same | s39.verifier_failure, s39.result_verifier_gets_result |
| 55 | `ActionSpec` writes nothing and passes the principal | yes | yes | same | s39.nothing_persisted, s39.principal_seen |
| 56 | `ActionSpec.assertThrows`: subclass accepted, wrong type and nothing-thrown messages, verifier runs | yes | yes | same | s39.throws_* |
| 57 | `ActionSpec` verifier that returns a falsy value fails (Dracula 74) | - | fails | read | s39.py_only.* - Java's `Consumer` cannot return a value, so there is nothing to run there |
| 58 | The plan has no delete; a soft delete is an update | no delete | no delete | read | API shape: neither `ActionPlan` has the method |
| 59 | An action can rename itself in `action_name` (Dracula 69) | cannot | `name` property | read | API shape: Java has no hook to run |

**Totals: 59 behaviours, 46 same, 10 differs, 3 read.**

Not a difference, but worth knowing in both ports (row 29): with `allowCrossShard` and the
default retry, a partial commit is retried as a whole, so the shard that already committed gets
its rows a second time. Turn retries off for cross-shard actions that are not safe to repeat.

## Differences

Most surprising first.

### 50. `execute` inside an open transaction joins it (Dracula 44)
- **What:** the caller opens a transaction on the shard, runs an action inside it, then rolls
  back. Java: the action committed on its own connection and stays (1 wallet, 1 outbox row).
  Python: the action's writes joined the caller's transaction and vanished with it (0 and 0).
- **Java:** `ActionExecutor.java:284` (`inTransactionChecked` opens a new transaction), `TransactionManager.java:104-122`
- **Python:** `action/executor.py:449` (`manager(shard).transaction()`), `persistence.py:138-142` (joins the open one)
- **Could match?** yes. The executor could always open its own connection (or refuse to run
  inside an open transaction). Cost: small in code, but it changes Dracula 44 for every caller.
- **Surprise:** high.

### 8, 9. ~~`action_params` and `payload` JSON use Python field names and decimals as text (Dracula 1)~~ fixed in `EKB-0290`
- **Fixed:** both are now the text Java's Jackson writes; the probe lines are identical.
- **What:** Java writes `{"countryCode": "DE"}` and `{"initialBalance": 10, "newBalance": 15.0000}`.
  Python writes `{"country_code": "DE"}` and `{"initial_balance": "10", "new_balance": "15.0000"}`.
  `modelId` / `modelName` match. A Java reader of a Python row does not find its fields.
- **Java:** `SingleTableJsonEventPersister.java:131-147` (Jackson)
- **Python:** `domain.py:121-147` (`payload()`), `_json.py:21` (`canonical`), `action/outbox.py:364`
- **Could match?** yes: camelCase keys and numbers on write, reversed on read. Changes every stored row.
- **Surprise:** high.

### 13. ~~A row with no event stores JSON `null` in `payload`, not SQL `NULL` (new)~~ fixed in `EKB-0291`
- **Fixed:** `json_column()` stores `None` as SQL `NULL`, for the notifications table too; tested on all four dialects.
- **What:** for the no-event row (empty plan, or an update with no event) Java leaves `payload`
  as SQL `NULL`. Python stores the JSON value `null`: `payload IS NULL` is false, and
  `jsonb_typeof(payload)` is `'null'`. Python reads it back as `None`, so Python is consistent with
  itself, but a SQL query, a CDC consumer or a Java reader sees a different value. Not run: how
  Java's fan-out reads a Python-written row of this kind; and MySQL/MariaDB (only PostgreSQL was run).
- **Java:** `SingleTableJsonEventPersister.java:74-87` (passes `null`), `EventEntityRepository.java:71-73`
- **Python:** `action/outbox.py:346` (`payload=None`), `schema.py:109,131` (SQLAlchemy `JSON` without `none_as_null=True`)
- **Could match?** yes: `JSON(none_as_null=True)` in `json_column()`. Cheap; the same column type
  is used for the notifications payload (area D) and should be checked there.
- **Surprise:** medium.

### 41. An action built with a different clock is refused (Dracula 12)
- **What:** executor on a fixed clock, action on the system clock. Java builds and runs it.
  Python refuses at build: "was built with a different clock than this ActionExecutor".
- **Java:** `ActionExecutor.java:99,411` (no comparison)
- **Python:** `action/executor.py:247-261`
- **Could match?** yes (drop the check), but it is deliberate protection; the known trap is two
  `SystemClock()` objects counting as different.
- **Surprise:** medium.

### 46. The executor builder needs an event persister (Dracula 14)
- **What:** Java's builder makes the default single-table persister when none is given. Python's
  `build()` refuses: "event_persister is required".
- **Java:** `ActionExecutor.java:103-105`
- **Python:** `action/executor.py:567`
- **Could match?** partly: Python needs the table, so the builder would need an `events(table)` setter.
- **Surprise:** medium.

### 32. `None` params are stored as `{}` (Dracula 75)
- **What:** Java refuses null params at commit (`IllegalArgumentException`) and rolls the rows
  back. Python commits and stores `action_params = {}`.
- **Java:** `SingleTableJsonEventPersister.java:141-147`
- **Python:** `action/outbox.py:409`
- **Could match?** yes: refuse `None`. Cost: a params class for every parameterless action.
- **Surprise:** low.

### 45. Two instances of one action type are refused (Dracula 61)
- **What:** Java's registry keeps the second instance without a word. Python refuses at build.
- **Java:** `ActionRegistry.java:63-66`
- **Python:** `action/registry.py:60`
- **Could match?** yes, but Java's silent replace is the weaker behaviour.
- **Surprise:** low.

### 47. An executor with no repositories is refused (new)
- **What:** Java builds an executor over an empty repository registry (it fails later, only if a
  change is staged). Python refuses at build: "at least one repository is required".
- **Java:** `ChangePersister.java:50-54` (no such check)
- **Python:** `action/persister.py:41-42`
- **Could match?** yes: drop the check. An executor that only runs no-change actions would then build.
- **Surprise:** low.

### 26. The cross-shard error names the shards in sorted order, not staging order
- **What:** staging mexico then global: Java names active `1:0`, requested `0:0` (first two in
  staging order). Python names active `0:0`, requested `1:0` (sorted). The refusal itself, and
  that nothing is written, are the same. Commit order when allowed is staging order in both.
- **Java:** `ActionExecutor.java:394-400`
- **Python:** `action/executor.py:387` (`sorted(by_shard)`)
- **Could match?** yes: take the first two keys of `by_shard`, which is already in staging order.
- **Surprise:** low.
