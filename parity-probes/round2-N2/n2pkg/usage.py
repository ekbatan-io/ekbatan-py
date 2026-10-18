"""Area N2: what mypy says about calls on the generated builders. Each line is a call a
Java developer would expect to compile (or not). Run: mypy n2pkg/usage.py."""

from __future__ import annotations

from decimal import Decimal

from .builders import wallet
from .generic_builders import box

wallet().kind("personal")  # Literal field: valid value
wallet().limits((1, 2, 3))  # tuple[int, ...] field: three ints
wallet().balance("oops")  # wrong type: should be refused
wallet().no_such_field(1)  # setter that does not exist (a stale builder): should be refused
box().content(Decimal("1"))  # generic field
