"""The HTTP layer, and nothing else.

Every endpoint here is four lines: read the request, name a unit of work, hand it parameters,
shape the reply. No transaction, no flush, no publish - those belong to the executor, and an
HTTP layer that mentioned them would be one that could get them wrong.

The DTOs are deliberately not the domain model. `Wallet` carries a version, timestamps and an
event list; a client needs none of those and should not be broken by a change to any of them.
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
            # Quantized, and not only for tidiness. What an action *returns* is the model it
            # staged, at whatever scale the caller handed in; what a later read returns is the
            # column's scale, which here is four places. So `POST /wallets` would answer
            # "100.00" and `GET /wallets/{id}` "100.0000" for the same untouched wallet - true,
            # harmless, and exactly the sort of thing a client writes a bug report about.
            #
            # Presentation scale is the API's business rather than the domain's, so it is
            # settled here, once, instead of by making the write path do a read it does not
            # need. Two places because these are euros; a currency table would decide it for a
            # service handling more than one.
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
    `from __future__ import annotations` produces against the *module* namespace, so a
    `Annotated[...]` alias defined inside a function is invisible to it and the parameter is
    read as an unfilled query parameter instead. The symptom is a 422 naming a field nobody
    declared.
    """
    service: WalletService = request.app.state.service
    return service


Service = Annotated[WalletService, Depends(service_of)]


def create_app(settings: Settings | None = None) -> FastAPI:
    """The app, and the service it runs on.

    A factory rather than a module-level `app = FastAPI()` so a test can build one against a
    throwaway database without touching the environment.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service = build(settings)
        service.start()
        app.state.service = service
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="Wallet", lifespan=lifespan)

    # -- the errors a caller should see, and the ones they should not -------------------------
    #
    # Mapped once, here, rather than caught in every endpoint. The framework's error kinds
    # exist precisely so a caller can tell a bad request from a lost race from an unreachable
    # database, because the three call for opposite responses - and that distinction is what
    # an HTTP status code is for.

    # A handler *returns* a response; raising from inside one is not caught again and becomes
    # a 500 - which is the opposite of what these exist for.

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

    @app.post("/wallets", response_model=WalletResponse, status_code=201)
    def open_wallet(body: OpenWalletRequest, service: Service) -> WalletResponse:
        return WalletResponse.of(
            service.executor.execute(
                OpenWalletAction,
                OpenWalletParams(
                    owner_id=body.owner_id,
                    currency=body.currency,
                    opening_balance=body.opening_balance,
                ),
            )
        )

    @app.get("/wallets/{wallet_id}", response_model=WalletResponse)
    def read_wallet(wallet_id: UUID, service: Service) -> WalletResponse:
        return WalletResponse.of(service.wallets.get(ShardedId[Wallet](wallet_id)))

    @app.post("/wallets/{wallet_id}/deposits", response_model=WalletResponse)
    def deposit(wallet_id: UUID, body: DepositRequest, service: Service) -> WalletResponse:
        """A deposit, serialised per wallet by a keyed lock.

        The lease spans **both** `perform()` and the executor's commit, which is the whole
        point and the easy thing to get wrong. Optimistic locking alone would be correct here -
        a concurrent deposit would lose the version check and raise - but correct by *failing*.
        Taking the key first means the second caller waits and then succeeds, which is what
        anyone depositing into a busy wallet actually wants.

        Different wallets take different keys and do not contend at all. The `with` block
        releases the lease however the block ends.
        """
        # `try_acquire`, not `acquire`: an HTTP request must not block forever. A lease that
        # cannot be had inside `LOCK_WAIT` comes back as None - which is *contention*, not a
        # failure, and the framework is careful to keep those distinguishable. Here that
        # becomes a 503 with a Retry-After, which is a caller's cue to come back rather than a
        # bug report.
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

    @app.post("/wallets/{wallet_id}/closure", response_model=WalletResponse)
    def close_wallet(wallet_id: UUID, service: Service) -> WalletResponse:
        return WalletResponse.of(
            service.executor.execute(
                CloseWalletAction, WalletIdParams(wallet_id=ShardedId[Wallet](wallet_id))
            )
        )

    @app.get("/wallets/{wallet_id}/alerts", response_model=list[AlertResponse])
    def read_alerts(wallet_id: UUID, service: Service) -> list[AlertResponse]:
        """What the in-process handler wrote, without anybody asking it to.

        A deposit emits an event; fan-out turns it into a delivery; the handler starts its own
        unit of work and writes an alert. None of that is in the deposit endpoint above, which
        is the point - and these rows are how you see it happened.
        """
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
