# `events`

The outbox. Domain rows and event rows commit in the same transaction, so this table is the
reliable source that in-process handlers, CDC connectors and broker publishers read *after* the
commit.

**Needed when:** you use the default persister, which is the normal setup.
**Written by:** the executor, inside the action's transaction.
**Built by:** `ekbatan.schema.outbox_table(metadata, name="events", schema=None)`.

It is not a `Model` or an `Entity`. It has no version and no soft-delete state - nothing updates
a committed event row except the `delivered` flag, and that belongs to the consumer.

## DDL

You do not write this by hand; `outbox_table()` describes it and your migration creates it. The
DDL below is the table SQLAlchemy emits for it, with the two indexes the examples' migrations add
(see [Indexes](#indexes) - they are yours to choose).

### PostgreSQL

```sql
CREATE TABLE events (
    id              UUID         NOT NULL,
    namespace       VARCHAR(255) NOT NULL,
    action_id       UUID         NOT NULL,
    action_name     VARCHAR(255) NOT NULL,
    action_params   JSONB        NOT NULL,
    started_date    TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    completion_date TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    model_id        VARCHAR(255),
    model_type      VARCHAR(255),
    event_type      VARCHAR(255),
    payload         JSONB,
    event_date      TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    delivered       BOOLEAN      NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX ix_events_action_id ON events (action_id);
CREATE INDEX ix_events_undelivered ON events (event_type, event_date)
    WHERE delivered = FALSE;
```

### MySQL and MariaDB

```sql
CREATE TABLE events (
    id              CHAR(36)     NOT NULL,
    namespace       VARCHAR(255) NOT NULL,
    action_id       CHAR(36)     NOT NULL,
    action_name     VARCHAR(255) NOT NULL,
    action_params   JSON         NOT NULL,
    started_date    DATETIME(6)  NOT NULL,
    completion_date DATETIME(6)  NOT NULL,
    model_id        VARCHAR(255),
    model_type      VARCHAR(255),
    event_type      VARCHAR(255),
    payload         JSON,
    event_date      DATETIME(6)  NOT NULL,
    delivered       BOOL         NOT NULL,
    PRIMARY KEY (id)
);
```

`DATETIME(6)` rather than bare `DATETIME`: MySQL's default truncates to whole seconds, silently,
and an outbox whose only ordering key is a second-resolution timestamp orders badly.

## Columns

| column | null | meaning |
| --- | :---: | --- |
| `id` | no | this row. For a cross-shard action, every shard receives rows with the same ids, so each holds a self-contained picture |
| `namespace` | no | the producing service. Dot-separated identifiers, validated at startup |
| `action_id` | no | one uuid per execution. Every row from one action shares it |
| `action_name` | no | the action class's name, e.g. `DepositMoneyAction` |
| `action_params` | no | the parameters, as JSON. A consumer can understand the command without joining to your tables - and can read a value the event deliberately does not carry |
| `started_date` | no | when `perform()` began, UTC |
| `completion_date` | no | when the persist phase finished, UTC |
| `model_id` | yes | the affected aggregate. Null only on a sentinel row |
| `model_type` | yes | its class name, e.g. `Wallet`. Null only on a sentinel row |
| `event_type` | yes | the event class's name. Null on a sentinel row. Two event classes may not share one |
| `payload` | yes | the event as JSON. Null on a sentinel row |
| `event_date` | no | when the event was produced, UTC. **The only ordering key** |
| `delivered` | no | false on every insert; the fan-out job flips it. A CDC-only deployment ignores it |

## Sentinel rows

An action that emits no events still writes **one row**, with `model_id`, `model_type`,
`event_type` and `payload` all null. The action happened, and that is a fact worth keeping.

A consumer skips them with `event_type IS NULL`. The Debezium transforms already do.

## Two things about this table specifically

**`action_params` is not null, deliberately.** Every row carries the full input of the unit of
work that produced it, so one row is a complete audit record without a join.

**`delivered` is the only mutable column, and it belongs to the consumer.** A producer that flips
it loses the event for every consumer that had not yet read it.

## Ordering

`event_date` is the only ordering key - there is no ordinal column. Under a real
clock that is fine. Under a **frozen** clock every row of a run ties and the order falls back to
a random uuid. The test suite fell into this more than once; assert on `action_name` or
`action_id` rather than on position.

## Dialect types

The columns, their order and nullability are the same on every database. Where the types differ:

| | PostgreSQL | MySQL and MariaDB |
| --- | --- | --- |
| `id`, `action_id` | `UUID` | `CHAR(36)`, the hyphenated text |
| `action_params`, `payload` | `JSONB` | `JSON` |
| the three times | `TIMESTAMP WITHOUT TIME ZONE` | `DATETIME(6)` |

### MariaDB's native UUID type

MariaDB 10.7+ has a native 16-byte `UUID` type. SQLAlchemy does not emit it, so the table here
says `CHAR(36)`: 36 bytes a value against 16. It matters at outbox scale and nowhere else. A
table created with the native type works too - a uuid written to it reads back the same, and
`CAST(id AS CHAR)` gives the usual hyphenated text - so if your migration says `UUID`, nothing
in your service changes.

A Java service never shares this table - it has its own, in its own database - but the Java
Debezium transforms read this one, so its columns line up with Java's; what differs is on
[Running beside a Java service](../../beside-java.md#the-events-table).

## Indexes

`outbox_table()` declares no indexes. Your migration creates them, because the right ones depend
on the database. The examples create two: `action_id`, to find every row of one action, and the
undelivered index below.

### The undelivered index, and what it costs

The fan-out job and a polling consumer look for rows not yet delivered. On PostgreSQL that index
is **partial** - it holds only the rows still waiting:

```sql
CREATE INDEX ix_events_undelivered ON events (event_type, event_date)
    WHERE delivered = FALSE;
```

MySQL and MariaDB have no partial indexes, so their migrations use a composite one:

```sql
CREATE INDEX ix_events_undelivered ON events (delivered, event_type, event_date);
```

The difference in size is large. Measured on PostgreSQL 17, 500,000 rows with a 1% backlog - a
steady-state outbox that CDC is keeping up with:

| index | size |
| --- | --- |
| `(delivered, event_type, event_date)` - the composite form | **3,528 kB** |
| `(event_type, event_date) WHERE delivered = FALSE` - the partial form | **56 kB** |

Sixty-three times, because the composite holds every row ever written and the partial one holds
only the backlog. Every insert pays for it, on the hot path.

**The index is yours, not the framework's.** `ekbatan.schema` declares the table's *shape* - the
contract a CDC connector binds to - and nothing about its physical design. It used to declare
three indexes, and that was the whole reason PostgreSQL went without the partial form: one
declaration cannot be `WHERE delivered = FALSE` on one database and a composite on another, so
it was the composite everywhere.

So write it in your migration, the way every example here does.

## See also

- [The outbox](../../concepts/outbox.md) - why this table exists
- [`event_notifications`](event-notifications.md) - what fan-out produces from these rows
- [Framework tables](../tables.md) - the map
