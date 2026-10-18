"""Area N2: a model extending another model - Java's @AutoBuilder refuses this at compile time."""

from __future__ import annotations

from dataclasses import dataclass

from .model import Wallet


@dataclass(frozen=True, kw_only=True)
class SavingsWallet(Wallet):
    rate: int = 0
