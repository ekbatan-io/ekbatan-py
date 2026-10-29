# Streaming with Debezium

The other consumer path: point a CDC connector at the outbox and ship committed rows to a broker.

**Nothing in this path is Python code.** The connector and the transforms run inside Kafka
Connect, whichever language wrote the row. What your service owes the pipeline is the **row
format** - the [`events`](../database/tables/events.md) table, written in the same transaction as your domain
change.

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

## Variant A — JSON on Kafka

The default, and the one to start with. Point Debezium at the outbox, let its JSON converter
emit the row as-is, and consumers read fields straight off the wire. No schemas to mount, no JAR
to deploy into Kafka Connect, and `kafka-console-consumer` shows you a readable message.

```
Your application
   │  one transaction per action
   ▼
┌────────────────────────────────────────────────────┐
│  domain rows  +  events            (jsonb)         │  ← committed atomically
└────────────────────────────────────────────────────┘
   │  outbox rows visible to CDC after commit
   ▼
┌────────────────────────────────────────────────────┐
│  Debezium (CDC connector)                          │  ← passthrough,
└────────────────────────────────────────────────────┘     no transform
   │  JSON message
   ▼
┌────────────────────────────────────────────────────┐
│  Kafka — raw topic                 (JSON)          │
└────────────────────────────────────────────────────┘
   │  router fans out by model_type / event_type
   ▼
┌────────────────────────────────────────────────────┐
│  per-model / per-event topics      (JSON)          │
└────────────────────────────────────────────────────┘
   │
   ▼
Your consumers — `json.loads`, and nothing else
```

Nothing in that diagram depends on the language your service is written in: the only thing the
producer has to do is write a row in `events`.

## Variant B — Binary on Kafka (Avro / Protobuf)

Two Kafka Connect transforms — `OutboxToAvroTransform` and `OutboxToProtobufTransform` —
encode the row before it reaches the broker.

