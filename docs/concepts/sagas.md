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

## Wallet transfer example

[`fastapi-wallet-saga-uv-pg`](https://github.com/ekbatan-io/ekbatan-py/tree/main/ekbatan-py-examples/fastapi-wallet-saga-uv-pg)
splits a transfer into three steps.

| step | action | what commits atomically | event |
| --- | --- | --- | --- |
| 1 | `InitiateTransferAction` | debit the source | `TransferInitiatedEvent` |
| 2 happy | `CompleteTransferAction` | credit the destination | `TransferCompletedEvent` |
| 2 failed | `CompleteTransferAction` | mark it failed on the source | `TransferFailedEvent` |
| 3 compensate | `RefundTransferAction` | credit the source back | `TransferRefundedEvent` |

The happy path is two commits; the failure path is three. Every commit has its own outbox rows,
so the trail shows what happened instead of pretending it was one invisible transaction.

Two details in that table are the whole design.

**Step 2's failure is an event, not an exception.** `CompleteTransferAction` emits
`TransferFailedEvent` on the *source* wallet and commits it. A compensating handler is then
triggered by a business outcome that was committed like any other — rather than by a thrown
exception, which would be retried forever and never reach a compensation.

**Each step touches one wallet.** Step 1 debits the source, step 2 credits the destination, step
3 credits the source back. That is what lets each one be an ordinary single-shard action with an
ordinary optimistic-lock version check, even when the two wallets live on different databases.
Compare [`allow_cross_shard=True`](../database/sharding.md#cross-shard-actions), which would make
this one action across two transactions with no recovery when the second fails.

A client sees `202` and polls `GET /transfers/{id}`. What that endpoint reads is worth knowing,
because it is the honest answer to "where does a saga's state live": **there is no saga table and
no coordinator to ask.** The markers each step writes *are* the state, and the endpoint derives
`IN_FLIGHT`, `COMPLETED` or `REFUNDED` from which of them exist. That is what choreography means
— and it is why adding a step needs no schema change.

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

## Idempotency is a constraint, not a check

Delivery is at-least-once, so a handler can run twice. The example's shape is worth copying
exactly:

```python
def perform(self, principal: str | None, params: CompleteTransferParams) -> Wallet | None:
    # Staged *before* the credit, so the unique constraint decides whether this delivery is
    # the first one. A replay stages the same marker, the insert is refused, and the credit
    # beside it is rolled back with it - which is why the money cannot arrive twice.
    self.plan.add(TransferStep.of(params.transfer_id, StepName.CREDIT))
    ...
```

A check-then-act has a window. This has none, because the database refuses the duplicate and
takes the credit down with it.

## Terminal failures are events, not exceptions

If the destination wallet is closed, retrying will never help. `CompleteTransferAction` emits
`TransferFailedEvent` and **returns normally**. Raising would make the handler retry a transfer
into a closed wallet forever — and "failed" is a business outcome that deserves a durable row
like any other.

## Forward-only compensation

The refund is not a rollback. By the time it runs, the debit has committed. The compensation is a
**new business fact**: the source was credited back because transfer X failed.

That is why the example emits `TransferRefundedEvent` rather than trying to undo the earlier
`TransferInitiatedEvent`. The history stays honest:

```
TransferInitiated -> TransferFailed -> TransferRefunded
```

You can explain the system's behaviour after the fact, because every step was durable.

## Where the next step is started

Do **not** execute another action from inside `perform()`. That would hide a second transaction
inside the first action's business logic and make the execution model hard to reason about.

The safe boundary is **after the previous action has committed and its event is being
consumed**. That consumer can be:

- an in-process handler running in your application (database-backed, restart-safe, any
  instance),
- a Kafka / Pulsar / RabbitMQ / SQS consumer fed by Debezium or by a publishing handler,
- a separate worker process that polls or streams the outbox,
- or a service-level coordinator that reacts to a committed event and starts the next step.

The important part is the boundary: the next step starts from a committed event, so it gets its
own transaction and its own durable outcome.

The runnable example uses in-process handlers, because every step lives in one application:

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
                from_wallet_id=ShardedId[Wallet](event.from_wallet_id),
                to_wallet_id=ShardedId[Wallet](event.to_wallet_id),
                amount=event.amount,
                currency=event.currency,
            ),
        )
```

Notice what the handler takes: **the executor**, not a connection. It runs the next step as its
own unit of work, with its own transaction and its own outbox rows. The transaction that produced
`TransferInitiatedEvent` committed and went away before this line ran — which is exactly the
property that makes the chain restartable.

Notice also what it reads: `envelope.event`, and nothing else. The event carries the whole
transfer, so the handler needs neither the producing action's parameters nor a second row nor any
knowledge of where the other wallet lives.

**There is no saga coordinator**, and that is the design rather than an omission. Each handler
knows one thing: given *this* event, run *that* action. Adding a fourth step means writing a
fifth event and a third handler, and changing nothing that already works. The trade is that
no single file shows you the whole workflow — the outbox does, after the fact.

## Design rules

**Carry a correlation id through every event.** The example mints a `transfer_id` in step one and
carries it through all four events.

**Make payloads self-contained.** A handler should not have to parse the producing action's
params to know what to do next. Put the identifiers and the amount in the event.

**Make every step idempotent.** Delivery is at-least-once; a handler can run twice after a crash.
The example's shape is worth copying exactly:

```python
def perform(self, principal: str | None, params: CompleteTransferParams) -> Wallet | None:
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

## What this gives you

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
- [`fastapi-wallet-saga-uv-pg`](../../ekbatan-py-examples/fastapi-wallet-saga-uv-pg) - all of it,
  running
