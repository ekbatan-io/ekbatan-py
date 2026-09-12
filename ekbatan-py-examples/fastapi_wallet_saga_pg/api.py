"""The HTTP layer, and the one endpoint a saga changes the shape of.

`POST /wallets/{id}/transfers` returns **202 Accepted**, not 200, and that is the whole point.
The transfer is not done when the response is written - step one has committed, the money has
left the source, and the rest happens as events are delivered. A 200 would be a lie about a
thing that is still in flight.

So the reply carries a `transfer_id` and a status endpoint to poll. That is what a saga costs a
client, and pretending otherwise is how people end up surprised by it in production.

Java's `spring-boot-wallet-saga-gradle-pg` adds the same endpoint to the same wallet controller.
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

from ekbatan.errors import EntityNotFoundError, StaleRecordError, ValidationError

from .actions import (
    InitiateTransferAction,
    OpenWalletAction,
    OpenWalletParams,
    TransferParams,
    TransferResult,
)
from .app import SagaService, build
from .model import StepName, Wallet, WalletId

MONEY = Decimal("0.01")


class OpenWalletRequest(BaseModel):
    owner_id: UUID
    currency: str = Field(min_length=3, max_length=3)
    opening_balance: Decimal = Field(ge=0)


class TransferRequest(BaseModel):
    to_wallet_id: UUID
    amount: Decimal = Field(gt=0)


class WalletResponse(BaseModel):
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
            currency=wallet.currency,
            balance=wallet.balance.quantize(MONEY),
            state=wallet.state.name,
        )


class TransferAccepted(BaseModel):
    """What a client gets back from something that has only started."""

    transfer_id: UUID
    #: The source, as it is *now* - already debited. The destination has not been credited yet.
    source: WalletResponse
    status_url: str


class TransferStatus(BaseModel):
    transfer_id: UUID
    #: Which steps have run. Empty until fan-out delivers step one's event.
    steps: list[str]
    #: Derived from the steps, for a client that does not want to know what a step is.
    state: str


def service_of(request: Request) -> SagaService:
    service: SagaService = request.app.state.service
    return service


Service = Annotated[SagaService, Depends(service_of)]


def create_app(url: str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service = build(url)
        app.state.service = service
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="Wallet transfers", lifespan=lifespan)

    @app.exception_handler(EntityNotFoundError)
    async def _missing(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(ValidationError)
    async def _rejected(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.exception_handler(StaleRecordError)
    async def _lost_a_race(request: Request, error: Exception) -> JSONResponse:
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
        return WalletResponse.of(service.wallets.get(WalletId(wallet_id)))

    @app.post("/wallets/{wallet_id}/transfers", response_model=TransferAccepted, status_code=202)
    def transfer(wallet_id: UUID, body: TransferRequest, service: Service) -> TransferAccepted:
        """**202**, because only step one has committed when this returns.

        The source is debited and `TransferInitiatedEvent` is in the outbox. Whether the
        destination is credited, or the transfer fails and is refunded, is decided later by
        handlers - so the honest answer here is "accepted", with somewhere to look.
        """
        result: TransferResult = service.executor.execute(
            InitiateTransferAction,
            TransferParams(
                from_wallet_id=WalletId(wallet_id),
                to_wallet_id=WalletId(body.to_wallet_id),
                amount=body.amount,
            ),
        )
        return TransferAccepted(
            transfer_id=result.transfer_id,
            source=WalletResponse.of(result.source),
            status_url=f"/transfers/{result.transfer_id}",
        )

    @app.get("/transfers/{transfer_id}", response_model=TransferStatus)
    def transfer_status(transfer_id: UUID, service: Service) -> TransferStatus:
        """Where a saga's state actually lives: the steps that have run.

        There is no saga table and no coordinator to ask - the markers each step writes *are*
        the state, which is what choreography means.
        """
        steps = service.steps.steps_of(transfer_id)
        if StepName.REFUND in steps:
            state = "REFUNDED"
        elif StepName.CREDIT in steps:
            state = "COMPLETED"
        else:
            state = "IN_FLIGHT"
        return TransferStatus(transfer_id=transfer_id, steps=[s.name for s in steps], state=state)

    @app.post("/transfers/deliver", status_code=200)
    def deliver(service: Service) -> dict[str, int]:
        """Runs the pipeline by hand, so this example stays readable.

        In a service, fan-out and dispatch are two distributed jobs and no endpoint like this
        exists - `fastapi_wallet_rest_pg` shows that. Here it is what lets a reader watch the
        saga advance one step at a time.
        """
        return {"delivered": service.deliver_pending_events()}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
