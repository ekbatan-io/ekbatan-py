"""The HTTP layer, and nothing else.

A transfer is answered with 202 once its first step has committed: the source is debited, and
the rest of the saga runs in the background. The endpoints are plain `def`: the framework is
synchronous, so FastAPI runs each on a worker thread. JSON field names are camelCase on the
wire and snake_case in the code.
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
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from ekbatan.ids import Id

from .actions import (
    InitiateTransferAction,
    WalletCloseAction,
    WalletCreateAction,
    WalletDepositMoneyAction,
)
from .app import Settings, WalletService, build
from .model import Wallet


class _Json(BaseModel):
    """camelCase names on the wire, as the Java service writes them."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CreateRequest(_Json):
    owner_id: UUID
    currency: str
    initial_balance: Decimal


class DepositRequest(_Json):
    amount: Decimal


class TransferRequest(_Json):
    from_wallet_id: UUID
    to_wallet_id: UUID
    amount: Decimal


class WalletResponse(_Json):
    id: UUID
    owner_id: UUID
    currency: str
    #: Written as text, "75.0000": a `Decimal` keeps every digit that way, where a JSON number
    #: would go through a float.
    balance: Decimal
    state: str
    version: int

    @classmethod
    def of(cls, wallet: Wallet) -> WalletResponse:
        return cls(
            id=wallet.id.value,
            owner_id=wallet.owner_id,
            currency=wallet.currency.code,
            balance=wallet.balance,
            state=wallet.state.name,
            version=wallet.version,
        )


class TransferResponse(_Json):
    transfer_id: UUID
    from_wallet: WalletResponse


def service_of(request: Request) -> WalletService:
    """The wired service, built once at startup and shared by every request."""
    service: WalletService = request.app.state.service
    return service


Service = Annotated[WalletService, Depends(service_of)]


def create_app(settings: Settings | None = None) -> FastAPI:
    """The app, and the service it runs on. A factory, so a test can build one with settings of
    its own, against a throwaway database."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Built and started on a worker thread: `build` runs the migrations and starting
        # registers the jobs, both sync - and the sync API is refused on the event loop's own
        # thread.
        service = await asyncio.to_thread(build, settings)
        await asyncio.to_thread(service.start)
        app.state.service = service
        try:
            yield
        finally:
            await asyncio.to_thread(service.close)

    app = FastAPI(title="Wallet saga", lifespan=lifespan)

    @app.post("/wallets", response_model=WalletResponse, status_code=201)
    def create(body: CreateRequest, service: Service) -> WalletResponse:
        wallet = service.executor.execute(
            "rest-user",
            WalletCreateAction,
            WalletCreateAction.Params(
                owner_id=body.owner_id, currency=body.currency, initial_balance=body.initial_balance
            ),
        )
        return WalletResponse.of(wallet)

    @app.post("/wallets/{id}/deposits", response_model=WalletResponse)
    def deposit(id: UUID, body: DepositRequest, service: Service) -> WalletResponse:
        with service.lock_provider.acquire(f"wallet:{id}", timedelta(seconds=10)):
            updated = service.executor.execute(
                "rest-user",
                WalletDepositMoneyAction,
                WalletDepositMoneyAction.Params(wallet_id=Id[Wallet].of(id), amount=body.amount),
            )
            return WalletResponse.of(updated)

    @app.post("/wallets/transfers", response_model=TransferResponse, status_code=202)
    def transfer(body: TransferRequest, service: Service) -> TransferResponse:
        result = service.executor.execute(
            "rest-user",
            InitiateTransferAction,
            InitiateTransferAction.Params(
                from_wallet_id=Id[Wallet].of(body.from_wallet_id),
                to_wallet_id=Id[Wallet].of(body.to_wallet_id),
                amount=body.amount,
            ),
        )
        return TransferResponse(
            transfer_id=result.transfer_id, from_wallet=WalletResponse.of(result.source_wallet)
        )

    @app.post("/wallets/{id}/close", response_model=WalletResponse)
    def close(id: UUID, service: Service) -> WalletResponse:
        closed = service.executor.execute(
            "rest-user",
            WalletCloseAction,
            WalletCloseAction.Params(wallet_id=Id[Wallet].of(id)),
        )
        return WalletResponse.of(closed)

    @app.get("/wallets/{id}", response_model=WalletResponse)
    def get(id: UUID, service: Service) -> WalletResponse:
        wallet = service.wallet_repository.find_by_id(id)
        if wallet is None:
            raise HTTPException(status_code=404)
        return WalletResponse.of(wallet)

    return app
