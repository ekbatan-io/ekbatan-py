# Sagas

A saga models one business workflow as a sequence of smaller, independently committed actions.
Use it when the workflow cannot honestly fit in one transaction: cross-shard transfers,
cross-database updates, cross-service work, or anything where a later step may need to compensate
for one that has already committed.

There is **no saga orchestrator here**, and none is planned. What the framework gives you is the
two pieces a saga needs:

- every action commits its own state and events atomically, and
- the outbox lets a committed event reliably start the next step.

So a saga is: **action → committed event → post-commit consumer → next action.** That is
choreography. There is no coordinator to ask what state a transfer is in, because the state *is*
the rows.

## Why not one cross-shard action?

`allow_cross_shard=True` lets an action stage changes that route to several shards, but each
shard still commits on its own. If A commits and B fails, nothing can make those two transactions
behave like one.

A saga accepts that instead of hiding it. Each step commits locally; if a later one fails, the
workflow records a failure event and runs a compensating action. The result is not one
all-or-nothing transaction. It is a durable, auditable workflow with explicit recovery - which is
the honest thing to have, because the alternative was never actually available.

## The worked example

[`ekbatan-py-examples/fastapi_wallet_saga_pg`](../../ekbatan-py-examples/fastapi_wallet_saga_pg)
runs on one PostgreSQL database with the in-process handler, but the shape is what you would use
across shards or services.

| step | action | what commits atomically | event |
| --- | --- | --- | --- |
| 1 | `InitiateTransferAction` | debit the source | `TransferInitiatedEvent` |
| 2 happy | `CompleteTransferAction` | credit the destination | `TransferCompletedEvent` |
| 2 failed | `CompleteTransferAction` | mark the transfer failed on the source | `TransferFailedEvent` |
| 3 compensate | `RefundTransferAction` | credit the source back | `TransferRefundedEvent` |

```
POST /wallets/{id}/transfers
        |
        v
InitiateTransferAction        debits the source, emits TransferInitiatedEvent
        |
        v
  post-commit consumer        a handler, a broker consumer, or a worker
        |
        v
CompleteTransferAction        destination can receive:  credit it, emit TransferCompleted
                              destination cannot:       emit TransferFailed on the source
        |
        v
  post-commit consumer
        |
        v
RefundTransferAction          credits the source back, emits TransferRefunded
```

The happy path is two commits; the failure path is three. Every commit has its own outbox rows,
so the trail shows what happened instead of pretending it was one invisible transaction.

## What a saga costs a client: 202, not 200

This is the part that has no counterpart in the Java page, and it is the first thing a caller
notices.

```
POST /wallets/{id}/transfers   ->  202 Accepted, + transfer_id + status_url
GET  /transfers/{id}           ->  IN_FLIGHT | COMPLETED | REFUNDED
```

When that response is written, **only step one has committed**: the money has left the source and
has arrived nowhere. A 200 would be a claim about something still in flight. Returning 202 with a
status URL is not politeness - it is the API telling the truth about what the database currently
says, and it is how people avoid being surprised by a saga in production.

The status endpoint reads the step markers, because that *is* the state. There is no saga table
and no coordinator to ask.

## Forward-only compensation

The refund is not a rollback. By the time it runs, the debit has committed. The compensation is a
**new business fact**: the source was credited back because transfer X failed.

That is why the example emits `TransferRefundedEvent` rather than trying to undo the earlier
`TransferInitiatedEvent`. The history stays honest:

```
TransferInitiated -> TransferFailed -> TransferRefunded
```

You can explain the system's behaviour after the fact, because every step was durable.

## Where the next step starts

**Not inside `perform()`.** That would hide a second transaction in the first action's business
logic. See [Actions → no nesting](actions.md#no-nesting).

The boundary is: after the previous action has committed and its event is being consumed. That
consumer can be an in-process handler, a broker consumer fed by Debezium, a worker polling the
outbox, or a coordinator in another service. The point is the boundary - the next step starts
from a committed event, so it gets its own transaction and its own durable outcome.

```python
class TransferInitiatedEventHandler:
    """Step one committed; run step two."""

    name = "transfer-initiated"
    event_type = TransferInitiatedEvent

    def __init__(self, executor: ActionExecutor) -> None:
        self.executor = executor

    def handle(self, envelope: EventEnvelope[TransferInitiatedEvent]) -> None:
        event = envelope.event
        self.executor.execute(
            CompleteTransferAction,
            CompleteTransferParams(
                transfer_id=event.transfer_id,
                from_wallet_id=WalletId(event.from_wallet_id),
                to_wallet_id=WalletId(event.to_wallet_id),
                amount=event.amount,
                currency=event.currency,
            ),
        )
```

A handler is a plain class - a `name`, an `event_type`, and a `handle`. No base class, no
decorator, no registration annotation: it is put in the registry by the wiring, which is where
you can see it.

## Design rules

**Carry a correlation id through every event.** The example mints a `transfer_id` in step one and
carries it through all four events.

**Make payloads self-contained.** A handler should not have to parse the producing action's
params to know what to do next. Put the identifiers and the amount in the event.

**Make every step idempotent.** Delivery is at-least-once; a handler can run twice after a crash.
The example's shape is worth copying exactly:

```python
def perform(self, params: CompleteTransferParams) -> Wallet | None:
    # Staged *before* the credit, so the unique constraint decides whether this delivery is
    # the first one. A replay stages the same marker, the insert is refused, and the credit
    # beside it is rolled back with it - which is why the money cannot arrive twice.
    self.plan.add(TransferStep.of(params.transfer_id, StepName.CREDIT))
    ...
```

Idempotency is a **unique constraint in the same transaction as the effect**, not a check-then-act
in application code. A check-then-act has a window; this has none, because the database refuses
the duplicate and takes the credit down with it.

**Record terminal business failures as events, not exceptions.** If the destination wallet is
closed, retrying will never help. `CompleteTransferAction` emits `TransferFailedEvent` and returns
normally. Raising would make the handler retry a transfer into a closed wallet forever - and
"failed" is a business outcome that deserves a durable row like any other.

**Keep compensation forward-only.** Do not erase earlier facts. Add a compensating action and a
new event.

**Pick the consumer by deployment shape.** In-process when every step lives in one application; a
broker when a later step belongs to another service. The action code does not change either way.

## What you get, and what you do not

You do not get global atomicity. A caller may observe intermediate state: the source is debited
before the destination is credited, or before the refund runs. The 202 is what makes that
visible rather than surprising.

What you get is a workflow that survives crashes and converges:

- each step commits atomically with the event announcing it,
- the next step is driven from a durable outbox row,
- failed business steps are visible in the event log,
- compensation is explicit and auditable.

That is usually the right trade when `allow_cross_shard=True` would otherwise hide a
partial-commit risk.

## See also

- [Actions](actions.md) - why actions are not nested
- [The outbox](outbox.md) - why each step gets a durable event
- [Sharding](sharding.md) - why cross-shard is refused by default
- [`fastapi_wallet_saga_pg`](../../ekbatan-py-examples/fastapi_wallet_saga_pg) - all of it,
  running
