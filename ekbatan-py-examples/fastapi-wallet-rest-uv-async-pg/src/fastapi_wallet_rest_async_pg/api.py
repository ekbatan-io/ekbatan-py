"""The HTTP layer, and nothing else - every endpoint an `async def` that awaits.

Every endpoint is four lines: read the request, name a unit of work, hand it parameters, shape
the reply. No transaction, no flush, no publish - those belong to the executor.

The endpoints run on the event loop itself, not in a thread pool: the executor and the
repositories are `ekbatan.aio`'s, and every database call is awaited. Nothing here blocks the
loop. A sync call here - the sync executor, a sync repository - would, for every request at once.

The DTOs are deliberately not the domain model. `Wallet` carries a version, timestamps and an
event list; a client needs none of those and should not be broken by a change to any of them.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from ekbatan.errors import EntityNotFoundError, StaleRecordError, ValidationError
from ekbatan.ids import ShardedId

from .actions import (
    CloseWalletAction,
    DepositMoneyAction,
    DepositParams,
    OpenWalletAction,
    OpenWalletParams,
    WalletIdParams,
)
from .app import Settings, WalletService, build
from .model import Wallet

#: How long a deposit may wait for the wallet's lock, and how long it may hold it.
#:
#: `max_hold` is not a timeout on your code - it is the promise that the lock is released even
#: if this process dies holding it. Set it above the worst case for the work inside, and
#: `max_wait` to something a caller is willing to wait rather than be refused.
LOCK_WAIT = timedelta(seconds=5)
LOCK_HOLD = timedelta(seconds=30)


class OpenWalletRequest(BaseModel):
    owner_id: UUID
    currency: str = Field(min_length=3, max_length=3)
    opening_balance: Decimal = Field(ge=0)


class DepositRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    recipient: str


#: Money on the wire, always at two places. See `WalletResponse.of`.
MONEY = Decimal("0.01")


class WalletResponse(BaseModel):
    """What a client gets. Not the model: no version, no events, no timestamps."""

    id: UUID
    owner_id: UUID
    currency: str
    balance: Decimal
    state: str

    @classmethod
    def of(cls, wallet: Wallet) -> WalletResponse:
        return cls(
            id=wallet.id.value,
            owner_id=wallet.owner_id,
            currency=wallet.currency.code,
            # Quantized here, once: what an action returns is at the scale the caller handed
            # in, what a later read returns is the column's (four places).
            balance=wallet.balance.quantize(MONEY),
            state=wallet.state.name,
        )


class AlertResponse(BaseModel):
    id: UUID
    wallet_id: UUID
    recipient: str
    message: str


def service_of(request: Request) -> WalletService:
    """The wired service, built once at startup and shared by every request.

    Module level, not nested inside `create_app`: FastAPI resolves the string annotations that
    `from __future__ import annotations` produces against the *module* namespace.
    """
    service: WalletService = request.app.state.service
    return service


Service = Annotated[WalletService, Depends(service_of)]


def create_app(settings: Settings | None = None) -> FastAPI:
    """The app, and the service it runs on. A factory, so a test can build one with settings
    of its own, against a throwaway database."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Built and started on a worker thread: `build` runs the migrations and starting
        # registers the jobs, both sync and both waiting on the database - and the sync API
        # is refused on the event loop's own thread.
        service = await asyncio.to_thread(build, settings)
        await asyncio.to_thread(service.start)
        app.state.service = service
        try:
            yield
        finally:
            await service.aclose()

    app = FastAPI(title="Wallet", lifespan=lifespan)

    # -- the errors a caller should see, mapped once ----------------------------------------

    @app.exception_handler(EntityNotFoundError)
    async def _missing(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(ValidationError)
    async def _rejected(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.exception_handler(StaleRecordError)
    async def _lost_a_race(request: Request, error: Exception) -> JSONResponse:
        # 409, not 500. The caller did nothing wrong and the right response is to try again.
        return JSONResponse(status_code=409, content={"detail": "the wallet changed; retry"})

    @app.exception_handler(PoolTimeoutError)
    async def _too_busy(request: Request, error: Exception) -> JSONResponse:
        # Waited `pool_timeout` for a database connection and got none: the service is past
        # what its pool can serve. A 503 with Retry-After tells the caller to come back.
        return JSONResponse(
            status_code=503, content={"detail": "too busy; retry"}, headers={"Retry-After": "1"}
        )

    @app.post("/wallets", response_model=WalletResponse, status_code=201)
    async def open_wallet(body: OpenWalletRequest, service: Service) -> WalletResponse:
        return WalletResponse.of(
            await service.executor.execute(
                OpenWalletAction,
                OpenWalletParams(
                    owner_id=body.owner_id,
                    currency=body.currency,
                    opening_balance=body.opening_balance,
                ),
            )
        )

    @app.get("/wallets/{wallet_id}", response_model=WalletResponse)
    async def read_wallet(wallet_id: UUID, service: Service) -> WalletResponse:
        return WalletResponse.of(await service.wallets.get(ShardedId[Wallet](wallet_id)))

    @app.post("/wallets/{wallet_id}/deposits", response_model=WalletResponse)
    async def deposit(wallet_id: UUID, body: DepositRequest, service: Service) -> WalletResponse:
        """A deposit, serialised per wallet by a keyed lock taken around the unit of work.

        The lease spans **both** `perform()` and the executor's commit. Optimistic locking alone
        would be correct here - a concurrent deposit would lose the version check and, past its
        retry, be a 409 - but correct by *failing*. Taking the key first means the second caller
        waits and then succeeds. The holder is this request's task: another request's task
        waits, on the event loop, without blocking it.
        """
        # `try_acquire`, not `acquire`: a request must not wait for ever. None is contention,
        # not a failure - a 503 with Retry-After.
        lease = await service.locks.try_acquire(f"wallet:{wallet_id}", LOCK_WAIT, LOCK_HOLD)
        if lease is None:
            raise HTTPException(
                status_code=503,
                detail="that wallet is busy; retry",
                headers={"Retry-After": "1"},
            )
        async with lease:
            return WalletResponse.of(
                await service.executor.execute(
                    DepositMoneyAction,
                    DepositParams(
                        wallet_id=ShardedId[Wallet](wallet_id),
                        amount=body.amount,
                        recipient=body.recipient,
                    ),
                )
            )

    @app.post("/wallets/{wallet_id}/closure", response_model=WalletResponse)
    async def close_wallet(wallet_id: UUID, service: Service) -> WalletResponse:
        return WalletResponse.of(
            await service.executor.execute(
                CloseWalletAction, WalletIdParams(wallet_id=ShardedId[Wallet](wallet_id))
            )
        )

    @app.get("/wallets/{wallet_id}/alerts", response_model=list[AlertResponse])
    async def read_alerts(wallet_id: UUID, service: Service) -> list[AlertResponse]:
        """What the event handler wrote, after the deposit, without anybody asking it to."""
        return [
            AlertResponse(
                id=alert.id.value,
                wallet_id=alert.wallet_id.value,
                recipient=alert.recipient.address,
                message=alert.message,
            )
            for alert in await service.alerts.find_for_wallet(ShardedId[Wallet](wallet_id))
        ]

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
