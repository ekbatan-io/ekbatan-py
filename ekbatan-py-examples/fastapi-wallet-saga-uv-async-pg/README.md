# fastapi-wallet-saga-uv-async-pg

A transfer between two wallets, as a saga, over HTTP on **PostgreSQL** on native asyncio.

It is the Python counterpart of the Java saga example: the same model, actions, handlers,
endpoints and test cases, under the same names, built three ways - uv, pip and Poetry.

<!-- build: written by scripts/sync-examples.py -->
```
make            # generate what is out of date, then test the built wheel, installed
make run        # start the database, run it, stop the database
```

`make run` needs only Docker: it starts the database `compose.yaml` describes - the image the tests use, with the login and database `ekbatan.yaml` defaults to - waits until it is ready, runs the app, and stops the database when the app stops. `docker compose down` throws the database away.

Built with **uv**. The same project with [pip and setuptools](../fastapi-wallet-saga-pip-async-pg) and [Poetry](../fastapi-wallet-saga-poetry-async-pg) differs only in `pyproject.toml`, `Makefile` and `.gitignore`.
<!-- /build -->

## What it does

| Endpoint | What runs |
| --- | --- |
| `POST /wallets` `{ownerId, currency, initialBalance}` | `WalletCreateAction`. 201 |
| `POST /wallets/{id}/deposits` `{amount}` | `WalletDepositMoneyAction`, under the keyed lock `wallet:{id}` |
| `POST /wallets/transfers` `{fromWalletId, toWalletId, amount}` | `InitiateTransferAction` - step one. 202, with the transfer id and the debited source |
| `POST /wallets/{id}/close` | `WalletCloseAction` |
| `GET /wallets/{id}` | the wallet, or 404 |

## The saga

A transfer is three units of work, each its own transaction, chained by the events they commit:

1. `InitiateTransferAction` debits the source and emits `TransferInitiatedEvent`. The endpoint
   answers once this has committed.
2. `TransferInitiatedEventHandler` runs `CompleteTransferAction`: it credits the destination
   (`TransferCompletedEvent`) - or, when the destination is missing, closed or in another
   currency, marks the transfer failed on the source (`TransferFailedEvent`).
3. `TransferFailedEventHandler` runs `RefundTransferAction`, which credits the source back
   (`TransferRefundedEvent`). Forward-only compensation, not a rollback: the debit committed long
   ago.

Delivery is at least once, so steps two and three each write a `TransferStep` marker in their
own transaction; a replayed event finds it and does nothing, and the unique key on
`(transfer_id, step)` stops two deliveries racing past that check.

## What is async, and what is not

The requests run on `ekbatan.aio`. Steps two and three run in the handlers, `async def`, on
dispatch's handler loop, with an executor and a registry of their own over `handlersConfig` - an
async pool belongs to the loop it is used on. What polls stays on threads.

## How the schema gets there

`schema.py` is generated, not written: `make` runs `python -m ekbatan.schemagen`, which starts a
PostgreSQL container, applies the migrations in `migrations/` to it, and reflects the result - so the
Python and the database cannot drift. The module records a fingerprint of the migrations it came
from and checks it on import, so a stale one refuses to load rather than quietly disagreeing with
the database.

Two forced types in `pyproject.toml`. Every `CHAR(3)` column is a currency code, whatever it is
called, and reads as the app's own `Currency` - the wallet's `java.util.Currency`, which Python
does not have. `CurrencyConverter`, in `converters.py`, turns the text into a `Currency` and back,
so the repository never sees the text. And `transfer_steps.step`, picked by its name,
reads as the `TransferStepName` enum. Action parameters and events keep the plain code,
`"currency": "EUR"`, because they are written to the outbox as JSON for other services to read.

## Its test

```
make
```

Against PostgreSQL in Testcontainers, it asks what the Java example's test asks: a transfer
credits the destination through the event handler, and a replayed step two credits nothing more;
a transfer to a closed wallet, or to one that does not exist, refunds the source, and a replayed
refund refunds nothing more; and the driver settings in `ekbatan.yaml` reach the database. Two
checks are its own: no object of the service shows the database password, and the migration files
pass the checks a deploy runs.
