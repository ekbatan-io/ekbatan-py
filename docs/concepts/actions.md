# Actions

An **action** is one unit of business work - `DepositMoneyAction`, `ShipOrderAction`,
`RenewSubscriptionAction`. It does **not** write to the database. It stages new and updated
domain objects on its plan. Only after `perform()` returns does the executor open a single
transaction, flush every staged change, write the outbox rows and commit.

That split is what makes the outbox implicit: domain rows and event rows are written together,
by the framework, never by your code.

## The two-phase lifecycle

```
        executor.execute(DepositMoneyAction, params)
                              │
   ┌─── Phase 1 - perform()   (no transaction yet) ─────────────┐
   │   read from repositories                                   │
   │   build new immutable models                               │
   │   attach events to them                                    │
   │   stage:  self.plan.add(order) / self.plan.update(wallet)  │
   │   return a result                                          │
   └────────────────────────────────────────────────────────────┘
                              │
   ┌─── Phase 2 - persist     (one transaction per shard) ──────┐
   │   group staged changes by shard                            │
   │   repositories write the domain rows                       │
   │   the persister writes the outbox rows                     │
   │   commit  -or-  roll back                                  │
   │   on StaleRecordError, retry the whole action              │
   └────────────────────────────────────────────────────────────┘
```

Phase 1 is pure construction. Phase 2 is the only place a transaction is opened, and it always
wraps every staged change together with the matching event rows.

Three consequences follow from the split, and they are most of why it exists.

**`perform()` cannot write, so it cannot half-write.** There is no flush, no `save()`, no
connection to reach for. The worst an action can do is stage something the executor then refuses,
and refusing costs nothing because nothing has been written.

**A retry is cheap and correct.** When a version check fails, phase 2 rolls back and the executor
re-runs phase 1 from the top with a **fresh plan** — re-reading the row it lost the race on, so
the second attempt is computed from the winner's state rather than patched onto the loser's.
An action that only reads, computes and stages is safe to re-run by construction; one that
posted to an external API in `perform()` is not, which is the real reason for the rule.

**Sharding stays out of the action.** The grouping step is in phase 2, so `perform()` stages
objects without knowing which database each one lands on. The same action body works on one
database and on eight.

The cost is the one thing you have to hold in your head: **`self.plan.update(x)` has not written
`x`.** It returns the row as it *will be* written — stamped, version already moved — so later
code in the same `perform()` reads the new state. But the database has not seen it, and will not
until `perform()` returns.

## Writing an action

```python
class DepositMoneyAction(Action[DepositParams, Wallet]):
    def __init__(self, wallets: WalletRepository) -> None:
        self.wallets = wallets

    def perform(self, params: DepositParams) -> Wallet:
        wallet = self.wallets.get(params.wallet_id)
        return self.plan.update(wallet.deposit(params.amount))
```

Read from a repository, build a new immutable `Wallet` with an event attached, stage it. No write
happens here.

Calling it:

```python
wallet = executor.execute(
    DepositMoneyAction,
    DepositParams(wallet_id=wallet_id, amount=Decimal("25.50")),
)
```

The executor opens one transaction, writes the wallet row with its incremented version, writes
the `MoneyDepositedEvent` row, commits. If anything raises, both roll back.

## ActionPlan

An in-memory accumulator, reached through `self.plan`:

```python
self.plan.add(entity)         # staged for INSERT
self.plan.update(entity)      # staged for UPDATE; returns the next version
self.plan.add_all(items)
self.plan.update_all(items)
```

Three rules:

- **One id may be staged at most once per action.** Re-staging the same id - `add` then `update`,
  or `add` twice - raises. Two changes to one row in one action is almost always a bug you would
  rather find here.
- **`update()` returns the object at its next version**, so later reads inside the same
  `perform()` see the increment.
- **The plan is single-writer.** It is a plain dict; concurrent mutation is a data race.

## Singletons + per-call state via a ContextVar

One instance per class, for the life of the application, shared by every concurrent invocation.
Its only state is what the constructor set.

Per-execution state - the plan, the clock, the principal - is bound into a **`ContextVar`** for
the duration of `perform()`. Java binds the same thing to a `ScopedValue`. So:

