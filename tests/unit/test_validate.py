"""`validate()`: the one place for a model's own rules, and it is not optional.

Every concrete `Model` and `Entity` defines `validate(self) -> None`. Ekbatan runs its built-in
checks first - required fields not `None`, `version >= 1`, the events copied, `updated_date`
falling back - and then the model's `validate()`, every time a model is made: `build()`,
`copy()...build()`, the constructor, `evolve`, `without_events`, `next_version`, and so every
`from_record` too. A class with no `validate()` is refused when it is defined, and a type
checker flags it where it is made.

A model's own `__post_init__` cannot switch the built-in checks off by forgetting
`super().__post_init__()`; and calling it does not make anything run twice.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from abc import ABC
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID, uuid4

import pytest

import ekbatan.domain as domain
from ekbatan.domain import Entity, EntityBuilder, GenericState, Model, ModelBuilder, ModelEvent
from ekbatan.errors import ValidationError
from ekbatan.ids import Id, ShardedId
from fixtures.domain import NOW, Wallet


class State(Enum):
    OPEN = "OPEN"
    DELETED = "DELETED"


#: What each model below records as it is checked, in order.
SEEN: list[str] = []


@pytest.fixture(autouse=True)
def _clear() -> None:
    SEEN.clear()


@dataclass(frozen=True, kw_only=True)
class Account(Model[ShardedId["Account"], State]):
    owner_id: UUID
    balance: Decimal

    def validate(self) -> None:
        # What the built-in checks have already done by the time this runs.
        SEEN.append(f"validate updated={self.updated_date is not None} events={type(self.events)}")
        if self.balance < 0:
            raise ValidationError("balance cannot be negative")


@dataclass(frozen=True, kw_only=True)
class Tag(Entity[Id["Tag"]]):
    label: str

    def validate(self) -> None:
        SEEN.append("validate")
        if not self.label.strip():
            raise ValidationError("label cannot be blank")


def an_account(balance: str = "10") -> Account:
    return Account(
        id=ShardedId[Account].random(),
        state=State.OPEN,
        version=1,
        created_date=NOW,
        owner_id=uuid4(),
        balance=Decimal(balance),
    )


# -- a class without validate() is refused where it is defined -------------------------------


def test_a_model_without_validate_is_refused_when_it_is_defined():
    with pytest.raises(TypeError) as refused:

        @dataclass(frozen=True, kw_only=True)
        class Forgotten(Model[ShardedId["Forgotten"], State]):
            name: str

    assert str(refused.value) == (
        "Forgotten must define validate(); write `pass` if it has no rules beyond its types"
    )


def test_an_entity_without_validate_is_refused_when_it_is_defined():
    with pytest.raises(TypeError) as refused:

        @dataclass(frozen=True, kw_only=True)
        class Forgotten(Entity[Id["Forgotten"]]):
            name: str

    assert str(refused.value) == (
        "Forgotten must define validate(); write `pass` if it has no rules beyond its types"
    )


def test_an_abstract_base_of_ones_own_is_let_through_and_its_models_are_checked():
    """A base that real models inherit from says it is abstract by listing `ABC`."""

    @dataclass(frozen=True, kw_only=True)
    class Audited[ID, S: Enum](Model[ID, S], ABC):
        audited_by: str

    @dataclass(frozen=True, kw_only=True)
    class Invoice(Audited[ShardedId["Invoice"], State]):
        total: Decimal

        def validate(self) -> None:
            if self.total < 0:
                raise ValidationError("total cannot be negative")

    invoice = Invoice(
        id=ShardedId[Invoice].random(),
        state=State.OPEN,
        version=1,
        created_date=NOW,
        audited_by="ada",
        total=Decimal(1),
    )
    assert invoice.total == 1
    with pytest.raises(ValidationError, match="total cannot be negative"):
        invoice.evolve(total=Decimal(-1))

    with pytest.raises(TypeError, match="Receipt must define validate"):

        @dataclass(frozen=True, kw_only=True)
        class Receipt(Audited[ShardedId["Receipt"], State]):
            total: Decimal

    with pytest.raises(TypeError, match="Can't instantiate abstract class Audited"):
        Audited(id=1, state=State.OPEN, version=1, created_date=NOW, audited_by="ada")  # type: ignore[abstract]


def test_a_base_with_a_validate_of_its_own_passes_it_down():
    @dataclass(frozen=True, kw_only=True)
    class Base(Model[ShardedId["Base"], State]):
        def validate(self) -> None:
            SEEN.append("base")

    @dataclass(frozen=True, kw_only=True)
    class Derived(Base):
        extra: int

    Derived(id=ShardedId[Derived].random(), state=State.OPEN, version=1, created_date=NOW, extra=1)
    assert SEEN == ["base"]


def test_events_and_builders_are_not_models():
    @dataclass(frozen=True, kw_only=True)
    class Happened(ModelEvent["Account"]):
        amount: Decimal

    class AccountBuilder(ModelBuilder[Account, ShardedId[Account], State]):
        builds = Account

    assert Happened(model_id="1", amount=Decimal(1)).amount == 1
    assert AccountBuilder is not None


# -- validate() runs every time a model is made -----------------------------------------------


def test_validate_runs_on_the_constructor():
    an_account()
    assert len(SEEN) == 1
    with pytest.raises(ValidationError, match="balance cannot be negative"):
        an_account("-1")


def test_validate_runs_on_build_and_copy_build():
    built = (
        ModelBuilder(Account)
        .id(ShardedId[Account].random())
        .state(State.OPEN)
        .version(1)
        .created_date(NOW)
        .owner_id(uuid4())
        .balance(Decimal(5))
        .build()
    )
    assert len(SEEN) == 1
    with pytest.raises(ValidationError, match="balance cannot be negative"):
        ModelBuilder(Account).copy_base(built).balance(Decimal(-5)).build()
    assert len(SEEN) == 2

    wallet = Wallet.open(
        id=ShardedId[Wallet].random(),
        owner_id=uuid4(),
        currency="EUR",
        balance=Decimal(1),
        created_date=NOW,
    )
    with pytest.raises(ValidationError, match="balance cannot be negative"):
        wallet.copy().balance(Decimal(-1)).build()


def test_validate_runs_on_evolve_without_events_and_next_version():
    account = an_account()
    account.evolve(balance=Decimal(2))
    account.without_events()
    account.next_version()
    assert len(SEEN) == 4
    with pytest.raises(ValidationError, match="balance cannot be negative"):
        account.evolve(balance=Decimal(-2))


def test_an_entity_s_validate_runs_too():
    tag = Tag(id=Id[Tag].random(), state=GenericState.ACTIVE, version=1, label="red")
    EntityBuilder(Tag).copy_base(tag).build()
    assert SEEN == ["validate", "validate"]
    with pytest.raises(ValidationError, match="label cannot be blank"):
        tag.evolve(label=" ")


def test_any_exception_from_validate_is_the_caller_s():
    @dataclass(frozen=True, kw_only=True)
    class Picky(Entity[Id["Picky"]]):
        def validate(self) -> None:
            raise LookupError("never")

    with pytest.raises(LookupError, match="never"):
        Picky(id=Id[Picky].random(), state=GenericState.ACTIVE, version=1)


# -- the built-in checks come first --------------------------------------------------------------


def test_the_built_in_checks_run_before_validate():
    with pytest.raises(ValidationError, match="version must be greater than or equal to 1"):
        Account(
            id=ShardedId[Account].random(),
            state=State.OPEN,
            version=0,
            created_date=NOW,
            owner_id=uuid4(),
            balance=Decimal(-1),  # validate() would refuse this too, had it been reached
        )
    with pytest.raises(ValidationError, match="balance cannot be null"):
        Account(
            id=ShardedId[Account].random(),
            state=State.OPEN,
            version=1,
            created_date=NOW,
            owner_id=uuid4(),
            balance=None,  # type: ignore[arg-type]
        )
    assert SEEN == [], "validate() never saw a row the built-in checks refuse"

    an_account()
    # By the time validate() runs, updated_date has fallen back and the events are a tuple.
    assert SEEN == ["validate updated=True events=<class 'tuple'>"]


# -- an own __post_init__ cannot switch the built-in checks off --------------------------------


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> list[type]:
    """Every run of the built-in checks, by the class they ran for."""
    runs: list[type] = []
    real = domain._required

    def counting(target: type) -> tuple[str, ...]:
        runs.append(target)
        return real(target)

    monkeypatch.setattr(domain, "_required", counting)
    return runs


@dataclass(frozen=True, kw_only=True)
class Forgetful(Model[ShardedId["Forgetful"], State]):
    """Its own `__post_init__`, with no `super().__post_init__()`."""

    balance: Decimal
    label: str = ""

    def __post_init__(self) -> None:
        SEEN.append("post_init")
        object.__setattr__(self, "label", self.label.strip())

    def validate(self) -> None:
        SEEN.append(f"validate label={self.label!r}")


@dataclass(frozen=True, kw_only=True)
class Careful(Model[ShardedId["Careful"], State]):
    """Its own `__post_init__`, calling `super().__post_init__()` as the docs used to say."""

    balance: Decimal

    def __post_init__(self) -> None:
        SEEN.append("post_init before super")
        super().__post_init__()
        SEEN.append("post_init after super")

    def validate(self) -> None:
        SEEN.append("validate")


@dataclass(frozen=True, kw_only=True)
class MoreCareful(Careful):
    """A second level, also calling super."""

    note: str = ""

    def __post_init__(self) -> None:
        SEEN.append("more before super")
        super().__post_init__()
        SEEN.append("more after super")


def make(cls: type[Any], **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "id": ShardedId[Any].random(),
        "state": State.OPEN,
        "version": 1,
        "created_date": NOW,
        "balance": Decimal(1),
    }
    values.update(overrides)
    return cls(**values)


def test_an_own_post_init_without_super_still_gets_the_built_in_checks():
    with pytest.raises(ValidationError, match="version must be greater than or equal to 1"):
        make(Forgetful, version=0)
    with pytest.raises(ValidationError, match="balance cannot be null"):
        make(Forgetful, balance=None)

    made = make(Forgetful, label="  ada ")
    assert made.updated_date == NOW, "the Model's own fallback ran"
    # The built-in checks first, then the model's own code, then validate() - seeing its result.
    assert SEEN[-2:] == ["post_init", "validate label='ada'"]


def test_an_own_post_init_calling_super_runs_nothing_twice(counted: list[type]):
    make(Careful)
    assert counted == [Careful], "the built-in checks ran once"
    assert SEEN == ["post_init before super", "post_init after super", "validate"]

    SEEN.clear()
    counted.clear()
    made = make(Careful).evolve(balance=Decimal(2))
    assert made.balance == 2
    assert counted == [Careful, Careful], "once per instance: the original and the evolved one"
    assert SEEN.count("validate") == 2


def test_two_levels_of_own_post_init_run_once_each(counted: list[type]):
    make(MoreCareful)
    assert counted == [MoreCareful]
    assert SEEN == [
        "more before super",
        "post_init before super",
        "post_init after super",
        "more after super",
        "validate",
    ]
    with pytest.raises(ValidationError, match="version must be greater than or equal to 1"):
        make(MoreCareful, version=0)


def test_an_own_post_init_on_an_entity_is_safe_too(counted: list[type]):
    @dataclass(frozen=True, kw_only=True)
    class Lookup(Entity[Id["Lookup"]]):
        code: str
        seen: ClassVar[int] = 0

        def __post_init__(self) -> None:
            type(self).seen += 1

        def validate(self) -> None:
            SEEN.append("validate")

    with pytest.raises(ValidationError, match="code cannot be null"):
        Lookup(id=Id[Lookup].random(), state=GenericState.ACTIVE, version=1, code=None)  # type: ignore[arg-type]
    Lookup(id=Id[Lookup].random(), state=GenericState.ACTIVE, version=1, code="x")
    assert Lookup.seen == 1
    assert SEEN == ["validate"]
    assert counted.count(Lookup) == 2


def test_a_slotted_model_with_an_own_post_init_works():
    @dataclass(frozen=True, kw_only=True, slots=True)
    class Slotted(Entity[Id["Slotted"]]):
        code: str
        extra: list[str] = field(default_factory=list)

        def __post_init__(self) -> None:
            SEEN.append("post_init")

        def validate(self) -> None:
            SEEN.append("validate")

    Slotted(id=Id[Slotted].random(), state=GenericState.ACTIVE, version=1, code="x")
    assert SEEN == ["post_init", "validate"]
    with pytest.raises(ValidationError, match="version must be greater"):
        Slotted(id=Id[Slotted].random(), state=GenericState.ACTIVE, version=0, code="x")

    @dataclass(frozen=True, kw_only=True, slots=True)
    class SlottedSuper(Entity[Id["SlottedSuper"]]):
        code: str

        def __post_init__(self) -> None:
            super().__post_init__()
            SEEN.append("post_init after super")

        def validate(self) -> None:
            SEEN.append("validate")

    SEEN.clear()
    SlottedSuper(id=Id[SlottedSuper].random(), state=GenericState.ACTIVE, version=1, code="x")
    assert SEEN == ["post_init after super", "validate"]


# -- type checkers flag a model without validate() ----------------------------------------------

CHECKED = """
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ekbatan import Entity, GenericState, Model
from ekbatan.ids import Id
from ekbatan.time import Instant


