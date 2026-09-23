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
        .with_event(MoneyDepositedEvent(model_id=str(self.id), amount=amount, new_balance=new_balance))
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

## Passing the table explicitly

`events=EVENTS` is not ceremony. The table is passed in rather than reached for globally, which
is what lets two services run in one process against two outboxes - exactly what the saga
example's tests do. It is also what makes the generated schema module the single source of truth
for the table's shape.

## The table is a cross-language contract

The columns are the same here and in Java, because a Debezium connector pointed at either one
must produce the same topic. That is what makes a Java service and a Python service able to read
each other's events:

| | |
| --- | --- |
| `id`, `action_id` | the row, and the action that wrote it |
| `namespace`, `action_name` | who did it, and what they did |
| `action_params` | the parameters the action was called with |
| `started_date`, `completion_date` | the action's span |
| `model_id`, `model_type` | which aggregate this event is about |
| `event_type`, `payload` | the event's name and its JSON |
| `event_date` | when it happened; the ordering key |
| `delivered` | the in-process dispatch flag |

One detail is worth knowing because it looks like a mistake: the payload's identity keys are
written as `modelId` and `modelName`, in Java's spelling rather than this port's. Java's local
event handler deserialises the payload straight into its event class and refuses one without
them. This port reads them back under its own names, so a payload written by either side rebuilds
a complete event here. It is a contract, not an oversight.

## What the outbox row means

An action that emits no events still writes **one row**, with `model_id`, `model_type`,
`event_type` and `payload` all null. The action happened, and that is a fact worth keeping.

A consumer skips them with `event_type IS NULL`. The Debezium transforms already do.

## Two consumer paths

The outbox is **just a table**. Anything that can read it can consume it.

**In-process.** Two jobs - fan-out and dispatch - drain the outbox and invoke typed handlers,
with retry and expiry. No broker, no CDC. This is the right shape for a monolith or an internal
service, and it is what every example here uses.

**CDC to Kafka.** Point Debezium at `events` and ship rows to per-event-type topics. The
transform that does the routing runs **inside Kafka Connect, in a JVM**, whichever language wrote
the row - so a Python service uses the same `ekbatan-debezium-smt` artifact a Java one does.
There is no Python counterpart to build, and none is planned. What this port owes is the row
format above.

Both can run against the same outbox at once. In-process fan-out flips `delivered` from false to
true, producing `UPDATE` rows; the Kafka transforms drop non-insert operations, so those flips
are invisible downstream.

## One trap worth knowing

`event_date` is the only ordering key — there is no ordinal column, in either port. Under a real
clock that is fine. Under a **frozen** clock every row of a run ties and the order falls back to
a random uuid. The test suite fell into this more than once.

## Ordering, and the thing to know about it

`event_date` is the only ordering key - there is no ordinal column. Under a real clock that is
fine. Under a **frozen** clock, every row of a run ties and the order falls back to a random
uuid, which is a trap the test suite fell into more than once. Assert on `action_name` or
`action_id` rather than on position, or use a running clock.

## See also

- [Actions](actions.md) - what stages the events that end up here
- [Models and entities](models-and-entities.md) - only models emit events
- [The dual-write trap](the-dual-write-trap.md) - why this exists at all
