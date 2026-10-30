# Streaming with Debezium

The other way to consume events: Debezium follows the outbox table and publishes each committed row
to Kafka, where any service can read it.

**Nothing on the publishing side is Python code.** Debezium, a change-data-capture (CDC)
connector, runs inside Kafka Connect. It reads committed rows from the database's log and
publishes them, whichever language wrote the row. What your service owes the pipeline is the
**row format**: the [`events`](../database/tables/events.md) table, written in the same transaction
as your domain change.

```
 your service
        │  one transaction
        ▼
 ┌──────────────────────────┐
 │  events                  │
 └──────────────────────────┘
        │  write-ahead log
        ▼
 ┌──────────────────────────┐
 │  Debezium                │  ← in Kafka Connect
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

This page serves two readers. If you **consume** the events, read the variants,
[Topic naming](#topic-naming) and [Reading the rows](#reading-the-rows). If you **run Kafka
Connect**, the binary variant also needs [the transforms](#smts-variant-b-only).

## Variant A — JSON on Kafka

The default, and the one to start with. Point Debezium at the outbox and let its JSON converter
send each row as it is. Consumers read the fields straight from the message with `json.loads`.
There are no schemas to mount and no JAR to add to Kafka Connect, and `kafka-console-consumer`
shows you a readable message.

## Variant B — Binary on Kafka (Avro / Protobuf)

Two transforms, `OutboxToAvroTransform` and `OutboxToProtobufTransform`, encode the row as Avro
or Protobuf before it reaches the broker. Kafka Connect calls plugins like these *single message
transforms*, or SMTs.

They run inside Kafka Connect, not in your service. Kafka Connect plugins are JARs, so these two
come from Ekbatan's Java project, attached to every one of its releases - see
[Running beside a Java service](../beside-java.md#streaming-the-events) for where to
download them.

How to configure them, and what they do with each record, is under [SMTs](#smts-variant-b-only)
below.

## Picking a variant

| Variant | Pick when… |
| --- | --- |
| **JSON** | Internal services; low-to-medium traffic; you want to read messages straight off the wire; no company-wide schema rules yet. |
| **Avro** | You already have (or want) a Schema Registry; you want schemas with compatibility tooling; payload size matters at volume. |
| **Protobuf** | Consumers in many languages; you already maintain `.proto` files; you prefer Google's stack over Confluent's. |

Both binary variants make smaller messages than JSON and hold every message to a schema. The
cost: a schema to manage per event type, a transform to deploy into Kafka Connect, and consumers
that need the matching library.

You can run more than one: JSON on a side topic for operations, while Avro feeds production
consumers. Same outbox, two connectors, different transform settings.

## Why the database always stores JSON, regardless of variant

The encoding only matters on the wire. The outbox row holds JSON whichever variant you run, for
two reasons. The row has readers other than the broker: the in-process handlers, a backfill, an
operator with `psql`. And every service writing to one outbox has to agree on one stored form.

The transform encodes on the way out. Change variant and nothing about the table changes.

That keeps four failures away:

- **Writes stay simple.** Encoding against a schema takes time and can fail. Kept out of the
  transaction, a stale schema cannot make `executor.execute(...)` fail at commit and roll back
  the domain change for a reason that has nothing to do with it.
- **A schema mismatch shows up in Kafka Connect**, where it can be retried, logged, alerted on
  and sent to a dead-letter topic - not in your application's request path, where it would be a
  500.
- **Schema changes happen in one place**, the transform's configuration, rather than across
  every service that produces or consumes the event.
- **An operator can read the outbox with plain SQL.** `jsonb` is greppable; Avro bytes are not,
  and the row you most want to read is always the one from the incident.

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
ekbatan.com.example.finance.event.WalletCreatedEvent
```

Three levels for three kinds of consumer: *everything from this service*, *everything about this
model*, *this one event type*.

`{EventType}` is the event class's name, without its module, so moving the class to another
module does not change its topic. Two different event classes with the same name in one service
are refused when their events are written, so they can never be merged into one topic by
accident.

