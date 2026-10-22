"""The HTTP layer, over two databases instead of one.

Read this next to `fastapi_wallet_rest_pg/api.py`: the endpoints are the same shape, and the
only line anywhere that knows there is more than one database is the country code on
`POST /wallets`. Everything after that is routed by the id.

That is the claim this example exists to make, and an HTTP layer is where it is easiest to check
- because a client hands you an id and nothing else, and the right row still comes back.
"""

from __future__ import annotations

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

from ekbatan.errors import CrossShardError, EntityNotFoundError, StaleRecordError, ValidationError
from ekbatan.ids import ShardedId

from .actions import (
    DepositMoneyAction,
    DepositParams,
    OpenWalletAction,
    OpenWalletParams,
    TransferParams,
    TransferWithinShardAction,
)
from .app import ShardedWallets, build
from .model import Wallet

MONEY = Decimal("0.01")

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
    #: The only routing decision in the application. Everything downstream reads the coordinate
    #: off the id instead.
    country: str = "GL"


class DepositRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    recipient: str


class TransferRequest(BaseModel):
    to_wallet_id: UUID
    amount: Decimal = Field(gt=0)


class WalletResponse(BaseModel):
    id: UUID
    owner_id: UUID
    currency: str
    balance: Decimal
    state: str
    #: Which database this wallet is on, read back out of its own id. Exposed because it is the
    #: whole point; a real service would have no reason to tell a client.
    shard: str

    @classmethod
    def of(cls, wallet: Wallet) -> WalletResponse:
        return cls(
            id=wallet.id.value,
            owner_id=wallet.owner_id,
            currency=wallet.currency.code,
            balance=wallet.balance.quantize(MONEY),
            state=wallet.state.name,
            shard=str(wallet.id.shard),
        )


class AlertResponse(BaseModel):
    id: UUID
    wallet_id: UUID
    recipient: str
    message: str


def service_of(request: Request) -> ShardedWallets:
    service: ShardedWallets = request.app.state.service
    return service


Service = Annotated[ShardedWallets, Depends(service_of)]


def create_app() -> FastAPI:
    """The app on the layout in `ekbatan.yaml`; its placeholders are read from the process
    environment."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service = build()
        service.start()
        app.state.service = service
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="Sharded wallet", lifespan=lifespan)

    @app.exception_handler(EntityNotFoundError)
    async def _missing(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(ValidationError)
    async def _rejected(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.exception_handler(StaleRecordError)
    async def _lost_a_race(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "the wallet changed; retry"})

    @app.exception_handler(PoolTimeoutError)
    async def _too_busy(request: Request, error: Exception) -> JSONResponse:
        # Waited `pool_timeout` for a database connection and got none: the service is past
        # what its pool can serve. A 503 with Retry-After tells the caller to come back.
        return JSONResponse(
            status_code=503, content={"detail": "too busy; retry"}, headers={"Retry-After": "1"}
        )

    @app.exception_handler(CrossShardError)
    async def _two_databases(request: Request, error: Exception) -> JSONResponse:
        """422, not 500. The framework refused this rather than failing at it.

        A transfer between shards is a request this service cannot honour as one unit of work -
        there is no two-phase commit - so it is the caller's request that is wrong, not the
        server. The reply says which two databases disagreed, because that is the thing a
        caller can act on.
        """
        return JSONResponse(
            status_code=422,
            content={
                "detail": "those wallets are on different databases; there is no cross-shard "
                "transaction. Move money between shards with a saga - see the "
                "fastapi_wallet_saga_pg example",
                "shards": str(error),
            },
        )

    @app.post("/wallets", response_model=WalletResponse, status_code=201)
    def open_wallet(body: OpenWalletRequest, service: Service) -> WalletResponse:
        """The one endpoint that chooses a database, from something the business knows."""
        return WalletResponse.of(
            service.executor.execute(
                OpenWalletAction,
                OpenWalletParams(
                    owner_id=body.owner_id,
                    currency=body.currency,
                    opening_balance=body.opening_balance,
                    country=body.country,
                ),
            )
        )

    @app.get("/wallets/{wallet_id}", response_model=WalletResponse)
    def read_wallet(wallet_id: UUID, service: Service) -> WalletResponse:
        """No shard argument, no lookup, no hint. The id says where the row is."""
        return WalletResponse.of(service.wallets.get(ShardedId[Wallet](wallet_id)))

    @app.post("/wallets/{wallet_id}/deposits", response_model=WalletResponse)
    def deposit(wallet_id: UUID, body: DepositRequest, service: Service) -> WalletResponse:
        """A deposit, serialised per wallet by a keyed lock taken around the unit of work.

        The lease spans both `perform()` and the commit, on whichever shard the wallet is on.
        Another request on the same wallet waits its turn - and then succeeds, rather than
        losing the version check.
        """
        # `try_acquire`, not `acquire`: a request must not wait for ever. None is contention,
        # not a failure - a 503 with Retry-After.
        lease = service.locks.try_acquire(f"wallet:{wallet_id}", LOCK_WAIT, LOCK_HOLD)
        if lease is None:
            raise HTTPException(
                status_code=503,
                detail="that wallet is busy; retry",
                headers={"Retry-After": "1"},
            )
        with lease:
            return WalletResponse.of(
                service.executor.execute(
                    DepositMoneyAction,
                    DepositParams(
                        wallet_id=ShardedId[Wallet](wallet_id),
                        amount=body.amount,
                        recipient=body.recipient,
                    ),
                )
            )

    @app.post("/wallets/{wallet_id}/transfers", response_model=list[WalletResponse])
    def transfer(wallet_id: UUID, body: TransferRequest, service: Service) -> list[WalletResponse]:
        """Two wallets, one transaction - and only when both are on the same database.

        Reaching a second shard is refused (422 above) rather than committed in halves.
        """
        source, destination = service.executor.execute(
            TransferWithinShardAction,
            TransferParams(
                from_wallet_id=ShardedId[Wallet](wallet_id),
                to_wallet_id=ShardedId[Wallet](body.to_wallet_id),
                amount=body.amount,
            ),
        )
        return [WalletResponse.of(source), WalletResponse.of(destination)]

    @app.get("/shards", response_model=dict[str, Decimal])
    def totals(service: Service) -> dict[str, Decimal]:
        """What each database holds - one query per shard, which is what a question without an
        id costs."""
        return {str(shard): total for shard, total in service.wallets.total_per_shard().items()}

    @app.get("/wallets/{wallet_id}/alerts", response_model=list[AlertResponse])
    def read_alerts(wallet_id: UUID, service: Service) -> list[AlertResponse]:
        """What the in-process handler wrote after a deposit, without anybody asking it to - on
        the default shard, whichever shard the wallet is on."""
        return [
            AlertResponse(
                id=alert.id.value,
                wallet_id=alert.wallet_id.value,
                recipient=alert.recipient.address,
                message=alert.message,
            )
            for alert in service.alerts.find_for_wallet(ShardedId[Wallet](wallet_id))
        ]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
