# Where this port disagrees with the charter's reference

Most of what follows is neutral: the two ports do the same thing in different idioms.
For the biased subset - every way this port is *behind* the Java one, in one place -
see [`docs/SHORTCOMINGS.md`](docs/SHORTCOMINGS.md).

Rule 1 of [the charter](https://github.com/ekbatan-io/ekbatan-spec) is that idiomatic beats
identical: a port must read as if written by someone fluent in the language, not translated
into it. That guarantees disagreements, and written disagreement is how a port stays honest -
silent divergence is how a spec becomes fiction.

Every entry below is a place where this port does something the Java implementation does not.
None of them changes observable behaviour; each one changes what the application has to write.

## How to read this

Every section below is labelled on the line under its heading:

| | |
| --- | --- |
| **forced** | CPython or its libraries left no choice. Nothing to review - the alternative does not exist |
| **chosen** | somebody decided, and it could have gone the other way. Links [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md), which lists all of them together |
| **parity** | recorded here because the mechanism differs, while the behaviour does not |

`uv run python scripts/divergence.py` refuses to pass if a section is unlabelled, or if a
**chosen** one does not link somewhere it will be argued about. It runs in `make lint`.

The labels exist because of a specific failure. `readonly_db()` was made to mean "a read,
defaulting to the primary" rather than Java's "the replica" - a judgement call about whose job
consistency is - and it was written up here as a neutral difference. It read like a fact about
the language for weeks, and was caught by being asked about rather than by being reviewed.

## An action reaches its three per-invocation values exactly as Java does

> **parity** - clock in the constructor, principal as a parameter, plan ambient. The same three mechanisms Java uses, for the same three values.

This was briefly not true, and the way it was found is the point: it was asked about rather than
reviewed - the same failure the labels at the top of this file exist because of.

For a while all three lived in `ContextVar`s, reached as `self.plan`, `self.principal` and
`self.clock`, with `current_principal()` and `current_clock()` alongside. That is more
*internally* consistent than Java, which uses a different mechanism for each - and it was the
wrong trade, because the shape a reader recognises is worth more here than a tidier one.

Now:

| | Java | here |
|---|---|---|
| clock | `Action(Clock clock)`, a `public final` field | `super().__init__(clock)`, a field |
| principal | `perform(Principal principal, PARAM params)` | `perform(self, principal, params)` |
| plan | `ScopedValue` -> `plan()` | `ContextVar` -> `self.plan` |

The plan stays ambient in both, and for the same reason: a repository call three frames down has
to stage onto it without mentioning it. The other two can be passed, so they are.

Two differences remain, and both are forced. The principal is a `str | None` rather than a
`java.security.Principal`, because there is no such interface here and inventing one to hold a
name would be a type for its own sake. And **this port checks that an action holds the
executor's clock**, at construction, refusing one that does not:

```
DepositMoneyAction was built with a different clock than this ActionExecutor.
```

Java cannot make that check and does not need to: a DI container hands the same `Clock` bean to
the action and to the executor. There is no container here - `build()` is a function you write -
so the two are wired separately and can be wired differently. The failure that produces is a
test that froze the executor's clock, asserted on a timestamp the action took from the system
clock, and passed on the machine it was written on.

## An action runs by type or by instance

> **chosen** - Java dispatches only on a class token; this accepts an instance too. An addition to the surface, not a fact about Python. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java dispatches on a class token, so `ActionRegistry` is the only way to reach an action:

```java
executor.execute(principal, DepositMoneyAction.class, params);
```

Both registries are here, with the same builders, and the class form works the same way:

```python
executor.execute(DepositMoney, DepositParams(...))
```

What the registry buys is the same in both ports: the wiring lives in one place, and a caller
holding the executor runs an action without also holding what the action was built with. The
example's handler and its job both reach for `DepositMoney` and know nothing about the wallet
repository it needs.

The instance form stays, because it costs nothing and a test almost always wants the object it
just built:

```python
run = DepositMoney(wallets)
executor.execute(run, DepositParams(...))
```

Two differences inside the builders, both for the same reason. Neither `with_action` nor
`with_model_repository` takes the type alongside the object - `type(action)` and
`repo.model_type` already say it, and a second way to say the same thing is a second way for it
to be wrong. And where Java's `repository(Class)` returns null for an unregistered type, this
raises and names the type: the null is what surfaced as a NullPointerException inside the
executor's shard grouping, which is `EKB-0076` on the Java side.

## Events carry no identity

> **chosen** - Java's event constructors take the model id and this port's do not. A user-visible API difference somebody decided. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

In Java every event constructor takes the id of the model it is about:

```java
new WalletMoneyDepositedEvent(id, amount, newBalance)
```

Here it does not. The event is attached to the model, so the framework already knows which
model and of what type, and fills those columns in at persist time:

```python
WalletMoneyDeposited(amount=amount, new_balance=new_balance)
```

A parameter that can only ever be passed one correct value is a parameter that will one day be
passed a different one. The stored row is identical.

The two fields themselves are Java's, with Java's names: `ModelEvent` declares `model_id` and
`model_name`, and `event.about(model)` is what fills them in. Only the moment differs - Java's
constructor, this port's persister. There is no extra type in between; an earlier version of
this port had an `EmittedEvent` pairing an event with its model, and it is gone.

One place they do not appear is `payload()`. They have columns of their own, which is where a
consumer reads them; Java leaves them in the payload as well, because they are public fields of
its base class and Jackson serialises everything public. `ActionEvent` - which is the wire
contract - describes the payload as "raw JSON" for the consumer to deserialise as it likes, and
reads `modelId` from the column.

## The builder is not generated

> **chosen** - A runtime builder that works with nothing generated is an addition; Java has only the generated one. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Both forms are here. `copy()` returns a builder with the same surface Java's has - `with_event`,
`with_initial_version`, `increase_version`, `copy_base`, `build`, and a setter for every field:

```python
wallet.copy().balance(new_balance).with_event(WalletMoneyDeposited(...)).build()
```

`evolve()` is the short form for a change touching one or two fields, and is what the reference
application uses:

```python
return self.evolve(balance=new_balance, emit=WalletMoneyDeposited(...))
```

The difference from Java is that no builder is generated. Java needs an annotation processor to
emit a CRTP builder per class; here `EntityBuilder` and `ModelBuilder` resolve fields from the
dataclass at call time, so a new model needs no build step and no generated file to keep in sync.

## `copy_base` takes every field, not only the framework's

> **forced** - A Java base class cannot enumerate a subclass's fields; a dataclass can. The behaviour follows from the language.

The name is Java's. What it copies is not: Java's `copyBase` hands over the six fields the base
class knows about and leaves the subclass's for the next lines, because a Java base class
cannot enumerate a subclass's fields. That is why every Java model writes its own `copy()`, and
why one of them eventually forgets a field.

Here the fields come off the dataclass, so `copy_base` takes all of them and `copy()` is
inherited. A model that gains a field gains it in both places at once. Override `copy()` only
to make a copy that deliberately differs - dropping a cache, resetting a counter.

## Identifiers get a named type, not a generic parameter

> **chosen** - Both languages erase, so `Id[Wallet]` would have worked. A named type per model is a convention this port adopted. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java writes `Id<Wallet>` and `ShardedId<Wallet>` and gets erasure at runtime, so
`id.equals(otherId)` compares uuids and nothing else. Python erases the same way, so the same
spelling has the same hole.

The port therefore gives each model a one-line id type:

```python
class WalletId(ShardedId["Wallet"]): ...
```

`WalletId(u) == NotificationId(u)` is then False with no checker involved, and they are two
entries in a set rather than one. `Id[Wallet]` still works and still satisfies a checker; the
named subclass is what makes the interpreter agree.

## `AbstractRepository` is just `Repository`, and is usable as it stands

> **chosen** - Java's four-name hierarchy collapses to fewer here. Structural, and could have been mirrored. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java's hierarchy is four names: a narrow `Repository` interface, an `AbstractRepository`
holding the implementation, and `ModelRepository` / `EntityRepository` on top.

`ModelRepository` and `EntityRepository` are here, and do what they do there: declare which
kind of thing the table holds, and refuse a model whose table cannot hold the timestamps a
model carries.

The other two collapse into one. `AbstractRepository` is abstract in Java because
`fromRecord` and `toRecord` have to be supplied; here they default to mapping by field name, so
the class works as it stands and calling it *Abstract* would be a lie -
`Repository(Wallet, WALLETS, registry)` is a legitimate line. The narrow `Repository` interface
existed to keep `ChangePersister` off the full surface; nothing here needs that yet, and a
Protocol added before anything wants it is a name to maintain for nobody.

## Row mapping is by field name unless you say otherwise

> **chosen** - Java requires `fromRecord`/`toRecord` on every repository. Defaulting them is this port's largest ergonomic decision. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java requires `toRecord` / `fromRecord` on every repository. Here they default to mapping
dataclass fields to columns of the same name, converting enums by name and timestamps to
storage form. Override them when the shapes differ; most repositories never do.

This is the largest single reduction in application code, and the one most at risk of being
undone by a well-meant refactor - so it is stated here.

## `created_date` is required, as Java requires it

> **parity** - Java's constructor says `Validate.notNull(builder.createdDate)` and `build()` throws without it. So does this. Brought to parity in `EKB-0192`.

Java's `Model` constructor:

```java
this.createdDate = Validate.notNull(builder.createdDate, "createdDate cannot be null")
        .truncatedTo(MICROS);
this.updatedDate = builder.updatedDate != null ? builder.updatedDate.truncatedTo(MICROS)
                                               : this.createdDate;
```

and its factories pass `clock.instant()` in. This port now does the same three things:
`created_date` is required and refused when null, `updated_date` falls back to it, and both are
normalised on the way in - `to_storage` is the analogue of `truncatedTo(MICROS)`, since an aware
value stored as-is would not compare equal to the same instant read back.

For a while this port left both unset and stamped them from the executor's clock when the change
was staged, on the grounds that two lines which are the same every time and wrong occasionally do
not belong in application code. That is a fair argument for a **default**; it was the wrong
argument for dropping the **validation**, and it left one class with two standards for
"required" - `id`, `state` and `version` refused at construction while the two timestamps were
silently optional. Java gets both: a required field, and a factory that supplies it from the
clock. Pass `self.clock.now()`, which is what `clock.instant()` is doing on the other side.

`Entity` has no timestamps in either port. A row that needs to know when it last changed almost
certainly has changes worth telling the rest of the system about, which makes it a `Model`.

The write path matches too, as of `EKB-0194`. **Nothing writes `updated_date` but you.** Java
writes `updatedDate` nowhere in `ekbatan-core` - `ModelRepository` only checks the column exists,
and `nextVersion()` carries whatever the row held forward - and this port briefly refreshed it
from the clock on every staged update. It no longer does:

```python
self.copy().updated_date(self.clock.now()).balance(new).build()
```

on the changes where it means something. The version still moves inside the write, under
`WHERE version = ?`, because that is the optimistic lock rather than a convenience.

The trade is stated plainly: a column named `updated_date` does not move on its own, and a row
that has never been marked still reads as last-updated-when-created. That is the cost of a
framework that does not rewrite a column the application set - which is the property that makes
the column usable for recording when *your* change happened.

## The per-invocation plan lives in a `ContextVar`

> **forced** - CPython has no `ScopedValue`. A `ContextVar` is the direct analogue.

Java uses a `ScopedValue`. A `ContextVar` is the direct analogue and is per-thread and
per-async-task at once, so the same mechanism works for a threaded service and an asyncio one
without the framework choosing for you.

Both share the property that matters: work handed to a pool from inside a unit of work does not
inherit the plan. That is correct - another thread is another connection, and quietly sharing
one would corrupt both - and it is the one thing about this that surprises people, so
`current_plan()` says so in its error message rather than returning `None`.

## Transactions are the caller's, and batching with them

> **parity** - Neither port's repository opens a transaction. Recorded because the mechanism differs, not the behaviour.

Neither port's repository opens a transaction. Inside one it joins; outside one each statement
commits itself - Java's `db()` is a jOOQ context over a DataSource with auto-commit left on, and
here it is a connection in `AUTOCOMMIT`. SQLAlchemy will not do that by default: an `execute`
outside a transaction quietly begins one, and closing without `commit` rolls it back, so a write
would vanish. `AUTOCOMMIT` is how you ask for what the JDBC driver gives Java for free.

One repository call is therefore **one statement**, however many rows, in both ports. Nothing is
chunked. Splitting a batch that would exceed the server's bind-parameter ceiling - 65535 on
Postgres, a packet size on MySQL - belongs to the caller, because only the caller knows what the
pieces mean if one of them fails.

This port did chunk, at five hundred rows, and opened a transaction to keep the pieces together.
That made large batches work where Java's fail, at the cost of the framework deciding what was
atomic on the caller's behalf. It now does neither.

The consequence is worth stating plainly, and is the same on both ports: a batch update whose
optimistic lock misses on one row raises, and the rows that did match are already written. Java's
`should_throw_optimistic_lock_exception_when_updateAll_with_stale_versions` asserts exactly that.
Inside a unit of work the executor's transaction takes the whole thing back; outside one there is
nothing to take it back. Run a batch inside a unit of work when the pieces belong together.

## Retries match an exception the way `except` does

> **chosen** - Java matches the exact class and walks only `getCause()`. Matching subclasses and `__context__` is a decision that changes which failures retry. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java matches the thrown exception's exact class, and walks only `getCause()`. Here a policy
matches subclasses, and the walk also follows `__context__` - the error that was being handled
when this one was raised - unless it was deliberately suppressed.

```python
RetryConfig(on=OperationalError)  # matches every driver subclass of it, as `except` would
```

Exact-class matching is the surprising rule in a language whose `except` is subclass-based, and
a policy naming a base class that then caught nothing would be a very quiet way to lose every
retry. The consequence is that this port retries strictly more than Java does for the same
configuration; the default policy is one class with no subclasses, so the default is identical.

## The connection accessors are Java's, exactly

> **parity** - Now identical, including the details. The earlier divergence here is the one this triage exists because of.

`db()`, `dbs()`, `readonly_db()`, `readonly_dbs()`, `tx_db()` and `tx_db_else_db()` mean what
their Java counterparts mean, down to the details that are easy to get wrong:

- **`readonly_db()` is the replica**, falling back to the primary when a shard has none. It does
  not join an open transaction - a replica has not seen what that transaction wrote, so joining
  would be a promise the connection cannot keep.
- **`db()` is the primary** and does not join an open transaction either. `tx_db_else_db()` is
  the one that joins.
- **`dbs()` and `readonly_dbs()` visit only the default shard** when the repository is
  unsharded, so a repository that opted out of sharding cannot scatter onto a sibling's
  databases.
- **The built-in reads use the primary**, as Java's do. Choosing to accept lag belongs to the
  caller, who is the only one who knows whether they just wrote the row they are about to read.

This was not always true. An earlier version of this port made `readonly_db()` mean "a read" and
default it to the primary, with `replica=True` to opt in - same routing, different name. It was
recorded here as a neutral difference, which was the mistake: it is a judgement call about whose
job consistency is, and it belonged in `docs/DECISIONS-TO-REVIEW.md` where it would have been
argued about rather than filed.

## The connection they hand back has a `rows()` on it

> **chosen** - jOOQ ends a query with the mapping and SQLAlchemy has no such verb, so this port adds one. It could as easily have been left to a comprehension. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

The accessors above mean exactly what Java's mean. What they *hand back* differs: a `Db`, which
is a `sqlalchemy.Connection` with one method added.

Java ends a custom query with the mapping, in the same chain as the query:

```java
ctx.selectFrom(WALLETS)
   .where(WALLETS.OWNER_ID.eq(ownerId))
   .and(WALLETS.STATE.ne(WalletState.DELETED.name()))
   .fetch(this::fromRecord);
```

SQLAlchemy has no verb for it. A bare connection leaves the mapping to the caller, afterwards:

```python
rows = conn.execute(statement).all()
return [self.from_row(row) for row in rows]
```

So `Db` supplies the missing one, and the order matches the Java:

```python
return conn.rows(statement).fetch(self.from_row)
```

`conn.rows(statement)` returns a `FetchResult`, whose terminals each take the mapper:
`fetch`, `fetch_first`, `fetch_one`, `fetch_one_or_none`, `each`, plus `yield_per`,
`partitions` and `unique` for shaping. Full table in
[`docs/database/repositories.md`](docs/database/repositories.md#examples).

Three things about it are deliberate:

- **It adds, it does not replace.** `Db` is a subclass, so `execute`, `begin`,
  `exec_driver_sql` and the rest are SQLAlchemy's, unchanged. A query with no rows to map - a
  count, a rowcount, an `EXISTS` - still goes through `execute`, because `rows()` would be
  claiming a shape the answer does not have.
- **The names are not jOOQ's.** There, `fetchOne` means *at most one, raise if there are two*
  and `fetchAny` means *the first*. Copying the spellings would give a `fetch_one` that does not
  mean "fetch one", so these say what they do instead.
- **`FetchResult` holds a `Result`; it does not subclass one.** Subclassing was shorter and was
  rejected: reproducing a `Result` means setting `_real_result`, `_metadata`,
  `_post_creational_filter` and `_unique_filter_state` by hand - four private attributes whose
  contract can change in a patch release. Everything `FetchResult` calls is documented API.

What it does **not** do is filter soft-deleted rows. A statement you build yourself carries
what you put in it, in both ports - which is why the example repositories write
`WALLETS.c.state != WalletState.DELETED.name` out, exactly where the Java writes
`.and(WALLETS.STATE.ne(...))`.

## An unregistered shard is refused, not defaulted

> **chosen** - Java falls back to the default shard; this raises. A typo routing writes somewhere nobody looks is the failure that decided it. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Asking the registry for a shard it does not hold raises, naming what is registered:

```
ValidationError: no database registered for shard 9:9; registered: ['0:0']
```

Java returns the default shard instead. That fallback has a real use - one codebase runs
unsharded in development and sharded in production with no branching - and a real cost: a
mistyped shard identifier is silent, and the writes land on the default where nobody thinks to
look for them. The bug it produces is invisible until somebody counts rows.

Refusing gives up the convenience. What replaces it is explicit: a repository constructed with
`NoShardingStrategy` resolves every key to the default shard, and its scatter accessors visit
only that shard. That is the supported way to run unsharded, rather than relying on every
lookup missing.

## Deleting

> **parity** - The same as Java in both ports, after some wandering.

The same as Java, after some wandering: there is no delete of any kind on the repository in
either port. `GenericState.DELETED` is the convention, every read filters it, and what deletion
*means* belongs to the model:

```python
def delete(self) -> Wallet:
    if self.state is WalletState.DELETED:
        return self
    return self.copy().with_event(WalletDeleted()).state(WalletState.DELETED).build()
```

then `repo.update(wallet.delete())`, or `plan.update(wallet.delete())` inside a unit of work.

This port briefly had `Repository.delete()` and `Repository.hard_delete()`. Both are gone. The
first wrote the DELETED state without emitting anything, so a wallet vanished from every read
in the system and nothing downstream was told; the second issued a physical `DELETE`, bypassing
the optimistic lock and leaving published events describing a row that no longer existed. An
application that genuinely needs a physical delete writes it against its own rule - see
`WalletRepository.erase` in the example, which refuses a wallet that still holds money.

## The scheduler is this framework's, not a dependency's

> **forced** - There is no maintained, synchronous, multi-dialect, database-backed scheduler for Python. Checked against the ecosystem rather than assumed.

Java delegates distributed jobs to db-scheduler. Python has no equivalent, which was checked
against the ecosystem rather than assumed. What this needs is all four of: maintained,
synchronous, multi-dialect, and database-backed. Every candidate fails at least one.

| candidate | stars | why not |
| --- | --- | --- |
| APScheduler 3.11 | 7.6k | its own FAQ: *"How do I share a single job store among one or more worker processes? Short answer: You can't."* Two instances give duplicate execution - the opposite of what a distributed job is |
| APScheduler 4.0 | same | has a shared data store, but its tracking issue has been open since September 2020 and `4.0.0a6` still says "should not be used in production" |
| procrastinate | 1.4k | Postgres only |
| pgqueuer | 1.5k | Postgres only |
| chancy, BeanQueue, dmi-scheduler | 286 / 28 / - | Postgres only |
| pyncette | **12** | the only real match on paper - leases, and Postgres *and* MySQL. But asyncio-only, so every `DistributedJob.run` would become `async def` in application code; and one contributor, 112 downloads a month |
| huey + SqlHuey | 5.5k | the one that is genuinely multi-dialect without a broker - SqlHuey stores the queue through peewee, so MySQL and Postgres both work. But each consumer's scheduler enqueues periodic tasks on its own, and `lock_task` is a mutex rather than a claim, so two consumers run every slot twice |
| celery, dramatiq, arq, rq, taskiq | large | need a broker - Redis, RabbitMQ, NATS - so not database-backed |
| hatchet | - | an SDK for a server you deploy, not a library |
| dbos | - | Postgres only |
| prefect, dagster, airflow, temporal | large | orchestration platforms; you would deploy Airflow to run a fan-out loop |
| sqlalchemy-celery-beat | 82 | stores the *schedule* in Postgres/MySQL/SQLite, which is the right half - but it is Celery Beat, and Celery's docs say *"you have to ensure only a single scheduler is running for a schedule at a time, otherwise you'd end up with duplicate tasks"*. The only lock in the package guards a `last_run_at` write-back, not a slot |
| django-celery-beat | 1.8k | the same, plus Django |
| `db-scheduler` on PyPI | - | unrelated package: 0.1.4, no cluster, claim or heartbeat in it |
| aistudio-scheduler-lock | - | the one ShedLock-alike; requires Django < 4.0 |

The top two rows were measured rather than taken from their documentation. Two processes, one
shared store, one job scheduled every second for eight seconds:

| | runs | outcome |
| --- | --- | --- |
| huey + SqlHuey | 16 over 8 slots | every slot run by both instances |
| huey + SqlHuey + `@huey.lock_task` | 16 over 8 slots | identical - the lock changes nothing |
| sqlalchemy-celery-beat | 14 over 7 slots | every slot dispatched by both instances |
| APScheduler 3.11 + SQLAlchemy job store | 14 over 8 slots | six slots run twice |
| this `JobRegistry` | 8 over 8 slots | four each, no slot twice |

huey's `lock_task` is worth its own line, because it looks like the answer and is not. It works
exactly as documented - a second acquire while the first is held is refused - but it is released
when the task returns, so two runs of one slot that do not overlap both proceed:

    held concurrently        second acquire refused
    acquire, release, again  second acquire succeeds

A `ttl` on that lock does not rescue it either, and this is worth writing down because the
suggestion looks so reasonable: `lock_task(name, ttl=...)` raises `NotImplementedError: ttl is
not supported by this storage` on every backend except Redis - including the SQL one, which is
the whole reason huey was a candidate. And even on Redis the ttl is a safety net for a holder
that crashed, not a claim on the slot: `TaskLock.__exit__` deletes the key unconditionally when
the task returns.

That is the distinction the whole table turns on. A mutex answers "is someone running this right
now"; a claim answers "has this slot already been taken". Only the second prevents duplicate
execution, and duplicate runs of a fast task are sequential, so the mutex is never contended.

For Celery Beat the probe intercepted `apply_entry` - the moment beat decides a slot is due -
so no broker was involved and nothing else could account for the number.

One pattern runs through the whole table, and it is the useful thing to take from it: several
Python libraries persist the *schedule* in a database. What none of them does is let two
processes race for a slot and have exactly one win. Those are different problems, and only the
second is why db-scheduler exists. A library that stores the schedule in Postgres and then tells
you to run exactly one instance has solved the half that was never hard.

pyncette is the interesting one and the reason this table exists rather than a sentence. It is
genuinely the right shape - a distributed scheduler with pluggable Postgres and MySQL backends -
and it would still be the wrong dependency. What would be delegated is the atomic claim, the code
that stops five servers each running the nightly report; a defect there is not a crash, it is the
report going out twice. That is not a thing to take from a solo project with a hundred downloads a
month, whatever its quality.

So the claim-and-heartbeat loop is implemented here - about two hundred lines - against
db-scheduler's own table layout, so a Java service and a Python service coordinate through one
`scheduled_tasks` table rather than each running the nightly report. That last requirement rules
out every row above on its own: none of them would use db-scheduler's schema.

Schedules are `Every`, `FixedDelay` and `Daily`. Cron is not built in: parsing it needs a
dependency, and a `Schedule` is one method.

## Handlers are duck-typed

> **forced** - A `Protocol` is how Python spells an interface. No behaviour differs.

Java's `EventHandler` is an interface. Here it is a `Protocol`: anything with `name`,
`event_type` and `handle` is a handler, so an existing class can subscribe without inheriting
anything. `aliases` is optional and read with `getattr`, which is what makes a handler rename
possible without every handler declaring an empty set.

## Names that still differ, and why

> **parity** - A table of names, each differing because the thing differs. No behaviour.

Everything that names the same concept now carries the same name, so a reader moving between the
two ports recognises what they are looking at. What is left differs because the thing itself
differs, not because the name was chosen freely:

| Java | here | why |
| --- | --- | --- |
| `AbstractRepository` | `Repository` | nothing is abstract; see above |
| `Repository` (interface) | - | the narrow interface has no consumer here yet |
| `EventEntity`, `EventEntityRepository` | - | rows are dicts through SQLAlchemy Core, not a mapped class |
| `EventNotification`, `EventNotificationRepository` | - | the same |
| `ModelId`, `Identifiable`, `ShardAwareId` | - | interfaces that exist to name a shape Python reads off the object |
| `ShardedUUID`, `Backoff` | `sharded_uuid()`, `backoff()` | a class holding only static methods is a function here |
| `ShardGroupConfig` + `ShardMemberConfig` | `ShardConfig` | one flat shard list rather than a group/member nesting |
| `Transaction` (package-private) | - | the auto-commit dance SQLAlchemy already does; the public `Transaction` here is the counterpart to the `DSLContext` Java hands the block, not to this |
| `ConnectionProvider` | - | `Engine` is pool, dialect and lifecycle in one |
| `ActionSpec`, `ActionAssert` | `run_action`, `StagedWork` | see above |
| `Entity.Builder`, `Model.Builder` | `EntityBuilder`, `ModelBuilder` | same two builders, unnested - a nested class is not a Python idiom, and `ModelBuilder` can then inherit rather than repeat |
| `resolveShardIdentifier()` | `.shard` | a property, because it reads a field of the id rather than doing work |
| `ModelId.stringValue()` | `str(id)` | what `__str__` is for; `Id.toString()` in Java returns the same string |
| `DatabaseRegistry.transactionManager(id)` | `.manager(shard)` | Java repeats the return type in the name; Python does not |
| `DatabaseRegistry.defaultTransactionManager()` | `.default` | a property, as it reads no argument |
| `DatabaseRegistry.allTransactionManagers()` | `.shards` + `.manager()` | its one caller is the fanout job, which wants the shard as well as the manager |
| `ShardingStrategy.resolveShardIdentifierById(id)` | `.shard_for_id(key)` | same idiom - the return type is `ShardIdentifier` and `shard_for` says so |
| `ShardingStrategy.resolveShardIdentifier(persistable)` | `.shard_for(persistable)` | as above |
| `ShardedUUID` (wrapper class) | `sharded_uuid()`, `read_shard()`, `uuid_instant()` | the class holds one `UUID` and three derivations of it; here they are three functions on a `UUID` |
| `LockKeyHash.hashUtf8(key)` | `lock_key_hash(key)` | a class of static methods is a function here - the value it computes is identical, and pinned by test |
| `ExecutionConfiguration.retryConfigs` (a `Map<Class, RetryConfig>`) | `.retry_configs` (an ordered tuple) | the class Java keys the map by lives on each config here as `on`, which can name several at once - `except (A, B)` is one clause, and a map key cannot be |
| `Retry` (class) | folded into the executor's loop | it has one caller and no state beyond what that caller already holds |

One place where matching Java costs something: `ExecutionConfiguration` sits next to
`ShardingConfig`, `ShardConfig` and `DataSourceConfig`, because that is the inconsistency Java
has and the name is the thing worth matching. `RetryConfig` follows Java rather than the
`RetryPolicy` it was called here.

## A row checks its own invariants, because Python has no constructor to hide them in

> **forced** - A frozen dataclass has no constructor body; `__post_init__` is where the same checks go.

Java's `Model` and `Entity` constructors validate: `id`, `state` and `version` non-null,
`version >= 1`, `events` non-null and copied. `ModelTest` and `EntityTest` pin each one by
message. Those checks are here too, in `__post_init__`, for the same reason - without them the
mistake surfaces as an `IntegrityError` from inside a batched insert, naming a column and a
driver, several frames from the line that made it.

Two things follow from Python that do not follow from Java. A subclass defining its own
`__post_init__` must call `super().__post_init__()`, where Java's constructor chaining is
automatic. And the events tuple is genuinely copied: `events` is annotated as a tuple, nothing
stops a list being passed, and a frozen model holding the caller's list is not frozen - append
to it afterwards and the model gains an event it was never built with, which the persister then
writes, in a transaction that had already decided what it was writing. Java says
`List.copyOf(...)` for exactly this and gets immutability out of it as well.

### The application's own fields are checked too, which Java's base class cannot do

Java validates a subclass's fields one `Validate.notNull` at a time, in every model's
constructor:

```java
Wallet(WalletBuilder builder) {
    super(builder);
    this.ownerId  = Validate.notNull(builder.ownerId,  "ownerId cannot be null");
    this.currency = Validate.notNull(builder.currency, "currency cannot be null");
    this.balance  = Validate.notNull(builder.balance,  "balance cannot be null");
}
```

It has no choice: a Java base class cannot enumerate a subclass's fields. A dataclass can, so
the rule is stated once, in `Persistable.__post_init__`, and reads the annotation:

| Java | here | `None` |
|---|---|---|
| `String currency` | `currency: str` | refused |
| `Optional<String> note`, present | `note: str \| None` | — |
| `Optional<String> note`, absent | `note: str \| None` | accepted |
| a `null` `Optional` **reference** | *no counterpart* | — |

Same semantics, one sentinel instead of two: Python's absence **is** `None` rather than a
wrapper that can itself be missing, so Java's fourth row has nothing to map to. The gain is that
it covers the fields an author would have forgotten as well as the ones they remembered -
`ekbatan-py-examples`' `Wallet` accepted `owner_id=None`, `currency=None` and `balance=None`
until `EKB-0193`, because three lines Java writes by hand had not been written.

Cached per class, since a dataclass's fields are fixed at definition: **1.6 us** per
construction, measured over 200,000.

## Equality compares the whole row

> **chosen** - **Behavioural and easy to trip over**: two wallets with one id and version but different balances are equal in Java and not here. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java's `Model.equals` compares `id`, `state`, `version`, `createdDate` and `updatedDate`, and
its `hashCode` is the id's alone - so two wallets with the same id and version but different
balances are equal in Java and not here, where a frozen dataclass compares every field. Events
are excluded in both, which is the part that matters: two readers of one row are looking at the
same wallet whether or not one of them is mid-change.

In practice the version moves whenever the content does, so the two agree on every row a
repository produces. Where they differ, this port is the stricter of the two, and a test
asserting `loaded == expected` means here what it looks like it means.

## A shard member carries one datasource pair, not a named map

> **chosen** - Java's `configFor(name)` map is dropped for a fixed pair. A surface removal somebody decided. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java's `ShardMemberConfig` holds a `configs` map with two reserved keys, `primaryConfig` and
`secondaryConfig`, plus any number of user-named entries reached through `configFor(name)`. Its
three DI modules use that to give the job scheduler a pool of its own -
`member.configFor("jobsConfig")` - so the scheduler's polling does not compete with the
application for connections.

Here a shard has a `primary` and an optional `replica`, and nothing else. The map exists in
Java because a DI container binds YAML into it and there is nowhere to hand an object; this
port hands the object: `job_registry().engine(create_engine(...))` gives the scheduler its own
pool directly, which is the same remedy with one less layer. Nothing binds configuration from a
property file here, which is also why `PropertyKeyNormalizer` has no counterpart.

Reopen it if configuration ever does come from a file: the named map is the right shape for
that, and this is the wrong shape for it.

## SQLite is a dialect here and is not one in Java

> **chosen** - An addition. Java refuses everything but the three. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

`DataSourceConfig` accepts `sqlite://`. Java accepts PostgreSQL, MySQL and MariaDB and refuses
everything else, including H2.

It is not a supported production dialect here either - what it buys is that the behavioural
suite runs a second after you write a test, with no daemon, and then runs against the real three
before anything is believed (`--dialects=all`). A framework whose tests need Docker to run at
all is a framework whose tests stop being run.

## A write without RETURNING rebuilds what it wrote

> **forced** - MySQL has no `RETURNING`. Both ports branch; the branches differ because the drivers do.

MySQL has no `INSERT ... RETURNING` and no `UPDATE ... RETURNING`, so there is nothing to read
back. Java takes the obvious branch - `if (dialect.equals(SQLDialect.MYSQL)) { execute(); return
List.copyOf(domainObjects); }` for inserts, `return domainObject.nextVersion()` for updates - and
hands the caller their own objects.

This port cannot. Java's types make a raw `UUID` unpassable where an `Id<Wallet>` belongs, and
`nextVersion()` goes through a builder that drops the event list; here neither is true, so the
same branch returned a model still carrying the events it had just persisted - which the next
update would write a second time - and a raw uuid where every other database returned a wrapped
one. Both paths now rebuild through the same readers a query uses, so one call has one shape on
four databases.

What is still dialect-dependent, in both ports, is *scale*: a `Decimal("2.50")` written to a
`NUMERIC(19,4)` column comes back as `2.5000` where RETURNING exists and as `2.50` where it does
not. Java's `should_preserve_converted_values_when_updateAll_batches_multiple_rows` sidesteps this
by re-reading rather than asserting on the return value, and the test here does the same.

## `plan.add` hands back the row you passed, as Java's does

> **parity** - Java's `ActionPlanTest.add_returns_the_entity` asserts `isSameAs`, and so does this port's test. Brought to parity in `EKB-0192`.

This used to hand back a stamped copy, because the framework filled in `created_date` and
`updated_date` and a caller holding the unstamped original was holding something that did not
describe the row about to be written. That reasoning was sound and its premise is gone:
`created_date` is required at construction now, so there is nothing left to fill in and `add`
hands back the object it was given.

`plan.update` matched Java already - `entity.nextVersion()` on both sides, so the version has
moved and the rest of the unit of work reads forward.

`add_all(None)` is the one difference left here. Java tolerates a null collection and returns
empty; here it is a `TypeError`. `None` where an iterable belongs is a mistake at the call site,
and returning empty lets it through to become "this unit of work staged nothing" - which commits
a sentinel row and looks like success.

## The test-support API is data, not an assertion DSL

> **chosen** - Java ships `ActionSpec`/`ActionAssert`. Returning data instead was a decision, and it is a smaller surface. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Java ships `ActionSpec` and `ActionAssert` - 24 fluent assertion methods, and 38 tests of those
methods. `run_action` returns a `StagedWork` instead: `result`, `added(Type)`, `updated(Type)`,
`additions`, `updates`, `staged`, `events`, `emitted(Type)`.

The reason is pytest. `assertAdded(Wallet.class, 2)` exists because a bare `assert` in Java
reports "expected true, got false"; `assert len(staged.added(Wallet)) == 2` already prints both
sides and the list. A DSL here would be a layer between the reader and what actually happened,
and it would need its own 38 tests.

`assertThrows` has no counterpart either - it is `pytest.raises`, which does more.

`VirtualClock` is the opposite case: it is ported in full, three modes and all, because those
are behaviour rather than assertion sugar. One addition - `VirtualClock(instant)` starts frozen
at that instant, where Java's constructor takes a real clock and starts running. `VirtualClock()`
still means what Java's does.

## Dispatch delivers one notification at a time

> **forced** - No virtual threads. Recorded as a shortcoming, not a preference.

Java invokes a batch's handlers in parallel on virtual threads and then commits the outcomes in
one batch `UPDATE` per bucket - one for the successes, one for the pre-flight expiries, one for
the post-failure expiries, and one per distinct `attempts` value among the retries. Here the
batch is delivered in order and each outcome is a row in one transaction.

Both are safe: an outcome is recorded for every row or for none, and a delivery that dies takes
nobody else's result with it. What differs is throughput on a large backlog with slow handlers,
and statement count - a hundred rows is a hundred `UPDATE`s here and about four there. Neither
has bitten yet, and the fix if it does is a thread pool plus bucketed updates, not a redesign.

One behaviour does differ. Java treats "no handler is registered under this name" as a delivery
failure, increments `attempts`, and lets the row expire through the retention window. Here the
row is left `PENDING` with its attempt count untouched and its retry rescheduled: a handler
removed by a bad deploy comes back, and its queue should still be there when it does. The row
still expires on the same retention window either way.

## Migrations run on Alembic, with the guard Alembic does not have

> **forced** - There is no Flyway for Python. The checksum guard restores what Flyway has and Alembic lacks.

Java uses Flyway. Here it is Alembic - SQLAlchemy's own migration tool, by the same author -
driven as a library: a `Config` built in memory rather than an `alembic.ini`, so an application
migrates itself with no external binary and no second copy of its connection settings.

Two things sit on top, both because Alembic does not have them and a sharded deployment needs
them. `migrate(config, location)` drives one `Config` per shard, sequentially and fail-fast,
naming the shard that failed and the ones already done. And `ekbatan_revision_checksums` records
a hash of every applied revision, verified before each upgrade.

That second one is not a refinement. Alembic's `alembic_version` holds a single row - the current
head - with no per-migration history and no record of what a revision contained, so editing an
applied revision is accepted in silence. Measured, not assumed: the upgrade ran again without
complaint while the database kept the old columns and the file described new ones. Flyway refuses
that; so does this.

## The Redis lock is `SET NX PX` on redis-py, not Redisson

> **forced** - There is no Redisson for Python. The mechanism underneath it is what this does directly.

Java uses Redisson's `RLock`. There is no Redisson for Python, and the mechanism underneath it is
`SET NX PX` plus a Lua compare-and-delete, which is what this does directly - verified against a
real Redis: mutual exclusion under 32 contending threads, a TTL that expires on its own, and a
release that refuses to delete a key it does not own.

`redis-py` because it is the only serious candidate that is synchronous (this framework is, and
`coredis` is async-only), ships `py.typed`, and is actively released - 8.1.0 on 2026-07-30 against
`valkey`'s 2025-08-11.

Re-entrancy works out the same way for opposite reasons. Java layers its own
`KeyedReentrantHolder` over Redisson to *correct* it: Redisson is reentrant already, but its
last-call-wins `leaseTime` would let an inner re-entry shorten the outer holder's TTL. Here the
same holder *provides* the behaviour, because nothing underneath does. Both make the outermost
`max_hold` the one that governs.

## The lock connection runs in autocommit, as Java's pooled one does

> **parity** - Not a divergence - a bug this port had and fixed, kept because finding the cause took two passes.

Not a divergence - a bug this port had and Java does not, recorded because finding the *cause*
took a second pass and the first pass looked convincing.

The symptom: a `try_acquire` that simply timed out **raised instead of returning None**, which is
the precise confusion the empty-result contract exists to prevent - a caller cannot tell a busy
lock from a broken one if both arrive the same way. `pg_advisory_lock` under a `lock_timeout`
raises `55P03` *and aborts the transaction*, after which Postgres refuses every later statement
on that connection; the `SET lock_timeout = 0` reset sat in a `finally`, ran on the aborted
transaction, and raised `InFailedSqlTransaction`.

The first fix was to keep the reset off that path. It works, and it is the wrong fix: it removes
one statement from an aborted transaction and leaves the next one to find it. The cause is that
this port opened the lock connection through `engine.connect()`, where SQLAlchemy begins a
transaction implicitly. Java borrows a pooled JDBC connection with `autoCommit=true`, so a failed
statement ends its own implicit transaction and leaves nothing behind. An advisory lock is
session-scoped in both Postgres and MySQL, so there was never a transaction to want.

Measured both ways: with the original `finally` restored and `AUTOCOMMIT` added, the suite passes;
the reset is simply no longer running against a poisoned connection. Both changes are in, because
the narrower one is still correct and costs nothing.

Two more things came out of reading `PostgresKeyedLockProvider` whole rather than inferring it
from its tests. A connection whose `lock_timeout` reset failed is now **invalidated** rather than
returned - Java calls it `dirty` and evicts it, because the next borrower would otherwise inherit
a timeout nobody set. And a failed `pg_advisory_unlock` invalidates too: the unlock did not
happen, so that session still holds the lock, and handing the connection back to the pool would
hand the lock with it.

## An action with no parameters passes `None`; one with parameters passes an object

> **parity** - Both ports refuse a scalar in the params column, with the same reasoning.

`action_params` is a JSON object column, and both ports refuse to write a scalar or a list into
it: `Action[DepositParams, Wallet]`, never `Action[str, Wallet]`. Java's message says the same -
"use a record/class params object". A bare `"MXN"` in that column is a row no typed consumer can
read, and `ActionEvent.action_params` is a mapping in both ports.

Where they differ is `None`. Java refuses it too, because an action there always takes a params
object, so a null reaching the check means somebody made a mistake. Here
`executor.execute(SomeAction(), None)` is simply how a parameterless action is invoked, so `None`
is normalised to `{}` rather than refused.

The rule that makes both coherent: `None` means "no parameters" and has an unambiguous
empty-object form; `"MXN"` means "one unnamed parameter" and does not - storing it would mean
inventing a key that no reader on either side knows to look for.

## There are no virtual threads, so each place Java forks one is answered separately

> **forced** - CPython has no equivalent. Five sites, five answers, each recorded.

Java is built around virtual threads and says so on its front page. CPython has nothing
equivalent: a thread is an OS thread, it costs about a megabyte of stack, and the GIL means
more of them buys concurrency on I/O but not parallelism on CPU. So "port the virtual threads"
is not a thing that can be done - each site where Java forks one is its own decision. There are
five in the main sources, and they went five different ways.

**The unit of work itself - no threads either side.** This is the one that matters most and it
needs none. Java binds the open transaction to a `ScopedValue`; here it is a `ContextVar`
holding a shard-to-connection mapping. Neither forks anything; both answer "is this execution
context already inside a transaction on this shard". The model above it is the host's to
choose - a thread per request, a pool, a single thread - and the framework works under any of
them because the state is per-context rather than global. Verified rather than assumed, in
`tests/integration/test_concurrency.py`: eight concurrent units of work take eight distinct
connections, cannot see each other, and a pool worker does not carry a transaction into its
next task.

**Work forked from inside an action - the same rule, stated the same way.** Java's own
documentation tells you to fan out on virtual threads, *join*, and only then touch the plan,
because a plain virtual thread does not inherit a `ScopedValue` binding. A `ContextVar` behaves
identically, and `current_plan()` raises rather than returning `None` so the mistake is loud.

**The keyed-lock watchdog - a real thread, and it costs.** Java gives each outermost hold a
`Thread.ofVirtual()` watchdog to force-release at `maxHold`. Here it is a `threading.Timer`,
which is an OS thread. Same behaviour, and fine at the scale a keyed lock is meant for - but a
service holding a thousand locks at once holds a thousand threads, where Java holds a thousand
virtual ones. If that ever becomes the constraint the fix is one timer thread with a heap of
deadlines, not a thread per hold.

**Job workers - a fixed pool, now with a knob.** Java hands db-scheduler
`Executors.newVirtualThreadPerTaskExecutor()`, so its worker count is unbounded and
`threads(jobs.size())` only sizes the polling batch. Here the workers are a fixed
`DaemonThreadPool`, defaulting to one per registered job - right, because a recurring task is a
single row and so has at most one execution in flight. `JobRegistryBuilder.workers(n)` changes
it, which Java has no need of and this port does.

**Fan-out and dispatch - sequential, where Java is parallel.** Java forks one virtual thread per
shard in `EventFanoutJob.drainOneRound`, and one per due notification in `EventHandlingJob`.
Both are sequential here: a round visits each shard in turn, and a batch is delivered in order.
The dispatch half and its consequences are under "Dispatch delivers one notification at a time";
the fan-out half is the same trade for the same reason. On an eight-shard deployment a fan-out
round therefore takes eight batches' time rather than one. It has not bitten, the rounds are
short because the work per shard is one bounded batch, and the fix if it does is a pool around
the existing `_drain_shard` - which is a change of one line and a pool to size, not a redesign.

## The framework is synchronous, and blocking is not free here as it is in Java

> **forced** - A blocking call on a virtual thread is cheap and on an event loop is not. A property of the runtimes.

Java is built on virtual threads, where a blocking call costs almost nothing: the carrier thread
is released and picked up by other work. That is what lets its whole API be blocking without
apology.

This port is synchronous too - there is no `async` or `await` anywhere in `src/` - and under a
threaded server that is exactly right, for the same reason. Under an **asyncio** server it is
not, because a blocking call there stops the entire event loop rather than one carrier thread.

Measured: a 0.5s framework call made from inside an `async def` handler let a background
heartbeat tick **5 times where a free loop manages about 55**. Every other request on that loop
stalled for the duration.

So under FastAPI, Litestar or any ASGI server, a repository or executor call belongs in a
worker thread::

    wallet = await asyncio.to_thread(wallets.get, wallet_id)
    # or, in Starlette/FastAPI
    wallet = await run_in_threadpool(wallets.get, wallet_id)

Two things make this less painful than it sounds. A plain `def` endpoint in FastAPI is already
run in a threadpool by the framework, so the ordinary way of writing one is safe without any of
the above - `ekbatan-py-examples/fastapi_wallet_rest_pg` is written that way throughout, and
that is why it needs no `to_thread` anywhere. And the ambient transaction lives in a
`ContextVar`, which is per-async-task as well as per-thread, so the state mechanism is already
correct if this port ever grows an async surface.

It has not grown one, and would not gain much by it: SQLAlchemy's async support is a wrapper
over the same drivers, and the framework's value is in what it does between the queries. But an
`async def` handler calling straight into it is a real mistake with a real cost, and nothing in
the API stops you making it.

## The pool knobs are SQLAlchemy's, and three of Hikari's have no counterpart

> **forced** - Different pool libraries expose different knobs. The missing three are in SHORTCOMINGS.md.

Found by diffing Java's public API against this port's definitions, one file at a time, after
the shutdown hook showed that reading a name and assuming a rename is not verification.

`DataSourceConfig` exposes different knobs on each side because the pools underneath are
different objects, not because anything was forgotten - but three of Java's have no equivalent
at all, and an operator moving across should know which:

| Java (Hikari) | here | note |
| --- | --- | --- |
| `jdbcUrl` | `url` | credentials live in the url here, so `username` / `password` are gone with it |
| `driverClassName` | the url's `+driver` | `postgresql+psycopg`, not a separate class name |
| `maximumPoolSize` | `pool_size` + `max_overflow` | SQLAlchemy splits the ceiling in two and this port leaves the split visible, because an operator tuning a pool needs to see the knobs their own tooling reports on |
| `minimumIdle` | **none** | `QueuePool` keeps `pool_size` connections and has no separate floor; there is nothing to set |
| `idleTimeout` | **none** | `pool_recycle` is the closest and is *not* the same thing - it caps a connection's total age, not how long it may sit unused |
| `leakDetectionThreshold` | **none** | SQLAlchemy has no borrowed-too-long warning. A connection held past a threshold goes unreported here |
| - | `pool_recycle` | maximum age, which Hikari calls `maxLifetime` and this config does not expose |
| - | `pool_pre_ping` | liveness check on checkout |

The two that cost something are `idleTimeout` and `leakDetectionThreshold`. Neither is a rename
away; both would have to be written. `leakDetectionThreshold` is the one worth arguing about -
a connection leaked inside a unit of work is exactly the failure this framework's transaction
handling exists to prevent, and Java gets a log line for it that this port does not.

## The scheduler has no shutdown hook, and the event jobs never see the shutdown

> **chosen** - Open and undecided rather than settled - `atexit` does not fire on SIGTERM, so matching Java means a process-wide signal handler. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Two halves of one gap, found by re-reading `JobRegistry.java` whole after noticing its ledger row
had been marked `done` at ledger-creation time rather than by a porting pass.

**The hook.** db-scheduler's own `SchedulerBuilder.registerShutdownHook` defaults to `false`;
Java's `JobRegistry.Builder` deliberately flips it to `true` and calls it in `build()`, so an
ordinary SIGTERM runs `Scheduler.stop()` and in-flight executions drain. This port has no
counterpart at either level - not on `JobRegistryBuilder`, and not on the vendored
`SchedulerBuilder`. Verified by running: building a registry adds no `atexit` callback and leaves
`SIGTERM` at `SIG_DFL`. So a container stop kills the process mid-execution; those rows stay
`picked` with a frozen heartbeat and are only revived by the dead-execution path, 60 seconds
later on framework defaults. `JobRegistry.stop()` and the `with` block do the right thing - the
divergence is only in what happens when nobody calls them.

**The loop.** Java's `EventFanoutJob.execute` is `while (!shouldStop(ctx))` around
`drainOneRound()`, sleeping `pollDelay` between idle rounds: one execution runs for the life of
the process and checks `ctx.getSchedulerState().isShuttingDown()` each round. This port's
`execute` calls `drain_until_idle()` and returns, letting the `FixedDelay` schedule bring it
back - and ignores `ctx` entirely, so there is no counterpart to `shouldStop`.

The restructure is defensible on its own: short executions free the worker thread, leave the row
unpicked between rounds, and fail over faster because any instance can pick it up on the next
poll rather than waiting out a dead-execution window. `drain_until_idle` also caps itself at
1000 rounds and warns, where Java would spin indefinitely. What is not defensible is that it was
never written down, and that it compounds with the missing hook: neither port half can currently
be asked to stop early.

## Additions with no Java counterpart

> **chosen** - Additions by definition. Each one is something this port has and Java does not. [DECISIONS-TO-REVIEW.md](docs/DECISIONS-TO-REVIEW.md)

Recorded because a reader comparing the two trees will find these and wonder what they map to.
Nothing in Java corresponds to any of them, confirmed by searching the Java for the concept
rather than the name.

| module | what | why there is no Java one |
| --- | --- | --- |
| `locks/inprocess.py` | `InProcessKeyedLockProvider`, on the same `KeyedReentrantHolder` as the SQL and Redis backends | Java ships only the three SQL providers and the Redis one; a single-instance deployment there is expected to take a SQL lock against the database it already has |
| `accounting.py` | counts statements issued, so "one unit of work is one transaction, N staged rows is one statement" is checkable | Java asserts the same claims through jOOQ's execute listener in its own tests, not through a shipped module |
| `schema.py` | the framework's tables as SQLAlchemy metadata | Java's DDL lives in the Flyway migration SQL; there is no programmatic table definition to port. This is the definition Alembic generates from, and the one the cross-compatibility scenarios check |
| `codegen.py` | generates typed builders | Java's annotation processor does this at compile time; the processor itself is `n/a` in the ledger |

## Not ported

> **parity** - `event-streaming` runs in a JVM whichever language wrote the row, so there is nothing to port.

`event-streaming`, and not planned. Publishing the outbox to Kafka is a Debezium connector plus
a routing transform, running inside Kafka Connect, in a JVM, regardless of which language wrote
the row. A Python service does not need a Python connector; it needs the same connector pointed
at its database. What it does owe is the schema that connector reads, which is
`ekbatan.schema` and is checked by the conformance suite's cross-compatibility scenarios.
