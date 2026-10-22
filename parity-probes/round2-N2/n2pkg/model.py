"""Area N2 codegen probe models: the twins of BuilderProbe.java's classes."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal
from uuid import UUID

from ekbatan.domain import GenericState, Model
from ekbatan.ids import Id

from .builders import HTTPRequestBuilder, WalletBuilder


@dataclass(frozen=True, kw_only=True)
class Wallet(Model[Id["Wallet"], GenericState, WalletBuilder]):
    owner_id: UUID
    balance: Decimal
    memo: str | None = None
    kind: Literal["personal", "business"] = "personal"
    limits: tuple[int, ...] = ()
    #: Java's derived field: computed, never passed in (Java: assigned in the constructor).
    label: str = field(init=False, default="wallet")

    def validate(self) -> None:
        pass  # no rules beyond its types


@dataclass(frozen=True, kw_only=True)
class HTTPRequest(Model[Id["HTTPRequest"], GenericState, HTTPRequestBuilder]):
    path: str

    def validate(self) -> None:
        pass  # no rules beyond its types