### Hyphens in the namespace are refused, and here is why

`namespace` must be **dot-separated identifiers, shaped like a package name**: letters, digits
and underscores, each segment starting with a letter or an underscore. The executor refuses
anything else when it is built.

A service name is the natural thing to put there, and service names usually have hyphens, so
this catches people out. The reason: the namespace is also the **schema package** for Avro and
Protobuf, and a Protobuf package name allows only letters, digits and `_`
(`ident = letter { letter | decimalDigit | "_" }`). `package my-service;` is a syntax error, not
a style choice: protoc reports `Expected ";"`.

Write `my_service`. It is refused up front rather than silently rewritten, because rewriting
would change both your topic names and the `namespace` column on every outbox row without anyone
asking.

## Output topics partitioned by `model_id`

For the per-model and per-event topics, use `model_id` as the Kafka partition key. That gives
**ordering per entity**: every event for one wallet lands in one partition and is consumed in
order, while different entities spread across partitions.

## The router (optional but useful)

The connector does not have to publish to every final topic. A common shape is one raw topic
per service, and a routing layer deriving the rest:

```
ekbatan.com.example.finance
        │
        ├─ Kafka Streams / ksqlDB / Flink / a small consumer-producer
        │
        ├─ ekbatan.com.example.finance.event.WalletMoneyDepositedEvent
        ├─ ekbatan.com.example.finance.model.Wallet
        └─ billing.wallet-deposits-by-owner
```

Keep the raw topic as the append-only record of everything the service emitted, and derive the
rest from it. Derived topics can have their own retention, schema, partition count and key, and
nothing changes in how actions write to the database. That is the property worth protecting:
the Kafka side never reaches back into the write path.

A route is a rule, not a step in a chain: one event can match several routes and go to all of
them. The router keeps no state, so you can run as many copies as you need. And it is safe to
crash: the raw topic keeps the events while it is down, so a router outage is a delay rather
than a loss.

## What every message carries

Every message, in every variant, carries the same 13 fields, the outbox row's columns: `id`,
`namespace`, `action_id`, `action_name`, `action_params`, `started_date`, `completion_date`,
`model_id`, `model_type`, `event_type`, `payload`, `event_date`, `delivered`. Only the encoding
differs. This page calls that shape the *envelope*; the event's own data sits inside it, in
`payload`.

In Python, `ekbatan.streaming.ActionEvent` is that shape as a frozen dataclass.
`ActionEvent.from_row(...)` builds one from a row's columns, such as a JSON message, and
`ActionEvent.from_avro(...)` and `ActionEvent.from_protobuf(...)` build one from the binary
messages. For the binary formats the shape is also fixed by the transforms' own schemas,
`ActionEvent.avsc` and the Protobuf descriptor, and both ship inside the package.

## Sentinel rows

An action that emitted no events still writes one outbox row, a *sentinel*, with `event_type`
NULL; `model_id`, `model_type` and `payload` are NULL there too. With Variant A a consumer skips
those itself (`event.is_sentinel` is true for one). The Variant B transforms already drop them.

## Reading the rows

For JSON messages (Variant A), build an `ActionEvent` from each message with `from_row`:

```python
from ekbatan.streaming import ActionEvent

for message in consumer:
    event = ActionEvent.from_row(message.value)
    if event.event_type == "WalletMoneyDepositedEvent":
        credit(event.model_id, event.payload["amount"])
```

`from_row` does not check the values, on purpose: four fields are legitimately empty on a
sentinel row, and a type that refused them would refuse valid messages.

**Turning a raw CDC message into a row is still your job.** A Debezium topic may carry the JSON
columns as strings rather than nested objects, and timestamps as bare epoch microseconds. Which
of those you get depends on the connector, the converter and the transform, so a helper that
guessed would be right for one pipeline and wrong for the next.

`from_row` handles the plain case. `from_message` undoes those two conversions when you say they
apply:

