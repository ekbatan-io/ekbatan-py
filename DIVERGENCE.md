# Where this port disagrees with the charter's reference

Rule 1 of [the charter](https://github.com/ekbatan-io/ekbatan-spec) is that idiomatic beats
identical: a port must read as if written by someone fluent in the language, not translated
into it. That guarantees disagreements, and written disagreement is how a port stays honest -
silent divergence is how a spec becomes fiction.

Every entry below is a place where this port does something the Java implementation does not.
None of them changes observable behaviour; each one changes what the application has to write.

## Actions are objects, not registered classes

Java dispatches by class token, so it needs an `ActionRegistry` between the caller and the
action. Python does not: an action is an object you built with the things it needs, and you
call it.

```python
executor.execute(deposit_money, DepositParams(...))     # here
executor.execute(principal, DepositMoneyAction.class, params)   # there
```

One fewer registry, one fewer place for a name to be wrong, and nothing to keep in sync.

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
emit a CRTP builder per class; here one `Builder` resolves fields from the dataclass at call
time, so a new model needs no build step and no generated file to keep in sync.

## `copyBase` is gone; `copy_from` replaces it

Java's `Model.Builder` has `copyBase`, which takes the framework's fields and leaves the
subclass's for you to set on the next lines. It exists to support hand-writing a `copy()`,
which is the boilerplate a Python base class does not need - so the builder has `copy_from`
instead, which takes every field, and the inherited `copy()` uses it.

Nothing was calling `copyBase`'s equivalent here, and two near-identical names on one builder
is a paper cut for every reader. If you want a `copy()` that deliberately differs, override it
and start from `copy_from`.

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

## Retries match an exception the way `except` does

Java matches the thrown exception's exact class, and walks only `getCause()`. Here a policy
matches subclasses, and the walk also follows `__context__` - the error that was being handled
when this one was raised - unless it was deliberately suppressed.

```python
RetryPolicy(on=OperationalError)  # matches every driver subclass of it, as `except` would
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

## Deleting is on the repository, not only on the model

Java has no delete of any kind: a caller writes `repository.update(row.delete())`, where the
model's own `delete()` returns a copy in the `DELETED` state.

Both are here, and `repo.delete(row)` is the same update under a name that says what it is for.
`hard_delete(id)` has no Java counterpart at all - it issues a real `DELETE`, and is deliberately
the longer thing to type, taking an id rather than a row so it cannot be reached by autocompleting
from something you happen to be holding.

## The scheduler is this framework's, not a dependency's

Java delegates distributed jobs to db-scheduler. There is no Python library with the same
shape, so the claim-and-heartbeat loop is implemented here - about two hundred lines - against
db-scheduler's own table layout, so a Java service and a Python service can coordinate through
one `scheduled_tasks` table rather than each running the nightly report.

Schedules are `Every`, `FixedDelay` and `Daily`. Cron is not built in: parsing it needs a
dependency, and a `Schedule` is one method.

## Handlers are duck-typed

Java's `EventHandler` is an interface. Here it is a `Protocol`: anything with `name`,
`event_type` and `handle` is a handler, so an existing class can subscribe without inheriting
anything. `aliases` is optional and read with `getattr`, which is what makes a handler rename
possible without every handler declaring an empty set.

## Not ported

`event-streaming`, and not planned. Publishing the outbox to Kafka is a Debezium connector plus
a routing transform, running inside Kafka Connect, in a JVM, regardless of which language wrote
the row. A Python service does not need a Python connector; it needs the same connector pointed
at its database. What it does owe is the schema that connector reads, which is
`ekbatan.schema` and is checked by the conformance suite's cross-compatibility scenarios.
