"""An event says which model it is about on its class line, and forgetting it is caught early.

Java cannot compile an event that names no model: `super(modelId, Wallet.class)` is the only
constructor. Python has no compile step, so the nearest two places are here. Strict mypy reports a
bare `ModelEvent` base, because the type parameter has no default to fall back on; and the class
is refused where it is defined - at import, before the program has done anything - rather than at
its first event, which may be in production.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from ekbatan.domain import ModelEvent


def test_an_event_that_names_no_model_is_refused_where_it_is_defined() -> None:
    with pytest.raises(
        TypeError,
        match=r"model class cannot be None: Forgot does not say which model it is about; "
        r'declare it as class Forgot\(ModelEvent\["Wallet"\]\)',
    ):

        @dataclass(frozen=True, kw_only=True)
        class Forgot(ModelEvent):  # type: ignore[type-arg]
            amount: Decimal


def test_a_subclass_of_a_named_event_and_a_generic_base_of_its_own_are_let_through() -> None:
    """A subclass inherits the model its parent named; a generic base names its model in its own
    subclasses, so it is not refused for naming none itself."""

    @dataclass(frozen=True, kw_only=True)
    class Deposited(ModelEvent["Wallet"]):
        amount: Decimal

    @dataclass(frozen=True, kw_only=True)
    class LargeDeposited(Deposited):
        pass

    @dataclass(frozen=True, kw_only=True)
    class Audited[M](ModelEvent[M]):
        by: str

    assert LargeDeposited(model_id="w-1", amount=Decimal("1")).model_name == "Wallet"
    assert Audited.__model_name__ is None


def test_strict_mypy_reports_an_event_that_names_no_model(tmp_path: Path) -> None:
    from mypy import api

    events = tmp_path / "events.py"
    events.write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations

            from dataclasses import dataclass
            from decimal import Decimal

            from ekbatan.domain import ModelEvent


            @dataclass(frozen=True, kw_only=True)
            class Forgot(ModelEvent):
                amount: Decimal
            """
        )
    )
    stdout, stderr, _ = api.run(
        [
            "--strict",
            "--show-error-codes",
            "--no-error-summary",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            "--config-file",
            "",
            str(events),
        ]
    )
    assert not stderr, stderr
    assert 'events.py:10: error: Missing type arguments for generic type "ModelEvent"' in stdout, (
        stdout
    )
    assert "[type-arg]" in stdout, stdout
