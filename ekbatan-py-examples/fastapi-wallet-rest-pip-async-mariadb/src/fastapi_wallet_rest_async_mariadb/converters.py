"""Converters: a column's plain value on one side, one of the domain's own classes on the other.

`schemagen` wraps these around columns when it writes `schema.py`, because `pyproject.toml`
says so under `[[tool.ekbatan.schemagen.forced-types]]`:

- every `CHAR(3)` column, whatever it is called, goes through `CurrencyConverter` - picked
  by its **type**;
- `alerts.recipient` goes through `EmailAddressConverter` - picked by its **name**.

From then on the repository writes a `Currency` and reads one back, and never sees the text.
There is no SQLAlchemy here. schemagen imports this module to check it, so it must not import
the generated `schema.py`.
"""

from __future__ import annotations

from ekbatan.schema import Converter

from .model import Currency, EmailAddress


class CurrencyConverter(Converter[str, Currency]):
    def from_db(self, value: str) -> Currency:
        return Currency(value)

    def to_db(self, value: Currency) -> str:
        return value.code


class EmailAddressConverter(Converter[str, EmailAddress]):
    def from_db(self, value: str) -> EmailAddress:
        return EmailAddress(value)

    def to_db(self, value: EmailAddress) -> str:
        return value.address
