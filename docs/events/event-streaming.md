# Streaming with Debezium

The other consumer path: point a CDC connector at the outbox and ship committed rows to a broker.

**Nothing in this path is Python code, and that is the point.** The connector and the transform
run inside Kafka Connect, in a JVM, whichever language wrote the row. A service written here uses
the *same* `ekbatan-debezium-smt` artifact a Java service uses, pointed at the same table. There
is no Python counterpart to build and none is planned.

What this port owes the pipeline is the **row format** - and that is
[`events`](../database/tables/events.md), verified column for column against Java's.

```
 your service (Python or Java)
        │  one transaction
        ▼
 ┌──────────────────────────┐
 │  events                  │
 └──────────────────────────┘
        │  write-ahead log
        ▼
 ┌──────────────────────────┐
 │  Debezium                │  ← a JVM, in Kafka Connect
 └──────────────────────────┘
        │
        ▼
 ┌──────────────────────────┐
 │  Kafka: the raw topic    │
 └──────────────────────────┘
        │  a router, optionally
        ▼
   per-model and per-event topics
```

## Topic naming

Three levels, all prefixed `ekbatan.{namespace}`:

```
ekbatan.{namespace}                        the raw topic - everything
ekbatan.{namespace}.model.{ModelType}      every event for one model type
ekbatan.{namespace}.event.{EventType}      one event type
```

With `namespace = "com.example.finance"`:

```
ekbatan.com.example.finance
ekbatan.com.example.finance.model.Wallet
ekbatan.com.example.finance.event.WalletOpenedEvent
```

Three levels for three consumption patterns: *everything from this service*, *everything about
this model*, *this one event type*.

### Hyphens in the namespace are refused, and here is why

`namespace` must be **dot-separated identifiers, shaped like a package** - letters, digits and
underscores, each segment starting with a letter or underscore. The executor rejects anything
else at construction.

A service name is the natural thing to put there and service names are conventionally hyphenated,
so this catches people out. The reason is that the namespace doubles as a **schema package**, and
protobuf's grammar is `ident = letter { letter | decimalDigit | "_" }`. `package my-service;` is
a syntax error, not a style preference - protoc reports `Expected ";"`.

Write `my_service`. It is refused up front rather than silently rewritten, because rewriting would
change both your topic names and the `namespace` column on every outbox row without anyone asking.

`{EventType}` is the event class's name. The persister rejects name collisions inside one service,
so routing stays module-move friendly without silently merging two different event classes.

## Partition by `model_id`

For the per-model and per-event topics, use `model_id` as the Kafka partition key. That gives
**per-entity ordering** - every event for one wallet lands in one partition and is consumed in
order - while different entities parallelise across partitions.

## Sentinel rows

An action that emitted no events still writes a row, with `event_type IS NULL`. A consumer skips
those; the transforms already do.

## Reading the rows

On the consumer side, `ekbatan.streaming.ActionEvent` is the shape - the same thirteen fields
under the same names as Java's `ActionEvent`:

```python
from ekbatan.streaming import ActionEvent

for message in consumer:
    event = ActionEvent.from_row(message.value)
    if event.event_type == "MoneyDepositedEvent":
        credit(event.model_id, event.payload["amount"])
```

Nothing is validated, deliberately: four fields are legitimately absent on a sentinel row, so a
type that rejected them would reject valid traffic.

**Mapping a raw CDC message is still the consumer's job**, in both ports. A Debezium topic carries
JSON columns as strings rather than nested objects, and timestamps as bare epoch microseconds -
and which of those you get depends on the connector, the converter and the transform. A helper
that guessed would be right for one pipeline and wrong for the next.

`from_row` handles the plain case. `from_message` takes the two conversions that are not
guessable:

```python
event = ActionEvent.from_message(
    message.value,
    json_columns_are_strings=True,
    timestamps_are_epoch_micros=True,
)
```

Keyword-only, which is the whole of what Java's builder is for. Its thirteen-argument constructor
put `model_type` next to `event_type` and `started_date` next to `completion_date` - adjacent
same-typed pairs that transpose silently and produce an event routed to the wrong topic or
carrying the wrong body.

## Running both paths at once

The in-process pipeline and this one coexist on one outbox. Fan-out flips `delivered` from false
to true, producing `UPDATE` rows; the transforms drop non-insert operations, so those flips never
become Kafka messages.

## What neither port ships

- **Kafka client wiring** - consumer and producer configuration, retry, dead-letter queues. Too
  infrastructure-specific to be right for anyone.
- **Debezium connector configuration** - the properties differ across PostgreSQL, MySQL and
  MariaDB and across deployment topologies.
- **A Schema Registry binding.** The transforms read schema files and need no registry.

## See also

- [`events`](../database/tables/events.md) - the contract this path depends on
- [The outbox](../concepts/outbox.md) - why the rows are there
- [In-process handlers](local-event-handler.md) - the other path
