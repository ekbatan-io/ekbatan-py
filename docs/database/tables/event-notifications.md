# `event_notifications`

Optional. Needed only when the in-process event handler is installed. It turns one durable event
row into **one delivery row per subscribed handler** - so an event with two handlers becomes two
rows with the same `event_id` and different `handler_name`.

**Needed when:** you handle events in-process.
**Written by:** the fan-out job creates rows; the dispatch job updates them.
**Built by:** `ekbatan.schema.notifications_table(metadata, name="event_notifications", schema=None)`.

Like `events`, this is an internal row shape rather than a `Model` or `Entity`. The dispatch job
updates rows directly as delivery state changes.

## DDL

### PostgreSQL

```sql
CREATE TABLE event_notifications (
    id              UUID         NOT NULL,
    event_id        UUID         NOT NULL,
    handler_name    VARCHAR(255) NOT NULL,
    namespace       VARCHAR(255) NOT NULL,
    action_id       UUID         NOT NULL,
    action_name     VARCHAR(255) NOT NULL,
    action_params   JSONB        NOT NULL,
    started_date    TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    completion_date TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    model_id        VARCHAR(255),
    model_type      VARCHAR(255),
    event_type      VARCHAR(255) NOT NULL,
    payload         JSONB,
    event_date      TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    state           VARCHAR(24)  NOT NULL,
    attempts        INTEGER      NOT NULL,
    next_retry_at   TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    created_date    TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_date    TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_event_notifications_event_handler UNIQUE (event_id, handler_name)
);

CREATE INDEX ix_event_notifications_due ON event_notifications (state, next_retry_at);
```

MySQL and MariaDB are the same with `JSON` for `JSONB`, `DATETIME(6)` for the timestamps and
`CHAR(36)` for the uuids - exactly as in [`events`](events.md).

### MySQL and MariaDB

Generated from the same table definition, so the shape is identical and only the types differ -
`CHAR(36)` for uuids, `DATETIME(6)` for the timestamps, `JSON` for the payloads:

```sql
CREATE TABLE event_notifications (
    id               CHAR(36)     NOT NULL,
    event_id         CHAR(36)     NOT NULL,
    handler_name     VARCHAR(255) NOT NULL,
    namespace        VARCHAR(255) NOT NULL,
    action_id        CHAR(36)     NOT NULL,
    action_name      VARCHAR(255) NOT NULL,
    action_params    JSON         NOT NULL,
    started_date     DATETIME(6)  NOT NULL,
    completion_date  DATETIME(6)  NOT NULL,
    model_id         VARCHAR(255),
    model_type       VARCHAR(255),
    event_type       VARCHAR(255) NOT NULL,
    payload          JSON,
    event_date       DATETIME(6)  NOT NULL,
    state            VARCHAR(24)  NOT NULL,
    attempts         INTEGER      NOT NULL,
    next_retry_at    DATETIME(6)  NOT NULL,
    created_date     DATETIME(6)  NOT NULL,
    updated_date     DATETIME(6)  NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_event_notifications_event_handler UNIQUE (event_id, handler_name)
);
```

`DATETIME(6)` rather than `DATETIME` is not cosmetic: a bare `DATETIME` truncates to whole
seconds on MySQL, and `next_retry_at` is compared against instants that are not whole seconds.

## Columns

Twelve of them are **copied from the source event row** at fan-out time, so a handler needs no
join to do its work:

| column | null | meaning |
| --- | :---: | --- |
| `event_id` | no | the outbox row this delivery is for |
| `namespace` | no | copied; the service that emitted the event |
| `action_id` | no | copied; correlates every row written by one invocation |
| `action_name` | no | copied; what ran |
| `action_params` | no | copied; where routing concerns live that do not belong in the event |
| `started_date` | no | copied; when the invocation began |
| `completion_date` | no | copied; when it committed |
| `model_id` | yes | copied; null only for a row whose source had no model |
| `model_type` | yes | copied; as above |
| `payload` | yes | copied; null only if the source row was malformed |
| `event_date` | no | copied; the ordering key on the source row |

The rest are the delivery state, and they are this table's own:

| column | null | meaning |
| --- | :---: | --- |
| `id` | no | this delivery row |
| `handler_name` | no | the handler's `name`. **Treat it as schema data** - see below |
| `event_type` | no | not null here, unlike in `events`: sentinel rows are never fanned out |
| `state` | no | `PENDING`, `FAILED`, `SUCCEEDED` or `EXPIRED` |
| `attempts` | no | failed attempts so far |
| `next_retry_at` | no | the earliest instant dispatch may try again; the event's `event_date` when fan-out writes the row |
| `created_date` | no | when fan-out created the row |
| `updated_date` | no | when it last changed |

## Indexes

### The unique constraint is the whole design

`UNIQUE (event_id, handler_name)` is what makes fan-out idempotent per handler. If fan-out runs
twice over the same event - after a crash, or because two instances raced - the second insert is
refused and no duplicate work exists.

This is the same shape the saga example uses for its step markers, and it is worth internalising:
**idempotency is a unique constraint inside the transaction that does the work**, not a
check-then-act in application code. A check-then-act has a window. This has none.

## State transitions

| state | meaning |
| --- | --- |
| `PENDING` | the initial state; not yet attempted, or attempted and not yet resolved |
| `FAILED` | an attempt raised and another retry is still allowed |
| `SUCCEEDED` | terminal. The handler returned normally |
| `EXPIRED` | terminal. The retry window elapsed without success |

`FAILED` is not terminal and `EXPIRED` is. A handler that will never succeed - a transfer into a
closed wallet - should **not** raise; it should record a business outcome and return, or it will
be retried until the window closes. See [Sagas](../../concepts/sagas.md).

## Handler names are schema

`handler_name` stores the handler's `name`, and old queued rows carry the old name. Renaming a
handler without saying so makes every queued row invisible to it - they will sit until they
expire.

The way through is `aliases`:

```python
class MoneyDepositedEventHandler:
    name = "money-deposited"
    aliases = ("deposit-alerts",)      # what it used to be called
    event_type = MoneyDepositedEvent
```

New rows are written with `name`; queued rows written under an alias still route. Drop the alias
once they have drained.

## Dialect types

The columns, their order, nullability and the unique constraint are the same on every database.
The difference is in the indexes:

**The due index is partial on PostgreSQL** - `(next_retry_at) WHERE state IN ('PENDING',
'FAILED')` - and `(state, next_retry_at)` on MySQL and MariaDB, which have no partial indexes.
`SUCCEEDED` and `EXPIRED` rows sit for the retention window and are never selected, so on
PostgreSQL they stay out of the index entirely.

It comes from your migration, not from `ekbatan.schema`, for the reason given on
[`events`](events.md#the-undelivered-index-and-what-it-costs): the framework declares the table's shape and nothing about its physical
design.

**`attempts` has no default.** The framework always writes the column. If you insert rows by
hand, give it a value - `0` for a row nobody has tried yet.

## See also

- [`events`](events.md) - the source rows
- [Framework tables](../tables.md) - the map
- [Sagas](../../concepts/sagas.md) - why a terminal business failure is an event, not an exception