```python
event = ActionEvent.from_message(
    message.value,
    json_columns_are_strings=True,
    timestamps_are_epoch_micros=True,
)
```

Building an `ActionEvent` yourself takes keyword arguments only, so two fields of the same type,
such as `model_type` and `event_type`, cannot be swapped by accident.

### Reading Avro and Protobuf

With the binary transforms, a record's value is bytes. Each format needs its extra:

```
uv add "ekbatan[avro]"        # fastavro
uv add "ekbatan[protobuf]"    # protobuf
```

Reading takes two steps in both formats: first the envelope, then the event's own `payload`,
with that event type's schema. The first step needs no schema of yours, so a consumer can route
on `event_type` without a schema for every event the topic carries, and decode only the payloads
it handles.

| Format | The envelope | Then the payload |
|---|---|---|
| Avro | `ActionEvent.from_avro(value)` | `read_avro_payload(event.payload, schema)`, with the event type's `.avsc` |
| Protobuf | `ActionEvent.from_protobuf(value)` | `MyEvent.FromString(event.payload)`, with the event type's message class |

**Avro:**

```python
import json
from pathlib import Path

from ekbatan.streaming import ActionEvent, read_avro_payload

DEPOSITED = json.loads(Path("schemas/WalletMoneyDepositedEvent.avsc").read_text())

for message in consumer:
    event = ActionEvent.from_avro(message.value)
    if event.event_type == "WalletMoneyDepositedEvent":
        body = read_avro_payload(event.payload, DEPOSITED)
        credit(event.model_id, body["amount"])
```

**Protobuf:**

```python
from ekbatan.streaming import ActionEvent
from myapp.events_pb2 import WalletMoneyDepositedEvent  # generated by protoc

for message in consumer:
    event = ActionEvent.from_protobuf(message.value)
    if event.event_type == "WalletMoneyDepositedEvent":
        body = WalletMoneyDepositedEvent.FromString(event.payload)
        credit(event.model_id, body.amount)
```

- **The envelope's schemas ship with the package**: `ActionEvent.avsc`, `ActionEvent.proto` and
  its compiled `ActionEvent.desc`, the same files the transforms are configured with. Nothing is
  generated for the envelope.
- **`payload` stays bytes until you read it.** It is the event's own Avro or Protobuf message.
  Read it with the same `.avsc` or message type that the transform's mapping names for that event
  type.
- **A message written with another schema is refused, not misread.** Each Avro message carries
  its writer's schema fingerprint. `from_avro` and `read_avro_payload` check it before reading a
  byte, and raise `ValueError` when it is not the schema in hand: another event type's, or
  another version of this one.
- **Other versions of a schema are read once you pass them.** Give the versions payloads may
  still be written with, older ones or a newer one deployed before your consumer, as
  `writer_schemas`: `read_avro_payload(event.payload, schema, writer_schemas=[older, newer])`.
  Avro's schema resolution converts each to your schema: a field yours lacks is dropped, and one
  the writer lacks takes your default. `ActionEvent.from_avro(value, writer_schemas=[...])` does
  the same for an envelope written by a newer ekbatan. Protobuf needs nothing like this: a field
  the reader does not know is skipped, and one the writer did not set reads as its zero value.
- **Anything that cannot be read raises `ValueError`**: bytes in another format, a message cut
  short or corrupt, an unknown schema, and a value the dataclass cannot hold - an id that is not
  a UUID, `action_params` that is not a JSON object, a time outside the years 1 to 9999 or finer
  than a microsecond. The error names the field, and never repeats what `action_params` holds.
- **The payload keeps its format's own types.** Avro gives a dict, as fastavro reads it: a
  `decimal` is a `Decimal`, a `timestamp-millis` a `datetime` in UTC. Protobuf gives your
  message, where an `int64` is an `int` and a `Timestamp` a `Timestamp`.
- **The envelope's times have no time zone attached** and hold UTC, as `from_message` gives
  them.

