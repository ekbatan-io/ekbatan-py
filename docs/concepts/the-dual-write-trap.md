# The dual-write trap

Every business change produces two things at once - a new state, and a record of how it got
there. Persisting them as two separate writes is where systems start to drift apart.

## The shape of the bug

You write an order to the database. Then you publish `OrderPlaced` to Kafka so the warehouse,
billing and the email worker all hear about it. Two writes, two systems, two round trips.

```
   app
  ↙   ↘
DB    Kafka
```

The bug is the gap between them. The database write succeeds, and then, before the publish,
the process crashes, the broker connection drops, or the server restarts. Now the database holds
an order nothing downstream will ever hear about. Or the reverse: the publish
succeeds, the database write rolls back, and three systems start working on an order that does
not exist.

Retries do not fix it. Retrying the publish after a successful commit can succeed *or*
duplicate. Retrying the commit after a successful publish can succeed *or* publish twice. Nothing can make the two writes one step across two systems, short of a distributed
transaction, and nobody runs those between a database and a broker.

This is the **dual-write problem**, and it is the most common source of "the events do not match
the data" in event-driven systems.

## The shape of the fix

Write the event to *the same database, in the same transaction* as the domain row. Let something
else carry it to the broker afterwards, by reading the database. One system decides whether the
change happened, and one transaction commits it.

```
       app
        ↓
  ┌─────────────────────────┐
  │  database (one tx)      │
  │   ┌──────┐  ┌────────┐  │
  │   │state │  │ outbox │  │
  │   └──────┘  └────────┘  │
  └─────────────────────────┘
        ↓
      Kafka  (Debezium, or a poller)
```

The outbox is an ordinary table in your application database. The framework inserts its row in
the same transaction as the state change, so the two land together or not at all. Something else then ships them onward: a worker inside your service that reads the table, or a
change-data-capture tool such as Debezium, which follows the database's own log of changes.

The dual write is gone. The only system that has to be transactionally consistent is the
database, which is what databases are for.

## Why it's a framework concern

The pattern is well known, and most teams bolt it on: add an `events` table, remember to write to
it in every transaction, build a poller. It works, and you carry the discipline forever. Every
new action has to remember. Every review has to check. The day somebody forgets, you have a
silent dual write, and it will not be noticed until a downstream system is measurably wrong.

This framework makes forgetting impossible. The executor writes the outbox row for every action,
at every commit. Your code stages a `WalletDepositMoneyAction`; the executor opens the transaction,
writes the wallet row, writes the matching `WalletMoneyDepositedEvent`, commits. There is no second
table to remember, because you never write to it.

That is the whole pitch. The rest of [Concepts](README.md) is how the pieces make that
structural rather than a convention somebody has to police.

## The one place the trap reappears

Later in this path your data may be split across several databases, called shards. An action
that writes to two shards at once is a dual write again: two databases, two commits, nothing
tying them together. That is why it is refused by default rather than merely discouraged. See
[Sharding](sharding.md) and [Sagas](sagas.md).

## See also

- [The outbox](outbox.md) - the table and the write path
- [Actions](actions.md) - the two-phase lifecycle that makes it automatic
- [Sagas](sagas.md) - coordinating multi-step work without pretending it is one transaction
