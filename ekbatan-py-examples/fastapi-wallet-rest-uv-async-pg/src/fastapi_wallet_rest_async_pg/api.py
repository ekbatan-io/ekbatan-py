"""The HTTP layer, and nothing else.

Every endpoint names a unit of work, hands it parameters, and shapes the reply. No transaction,
no flush, no publish - those belong to the executor.

The endpoints are `async def` and await `ekbatan.aio`: nothing here blocks the event loop. JSON
field names are camelCase on the wire - `ownerId`, `initialBalance` - and snake_case in the
code.
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

from ekbatan.ids import ShardedId

from .actions import WalletCloseAction, WalletCreateAction, WalletDepositMoneyAction
from .app import Settings, WalletService, build
from .model import Wallet


class _Json(BaseModel):
    """camelCase names on the wire, as the Java service writes them."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CreateRequest(_Json):
    country_code: str | None = None
    owner_id: UUID
    currency: str
    initial_balance: Decimal


class DepositRequest(_Json):
    amount: Decimal
    recipient: str


class WalletResponse(_Json):
    id: UUID
    shard_group: int
    shard_member: int
    owner_id: UUID
    currency: str
    #: Written as text, "15.0000": a `Decimal` keeps every digit that way, where a JSON number
    #: would go through a float.
    balance: Decimal
    state: str
    version: int

    @classmethod
    def of(cls, wallet: Wallet) -> WalletResponse:
        shard = wallet.id.shard
        return cls(
            id=wallet.id.value,
            shard_group=shard.group,
            shard_member=shard.member,
            owner_id=wallet.owner_id,
            currency=wallet.currency.code,
            balance=wallet.balance,
            state=wallet.state.name,
            version=wallet.version,
        )


def service_of(request: Request) -> WalletService:
    """The wired service, built once at startup and shared by every request."""
    service: WalletService = request.app.state.service
    return service


Service = Annotated[WalletService, Depends(service_of)]


def create_app(settings: Settings | None = None) -> FastAPI:
    """The app, and the service it runs on. A factory, so a test can build one with settings of
    its own, against throwaway databases."""

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

    @app.post("/wallets", response_model=WalletResponse, status_code=201)
    async def create(body: CreateRequest, service: Service) -> WalletResponse:
        wallet = await service.executor.execute(
            WalletCreateAction,
            WalletCreateAction.Params(
                country_code=body.country_code,
                owner_id=body.owner_id,
                currency=body.currency,
                initial_balance=body.initial_balance,
            ),
            principal="rest-user",
        )
        return WalletResponse.of(wallet)

    @app.post("/wallets/{id}/deposits", response_model=WalletResponse)
    async def deposit(id: UUID, body: DepositRequest, service: Service) -> WalletResponse:
        # The lease spans the whole unit of work - `perform` and the commit - so two deposits
        # into one wallet take turns instead of the second losing on the version check.
        async with service.lock_provider.acquire(f"wallet:{id}", timedelta(seconds=10)):
            updated = await service.executor.execute(
                WalletDepositMoneyAction,
                WalletDepositMoneyAction.Params(
                    wallet_id=ShardedId[Wallet].of(id), amount=body.amount, recipient=body.recipient
                ),
                principal="rest-user",
            )
            return WalletResponse.of(updated)

    @app.post("/wallets/{id}/close", response_model=WalletResponse)
    async def close(id: UUID, service: Service) -> WalletResponse:
        closed = await service.executor.execute(
            WalletCloseAction,
            WalletCloseAction.Params(wallet_id=ShardedId[Wallet].of(id)),
            principal="rest-user",
        )
        return WalletResponse.of(closed)

    @app.get("/wallets/{id}", response_model=WalletResponse)
    async def get(id: UUID, service: Service) -> WalletResponse:
        wallet = await service.wallet_repository.find_by_id(id)
        if wallet is None:
            raise HTTPException(status_code=404)
        return WalletResponse.of(wallet)

    return app