- **An action must not have mutable instance state.** Anything beyond the constructor breaks
  under concurrency.
- **`self.plan` must be reached from the thread or task that is running `perform()`.** A
  `ContextVar` is per-context; work handed to a pool does not inherit the binding.

That second point is not a limitation to work around - it is the design. A pool thread that
outlived the unit of work while holding its connection is exactly the bug this prevents.
`current_plan()` raises with a message saying so rather than returning `None`.

## Plan is single-writer; forking is allowed but not encouraged

Deliberately. Internally it is a plain `dict` keyed by type, holding plain dicts keyed by id, and
nothing guards them. Within `perform()`, `add`, `add_all`, `update` and `update_all` must all
happen on the thread that entered it. If the action fans work out to a pool to gather data,
**join the children first and only then stage from the thread that invoked `perform()`.**

The GIL is not the reprieve it looks like. A single dict assignment is atomic; the staging path
is not — it checks whether the id is already staged, stamps the row, and then assigns, and two
threads can both pass the check. The failure is a lost write with a correct-looking traceback,
which is exactly the kind you find in production rather than in a test.

## No nested actions inside `perform()`

An action must **not** execute another action inside `perform()`. There is no support for
composition and that is deliberate: nesting blurs the transaction boundary, hides coupling, and
makes the flow hard to follow.

If two operations must happen together, they are one action. If they are independent, run them
separately from the caller. If one must follow the other, orchestrate above the framework.

The exception is chaining *after* the commit: a handler, a consumer or a worker reacts to a
committed event and starts the next action in its own transaction. That is what a
[saga](sagas.md) is.

## Retries on optimistic-lock conflicts

Every persistable carries a `version`, and updates always include `WHERE version = ?`. If another
transaction got there first, zero rows match and `StaleRecordError` unwinds the whole thing.

The default is one retry after 100 ms - enough to absorb ordinary contention, not enough to hide
a real problem. Tune it per call:

```python
config = ExecutionConfiguration(
    retry_configs=(RetryConfig(on=StaleRecordError, max_retries=3, delay=0.05),)
)
executor.execute(DepositMoneyAction, params, config=config)
```

`ExecutionConfiguration.no_retry()` is the other end: a failure reaches the caller unchanged, for
work whose side effects are not safe to repeat.

Three details worth knowing:

**`max_retries` counts retries, not attempts.** `max_retries=1` runs the action twice. It is
named for what it counts because `attempts=1` running twice is the sort of thing nobody catches
in review and everybody hits in production.

**Matching looks through the cause chain,** so a driver wrapping a conflict is still recognised.
It walks `__cause__` first and then `__context__`, the same order Python's own traceback does.

**Matching is by `isinstance`, so a subclass matches.** This is a real divergence: Java matches
the exception class exactly, and a policy for `RuntimeException` there does *not* catch an
`IllegalStateException`. Here a policy for `EkbatanError` catches every framework error. Order is
the only precedence rule, so put narrow policies before broad ones.

**The retry replays the whole action from phase 1, with a fresh plan.** Side effects in
`perform()` that are outside the plan - logging, counters, an HTTP call - replay too. Keep
`perform()` pure.

## Cross-shard actions

An action whose changes span shards raises `CrossShardError`. Opt in per call:

```python
executor.execute(TransferAction, params,
                 config=ExecutionConfiguration(allow_cross_shard=True))
```

With it enabled, each shard gets its own transaction and commits independently - there is no
two-phase commit - and the action's outbox row is written to every involved shard so each holds
the full context.

Treat it as an escape hatch. The default refusal is the point: once two databases are involved,
all-or-nothing is not available, and a partial commit is the [dual-write
trap](the-dual-write-trap.md) wearing a different hat. If a partial commit would need
compensating, model the work as a [saga](sagas.md) instead.

## See also

- [Models and entities](models-and-entities.md) - what `plan.add` and `plan.update` consume
- [The outbox](outbox.md) - what phase 2 writes
- [Sagas](sagas.md) - multi-step workflows, and compensation
- [Sharding](sharding.md) - how phase 2 groups changes
- [Concurrency](../runtime/concurrency.md) - the ContextVar, and forking work safely
