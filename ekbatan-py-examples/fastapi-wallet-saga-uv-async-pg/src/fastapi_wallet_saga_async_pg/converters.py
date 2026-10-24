"""Converters: a column's plain value on one side, one of the domain's own classes on the other.

`schemagen` wraps these around columns when it writes `schema.py`, because `pyproject.toml` says
so under `[[tool.ekbatan.schemagen.forced-types]]`:

- every `CHAR(3)` column, whatever it is called, goes through `CurrencyConverter` - picked by
  its **type**;
- `transfer_steps.step` goes through `TransferStepNameConverter` - picked by its **name**.

There is no SQLAlchemy here. schemagen imports this module to check it, so it must not import
the generated `schema.py`.
"""

from __future__ import annotations

from ekbatan.schema import Converter

from .model import Currency, TransferStepName


class CurrencyConverter(Converter[str, Currency]):
    def from_db(self, value: str) -> Currency:
        return Currency(value)

    def to_db(self, value: Currency) -> str:
        return value.code


class TransferStepNameConverter(Converter[str, TransferStepName]):
    """By the member's name, `COMPLETE_TRANSFER`, as every enum in these tables is stored."""

    def from_db(self, value: str) -> TransferStepName:
        return TransferStepName[value]

    def to_db(self, value: TransferStepName) -> str:
        return value.name
