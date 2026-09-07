# Where this port disagrees with the charter's reference

Rule 1 of [the charter](https://github.com/ekbatan-io/ekbatan-spec) is that idiomatic beats
identical: a port must read as if written by someone fluent in the language, not translated
into it. That guarantees disagreements, and written disagreement is how a port stays honest -
silent divergence is how a spec becomes fiction.

Every entry below is a place where this port does something the Java implementation does not.
None of them changes observable behaviour; each one changes what the application has to write.

## An action runs by type or by instance

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

The name is Java's. What it copies is not: Java's `copyBase` hands over the six fields the base
class knows about and leaves the subclass's for the next lines, because a Java base class
cannot enumerate a subclass's fields. That is why every Java model writes its own `copy()`, and
why one of them eventually forgets a field.

Here the fields come off the dataclass, so `copy_base` takes all of them and `copy()` is
inherited. A model that gains a field gains it in both places at once. Override `copy()` only
to make a copy that deliberately differs - dropping a cache, resetting a counter.

## Identifiers get a named type, not a generic parameter

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

Java requires `toRecord` / `fromRecord` on every repository. Here they default to mapping
dataclass fields to columns of the same name, converting enums by name and timestamps to
storage form. Override them when the shapes differ; most repositories never do.

This is the largest single reduction in application code, and the one most at risk of being
undone by a well-meant refactor - so it is stated here.

## Timestamps are stamped when the change is staged

Java leaves `createdDate` / `updatedDate` to the application. Here the plan stamps them from
the executor's clock, so the value the action is holding is the value that will be written.

This is a behavioural divergence rather than an ergonomic one, and it is deliberate: the
conformance scenario `time-and-types/timestamps_do_not_shift_with_the_session_zone` asserts the
stamped behaviour. Two lines that are the same every time and wrong occasionally do not belong
in application code.

A repository used on its own stamps the same way, so `repo.update(row)` and a staged update
leave the same `updated_date`. The version is not stamped there and never was: like Java's, it
moves inside the write, under `WHERE version = ?`, so a caller hands in the row as they read it
and gets it back one version on.

## The per-invocation plan lives in a `ContextVar`

Java uses a `ScopedValue`. A `ContextVar` is the direct analogue and is per-thread and
per-async-task at once, so the same mechanism works for a threaded service and an asyncio one
without the framework choosing for you.

Both share the property that matters: work handed to a pool from inside a unit of work does not
inherit the plan. That is correct - another thread is another connection, and quietly sharing
one would corrupt both - and it is the one thing about this that surprises people, so
`current_plan()` says so in its error message rather than returning `None`.

## Transactions are the caller's, and batching with them

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

## `readonly_db` means "a read", not "the replica"

Java's `readonlyDb()` always hands back the read-replica context, and nothing built into its
`AbstractRepository` calls it - every built-in read uses the primary.

Here every built-in read goes through `readonly_db`, which is the primary by default and takes
`replica=True` to accept a replica's lag. Same routing as Java, one accessor instead of the
built-in reads quietly bypassing the one named for reading. The name says what the caller is
doing; the keyword says what they are willing to pay for it.

## Deleting

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

Java's `EventHandler` is an interface. Here it is a `Protocol`: anything with `name`,
`event_type` and `handle` is a handler, so an existing class can subscribe without inheriting
anything. `aliases` is optional and read with `getattr`, which is what makes a handler rename
possible without every handler declaring an empty set.

## Names that still differ, and why

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

## Equality compares the whole row

Java's `Model.equals` compares `id`, `state`, `version`, `createdDate` and `updatedDate`, and
its `hashCode` is the id's alone - so two wallets with the same id and version but different
balances are equal in Java and not here, where a frozen dataclass compares every field. Events
are excluded in both, which is the part that matters: two readers of one row are looking at the
same wallet whether or not one of them is mid-change.

In practice the version moves whenever the content does, so the two agree on every row a
repository produces. Where they differ, this port is the stricter of the two, and a test
asserting `loaded == expected` means here what it looks like it means.

## A shard member carries one datasource pair, not a named map

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

`DataSourceConfig` accepts `sqlite://`. Java accepts PostgreSQL, MySQL and MariaDB and refuses
everything else, including H2.

It is not a supported production dialect here either - what it buys is that the behavioural
suite runs a second after you write a test, with no daemon, and then runs against the real three
before anything is believed (`--dialects=all`). A framework whose tests need Docker to run at
all is a framework whose tests stop being run.

## A write without RETURNING rebuilds what it wrote

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

## `plan.add` hands back the stamped row, not the argument

Java's `ActionPlanTest.add_returns_the_entity` asserts `isSameAs` - the very object you passed.
Here it is the stamped copy, because the framework fills in `created_date` and `updated_date`
and a caller holding the unstamped original is holding something that does not describe the row
about to be written. `plan.update` matches Java exactly: the version has already moved, so the
rest of the unit of work reads forward.

`addAll(null)` differs for the same sort of reason. Java tolerates a null collection and returns
empty; here it is a `TypeError`. `None` where an iterable belongs is a mistake at the call site,
and returning empty lets it through to become "this unit of work staged nothing" - which commits
a sentinel row and looks like success.

## The test-support API is data, not an assertion DSL

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

## Not ported

`event-streaming`, and not planned. Publishing the outbox to Kafka is a Debezium connector plus
a routing transform, running inside Kafka Connect, in a JVM, regardless of which language wrote
the row. A Python service does not need a Python connector; it needs the same connector pointed
at its database. What it does owe is the schema that connector reads, which is
`ekbatan.schema` and is checked by the conformance suite's cross-compatibility scenarios.
