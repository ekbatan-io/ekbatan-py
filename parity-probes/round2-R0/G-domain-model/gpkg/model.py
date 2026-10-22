"""Area G codegen probe model: the twin of CodegenParityProbe.Wallet (Java)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ekbatan.domain import GenericState, Model
from ekbatan.ids import Id

from .builders import WalletBuilder


@dataclass(frozen=True, kw_only=True)
class Wallet(Model[Id["Wallet"], GenericState, WalletBuilder]):
    owner_id: UUID
    balance: Decimal
    #: Java's `String memo`, which its constructor leaves unchecked.
    memo: str

    def validate(self) -> None:
        pass  # no rules beyond its types