**A consumer in another language reads the same bytes**, because the wire format is the
contract. For Avro the transform writes *single-object encoding*: a two-byte marker, the
schema's 8-byte fingerprint, then the record. A reader that expects bare Avro binary fails on
the header rather than misreading the data, which is the failure you want.

## SMTs (Variant B only)

This part is for whoever runs Kafka Connect; a consumer needs none of it.

The two transforms are **Kafka Connect plugins, not Python packages**: JARs that run inside the
Connect worker. Your service only writes the rows they read.

- **`OutboxToAvroTransform`** encodes the outbox row into an Avro `ActionEvent` envelope, with
  the `payload` field encoded against an **Avro schema per `event_type`** that you supply.
- **`OutboxToProtobufTransform`** does the same with Protobuf descriptor sets instead of Avro
  schemas.

**Installing them.** The JARs are **not published to a package index**: Connect loads plugins
from the filesystem. Download them from the Java project's
[releases page](https://github.com/ekbatan-io/ekbatan/releases) -
[Running beside a Java service](../beside-java.md#streaming-the-events) names the two - and
put each in **its own directory** under the worker's `plugin.path`. The separate directory
matters: Connect gives each plugin directory its own class loader, which stops one plugin's
bundled libraries clashing with another's.

**What happens to each record.** Every record gets exactly one of these outcomes:

| The record | Outcome |
| --- | --- |
| an event row inserted (`c`), or read in a snapshot (`r`) | **encoded** and published |
| a sentinel row (`event_type` null) | **skipped** |
| any other change to the outbox: fan-out's `delivered` flip (an update), a delete, its tombstone | **skipped** |
| Debezium's own housekeeping: heartbeats, schema-change notices, transaction metadata | **dropped**, with one WARN |
| a corrupt event row: `event_type` set but `payload` null, no schema or descriptor mapped for it, a payload that will not encode | **raises** |
| a data row from a table that is not the outbox | **raises** |

Why:

- **Only inserts and snapshot reads are published.** Fan-out's `UPDATE delivered = TRUE` is
  filtered out, so the in-process and Kafka paths can share one `events` table without
  publishing twice.
- **A malformed real event raises**, so that an operator sees it. Sentinel rows are expected,
  and skipped.
- **A row from another table raises**, because silently throwing away someone else's data is
  worse than stopping.
- **Tombstones are not republished.** A delete produces a change event and a tombstone under the
  same key. The outbox key is the row id the `ActionEvent` was published under, so forwarding the
  tombstone would let a compacted topic erase an event that had already been delivered. Pruning
  old `events` rows can never unpublish the facts they recorded.
- **No record is passed through untouched.** The transform emits `bytes`, so its connector runs
  `ByteArrayConverter`, which accepts only bytes or null. Handing back an unrecognised record
  would guarantee the converter throws.

Both transforms also:

- **Convert each column through its Connect schema**, rather than copying the raw value. The
  same column arrives as a different type depending on the database and the connector's
  `time.precision.mode`: a boolean is a real boolean on PostgreSQL and a `TINYINT(1)` on MySQL
  and MariaDB, and a timestamp arrives as epoch millis, micros, nanos or an ISO-8601 string. All
  timestamps are normalised to **epoch microseconds**, which loses nothing for every form
  Debezium produces here. There is no dialect setting: the transform goes by what the record says
  its columns are, so one row gives identical bytes on all three databases.
- **Check the `ActionEvent` schema at startup.** Every field of the configured schema must be
  bound to a column. One that is not fails the connector at once, rather than shipping that field
  unset on every message. A schema with only some of the fields is accepted.

### SMT configuration

Each transform takes five properties. The two schema properties are mandatory; the connector will
not start without them.

| Property | Avro | Protobuf | Default | Meaning |
|---|---|---|---|---|
| payload schemas | `payload.schemas` | `payload.descriptors` | *(required)* | Comma-separated `<qualified-name>:<path>` pairs — one per event type |
| envelope schema | `action.event.schema` | `action.event.descriptor` | *(required)* | Path to `ActionEvent.avsc`, or to the protobuf `.desc` descriptor set |
| `payload.field` | ✔ | ✔ | `payload` | Outbox **column** holding the JSON payload |
| `event.type.field` | ✔ | ✔ | `event_type` | Outbox **column** holding the event-type discriminator |
| `namespace.field` | ✔ | ✔ | `namespace` | Outbox **column** holding the namespace; with the event type it selects the schema |

`payload.field` and `event.type.field` name the source **column on the outbox row**, never the
target field in the `ActionEvent` schema. The target names are fixed by the schema.

The transform class names are
`io.ekbatan.events.streaming.debeziumsmt.avro.OutboxToAvroTransform` and
`io.ekbatan.events.streaming.debeziumsmt.protobuf.OutboxToProtobufTransform`.

#### Naming a payload schema

The mapping key is the schema's **own fully-qualified name**, built from three things:

```
com.example.finance . avro . WalletMoneyDepositedEvent
└──── namespace ───┘  └fmt┘  └─── event_type ───┘
```

- **namespace**: the row's `namespace` column, which is what you passed to
  `ActionExecutor(namespace=...)`
- **format**: literally `avro` or `proto`, depending on which transform you are configuring
- **event type**: the event class's name, which for a Python producer is the dataclass's
  `__name__` unless [`ModelEvent`](../concepts/models-and-entities.md) resolution says otherwise

Your schema must *declare* that name: for Avro, the `.avsc`'s `namespace` plus `name`; for
Protobuf, the `.proto`'s `package` plus message name. Both transforms check this at startup and
refuse to start on a mismatch, quoting both the configured and the declared name.

```properties
transforms=outbox
transforms.outbox.type=io.ekbatan.events.streaming.debeziumsmt.avro.OutboxToAvroTransform
transforms.outbox.action.event.schema=/kafka/schemas/ActionEvent.avsc
transforms.outbox.payload.schemas=\
  com.example.finance.avro.WalletCreatedEvent:/kafka/schemas/WalletCreatedEvent.avsc,\
  com.example.finance.avro.WalletMoneyDepositedEvent:/kafka/schemas/WalletMoneyDepositedEvent.avsc

# Mandatory. The SMT emits bytes; every other converter rejects them.
value.converter=org.apache.kafka.connect.converters.ByteArrayConverter
key.converter=org.apache.kafka.connect.storage.StringConverter
```

Spelling the name out in full has three benefits.

**The namespace is part of the key**, so two services sharing one `events` table (the reason the
`namespace` column exists) can each define `OrderCreated` with different shapes. Keyed on the
event type alone, one would be encoded with the other's schema, and it would silently succeed
whenever the two happened to have compatible fields. There is no bare wildcard either: it would
quietly match namespaces it was never meant for.

**Protobuf messages are found by full name.** A `.desc` built with `--include_imports` contains
every imported file too, so a library message with the same short name could be picked by
mistake. A full name matches at most one message, so that cannot happen.

**The key, the name looked up, and the name in any error message are the same string**, so you
can compare an error with your config by eye.

This is also why the namespace must be
[dot-separated identifiers, with no hyphens](#hyphens-in-the-namespace-are-refused-and-here-is-why).

### Payload encoding

The `payload` column holds JSON, and the transform re-encodes it against the schema you
configured for that `event_type`. This framework writes the JSON with field names in camelCase,
in declaration order, decimals as JSON numbers and times as ISO-8601 text. A Java service writes
the same text, byte for byte, so a schema written for one producer encodes the other's rows
unchanged - see [event payloads](../beside-java.md#event-payloads).

The rule: **the schema decides the type**, and a value that does not fit **fails, naming the
field path**. It is never forced into a default:

| Situation | Behaviour |
|---|---|
| The value's type disagrees with the schema | Fails, naming the field — e.g. *Field `WalletDeposit.count` is declared LONG so it needs a number, but the payload has a STRING* |
| `["null", T]` — Avro's spelling of optional | Decided by the schema alone: `null` to the null branch, otherwise `T` |
| A union with several non-null branches | The branch that converts *and* accounts for most of the JSON's keys; ties break toward the tighter schema, then declaration order with a WARN |
| `decimal`, `timestamp-millis`/`-micros`/`-nanos`, `date`, `uuid` | Handled as those types. A decimal too precise for the schema's scale fails rather than rounding silently |
| A timestamp given as a **number** | Taken to be already in the unit the schema declares — no scaling. `1700000000000` in a `timestamp-micros` field reads as 1.7 million microseconds: January 1970, not November 2023. A number carries no unit, so nothing can detect the mistake |
| A field absent from the JSON | Its schema default, or a failure if it has none |
| A field present in the JSON but not in the schema | Dropped, with one WARN — so an additive change to an event class cannot take the pipeline down, but the drift is visible |

As a Python producer, read the timestamp row twice. This library writes a `datetime` in a payload
as an **ISO-8601 string**, which converts to whatever unit the field declares. Put a bare epoch
number in a payload yourself, and the unit is a guess.

### Envelope timestamps

`started_date`, `completion_date` and `event_date` are declared as **date types, not numbers**, in
both binary formats:

| Format | Declaration | What a consumer's generated code hands them |
|---|---|---|
| Avro | `{"type": "long", "logicalType": "timestamp-micros"}` | an instant |
| Protobuf | `google.protobuf.Timestamp` | seconds + nanos |
| JSON | ISO-8601 string | a string you parse |

This matters more than it looks. A bare number cannot say what it counts. Read microseconds as
milliseconds and you get a date in the year 58535, with no error anywhere; the reverse mistake
lands three weeks after 1970. Making the unit part of the type rules that mistake out.

Both binary formats are precise to the microsecond, because that is what the database holds:
`TIMESTAMP` on PostgreSQL and `DATETIME(6)` on MySQL and MariaDB all keep six fractional digits,
and [`timestamp_column()`](../database/multi-database.md) makes sure of it. Protobuf's `Timestamp`
has a nanosecond field, so its last three digits are always zero. Avro uses `timestamp-micros`
and **not** `timestamp-nanos`, on purpose: `timestamp-nanos` arrived only in Avro 1.12, so a
consumer on 1.11 would silently read a bare number, the very ambiguity above. It would also
promise precision no supported database can store.

Timestamps *inside* `payload` are a separate matter; see [Payload encoding](#payload-encoding).

### Unwrapped records and `ExtractNewRecordState`

Debezium normally delivers each change wrapped in its own *change envelope*,
`{ before, after, source, op, ts_ms }`: the row sits in `after`, and `op` says what happened.
Both transforms read that shape and publish only `c` and `r`. That filter lists what to publish,
not what to drop, so any operation a future Debezium release adds is held back rather than
published unexamined.

Some pipelines put Debezium's `ExtractNewRecordState` first, which **unwraps** the change
envelope: the record becomes the row's columns directly. The transforms handle that shape too: a
record that carries the configured payload and event-type columns is treated as the row.

**The catch: unwrapping throws away `op`**, which lived on the change envelope. The transform
then cannot tell an `INSERT` from an `UPDATE`.

**When that matters.** Only if something updates `events`, and exactly one thing in this
framework does: the **fan-out job** sets `delivered = TRUE` on every row it fans out.

| Deployment | Effect of unwrapping without `op` |
|---|---|
| Kafka only, no in-process handlers | Harmless — nothing ever updates the outbox |
| Fan-out **and** Debezium on the same outbox | The flip would be republished as a duplicate, so the SMT filters on `delivered` instead |

Such a duplicate has the same `id` and the same payload; only `delivered` differs. A consumer
that drops duplicates by `id` would not notice; one that treats each message as a new fact would
process it twice.

**What the transform does about it.** It falls back to the row's own `delivered` column, which
tells it the same thing for the only `UPDATE` this table ever sees. The framework keeps the rule
this depends on: every row is inserted with `delivered = False`, and only the fan-out job sets it
to true. So `delivered = TRUE` *is* the flip, and `delivered = FALSE` *is* the insert. Duplicates
are filtered with no configuration on your part.

**One consequence:** during an initial snapshot, rows already marked delivered are *not*
published, because without `op` a snapshot read looks just like the flip. The transform logs one
WARN when it falls back like this, naming the limitation.

**Better configurations**, which avoid the question entirely:

1. **Run the SMT before the unwrap**: `transforms=encodeAvro,unwrap` rather than
   `transforms=unwrap,encodeAvro`. The transform sees the change envelope and filters on `op`.
2. **Carry `op` through the unwrap.** Configure `ExtractNewRecordState` with `add.fields=op`,
   which adds `__op` to the row. The transforms honour it, and an explicit `op` always wins over
   the `delivered` fallback, so the snapshot limitation above goes away.
3. **Do not run both delivery paths against one outbox.** If Kafka is the only consumer, do not
   register the fan-out job; nothing then updates the table.

### SMT error handling

In CI and staging, failing fast is useful: a missing schema mapping should break the connector, so
you notice the pipeline is misconfigured. In production, one corrupt outbox row should not stop
the connector for good. Add Kafka Connect's error tolerance to the Debezium connector config:

```properties
# Retry transient transform/converter failures for up to 10 minutes.
errors.retry.timeout=600000
errors.retry.delay.max.ms=30000

# Skip records that still fail after retries instead of killing the connector task.
errors.tolerance=all

# Log the failed record context. Keep messages disabled unless your logs are allowed
# to contain event payloads or action params.
errors.log.enable=true
errors.log.include.messages=false
```

`errors.log.include.messages=false` is there for a reason: `action_params` holds the action's
full input, which makes it the column most likely to hold something your logs must not contain.

## What the framework does *not* publish

- **Kafka client wiring**: consumer and producer configuration, retries, dead-letter queues. It
  depends too much on your infrastructure for one setup to be right for everyone.
- **Debezium connector configuration**: the properties differ across PostgreSQL, MySQL and
  MariaDB, and across deployments.
- **A Schema Registry binding.** The transforms need none, on purpose: they read schema files
  and write Avro's single-object encoding (`0xC3 0x01` plus an 8-byte schema fingerprint) or
  plain Protobuf.

That last one hides a trap: **you cannot simply swap in a converter backed by a Schema
Registry.** The transform emits `bytes` with a `BYTES` Connect schema, so an `AvroConverter`
would register a useless `"bytes"` schema and wrap the already-encoded message inside it. And
Confluent's wire format (`0x00` plus a 4-byte schema id) is not the one written here anyway.

With a Schema Registry you have two options:

1. **Keep the transform** and treat the schema files as the contract. This is the supported
   setup, and the fingerprint in each message identifies the schema.
2. **Drop the transform** and use Debezium with a registry-backed converter directly. Then
   `payload` stays a JSON string rather than nested binary, and your consumer parses it.

## Running both paths at once

The in-process handlers and this path can share one outbox. Fan-out flips `delivered` from false
to true, which is an `UPDATE`. The Variant B transforms drop every change that is not an insert,
so those flips never become Kafka messages.
[Unwrapped records](#unwrapped-records-and-extractnewrecordstate) covers the one setup where that
needs care.

With Variant A - JSON, no transform - nothing drops them: Debezium publishes each flip as an
update, and a consumer sees the event's row a second time. Set the connector's
`skipped.operations` to `u,d,t`, so that it publishes inserts only.

## See also

- [`events`](../database/tables/events.md) - the contract this path depends on
- [The outbox](../concepts/outbox.md) - why the rows are there
- [In-process handlers](local-event-handler.md) - the other path
