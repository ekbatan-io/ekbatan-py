"""The HTTP layer: three endpoints over the actions, and nothing else.

The endpoints are `async def` and await `ekbatan.aio`: nothing here blocks the event loop. JSON
names are camelCase on the wire.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from ekbatan.ids import Id

from .actions import WalletCreateAction, WalletDepositMoneyAction
from .app import WalletService, build
from .model import Wallet


class _Json(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CreateRequest(_Json):
    owner_id: UUID
    currency: str
    initial_balance: Decimal


class DepositRequest(_Json):
    amount: Decimal
    recipient: str


def service(request: Request) -> WalletService:
    wired: WalletService = request.app.state.service
    return wired


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Built and started on a worker thread: the migrations and the jobs' registration are
        # sync, and the sync API is refused on the event loop's own thread.
        app.state.service = await asyncio.to_thread(build)
        await asyncio.to_thread(app.state.service.start)
        try:
            yield
        finally:
            await app.state.service.aclose()

    app = FastAPI(title="Wallet", lifespan=lifespan)

    @app.post("/wallets", status_code=201)
    async def create(body: CreateRequest, request: Request) -> dict[str, str]:
        wallet = await service(request).executor.execute(
            WalletCreateAction,
            WalletCreateAction.Params(
                owner_id=body.owner_id, currency=body.currency, initial_balance=body.initial_balance
            ),
            principal="rest-user",
        )
        return {"id": str(wallet.id.value), "balance": str(wallet.balance)}

    @app.post("/wallets/{id}/deposits")
    async def deposit(id: UUID, body: DepositRequest, request: Request) -> dict[str, str]:
        wallet = await service(request).executor.execute(
            WalletDepositMoneyAction,
            WalletDepositMoneyAction.Params(
                wallet_id=Id[Wallet].of(id), amount=body.amount, recipient=body.recipient
            ),
            principal="rest-user",
        )
        return {"id": str(wallet.id.value), "balance": str(wallet.balance)}

    @app.get("/wallets/{id}/notifications")
    async def notifications(id: UUID, request: Request) -> list[dict[str, Any]]:
        """What the event handler wrote, without the deposit asking it to."""
        found = await service(request).notification_repository.find_all_by_wallet_id(id)
        return [
            {"kind": n.kind.name, "recipient": n.recipient, "message": n.message} for n in found
        ]

    return app
