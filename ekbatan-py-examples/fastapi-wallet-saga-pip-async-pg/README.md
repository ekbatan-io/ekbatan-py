# fastapi-wallet-saga-pip-async-pg

A wallet-to-wallet transfer, decomposed into three single-aggregate steps chained by events,
with compensation when a step cannot finish - on **native asyncio**, `ekbatan.aio`. The same
saga as [`fastapi-wallet-saga-uv-pg`](../fastapi-wallet-saga-uv-pg): the same model, events,
converters, migration, compensation and tests.

No broker, no orchestrator, no saga framework. The whole choreography runs through the same
in-process event handler every other example uses — just chained differently.

```
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=ekbatan -e POSTGRES_USER=ekbatan -e POSTGRES_DB=wallet postgres:17-alpine
```

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # run it; the database comes first, above
```

Built with **pip and setuptools**. The same project with [uv](../fastapi-wallet-saga-uv-async-pg) and [Poetry](../fastapi-wallet-saga-poetry-async-pg) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.

The venv is made with `python3`, which must be 3.13 or later - `make PYTHON=python3.13` if yours is older.
<!-- /build -->

## What is async, and what is not

| | here |
| --- | --- |
| the endpoints | `async def`, awaiting the service - nothing blocks the event loop |
| step one, and every read | an `AsyncActionExecutor` and async repositories, on asyncpg |
| steps two and three | async: `TransferInitiatedEventHandler` and `TransferFailedEventHandler` are `async def` and await `CompleteTransferAction` and `RefundTransferAction` through an `AsyncActionExecutor` of their own, on dispatch's handler loop - after the step before has committed, never inside it |
| the jobs | sync: db-scheduler, and fan-out and dispatch, on its threads |
| migrations, `schemagen` | sync, through psycopg |

Three pools, from one url that names psycopg; the async ones connect through asyncpg:

| pool | used by | driver |
| --- | --- | --- |
| `primaryConfig` | the requests: step one, and the reads | asyncpg |
| `handlersConfig` | steps two and three, on the handler loop | asyncpg |
| `jobsConfig` | db-scheduler, and fan-out and dispatch | psycopg |

The handlers' registry, repositories and actions are their own, built for the handler loop and
closed on it at shutdown: an async pool belongs to the loop it is used on.

The database, the namespace and the event handling switch are in `ekbatan.yaml`, in the same
keys as Java's `application.yml`; each pool puts its sessions on UTC with
`options: -c TimeZone=UTC`, whatever the server's default. As in Java, the whole saga depends on
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
| `test_fastapi_wallet_saga_async_pg.py` | happy path, three compensation paths, and a replay that credits nothing |

## Two forced types

`pyproject.toml` tells schemagen to turn two plain text columns into this app's own types:

```toml
# Every CHAR(3) column is a currency code, so the rule picks columns by their type.
[[tool.ekbatan.schemagen.forced-types]]
converter = "fastapi_wallet_saga_async_pg.converters.CurrencyConverter"
include-types = "CHAR\\(3\\)"

# One column, picked by its name: the saga's step, as the StepName enum.
[[tool.ekbatan.schemagen.forced-types]]
converter = "fastapi_wallet_saga_async_pg.converters.StepNameConverter"
include-expression = "transfer_steps\\.step"
```

and schemagen writes these two lines into `schema.py`:

```python
Column("step", converted(VARCHAR(length=24), StepNameConverter()), nullable=False)
Column("currency", converted(CHAR(length=3), CurrencyConverter()), nullable=False)
```

and, in the records beside the tables, the converters' own types - so a repository's
`to_record` that hands the table a plain string where a `Currency` belongs is a mypy error:

```python
class TransferStepsRecord(TypedDict):
    ...
    step: StepName


class WalletsRecord(TypedDict):
    ...
    currency: Currency
```

- **Picked by type.** Every `CHAR(3)` column is a currency code. A wallet's `currency` is a
  `Currency`, which takes only three capital letters; the destination check in step two compares
  its code with the one the transfer's event carries.
- **Picked by name.** `transfer_steps.step` is a `StepName`. The step markers are written and
  read as `StepName.CREDIT` and `StepName.REFUND`, `steps_of` gets them straight from the column,
  and a query can say `TRANSFER_STEPS.c.step == StepName.REFUND`. The column holds `CREDIT`.

The classes are in `model.py` and the converters in `converters.py`; neither knows SQLAlchemy.
Events keep the plain text, `"currency": "EUR"`, because they are JSON for other services to
read. The `state` columns are left as text on purpose: the framework itself reads them as text.
The test checks each value in Python, in the table and through the API.

## Running on PostgreSQL

As Java's `spring-boot-wallet-saga-gradle-pg` does. Fan-out and dispatch are two distributed
jobs and nobody calls them, so a step lands whenever the jobs reach it; the tests wait for the
saga to settle, up to 15 seconds, as Java's do. For a run that needs nothing installed,
`wallet_service_async_sqlite` is the one.

The tests are the sync twin's, plus two: the requests and the handlers really run on asyncpg
and the jobs on psycopg, and the saga's later steps went through the handlers' own pool on the
handler loop - asked in `pg_stat_activity`. A test that drives the service directly awaits on a
loop of its own (a `HandlerLoop`).

One thing the tests had to get right, and it is worth knowing: the event trail is read in
`event_date` order, and the outbox has **no ordinal column**. Under a frozen test clock every
step ties on the same instant and the order falls back to a random uuid. Each saga step is a
separate unit of work at a different instant, which is exactly what makes the trail readable —
so these tests run on a real clock rather than a frozen one.
