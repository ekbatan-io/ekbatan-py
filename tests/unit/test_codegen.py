"""Generating typed builders.

The runtime builder works for every model there will ever be. What it cannot do is tell a type
checker that `.balance(...)` takes a Decimal, because its setters are resolved by name and a
checker sees only __getattr__. That is the one thing worth generating, and the only thing this
generates.
"""

from __future__ import annotations

import re
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


def test_a_model_gets_a_ModelBuilder_and_an_entity_a_Builder():
    """An entity has no timestamps and no events, so offering setters for them would be an
    error waiting for someone to call it."""
    source = generate([MODULE])
    assert "class WalletBuilder(ModelBuilder[Wallet, WalletId, WalletState]):" in source
    assert (
        "class NotificationBuilder(EntityBuilder[Notification, NotificationId, GenericState]):"
        in source
    )
    assert "builds = Wallet" in source


def test_only_the_fields_the_model_added_are_declared():
    """id, state, version and the timestamps are typed on the base through its parameters -
    exactly as Java's Model.Builder types them for every generated builder, which is why a
    generated one there declares three methods rather than eight."""
    source = generate([MODULE])
    declared = set(re.findall(r"^    def (\w+)\(self, value", source, re.M))

    assert {"owner_id", "currency", "balance"} <= declared
    assert declared.isdisjoint(
        {"id", "state", "version", "created_date", "updated_date", "events"}
    ), f"these come from the base and should not be re-declared: {declared}"


def test_the_setters_carry_the_field_s_real_type():
    source = generate([MODULE])
    assert "def balance(self, value: Decimal) -> Self:" in source
    assert "def currency(self, value: str) -> Self:" in source


def test_the_base_is_parameterised_so_id_and_state_are_typed_without_being_declared():
    source = generate([MODULE])
    assert "[Wallet, WalletId, WalletState]" in source, (
        "an id resolved to the concrete type, not left as a type variable"
    )


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
    from ekbatan.domain import EntityBuilder

    with pytest.raises(ValidationError, match="does not know what it builds"):
        EntityBuilder()


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
