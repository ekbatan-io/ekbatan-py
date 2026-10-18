# N4: public API, errors and the testing helper

Part of [parity round 2](../PARITY-ROUND-2-LEDGER.md).

**Scope and what was run.** Three things: (1) the public API a Java user calls - every public
type of `ekbatan-core`, `local-event-handler`, `ekbatan-distributed-jobs`, `ekbatan-keyed-lock-redis`
and `ekbatan-test-support`, listed against Python's (method lists taken from both by script, then
read; known Draculas not repeated); (2) errors - the hierarchy in both, and fifteen failures run
in both ports on two PostgreSQL containers (global `0:0`, mexico `1:0`), printing type, message,
cause chain and whether the framework's base class catches it; (3) the testing helper -
`ActionSpec`, `ActionAssert` and `VirtualClock` against `ekbatan.testing` and `ekbatan.clock`,
only what round 1 area B did not cover. Java ran as a temporary JUnit test in a throwaway
worktree at `aee7d3a` (module `postgres-sharded`, with `ekbatan-test-support` added as a test
dependency for the run; both removed afterwards). Python ran against today's `main`. Sources and
outputs are in [`parity-probes/round2-N4/`](../../parity-probes/round2-N4/):
`ParityN4ProbeTest.java` / `java.out`, `probe_errors.py` / `python.out` (same keys, line for
line), and `probe_base_class.py` / `python-base-class.out` (Python only: Java has no common base
to twin). Not re-reported, unchanged since round 1: the stale-record message without id and
version (A15), `inTransaction` wrapping (A40/C53), the cross-shard shard order (B26).