They run inside Kafka Connect, not in your service: your service only writes the rows. Kafka
Connect plugins are JARs, so these two come from Ekbatan's Java project, as the
`ekbatan-debezium-smt` artifact - see
[Running beside a Java service](../beside-java.md#streaming-the-events) for where to get them.

Their configuration, payload encoding, envelope timestamps, the `ExtractNewRecordState`
interaction and the error-handling options are under [SMTs](#smts-variant-b-only) below.

## Picking a variant

| Variant | Pick when… |
| --- | --- |
| **JSON** | Internal services; low-to-medium traffic; you want to read messages straight off the wire; no organisation-wide schema governance yet. |
| **Avro** | You already have (or want) a Schema Registry; you want schemas with compatibility tooling; payload size matters at volume. |
| **Protobuf** | Polyglot consumers; you already maintain `.proto` files; you prefer Google's stack over Confluent's. |

Both binary variants compress better than JSON and impose stronger schema discipline, at the
cost of managing per-event-type schemas, deploying the transform into Kafka Connect, and
consumers needing the matching SDK.

They are not mutually exclusive: run JSON on a side topic for operations while Avro feeds
production consumers. Same outbox, two connectors, different transform configuration.

## Why the database always stores JSON, regardless of variant

The encoding is a *wire* concern, not a storage one. The outbox row holds JSON whichever variant
you run, because the row has readers that are not the broker — the in-process handler, a
backfill, an operator with `psql` — and because every service writing to one outbox has to agree
on one stored representation.

The transform encodes on the way out. Change variant and nothing about the table changes.

Four things follow from that, and each one is a failure you do not get:

- **The write path stays small.** Serialising against a schema costs time and can fail; keeping
  it out of the transaction means a stale schema cannot make `executor.execute(...)` fail at
  commit, with the domain change rolled back for a reason that has nothing to do with the domain.
- **A schema mismatch surfaces in Connect**, where it can be retried, logged, alerted on and
  routed to a dead-letter topic - not in your application's hot path, where it is a 500.
- **Schema evolution is centralised** in the transform's configuration, rather than smeared
  across every service that produces or consumes the event.
- **An operator can read the outbox with plain SQL.** `jsonb` is greppable; Avro bytes are not,
  and the row you most want to read is always the one from the incident.

## Consumer-side envelope contracts

Every variant carries the same 13-field shape: `id`, `namespace`, `action_id`, `action_name`,
`action_params`, `started_date`, `completion_date`, `model_id`, `model_type`, `event_type`,
`payload`, `event_date`, `delivered`. Only the encoding differs.

For a Python consumer reading raw Debezium JSON, `ActionEvent.from_row(...)` in
`ekbatan.streaming` is that shape as a frozen dataclass — the consumer-side view of an outbox
row, with the decimal and timestamp conventions already applied. For the binary formats, the
shape is the transforms' own schemas: `ActionEvent.avsc`, or the protobuf descriptor. Both ship
inside the package, and `ActionEvent.from_avro(...)` and `ActionEvent.from_protobuf(...)` read
their messages into the same dataclass - see [Reading Avro and Protobuf](#reading-avro-and-protobuf).

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

## Output topics partitioned by `model_id`

For the per-model and per-event topics, use `model_id` as the Kafka partition key. That gives
**per-entity ordering** - every event for one wallet lands in one partition and is consumed in
order - while different entities parallelise across partitions.

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
rest from it. Derived topics can carry their own retention, schema, partition count and key
without anything changing in how actions write to the database — which is the property worth
protecting: the broker topology never reaches back into the write path.

A route is a rule, not a pipeline stage: one event can match several and go to all of them.
Whatever runs the routing is **stateless and horizontally scalable**, and it is crash-safe for a
reason worth naming - the raw topic buffers while it is down, so a router outage is a delay
rather than a loss.

## SMTs (Variant B only)

The two transforms are **Kafka Connect plugins, not Python packages** — JARs that run inside the
Connect worker. Nothing below runs in *your* service; it only writes the rows the transforms
read.

- **`OutboxToAvroTransform`** — encodes the Debezium outbox row into an Avro `ActionEvent`
  envelope, with the `payload` field encoded against a **per-`event_type` Avro schema** you
  supply.
- **`OutboxToProtobufTransform`** — the same shape, protobuf descriptor sets instead of Avro
  schemas.

Both:

- **Drop operations other than `c` (create) and `r` (read/snapshot).** The `UPDATE delivered =
  TRUE` writes from the fan-out job are filtered out, so the in-process and Kafka paths can
  coexist on one `events` table without double-publishing.
- **Skip sentinel rows** where `event_type` is null.
- **Raise on a corrupt event row** — `event_type` not null with `payload` null, a missing schema
  or descriptor mapping, a payload that will not encode. Sentinel rows are skipped; a malformed
  real event should be visible to an operator.
- **Convert each column through its Connect schema** rather than copying the raw value. The same
  logical column arrives as a different type depending on the database and the connector's
  `time.precision.mode`: a boolean is a real boolean on PostgreSQL and a `TINYINT(1)` on MySQL
  and MariaDB, and a timestamp arrives as epoch millis, micros, nanos or an ISO-8601 string. All
  timestamps are normalised to **epoch microseconds**, which is lossless for every form Debezium
  produces here. There is no dialect setting — the transform branches on what the record says its
  columns are, so one row yields identical bytes on all three databases.
- **Never pass a record through untouched.** The transform emits `bytes`, so its connector runs
  `ByteArrayConverter`, which serialises only bytes or null; handing back an unrecognised record
  would guarantee the converter throws. Every record gets one of four outcomes: an outbox row is
  **encoded**; something recognised but deliberately unpublished is **skipped** (the `delivered`
  flip, a delete, its tombstone); Debezium's own housekeeping — heartbeats, schema-change notices,
  transaction metadata — is **dropped** with one WARN; and a data row from a table that is not
  the outbox **raises**, because silently discarding someone else's data is worse than stopping.
- **Do not republish tombstones.** A delete produces a change event and a tombstone under the same
  key. The outbox key is the row id the `ActionEvent` was published under, so forwarding the
  tombstone would let a compacted topic erase an event that had already been delivered — pruning
  old `events` rows can never unpublish the facts they recorded.
- **Validate the `ActionEvent` schema at startup.** Every field of the configured schema must have
  a column binding; one that does not fails the connector immediately rather than shipping that
  field unset on every message. A schema carrying a subset of the fields is accepted.

> `payload.field` and `event.type.field` name the source **column on the outbox row**, never the
> target field in the `ActionEvent` schema. The target names are fixed by the schema.

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

The transform class names are
`io.ekbatan.events.streaming.debeziumsmt.avro.OutboxToAvroTransform` and
`io.ekbatan.events.streaming.debeziumsmt.protobuf.OutboxToProtobufTransform`.

#### Naming a payload schema

The mapping key is the schema's **own fully-qualified name**, built from three things:

```
com.example.finance . avro . WalletMoneyDepositedEvent
└──── namespace ───┘  └fmt┘  └─── event_type ───┘
```

- **namespace** — the row's `namespace` column, which is what you passed to
  `ActionExecutor(namespace=...)`
- **format** — literally `avro` or `proto`, according to which transform you are configuring
- **event type** — the event class's name, which for a Python producer is the dataclass's
  `__name__` unless [`ModelEvent`](../concepts/models-and-entities.md) resolution says otherwise

Your schema must *declare* that name — for Avro the `.avsc`'s `namespace` + `name`, for protobuf
the `.proto`'s `package` + message name. Both transforms check at startup and refuse to start on
a mismatch, quoting both the configured and the declared name.

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

Three things follow from spelling the name out in full.

**The namespace is part of the key**, so two services sharing one `events` table — the reason the
`namespace` column exists — can each define `OrderCreated` with different shapes. Keyed on the
event type alone, one of them is encoded with the other's schema, and silently succeeds whenever
the two happen to be field-compatible. There is no bare wildcard: it would quietly match
namespaces it was never meant to.

**Protobuf messages resolve by fully-qualified name.** A `.desc` built with `--include_imports`
contains every imported file too, so a library message sharing a short name could be picked
instead. A qualified name matches at most one message, so the ambiguity cannot arise rather than
merely being detected.

**The key, the name searched for, and the name in any error message are the same string**, so a
failure can be diffed against your config by eye.

The namespace must be [dot-separated identifiers](#topic-naming) — no hyphens, which this
framework refuses at construction for exactly this reason.

The SMT JARs are **not published to a package index** — Connect loads plugins from the
filesystem. Take them from the Java project's build - see
[Running beside a Java service](../beside-java.md#streaming-the-events) - and install each in **its own
directory** under the worker's `plugin.path`. The separate directory matters: Connect gives each plugin directory its
own classloader, which is what stops a plugin's bundled dependencies colliding with anything
else's.

### Payload encoding

The `payload` column holds JSON, which the SMT re-encodes against the per-`event_type` schema you
configure. This framework writes field names in camelCase, in declaration order, decimals as JSON
numbers and times as ISO-8601 text. A Java service writes the same text, byte for byte, so a
schema written for one producer encodes the other's rows unchanged - see
[event payloads](../beside-java.md#event-payloads).

The rule is that **the schema decides the type**, and a value that does not fit **fails, naming
the field path**. It is never coerced to a default:

| Situation | Behaviour |
|---|---|
| The value's type disagrees with the schema | Fails, naming the field — e.g. *Field `WalletDeposit.count` is declared LONG so it needs a number, but the payload has a STRING* |
| `["null", T]` — Avro's spelling of optional | Decided by the schema alone: `null` to the null branch, otherwise `T` |
| A union with several non-null branches | The branch that converts *and* accounts for most of the JSON's keys; ties break toward the tighter schema, then declaration order with a WARN |
| `decimal`, `timestamp-millis`/`-micros`/`-nanos`, `date`, `uuid` | Handled as those types. A decimal too precise for the schema's scale fails rather than rounding silently |
| A timestamp given as a **number** | Taken to be already in the unit the schema declares — no scaling. `1700000000000` in a `timestamp-micros` field reads as 1.7 million microseconds: January 1970, not November 2023. A number carries no unit, so nothing can detect the mistake |
| A field absent from the JSON | Its schema default, or a failure if it has none |
| A field present in the JSON but not in the schema | Dropped, with one WARN — so an additive change to an event class cannot take the pipeline down, but the drift is visible |

The timestamp row is the one to read twice as a Python producer. A `datetime` in a payload is
serialised by this library as an **ISO-8601 string**, which converts to whichever unit the field
declares. Put a bare epoch number in a payload yourself and you have opted into the ambiguity.

### How to read the bytes

Two levels in both formats: decode the envelope, then decode its `payload` field with the event's
own schema.

| Format | The envelope | Then the payload |
|---|---|---|
| Avro | `ActionEvent.from_avro(value)` | `read_avro_payload(event.payload, schema)`, with the event type's `.avsc` |
| Protobuf | `ActionEvent.from_protobuf(value)` | `MyEvent.FromString(event.payload)`, with the event type's message class |

How each is used is under [Reading Avro and Protobuf](#reading-avro-and-protobuf).

The wire format is the contract, so a consumer in any language reads the same bytes.

For Avro the SMT emits **single-object encoding**: a two-byte marker, the schema's 8-byte
fingerprint, then the record. A reader that expects bare Avro binary will fail on the header
rather than mis-decode, which is the failure you want.

### Envelope timestamps

`started_date`, `completion_date` and `event_date` are declared as **date types, not numbers**, in
both binary formats:

| Format | Declaration | What a consumer's generated code hands them |
|---|---|---|
| Avro | `{"type": "long", "logicalType": "timestamp-micros"}` | an instant |
| Protobuf | `google.protobuf.Timestamp` | seconds + nanos |
| JSON | ISO-8601 string | a string you parse |

This matters more than it looks. A bare integer cannot say what it counts: a consumer reaching
for the usual reflex and reading microseconds as millis gets a timestamp in the year 58535, with
nothing anywhere raising an error; the reverse mistake lands three weeks after the epoch. Making
the unit part of the type is what stops the mistake being expressible.

Both are microsecond resolution, because that is what the source holds — `TIMESTAMP` on
PostgreSQL and `DATETIME(6)` on MySQL and MariaDB are all six fractional digits, and
[`timestamp_column()`](../database/multi-database.md) is what guarantees it. Protobuf's `Timestamp`
has a nanosecond field, so its last three digits are always zero. Avro is deliberately **not**
`timestamp-nanos`: that logical type arrived only in Avro 1.12, so a consumer on 1.11 would
silently fall back to a bare long, reintroducing the exact ambiguity — and it would advertise
precision no supported database can store.

Timestamps *inside* `payload` are a separate matter, and are not ambiguous here: this library
serialises `datetime` as ISO-8601. Hand it a pre-formatted epoch number and you are back to a
number with no unit.

### Unwrapped records and `ExtractNewRecordState`

Debezium normally delivers each change inside an **envelope** — `{ before, after, source, op,
ts_ms }` — where the row sits in `after` and `op` says what happened. Both SMTs read that shape
and publish only `c` and `r`. The filter is an allow-list, so any operation a future Debezium
release adds is withheld rather than published unexamined.

Some pipelines put Debezium's `ExtractNewRecordState` in front, which **unwraps** the envelope:
the record value becomes the row's columns directly. The SMTs support that shape too — if the
value carries the configured payload and event-type columns, it is treated as the row.

**The catch: unwrapping discards `op`.** It lives on the envelope, and the envelope is gone. The
SMT then cannot tell an `INSERT` from an `UPDATE`.

**When that matters.** Only if something updates `events`, and exactly one thing in this
framework does: the **fan-out job** sets `delivered = TRUE` on every row it fans out.

| Deployment | Effect of unwrapping without `op` |
|---|---|
| Kafka only, no in-process handlers | Harmless — nothing ever updates the outbox |
| Fan-out **and** Debezium on the same outbox | The flip would be republished as a duplicate, so the SMT filters on `delivered` instead |

The duplicate carries the same `id` and the same payload; only `delivered` differs. A consumer
that deduplicates on `id` will not notice; anything treating each message as a distinct fact will
double-process.

**What the SMT does about it.** It falls back to the row's own `delivered` column, which carries
the same information for the only `UPDATE` this table ever sees. This framework upholds the
invariant the transform depends on: the outbox writer inserts every row with `delivered = False`,
and the sole writer that sets it true is the fan-out job — so `delivered = TRUE` *is* the flip and
`delivered = FALSE` *is* the insert. Duplicates are filtered with no configuration on your part.

**One consequence worth knowing:** during an initial snapshot, rows already marked delivered are
*not* replayed, because a snapshot read is indistinguishable from the flip without `op`. The SMT
logs one WARN when it takes this fallback, naming the limitation.

**Preferred configurations**, which sidestep the question entirely:

1. **Run the SMT before the unwrap** — `transforms=encodeAvro,unwrap` rather than
   `transforms=unwrap,encodeAvro`. The SMT sees the envelope and filters on `op`.
2. **Carry `op` through the unwrap.** Configure `ExtractNewRecordState` with `add.fields=op`,
   which adds `__op` to the row. The SMTs honour it, and an explicit `op` always beats the
   inferred `delivered` signal, so snapshot replay works again.
3. **Do not run both delivery paths against one outbox.** If Kafka is the only consumer, do not
   register the fan-out job; nothing then updates the table.

### SMT error handling

In CI and staging, failing fast is useful: a missing schema mapping should break the connector so
you notice the pipeline is misconfigured. In production, one corrupt outbox row should not wedge
the connector forever. Add Connect's error tolerance to the Debezium connector config:

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

`errors.log.include.messages=false` is the default here for a reason: `action_params` carries the
full input of the unit of work, which is exactly the column most likely to hold something your
log is not cleared for.

## Sentinel rows

An action that emitted no events still writes a row, with `event_type IS NULL`. A consumer skips
those; the transforms already do.

## Reading the rows

On the consumer side, `ekbatan.streaming.ActionEvent` is the shape - the envelope's thirteen
fields, as a frozen dataclass:

```python
from ekbatan.streaming import ActionEvent

for message in consumer:
    event = ActionEvent.from_row(message.value)
    if event.event_type == "WalletMoneyDepositedEvent":
        credit(event.model_id, event.payload["amount"])
```

Nothing is validated, deliberately: four fields are legitimately absent on a sentinel row, so a
type that rejected them would reject valid traffic.

**Mapping a raw CDC message is still the consumer's job.** A Debezium topic carries
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

`ActionEvent` itself takes keyword arguments only. `model_type` sits next to `event_type`, and
`started_date` next to `completion_date` - same-typed pairs that, passed by position, would swap
silently and produce an event routed to the wrong topic or carrying the wrong body.

### Reading Avro and Protobuf

With the binary transforms, a record's value is bytes. Each format needs its extra:

```
uv add "ekbatan[avro]"        # fastavro
uv add "ekbatan[protobuf]"    # protobuf
```

Reading takes two steps in both, and the first needs no schema of yours: the envelope, then -
only for the events you handle - the payload, with that event type's own schema. A consumer
routes on `event_type` without a schema for every event the topic carries.

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

```python
from ekbatan.streaming import ActionEvent
from myapp.events_pb2 import WalletMoneyDepositedEvent  # generated by protoc

for message in consumer:
    event = ActionEvent.from_protobuf(message.value)
    if event.event_type == "WalletMoneyDepositedEvent":
        body = WalletMoneyDepositedEvent.FromString(event.payload)
        credit(event.model_id, body.amount)
```

- **The envelope's schemas ship with the package** - `ActionEvent.avsc`, `ActionEvent.proto`
  and its compiled `ActionEvent.desc`, the files the transforms are configured with. Nothing is
  generated for the envelope.
- **`payload` stays bytes until you read it** - the event's own Avro or protobuf message. Read it
  with the same `.avsc` or message type the transform's mapping names for that event type.
- **A message written with another schema is refused, not misread.** Each Avro message carries
  its writer's schema fingerprint; `from_avro` and `read_avro_payload` check it before reading a
  byte, and raise `ValueError` when it is not the schema in hand - another event type's, or
  another version of this one.
- **Other versions of a schema are read once you give them.** Pass the versions payloads may
  still be written with - older ones, or a newer one deployed before your consumer - as
  `writer_schemas`: `read_avro_payload(event.payload, schema, writer_schemas=[older, newer])`.
  Avro's resolution takes each to your schema: a field yours lacks is dropped, and one the writer
  lacks takes your default. `ActionEvent.from_avro(value, writer_schemas=[...])` does the same
  for an envelope written by a newer ekbatan. Protobuf needs nothing of the kind: a field the
  reader does not know is skipped, and one the writer did not set reads as its zero value.
- **Anything that cannot be read is a `ValueError`**: bytes in another format, a message cut
  short or corrupt, an unknown schema, and a value the dataclass cannot hold - an id that is not
  a UUID, `action_params` that is not a JSON object, a time outside years 1 to 9999 or finer than
  a microsecond. The error names the field, and never repeats what `action_params` holds.
- **The payload keeps its format's own types.** Avro gives a dict, as fastavro reads it: a
  `decimal` is a `Decimal`, a `timestamp-millis` a `datetime` in UTC. Protobuf gives your
  message, where an `int64` is an `int` and a `Timestamp` a `Timestamp`.
- **The envelope's times have no zone attached** and hold UTC, as `from_message` gives them.

## Running both paths at once

The in-process pipeline and this one coexist on one outbox. Fan-out flips `delivered` from false
to true, producing `UPDATE` rows; the transforms drop non-insert operations, so those flips never
become Kafka messages.

## What the framework does *not* publish

- **Kafka client wiring** - consumer and producer configuration, retry, dead-letter queues. Too
  infrastructure-specific to be right for anyone.
- **Debezium connector configuration** - the properties differ across PostgreSQL, MySQL and
  MariaDB and across deployment topologies.
- **A Schema Registry binding.** The transforms deliberately need none — they read schema files
  and emit Avro's single-object encoding (`0xC3 0x01` plus an 8-byte schema fingerprint) or plain
  protobuf.

That last one has a trap worth spelling out. **You cannot simply swap in a Schema-Registry-backed
converter.** The SMT emits `bytes` with a `BYTES` Connect schema, so an `AvroConverter` would
register a useless `"bytes"` schema and wrap the already-encoded payload inside it — and
Confluent's wire format (`0x00` plus a 4-byte schema id) is not the one written here anyway.

Registry users have two honest options:

1. **Keep the SMT** and treat the schema files as the contract. This is the supported shape, and
   the fingerprint in each message is what identifies the schema.
2. **Drop the SMT** and use Debezium plus a registry-backed converter directly — in which case
   `payload` stays a JSON string rather than nested binary, and your consumer parses it.

## See also

- [`events`](../database/tables/events.md) - the contract this path depends on
- [The outbox](../concepts/outbox.md) - why the rows are there
- [In-process handlers](local-event-handler.md) - the other path
