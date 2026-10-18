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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Request
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
    """The app, and the service it runs on. A factory, so a test can build one against a
    throwaway database without touching the environment."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # `build` is sync and runs the migrations: once, at startup, before any request.
        service = build(settings)
        service.start()
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
        """A deposit, under the optimistic lock.

        Two deposits racing on one wallet: the second loses the version check, and the executor
        runs it again on a fresh read - once, by default (`ExecutionConfiguration`). One that
        still loses comes back as a 409, and the caller retries. Nothing is ever lost or written
        twice; under heavy contention on one wallet some callers are asked to retry.
        """
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
        """What the event handler wrote, on its own thread, without anybody asking it to."""
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
