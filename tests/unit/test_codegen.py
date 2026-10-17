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
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from ekbatan.codegen import generate, models_in
from ekbatan.errors import ValidationError
from ekbatan.ids import ShardedId

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
    assert (
        'class WalletBuilder(ModelBuilder["Wallet", "ShardedId[Wallet]", "WalletState"]):' in source
    )
    entity = 'class NotificationBuilder(EntityBuilder["Notification", "Id[Notification]", '
    assert entity + '"GenericState"]):' in source
    assert 'builds_ref = "fixtures.domain:Wallet"' in source, (
        "the builder can find its model even when imported before the model module is"
    )


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
    assert '["Wallet", "ShardedId[Wallet]", "WalletState"]' in source, (
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

    from fixtures.domain import Wallet, WalletState

    built = (
        namespace["wallet"]()  # type: ignore[operator]
        .id(ShardedId[Wallet].random())
        .state(WalletState.OPENED)
        .owner_id(UUID(int=1))
        .currency("EUR")
        .balance(Decimal("10.00"))
        .with_initial_version()
        .created_date(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
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


# -- the id type written on the class line ------------------------------------------------------


def test_the_id_type_on_a_class_line_is_resolved_to_the_model_it_names():
    """`class Wallet(Model[ShardedId["Wallet"], ...])` has to quote the name - the class does not
    exist yet - and Python never evaluates a type argument on a class line. Until this was
    resolved the generator rendered the leftover reference as `Any`, and wrote
    `ModelBuilder["Wallet", "ShardedId[Any]", ...]`: a builder whose id a checker accepted for
    every model, which is the one mistake Java's spelling of an id exists to catch."""
    from ekbatan.ids import Id
    from ekbatan.repository import _concrete_types
    from fixtures.domain import Notification, Wallet

    assert _concrete_types(Wallet)["id"] == ShardedId[Wallet]
    assert _concrete_types(Notification)["id"] == Id[Notification]


def test_a_name_that_cannot_be_looked_up_is_left_as_it_was():
    """A model defined inside a function has no module-level name to resolve against. It keeps
    the reference, which the repository never needed resolved: it rebuilds an id from the
    class, not from the argument."""
    import typing
    from dataclasses import dataclass
    from enum import Enum

    from ekbatan.domain import Model
    from ekbatan.repository import _concrete_types

    class LocalState(Enum):
        OPEN = "OPEN"

    @dataclass(frozen=True, kw_only=True)
    class Local(Model[ShardedId["Local"], LocalState]):
        pass

    hint = _concrete_types(Local)["id"]
    assert typing.get_origin(hint) is ShardedId
    assert typing.get_args(hint) == (typing.ForwardRef("Local"),)


# -- a new project: src/ layout, and no builders.py yet -----------------------------------------

NEW_MODEL = """
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from ekbatan import Model, ShardedId

from .builders import WalletBuilder


class WalletState(Enum):
    OPENED = "OPENED"


@dataclass(frozen=True, kw_only=True)
class Wallet(Model[ShardedId["Wallet"], WalletState, WalletBuilder]):
    balance: Decimal
"""


def a_new_project(root: Path) -> Path:
    """What `uv init wallet` leaves, plus a model that imports the builders it does not have."""
    package = root / "src" / "wallet"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "model.py").write_text(NEW_MODEL)
    return package


def run_codegen(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ekbatan.codegen", *args],
        capture_output=True,
        text=True,
        cwd=root,
        env={**__import__("os").environ, "PYTHONPATH": str(root / "src")},
    )


def test_the_first_run_works_before_builders_py_exists(tmp_path: Path):
    """The model imports `builders.py`, the very file this writes. It used to fail on that import
    - so a new project could never generate its first builders (Dracula bug 6)."""
    package = a_new_project(tmp_path)

    result = run_codegen(tmp_path, "wallet.model", "--out", "src/wallet/builders.py")

    assert result.returncode == 0, result.stderr
    assert "class WalletBuilder" in (package / "builders.py").read_text()
    imported = run_codegen(tmp_path, "wallet.model")  # and now the model imports for real
    assert imported.returncode == 0, imported.stderr


def test_under_a_src_layout_the_package_comes_from_the_module_not_the_folder(tmp_path: Path):
    """`src/wallet/builders.py` read from the current folder is `src.wallet`; it is `wallet`."""
    package = a_new_project(tmp_path)

    run_codegen(tmp_path, "wallet.model", "--out", "src/wallet/builders.py")

    source = (package / "builders.py").read_text()
    assert "from .model import" in source
    assert 'builds_ref = "wallet.model:Wallet"' in source
    assert "src.wallet" not in source


def test_the_stand_in_is_gone_once_the_file_exists(tmp_path: Path):
    """A second run imports the real builders.py, as a regeneration always did."""
    package = a_new_project(tmp_path)
    run_codegen(tmp_path, "wallet.model", "--out", "src/wallet/builders.py")
    first = (package / "builders.py").read_text()

    again = run_codegen(tmp_path, "wallet.model", "--out", "src/wallet/builders.py")

    assert again.returncode == 0, again.stderr
    assert (package / "builders.py").read_text() == first
