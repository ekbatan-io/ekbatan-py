# fastapi-wallet-saga-pip-pg

A wallet-to-wallet transfer, decomposed into three single-aggregate steps chained by events,
with compensation when a step cannot finish.

No broker, no orchestrator, no saga framework. The whole choreography runs through the same
in-process event handler every other example uses — just chained differently.

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # run it; the database comes first, above
```

Built with **pip and setuptools**. The same project with [uv](../fastapi-wallet-saga-uv-pg) and [Poetry](../fastapi-wallet-saga-poetry-pg) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

The venv is made with `python3`, which must be 3.13 or later - `make PYTHON=python3.13` if yours is older.
<!-- /build -->

The database, the namespace and the event handling switch are in `ekbatan.yaml`, in the same
keys as Java's `application.yml`. As in Java, the whole saga depends on
`local-event-handler.handling.enabled: true`: every step after the first is started by a handler,
run by the fan-out and handling jobs in the background. Switch it off and the saga stops after
step one.

## What a saga costs a client

One endpoint changes shape, and `api.py` is where you see it:

```
POST /wallets/{id}/transfers   ->  202 Accepted, + transfer_id + status_url
GET  /transfers/{id}           ->  IN_FLIGHT | COMPLETED | REFUNDED
```

**202, not 200.** When that response is written only step one has committed: the money has left
the source and has arrived nowhere. A 200 would be a lie about something still in flight, and
pretending otherwise is how people are surprised by sagas in production.

The status endpoint reads the `TransferStep` markers, because that *is* the state - there is no
saga table and no coordinator to ask. That is what choreography means.

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

## Running on PostgreSQL

As Java's `spring-boot-wallet-saga-gradle-pg` does. Fan-out and dispatch are two distributed
jobs and nobody calls them, so a step lands whenever the jobs reach it; the tests wait for the
saga to settle, up to 15 seconds, as Java's do. For a run that needs nothing installed,
`wallet_service_sqlite` is the one.

One thing the tests had to get right, and it is worth knowing: the event trail is read in
`event_date` order, and the outbox has **no ordinal column**. Under a frozen test clock every
step ties on the same instant and the order falls back to a random uuid. Each saga step is a
separate unit of work at a different instant, which is exactly what makes the trail readable —
so these tests run on a real clock rather than a frozen one.
