# `event_notifications`

Optional: needed only when you use the in-process event handler. Fan-out turns each event row
into **one delivery row per handler** that subscribes to it, so an event with two handlers becomes
two rows, with the same `event_id` and different `handler_name`.

**Needed when:** you handle events in-process.
**Written by:** the fan-out job creates rows; the dispatch job updates them.
**Built by:** `ekbatan.schema.notifications_table(metadata)`.
**Where:** `eventlog.event_notifications`. On PostgreSQL `eventlog` is a schema in each database;
on MySQL and MariaDB it is a database of its own, one per server; on SQLite the table is plain
`event_notifications`, in the file. The place is fixed, because the framework names the table
itself - see [Framework tables](../tables.md).

Like `events`, it is a plain row shape, not a `Model` or an `Entity`. The dispatch job updates
its rows directly as delivery state changes.

## DDL

As with `events`, `notifications_table()` describes the table and your migration creates it. The
DDL below includes the index the examples' migrations add to find the rows due for delivery - the
due index. The index is yours to choose.

### PostgreSQL

```sql
CREATE SCHEMA IF NOT EXISTS eventlog;

CREATE TABLE eventlog.event_notifications (
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

CREATE INDEX ix_event_notifications_due ON eventlog.event_notifications (next_retry_at)
    WHERE state IN ('PENDING', 'FAILED');
```

### MySQL and MariaDB

The same table, from the same definition. Only the types differ - `CHAR(36)` for the uuids,
`DATETIME(6)` for the timestamps, `JSON` for the payloads - exactly as in
[`events`](events.md). The due index is a composite one, because MySQL and
MariaDB have no partial indexes:

```sql
CREATE DATABASE IF NOT EXISTS eventlog;

CREATE TABLE eventlog.event_notifications (
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

CREATE INDEX ix_event_notifications_due ON eventlog.event_notifications (state, next_retry_at);
```

`DATETIME(6)` rather than `DATETIME` matters here: a bare `DATETIME` cuts times to whole seconds
on MySQL, and `next_retry_at` is compared against times that are not whole seconds.

## Columns

Twelve columns are **copied from the event row** when fan-out creates the delivery row, so a
handler needs no join to do its work:

| column | null | meaning |
| --- | :---: | --- |
| `event_id` | no | the outbox row this delivery is for |
| `namespace` | no | copied; the service that emitted the event |
| `action_id` | no | copied; shared by every row one action execution wrote |
| `action_name` | no | copied; what ran |
| `action_params` | no | copied; the action's input, for anything a handler needs that the event does not carry |
| `started_date` | no | copied; when the action began |
| `completion_date` | no | copied; when it committed |
| `model_id` | yes | copied; null only for a row whose source had no model |
| `model_type` | yes | copied; as above |
| `event_type` | no | copied. Never null here, unlike in `events`: sentinel rows are never fanned out |
| `payload` | yes | copied; null only if the source row was malformed |
| `event_date` | no | copied; the ordering key on the source row |

The rest belong to this table: which handler the row is for, and how its delivery is going.

| column | null | meaning |
| --- | :---: | --- |
| `id` | no | this delivery row |
| `handler_name` | no | the handler's `name()`. **Treat it as schema data** - see [below](#handler-names-are-schema) |
| `state` | no | `PENDING`, `FAILED`, `SUCCEEDED` or `EXPIRED` |
| `attempts` | no | failed attempts so far |
| `next_retry_at` | no | the earliest time dispatch may try again; the event's `event_date` when fan-out writes the row |
| `created_date` | no | when fan-out created the row |
| `updated_date` | no | when it last changed |

## Indexes

### The unique constraint is the whole design

`UNIQUE (event_id, handler_name)` is what makes fan-out safe to repeat: each handler gets each
event once. If fan-out runs twice over the same event - after a crash, or because two instances
raced - the second insert is refused, and no duplicate work is created.

The saga example uses the same idea for its step markers, and it is worth copying: **to make work
safe to repeat, put a unique constraint inside the transaction that does the work.** Checking
first in code - "is there a row? if not, insert one" - leaves a gap between the check and the
insert, where another instance can slip in. A unique constraint leaves no gap.

## State transitions

| state | meaning |
| --- | --- |
| `PENDING` | the initial state; not yet attempted, or attempted and not yet resolved |
| `FAILED` | an attempt raised and another retry is still allowed |
| `SUCCEEDED` | final. The handler returned normally |
| `EXPIRED` | final. The retry window elapsed without success |

`FAILED` is not final; `EXPIRED` is. A handler that can never succeed - a transfer into a closed
wallet, say - should **not** raise. It should record a business outcome and return; otherwise it
is retried until the window closes. See [Sagas](../../concepts/sagas.md).

## Handler names are schema

`handler_name` stores the handler's `name()`, so rows already queued carry the name the handler
had when they were written. Rename a handler and every row queued under the old name is invisible
to it: those rows sit until they expire.

Keep the old name in `aliases()`:

```python
class WalletMoneyDepositedEventHandler(EventHandler[WalletMoneyDepositedEvent]):
    def name(self) -> str:
        return "wallet-money-deposited-notification"

    def aliases(self) -> set[str]:
        return {"wallet-deposit-alert"}      # what it used to be called

    def event_type(self) -> type[WalletMoneyDepositedEvent]:
        return WalletMoneyDepositedEvent
```

New rows are written with `name()`, and queued rows written under an alias still reach the
handler. Drop the alias once those old rows have been handled.

## Dialect types

The columns, their order, nullability and the unique constraint are the same on every database.
The difference is in the indexes.

**The due index is partial on PostgreSQL**: `(next_retry_at) WHERE state IN ('PENDING',
'FAILED')`. On MySQL and MariaDB, which have no partial indexes, it is `(state, next_retry_at)`.
`SUCCEEDED` and `EXPIRED` rows stay for the retention window and are never selected, so on
PostgreSQL they stay out of the index entirely.

It comes from your migration, not from `ekbatan.schema`, for the reason given on
[`events`](events.md#the-undelivered-index-and-what-it-costs): the framework declares the table's shape and nothing about its physical
design.

**`attempts` has no default in the database.** The framework always writes the column. If you
insert rows by hand, give it a value: `0` for a row nobody has tried yet.

## See also

- [`events`](events.md) - the source rows
- [Framework tables](../tables.md) - the map
- [Sagas](../../concepts/sagas.md) - why a terminal business failure is an event, not an exception
