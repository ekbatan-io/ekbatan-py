# The outbox

The central guarantee. Every action's commit is one database transaction that touches as many
domain tables as the action needs **plus** the outbox. They land together, or none of them do.

```
┌──────────────────  ONE DATABASE TRANSACTION  ──────────────────────┐
│                                                                    │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────────┐    │
│  │    wallets     │  │     orders     │  │       events       │    │
│  │   (UPDATE)     │  │    (INSERT)    │  │      (INSERT)      │    │
│  ├────────────────┤  ├────────────────┤  ├────────────────────┤    │
│  │ id             │  │ id             │  │ id                 │    │
│  │ balance        │  │ wallet_id      │  │ action_id          │    │
│  │ version        │  │ amount         │  │ event_type         │    │
│  │ ...            │  │ status         │  │ payload  (JSON)    │    │
│  └────────────────┘  └────────────────┘  └────────────────────┘    │
│        domain              domain                outbox            │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
       commit (every row persists)  —or—  rollback (none of them do)
```

There is no second write to a broker, no two-phase commit, and no retry loop keeping two systems
in step. After the commit, the rows in `events` are the canonical record of what happened.

## How rows get there

Every row arrives the same way, and no other way exists:

```text
Model method
  -> returns a new immutable model
  -> with one or more ModelEvents attached

Action.perform()
  -> reads the current state
  -> calls the model method
  -> stages the new model on self.plan

the executor
  -> opens one database transaction
  -> writes the domain row
  -> writes the attached event rows to `events`
  -> commits, or rolls back all of it
```

For a wallet deposit, that first step is:

```python
def deposit(self, amount: Decimal) -> Wallet:
    new_balance = self.balance + amount
    return (
        self.copy()
        .with_event(WalletMoneyDepositedEvent(model_id=str(self.id), amount=amount, new_balance=new_balance))
        .balance(new_balance)
        .build()
    )
```

The event is attached **before** persistence and written **after** it. In between it waits in the
action's staged changes, touching nothing. Only the executor writes it, and only in the same
transaction as the wallet row.

**That is the important part:** your code creates domain facts; the framework persists those
facts atomically with the state they describe. Neither half can happen without the other,
because neither half is a separate call you could make.

And this is the table they land in - [`events`](../database/tables/events.md) has its DDL on
every dialect, column by column.

## Where the table is

Nothing passes the table to the framework: the executor's default event writer names it itself,
and so do the event jobs that read it. It is `eventlog.events` - in a schema of its own in each
database on PostgreSQL, in a database of its own, one per server, on MySQL and MariaDB - and plain
`events` in the file on SQLite; see
[Schema vs database](../database/multi-database.md#schema-vs-database) for why. The generated
schema module declares the same table as `EVENTS`, so a test can read it back.

## The table's columns

The columns are fixed, because everything that reads the outbox binds to them - a Debezium
connector and the Java transforms it runs (see
[Running beside a Java service](../beside-java.md#the-events-table)), and the in-process
handlers:

| | |
| --- | --- |
| `id`, `action_id` | the row, and the action that wrote it |
| `namespace`, `action_name` | who did it, and what they did |
| `action_params` | the parameters the action was called with |
| `started_date`, `completion_date` | the action's span |
| `model_id`, `model_type` | which aggregate this event is about |
| `event_type`, `payload` | the event's name - its class name, which an event class cannot override - and its JSON |
| `event_date` | when it happened; the ordering key |
| `delivered` | the in-process dispatch flag |

`action_params` and `payload` hold JSON: field names in camelCase (`new_balance` is
`newBalance`) in declaration order, decimals as JSON numbers, times as ISO-8601 text, and the
payload starts with `modelId` and `modelName`. That shape is what a Java consumer of the same
rows reads - see [Event payloads](../beside-java.md#event-payloads). How a handler reads a payload
back is on [Models and entities](models-and-entities.md#modelevent).

## What the outbox row means

An outbox row is not "a Kafka message stored in SQL". It is a committed event, with enough around
it for a consumer to know where it came from: the action that committed it, the model it is
about, its type, and the event itself as JSON.

An action that emits no events still writes **one row**, with `model_id`, `model_type`,
`event_type` and `payload` all null. The action happened, and that is a fact worth keeping. A
consumer skips those rows by checking `event_type IS NULL`; the Debezium transforms already do.

## Two consumer paths

The outbox is **just a table**. Anything that can read it can consume it.

**In-process.** Two jobs - fan-out and dispatch - drain the outbox and invoke typed handlers,
with retry and expiry. No broker, no CDC. This is the right shape for a monolith or an internal
service, and it is what every example here uses.

**CDC to Kafka.** Point Debezium at `events` and ship rows to per-event-type topics. The
transform that does the routing runs **inside Kafka Connect**, which is a Java program whatever
language wrote the row, so you use the ready-made transform JARs and build nothing in Python -
see [Streaming the events](../beside-java.md#streaming-the-events). What this framework owes is
the row format above.

Both can run against the same outbox at once. The in-process path marks each row `delivered`
once it has handled it; the Kafka path ignores those updates, so they never become Kafka messages.

## One trap worth knowing

`event_date` is the only ordering key - there is no ordinal column. With a real clock that is
fine. A test that freezes its clock gives every row of a run the same time, so their order is
effectively random: pick a row by its `action_name` or `action_id`, not by its position, or use a
running clock.

## See also

- [Actions](actions.md) - what stages the events that end up here
- [Models and entities](models-and-entities.md) - only models emit events
- [The dual-write trap](the-dual-write-trap.md) - why this exists at all