| # | Difference (plain, one line) | Java | Python | Verdict | Why (one line) | Evidence |
|---|---|---|---|---|---|---|
| 1 | A plain (non-dataclass) object in action params or an event is stored as its `repr`, memory address included | writes its public fields: `{"thing": {"amount": 5, "currency": "EUR"}}`; no fields gives `{}` | `{"thing": "<__main__.Money object at 0x...>"}` - data lost, silently | fix | Silent data loss in the outbox, and a Java reader gets a string where it expects an object | run: `e10.stored_params`, `e10b.*` |
| 2 | `ConnectivityError` is exported and documented as "the database could not be reached", but nothing raises it | no such class; a down database gives jOOQ `DataAccessException` | a down database gives SQLAlchemy `OperationalError`; `except ConnectivityError` never fires | fix | A handler written against the documented class is dead code | run: `e7.unreachable.is_ConnectivityError` |
| 3 | The website's "learn/actions" page says a retry policy matches subclasses, so a policy on `EkbatanError` catches every framework error | exact class | exact class (since `EKB-0198`); the reference page and `docs/` say so, the learn page does not | fix | A reader following the page gets no retry and is told the opposite | run: `e12.policy_on_base_class.*` (1 attempt in both) |
| 4 | `run_action`'s docstring says "the executor refuses a second clock" | - | the executor accepts it since `EKB-0302` | fix | A stale reason in the testing helper's own help text | read: `testing.py:113-117`; round 1 B41 run |
| 5 | `assert_throws(Exception, ...)` passes when the action fails a plain `assert` | the `AssertionError` (an `Error`) escapes and the test fails | passes: `AssertionError` is an `Exception` in Python | leave | Python's hierarchy; only bites a catch-all `assert_throws(Exception)` | run: `t2.*` |
| 6 | `assert_throws(OtherType, ...)` when the action fails an `assert` | the raw `AssertionError("assert inside perform")` | `AssertionError("Expected ValidationError but got AssertionError")`, the original as its cause | leave | The test fails either way and both messages point at it | run: `t1.*` |
| 7 | Some failures the framework raises on purpose are not `EkbatanError`, though its docstring says "base for everything this framework raises on purpose" | no common base at all | `TypeError` (repository without `to_row`/`from_row`), `AttributeError` (unknown builder setter), `NotImplementedError` (no `perform`), `ValueError` (`backoff`, `ActionEvent.from_row`) | leave | Each is the built-in Python raises for that mistake; Java has no base to compare, and these are coding mistakes, not runtime failures | run: `python-base-class.out` |
| 8 | `StaleRecordError()` with no message fails | `new StaleRecordException()` works | `TypeError`: a message is required | leave | Rare: application code raising it by hand nearly always has a message | run: `e13.*` |
| 9 | An action built with a null clock | builds, and `ActionSpec` runs it | refused at construction (`ValidationError`) | leave | Python is stricter on purpose; a Java test passes a clock anyway | run: `t3.*` |
| 10 | Lock backend failure message wording | `Failed to acquire lock for key wallet:1: could not obtain a connection` | `lock 'wallet:1': could not obtain a connection` | leave | Same type, same `key` field, same cause; only a log grep would notice | run: `e8.*` |
| 11 | `getById` miss message prints the id's repr | `No entity found with id: Wallet[id=<uuid>]` | `no Wallet with id ShardedId(value=UUID('<uuid>'))` | leave | Names type and id in both; noisier only | run: `e1.*` |
| 12 | Unregistered action | `NullPointerException` "No action registered for class" (no class named) | `ValidationError` naming the class and listing what is registered | leave | Python's is the better message | run: `e3.*` |
| 13 | Event-handler registry lookups renamed, and the registry can be added to after build | `handlerFor`, `handlersFor`, `handledEventTypes()` (a set); immutable | `by_name`, `subscribers`, `event_types` (a sorted list); `add()` is public | leave | Naming only; nothing in the framework adds after build | read: `events/handler.py:118,164-178` |
| 14 | Public Java types with no Python counterpart | `ConnectionProvider`, `DataSources`, the jOOQ converters, `ShardAwareId`, `Identifiable`, `ModelId`, `PropertyKeyNormalizer`, `Backoff`, test-support `ClasspathTransferable` | SQLAlchemy engines and types in their place; `backoff()` a function; markers not needed; no classpath | leave | Each is a Java or JDBC plumbing type Python has no need for | read |
| 15 | Database unreachable | registry builds; first read fails after 30 s; `DataAccessException <- SQLTransientConnectionException <- PSQLException` | registry builds; first read fails after 30 s; `OperationalError <- OperationalError` | same | Lazy pools and a 30 s wait in both; the driver's error passes through unwrapped | run: `e7.*` |
| 16 | Duplicate primary key in `execute` | jOOQ `IntegrityConstraintViolationException`, unwrapped | `IntegrityError`, unwrapped | same | Driver error reaches the caller in both | run: `e9.*` |
| 17 | Unknown shard with policy FAIL, through `execute` | `IllegalArgumentException`, nothing written | `ValidationError` (a `ValueError`), same text, nothing written | same | Same point, same effect | run: `e6.*` |
| 18 | Cross-shard refusal | `CrossShardException`, `activeShard` / `requestedShard` | `CrossShardError`, `active_shard` / `requested_shard` | same | Wording and shard printing differ (Dracula 66) | run: `e5.*` |
| 19 | The plan reached outside an execution | `IllegalStateException` | `EkbatanError` | same | Refused in both, at the same call | run: `e4.*` |
| 20 | `VirtualClock.advance` while running | `IllegalStateException` "Cannot advance: clock is not paused" | `EkbatanError` "cannot advance: the clock is not paused..." | same | Refused in both | run: `e11.*` |
| 21 | Retry policy on a base class does not retry a subclass | 1 attempt | 1 attempt | same | Exact-class matching in both | run: `e12.*` |
| 22 | Lock backend down on `tryAcquire` | `LockAcquisitionException`, `key` field, cause chain to the driver | `LockAcquisitionError`, `key` field, cause chain to the driver | same | Round 1's fix holds | run: `e8.*` |
| 23 | `ActionSpec` / `ActionAssert` surface | 24 public methods and overloads | every one in snake case, overloads as optional arguments, plus `run_action` / `StagedWork` (Dracula 79) | same | Method for method | read: `testing.py` against `ActionAssert.java`, `ActionSpec.java` |
| 24 | `VirtualClock` methods | `pause`, `pauseAt`, `advance`, `resumeFrom`, `resume` | the same five in snake case; no `withZone` (Dracula 59) | same | Same modes and refusals | read: `clock.py:39-123`, `VirtualClock.java` |

## Fix details

### 1. A plain object in params or an event is written as its `repr`

> **Fixed (`EKB-0316`):** refused with a `ValidationError` naming the class, before anything is
> written. Python reads back dataclasses only, so writing the object's fields as Java does would
> have stored data the port cannot read back; a dataclass gives Java's JSON.
- **What:** `canonical()` handles dataclasses, mappings, sequences and known scalars; anything
  else falls through to `str(value)`. A params or event field holding a plain class, an attrs
  class or a pydantic model is stored as `"<module.Money object at 0x10bfee270>"`. No error, and
  the address changes every run. Java's Jackson writes the object's public fields and getters
  (`{"amount": 5, "currency": "EUR"}`), or `{}` when it has none. The same function writes event
  payloads, so an event with such a field loses it too.