class State(Enum):
    OPEN = "OPEN"


@dataclass(frozen=True, kw_only=True)
class Missing(Model[Id["Missing"], State]):
    name: str


@dataclass(frozen=True, kw_only=True)
class Present(Model[Id["Present"], State]):
    name: str

    def validate(self) -> None:
        pass


@dataclass(frozen=True, kw_only=True)
class MissingEntity(Entity[Id["MissingEntity"]]):
    name: str


def make(when: Instant) -> None:
    Missing(id=Id[Missing].random(), state=State.OPEN, version=1, created_date=when, name="x")
    Present(id=Id[Present].random(), state=State.OPEN, version=1, created_date=when, name="x")
    MissingEntity(id=Id[MissingEntity].random(), state=GenericState.ACTIVE, version=1, name="x")
"""


def _lines_of(source: str) -> dict[str, int]:
    return {
        name: next(
            i
            for i, text in enumerate(source.splitlines(), 1)
            if text.strip().startswith(f"{name}(")
        )
        for name in ("Missing", "Present", "MissingEntity")
    }


def test_mypy_flags_a_model_without_validate(tmp_path: Path) -> None:
    from mypy import api

    module = tmp_path / "models.py"
    module.write_text(CHECKED)
    stdout, stderr, _ = api.run(
        [
            "--strict",
            "--show-error-codes",
            "--no-error-summary",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            "--config-file",
            "",
            str(module),
        ]
    )
    assert not stderr, stderr
    lines = _lines_of(CHECKED)
    errors = [line for line in stdout.splitlines() if ": error:" in line]
    assert any(
        f'models.py:{lines["Missing"]}: error: Cannot instantiate abstract class "Missing" with '
        f'abstract attribute "validate"  [abstract]' in e
        for e in errors
    ), stdout
    assert any(
        f"models.py:{lines['MissingEntity']}: error: Cannot instantiate abstract class "
        f'"MissingEntity" with abstract attribute "validate"  [abstract]' in e
        for e in errors
    ), stdout
    assert len(errors) == 2, stdout


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not on PATH")
def test_pyright_flags_a_model_without_validate(tmp_path: Path) -> None:
    import ekbatan

    module = tmp_path / "models.py"
    module.write_text(CHECKED)
    root = Path(ekbatan.__file__).parent.parent.as_posix()
    (tmp_path / "pyrightconfig.json").write_text(json.dumps({"extraPaths": [root]}))
    done = subprocess.run(
        ["pyright", str(module)], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    lines = _lines_of(CHECKED)
    out = textwrap.dedent(done.stdout)
    assert (
        f'models.py:{lines["Missing"]}:5 - error: Cannot instantiate abstract class "Missing"'
        in out
    ), out
    assert (
        f"models.py:{lines['MissingEntity']}:5 - error: Cannot instantiate abstract class "
        f'"MissingEntity"' in out
    ), out
    assert "2 errors" in out, out
