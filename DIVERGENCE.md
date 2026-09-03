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

## `evolve()` instead of a builder

Java needs a generated CRTP builder to produce a changed copy of an immutable model. Python has
`dataclasses.replace`, so a change is one call and the event rides along with it:

```python
return self.evolve(balance=new_balance, emit=WalletMoneyDeposited(...))
```

No generated builder, no `copy().withEvent(...).build()`, and no annotation processor.

## Row mapping is by field name unless you say otherwise

Java requires `toRecord` / `fromRecord` on every repository. Here they default to mapping
dataclass fields to columns of the same name, converting enums by name and timestamps to
storage form. Override them when the shapes differ; most repositories never do.

This is the largest single reduction in application code, and the one most at risk of being
undone by a well-meant refactor - so it is stated here.

## `Entity` has a fixed lifecycle

Java parameterises `Entity` by a state enum. Here it is always `GenericState` - active or
deleted. Something needing a lifecycle of its own almost certainly has state changes worth
telling the rest of the system about, which makes it a `Model`. Subclass `Persistable`
directly in the rare case that is wrong.

## Timestamps are stamped when the change is staged

Java leaves `createdDate` / `updatedDate` to the application. Here the plan stamps them from
the executor's clock, so the value the action is holding is the value that will be written.

This is a behavioural divergence rather than an ergonomic one, and it is deliberate: the
conformance scenario `time-and-types/timestamps_do_not_shift_with_the_session_zone` asserts the
stamped behaviour. Two lines that are the same every time and wrong occasionally do not belong
in application code.

## The per-invocation plan lives in a `ContextVar`

Java uses a `ScopedValue`. A `ContextVar` is the direct analogue and is per-thread and
per-async-task at once, so the same mechanism works for a threaded service and an asyncio one
without the framework choosing for you.

Both share the property that matters: work handed to a pool from inside a unit of work does not
inherit the plan. That is correct - another thread is another connection, and quietly sharing
one would corrupt both - and it is the one thing about this that surprises people, so
`current_plan()` says so in its error message rather than returning `None`.

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
