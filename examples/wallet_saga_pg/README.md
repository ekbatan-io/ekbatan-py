# wallet_saga_pg

A wallet-to-wallet transfer, decomposed into three single-aggregate steps chained by events,
with compensation when a step cannot finish.

No broker, no orchestrator, no saga framework. The whole choreography runs through the same
in-process event handler every other example uses — just chained differently.

```
uv run pytest examples/wallet_saga_pg
```

## Why a transfer needs this

Moving money touches two aggregates, and one unit of work commits to one transaction. Doing
both in one transaction would work *here* — the two wallets are on the same database — and
stops working the first time they are on different shards, or the destination moves to another
service. So the transfer is three steps, each its own transaction:

```
InitiateTransferAction          debit the source, emit TransferInitiatedEvent
  └─► CompleteTransferAction    credit the destination, emit TransferCompletedEvent
      │                   ...or, if it cannot, emit TransferFailedEvent on the SOURCE
      └─► RefundTransferAction  credit the source back, emit TransferRefundedEvent
```

Two commits in the happy case, three when it compensates. Each independently durable.

## The four ideas

**Forward-only compensation.** `RefundTransferAction` is not a rollback. Step one's debit committed
long ago and cannot be undone; the refund is a *new* action with its own commit and its own
event. The outbox is what makes the compensation exactly as durable as the debit it undoes.

**Choreography, not orchestration.** There is no saga state machine. Each handler knows one
thing: given this event, run that action. A fourth step means a new event and a new handler,
and nothing that already works has to change.

**The failure lands on the source.** `TransferFailedEvent` is emitted on the *source* wallet, because
the destination may be closed — or may not exist at all, in which case there is no aggregate
there to attach anything to. It also means one aggregate's trail tells the whole story:

```
TransferInitiatedEvent → TransferFailedEvent → TransferRefundedEvent
```

which is what an audit query on `events WHERE model_id = ?` gets to rely on.

**A failure is a business outcome, not an exception.** `CompleteTransferAction` returns normally when
the destination is closed. Raising would make the delivery retry, and retrying a transfer into
a closed wallet will never start working.

## Idempotency, and why it is a constraint rather than a check

Local delivery is **at-least-once** by design — the alternative is losing one. So the steps that
move money stage a `TransferStep` marker *before* the wallet update, under a unique index on
`(transfer_id, step)`. A replay stages the same marker, the insert is refused, and the credit
beside it is rolled back with it.

A read-then-check would lose the race it exists to win: two deliveries can both read "not there"
before either writes. The constraint cannot.

The marker is named for the **step**, not the outcome — `CREDIT` is staged whether the credit
happened or the transfer was failed instead, because what it guards is "step two has run".

## What to look at, in order

| File | What it shows |
|---|---|
| `model.py` | the four saga methods on `Wallet`, and why `mark_transfer_failed` changes no balance |
| `actions.py` | three steps, each one aggregate, each its own transaction |
| `handlers.py` | the chaining — nine lines each, and no coordinator anywhere |
| `schema.py` | the unique constraint that does the idempotency |
| `test_wallet_saga.py` | happy path, three compensation paths, and a replay that credits nothing |

## Running on SQLite

Deliberately. This example is about the saga, and the steps are driven by hand through
`deliver_pending_events()` so they happen where a reader can see them. In a service, fan-out
and dispatch are two distributed jobs and nobody calls them — `fastapi_wallet_rest_pg` shows that shape.

One thing the tests had to get right, and it is worth knowing: the event trail is read in
`event_date` order, and the outbox has **no ordinal column**. Under a frozen test clock every
step ties on the same instant and the order falls back to a random uuid. Each saga step is a
separate unit of work at a different instant, which is exactly what makes the trail readable —
so these tests run on a real clock rather than a frozen one.