- **Java:** `SingleTableJsonEventPersister.java:131-147` (Jackson `valueToTree`).
- **Python:** `_json.py:131` (`return str(value)`), reached from `action/outbox.py:334` (params)
  and `:373` (payloads).
- **What matching would cost:** small. Either refuse such a value with a `ValidationError` that
  names its type (one line, loud, safe), or write its public attributes (`vars()` without
  `_`-names, plus properties) as Jackson writes public fields and getters. Keep `str()` only for
  types whose text is their value (paths, IP addresses), as Jackson does for its own.

### 2. `ConnectivityError` is never raised
- **What:** `errors.py` exports it as "the database could not be reached" and `ekbatan` exports
  it at top level. Nothing in `src/` raises it: a down database reaches the caller as SQLAlchemy's
  `OperationalError` (shown by the run, after the 30 s pool wait, as Java's after Hikari's). The
  only uses are tests that raise it themselves as a sample retryable error.
- **Java:** no such class; the driver's error passes through as `DataAccessException`.
- **Python:** `errors.py:97-98`, `__init__.py:66,154`; `tests/integration/test_actions.py:160,258,273,291`.
- **What matching would cost:** small. Remove the class (Java has none; the tests define their
  own sample error). Raising it instead would mean wrapping driver errors at every connection,
  which Java does not do.

### 3. The learn page contradicts the retry rule
- **What:** "Matching is by `isinstance`, so a subclass matches ... Here a policy for
  `EkbatanError` catches every framework error." Both ports match the exact class: a policy on
  `EkbatanError` / `RuntimeException` and an action raising a stale-record error ran once in each.
  `docs/concepts/actions.md:174` and `website/src/pages/reference/action.mdx:161` already say so.
- **Java:** `Retry.java:74` (`retryConfigs.get(current.getClass())`).
- **Python:** `action/executor.py:75-86` (`type(error) in exact`); the stale text is
  `website/src/pages/learn/actions.mdx:185-188`.
- **What matching would cost:** a paragraph: copy the reference page's wording.

### 4. `run_action`'s docstring gives a reason that no longer exists
- **What:** "There is no override here for the same reason the executor refuses a second clock".
  The executor stopped refusing a second clock in `EKB-0302` (round 1 B41, run).
- **Java:** `ActionSpec.java:58-62` (`new ActionPlan()`, no clock involved).
- **Python:** `testing.py:113-117`.
- **What matching would cost:** rewrite one sentence: the plan stamps from the action's clock;
  pass a `VirtualClock` to the action for a fixed instant.

## The exception hierarchy, side by side

For reference; no verdict beyond the rows above. Java has no common base: every framework error
is a plain `RuntimeException` subclass or a JDK exception. Python has one base, `EkbatanError`.

| Failure | Java | Python |
|---|---|---|
| Row required, not there | `EntityNotFoundException` | `EntityNotFoundError` (also `LookupError`) |
| Lost version race | `StaleRecordException` | `StaleRecordError` |
| Two shards in one unit of work | `CrossShardException` | `CrossShardError` |
| Lock backend failed | `LockAcquisitionException` | `LockAcquisitionError` |
| Bad argument, missing value, misuse | `NullPointerException`, `IllegalArgumentException`, `IllegalStateException` | `ValidationError` (also `ValueError`); a few misuse cases plain `EkbatanError` |
| Dialect cannot do it | `UnsupportedOperationException` | `UnsupportedError` |
| Database unreachable | jOOQ `DataAccessException` | SQLAlchemy `OperationalError` (`ConnectivityError` exists, unused - row 2) |
| Driver errors (duplicate key...) | jOOQ `DataAccessException` subclasses | SQLAlchemy `DBAPIError` subclasses |
| Migration | `FlywayException` | `MigrationError` (an `EkbatanError`) |
| Test assertion | `AssertionError` | `AssertionError` |

A Java developer's `catch (RuntimeException e)` around a call maps to Python's
`except Exception`; `except EkbatanError` is narrower - it catches the framework's own checks,
not driver errors (as in Java, where nothing would) and not the coding mistakes in row 7.

## Totals

**24 checked: 10 same, 4 fix, 10 leave, 0 needs you.**
