"""Area N2: generated builders - the corners round 1 (area G) did not run.
Twin: BuilderProbe.java (compiled with the real ekbatan-annotation-processor).
Run from the repo root: PYTHONPATH=parity-probes/round2-N2 uv run --no-sync python parity-probes/round2-N2/builder_probe.py
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from ekbatan.domain import GenericState
from ekbatan.ids import Id

from n2pkg import builders, generic_builders, sub_builders
from n2pkg.generic_model import Box
from n2pkg.model import HTTPRequest, Wallet
from n2pkg.sub_model import SavingsWallet


def p(key: str, value: object) -> None:
    print(f"PROBE {key}: {value}")


def attempt(fn):
    try:
        return f"OK {fn()}"
    except Exception as e:  # noqa: BLE001
        return f"ERR {type(e).__name__}: {str(e).splitlines()[0][:150]}"


ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
OWNER = UUID("0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b")
T0 = datetime(2025, 1, 1, tzinfo=UTC)


def full():
    return (
        builders.wallet()
        .id(Id.of(ID))
        .state(GenericState.ACTIVE)
        .created_date(T0)
        .with_initial_version()
        .owner_id(OWNER)
        .balance(Decimal("1.00"))
    )


p("builder.factoryName", builders.wallet.__name__)
p("builder.factoryNameAcronym", builders.h_t_t_p_request.__name__)
p("builder.getterAfterSet", attempt(lambda: builders.wallet().balance(Decimal("1.00")).balance()))
p("builder.factoryFromSource", attempt(lambda: builders.wallet(full().build()).build().balance))
p("builder.derivedFieldSetter", attempt(lambda: full().label("x").build().label))
p("builder.derivedFieldBuild", attempt(lambda: full().build().label))
p("builder.copyModelWithDerivedField", attempt(lambda: full().build().copy().build().label))
p("builder.wrongTypeAtRuntime", attempt(lambda: type(full().balance("oops").build().balance).__name__))
p("builder.unknownSetter", attempt(lambda: full().no_such_field(1)))
p("builder.generic.build", attempt(lambda: generic_builders.box().id(Id.of(ID)).state(GenericState.ACTIVE)
                                   .created_date(T0).with_initial_version().content("hello").build().content))
p("builder.generic.builderIsGeneric", hasattr(generic_builders.BoxBuilder, "__type_params__") and bool(generic_builders.BoxBuilder.__type_params__))
sw = (sub_builders.savings_wallet().id(Id.of(ID)).state(GenericState.ACTIVE).created_date(T0)
      .with_initial_version().owner_id(OWNER).balance(Decimal("1.00")).rate(3))
p("builder.subclass.generated", attempt(lambda: type(sw.build()).__name__))
p("builder.subclass.copyBuilderType", attempt(lambda: type(sw.build().copy()).__name__))
p("builder.subclass.copyBuild", attempt(lambda: type(sw.build().copy().build()).__name__))
p("builder.builderClassFinal", getattr(builders.WalletBuilder, "__final__", False))
p("builder.builderConstructorPublic", attempt(lambda: type(builders.WalletBuilder()).__name__))
