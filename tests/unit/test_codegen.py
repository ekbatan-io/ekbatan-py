"""Generating typed builders.

The runtime builder works for every model there will ever be. What it cannot do is tell a type
checker that `.balance(...)` takes a Decimal, because its setters are resolved by name and a
checker sees only __getattr__. That is the one thing worth generating, and the only thing this
generates.
"""

from __future__ import annotations

import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from ekbatan.codegen import generate, models_in
from ekbatan.errors import ValidationError

MODULE = "fixtures.domain"


def test_it_finds_the_models_a_module_defines():
    found = {model.__name__ for model in models_in([MODULE])}
    assert {"Wallet", "Notification"} <= found


def test_it_ignores_what_a_module_merely_imports():
    """Generating a builder for something another module owns puts two of them in the
    codebase, and the second goes stale unnoticed."""
    for model in models_in([MODULE]):
        assert model.__module__ == MODULE


def test_the_generated_source_declares_a_builder_per_model():
    source = generate([MODULE])
    assert "class WalletBuilder(Builder[Wallet]):" in source
    assert "class NotificationBuilder(Builder[Notification]):" in source
    assert "builds = Wallet" in source


def test_the_setters_carry_the_field_s_real_type():
    source = generate([MODULE])
    assert "def balance(self, value: Decimal) -> WalletBuilder:" in source
    assert "def currency(self, value: str) -> WalletBuilder:" in source
    assert "def id(self, value: WalletId) -> WalletBuilder:" in source, (
        "a type variable resolved to the concrete id type, not left as ID"
    )
    assert "def state(self, value: WalletState) -> WalletBuilder:" in source
    assert "def created_date(self, value: datetime | None) -> WalletBuilder:" in source, (
        "an optional stays optional"
    )


def test_events_are_left_to_the_base():
    source = generate([MODULE])
    assert "def events(self" not in source, "with_event and events() are already typed there"


def test_it_emits_a_factory_named_after_the_model():
    source = generate([MODULE])
    assert "def wallet(source: Wallet | None = None) -> WalletBuilder:" in source


def test_imports_are_relative_when_the_output_lives_in_the_same_package():
    """Absolute ones make a checker see the same file under two module names and give up."""
    assert "from .domain import" in generate([MODULE], package="fixtures")
    assert f"from {MODULE} import" in generate([MODULE])


def test_the_output_is_valid_python_that_actually_builds_a_model(tmp_path: Path):
    source = generate([MODULE], package="fixtures")
    namespace: dict[str, object] = {}
    exec(
        compile(source.replace("from .domain import", f"from {MODULE} import"), "<gen>", "exec"),
        namespace,
    )

    from fixtures.domain import Wallet, WalletId, WalletState

    built = (
        namespace["wallet"]()  # type: ignore[operator]
        .id(WalletId.random())
        .state(WalletState.OPENED)
        .owner_id(UUID(int=1))
        .currency("EUR")
        .balance(Decimal("10.00"))
        .with_initial_version()
        .build()
    )
    assert isinstance(built, Wallet)
    assert built.balance == Decimal("10.00")


def test_a_generated_builder_still_refuses_an_unknown_field_at_runtime():
    """A checker will not catch a typo - __getattr__ is inherited - so the runtime must."""
    source = generate([MODULE], package="fixtures")
    namespace: dict[str, object] = {}
    exec(
        compile(source.replace("from .domain import", f"from {MODULE} import"), "<gen>", "exec"),
        namespace,
    )

    with pytest.raises(AttributeError, match="no field 'nonsense'"):
        namespace["wallet"]().nonsense(1)  # type: ignore[operator]


def test_a_module_with_no_models_is_an_error_rather_than_an_empty_file():
    with pytest.raises(SystemExit, match="no models or entities"):
        generate(["ekbatan.clock"])


def test_a_builder_that_does_not_know_what_it_builds_says_so():
    from ekbatan.domain import Builder

    with pytest.raises(ValidationError, match="does not know what it builds"):
        Builder()


def test_the_command_line_writes_a_file(tmp_path: Path):
    out = tmp_path / "builders.py"
    result = subprocess.run(
        [sys.executable, "-m", "ekbatan.codegen", MODULE, "--out", str(out)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.returncode == 0, result.stderr
    assert out.exists()
    assert "class WalletBuilder" in out.read_text()
