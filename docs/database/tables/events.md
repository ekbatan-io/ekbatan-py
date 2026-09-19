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

You do not write this by hand; `outbox_table()` produces it and your migration creates it. The
DDL below is what SQLAlchemy actually emits, so it is what you would compare against a Java
service's table.

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
CREATE INDEX ix_events_undelivered ON events (delivered, event_type, event_date);
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

`event_date` is the only ordering key - there is no ordinal column, in either port. Under a real
clock that is fine. Under a **frozen** clock every row of a run ties and the order falls back to
a random uuid. The test suite fell into this more than once; assert on `action_name` or
`action_id` rather than on position.

## Dialect types

Verified by generating both and comparing, rather than by reading:

| | agrees? |
| --- | --- |
| column names, order, nullability | **identical** |
| PostgreSQL types | **identical** |
| MySQL types | **identical**. Java writes `CHARACTER SET ascii` on the uuid columns; that is an index-size optimisation, not a format difference |
| MariaDB `id`, `action_id` | **differs.** Java uses MariaDB's native `UUID` type; this port emits `CHAR(36)`. See below |
| schema | **differs by default.** Java uses `eventlog`; pass `schema="eventlog"` to match |
| index names | `ix_events_action_id` here, `idx_events_action_id` in Java. Cosmetic |
| the undelivered index | **differs on PostgreSQL.** See below |

### MariaDB's native UUID type

Java's MariaDB DDL declares `id UUID`. MariaDB 10.7+ has a native 16-byte UUID type;
SQLAlchemy does not emit it, so this port's DDL says `CHAR(36)`.

**This does not break interop, and that was checked rather than assumed.** Against a table
created with Java's DDL, this port writes a UUID and reads it back identical, and
`CAST(id AS CHAR)` in the database gives the canonical hyphenated text. Either DDL works with
either port; whichever service migrates first decides the storage.

The difference is 16 bytes per value against 36. It matters at outbox scale and nowhere else.

### The undelivered index, and what it costs

Java creates a **partial** index on PostgreSQL:

```sql
CREATE INDEX events_undelivered ON eventlog.events (event_type, event_date)
    WHERE delivered = FALSE;
```

This port emits the MySQL-shaped composite index on every dialect, because the table factory
builds one `Index` with no dialect variation:

```sql
CREATE INDEX ix_events_undelivered ON events (delivered, event_type, event_date);
```

Functionally the same queries are served. The cost is size and write amplification, and it is
not small. Measured on PostgreSQL 17, 500,000 rows with a 1% backlog - a steady-state outbox that
CDC is keeping up with:

| index | size |
| --- | --- |
| `(delivered, event_type, event_date)` - this port | **3,528 kB** |
| `(event_type, event_date) WHERE delivered = FALSE` - Java | **56 kB** |

Sixty-three times larger, because the composite index holds every row ever written and the
partial one holds only the backlog. Every insert pays for it, on the hot path.

If your outbox is large and PostgreSQL is your dialect, **drop the generated index and create the
partial one in your own migration**. It is recorded in
[SHORTCOMINGS.md](../../SHORTCOMINGS.md) as a real gap rather than a difference of taste.

## Indexes

### The undelivered index, and what it costs

Java creates a **partial** index on PostgreSQL:

```sql
CREATE INDEX events_undelivered ON eventlog.events (event_type, event_date)
    WHERE delivered = FALSE;
```

This port emits the MySQL-shaped composite index on every dialect, because the table factory
builds one `Index` with no dialect variation:

```sql
CREATE INDEX ix_events_undelivered ON events (delivered, event_type, event_date);
```

Functionally the same queries are served. The cost is size and write amplification, and it is
not small. Measured on PostgreSQL 17, 500,000 rows with a 1% backlog - a steady-state outbox that
CDC is keeping up with:

| index | size |
| --- | --- |
| `(delivered, event_type, event_date)` - this port | **3,528 kB** |
| `(event_type, event_date) WHERE delivered = FALSE` - Java | **56 kB** |

Sixty-three times larger, because the composite index holds every row ever written and the
partial one holds only the backlog. Every insert pays for it, on the hot path.

If your outbox is large and PostgreSQL is your dialect, **drop the generated index and create the
partial one in your own migration**. It is recorded in
[SHORTCOMINGS.md](https://github.com/ekbatan-io/ekbatan-py/blob/main/docs/SHORTCOMINGS.md) as a real gap rather than a difference of taste.

## See also

- [The outbox](../../concepts/outbox.md) - why this table exists
- [`event_notifications`](event-notifications.md) - what fan-out produces from these rows
- [Framework tables](../tables.md) - the map
