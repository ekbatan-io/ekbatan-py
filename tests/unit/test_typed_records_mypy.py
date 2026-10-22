"""mypy holds a repository to its table's record: the proof that the records are worth having.

A small application is written into a temporary directory - a model, a converter, a schema
rendered by schemagen from a table built by hand - and beside it one repository written right
and several written wrong, each wrong in exactly one way:

- ``to_record`` leaves a column out;
- ``to_record`` misspells one, or adds one the table does not have;
- ``to_record`` gives a column the wrong type - a ``str`` balance, and a ``str`` where the
  converter's column wants a ``Currency``;
- ``from_record`` reads a key the record does not have, or reads one as the wrong type;
- ``from_record`` takes another table's record;
- a fetch mapper reads a row by attribute rather than by column name.

One ``mypy --strict`` run checks them all. The right repository must be clean and each wrong
one must fail with the error it was written to provoke, on its line - so each case is proven
red, and the clean one green, in the same run. No database: this is the type checker's work.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest
from sqlalchemy import BIGINT, CHAR, NUMERIC, TIMESTAMP, UUID, VARCHAR, Column, MetaData, Table

from ekbatan.schemagen import ForcedType, SchemagenConfig, render

MODEL = """
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from uuid import UUID

from ekbatan import Model, ShardedId


@dataclass(frozen=True)
class Currency:
    code: str


class WalletState(Enum):
    OPENED = "OPENED"


@dataclass(frozen=True, kw_only=True)
class Wallet(Model[ShardedId["Wallet"], WalletState]):
    owner_id: UUID
    currency: Currency
    balance: Decimal
"""

CONVERTERS = """
from __future__ import annotations

from .model import Currency


class CurrencyConverter:
    def from_db(self, value: str) -> Currency:
        return Currency(value)

    def to_db(self, value: Currency) -> str:
        return value.code
"""

#: The repository, written right. Each wrong one is this with one line changed.
RIGHT = """
from __future__ import annotations

from sqlalchemy import select

from ekbatan import DatabaseRegistry, ModelRepository, ShardedId

from .model import Currency, Wallet, WalletState
from .schema import WALLETS, AlertsRecord, WalletsRecord


class WalletRepository(ModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]):
    def __init__(self, registry: DatabaseRegistry) -> None:
        super().__init__(Wallet, WALLETS, registry)

    def to_record(self, wallet: Wallet) -> WalletsRecord:
        return WalletsRecord(
            id=wallet.id.value,
            version=wallet.version,
            state=wallet.state.name,
            owner_id=wallet.owner_id,
            currency=wallet.currency,
            balance=wallet.balance,
            created_date=wallet.created_date,
            updated_date=wallet.updated_date or wallet.created_date,
        )

    def from_record(self, record: WalletsRecord) -> Wallet:
        return Wallet(
            id=ShardedId[Wallet](record["id"]),
            version=record["version"],
            state=WalletState[record["state"]],
            owner_id=record["owner_id"],
            currency=record["currency"],
            balance=record["balance"],
            created_date=record["created_date"],
            updated_date=record["updated_date"],
        )

    def currencies(self) -> list[str]:
        with self.readonly_db() as conn:
            return conn.rows(select(WALLETS.c.currency)).fetch(lambda row: str(row["currency"]))

    def everything(self) -> list[Wallet]:
        with self.readonly_db() as conn:
            return conn.rows(select(WALLETS)).fetch(self.from_record)


def unused(record: AlertsRecord) -> None:
    pass
"""

#: (name, the line of RIGHT to change, what it becomes, the mypy error code expected there)
WRONG = [
    ("missing_column", "            balance=wallet.balance,\n", "", "typeddict-item"),
    (
        "misspelt_column",
        "            balance=wallet.balance,\n",
        "            balanse=wallet.balance,\n",
        "typeddict-unknown-key",
    ),
    (
        "extra_column",
        "            balance=wallet.balance,\n",
        '            balance=wallet.balance,\n            colour="red",\n',
        "typeddict-unknown-key",
    ),
    (
        "wrong_type",
        "            balance=wallet.balance,\n",
        "            balance=str(wallet.balance),\n",
        "typeddict-item",
    ),
    (
        "converter_type",
        "            currency=wallet.currency,\n",
        "            currency=wallet.currency.code,\n",
        "typeddict-item",
    ),
    (
        "wrong_key_read",
        '            balance=record["balance"],\n',
        '            balance=record["balanse"],\n',
        "typeddict-item",
    ),
    (
        "read_as_wrong_type",
        '            currency=record["currency"],\n',
        '            currency=Currency(record["currency"]),\n',
        "arg-type",
    ),
    (
        "another_tables_record",
        "    def from_record(self, record: WalletsRecord) -> Wallet:\n",
        "    def from_record(self, record: AlertsRecord) -> Wallet:  # type: ignore[empty-body]\n",
        "override",
    ),
    (
        "row_read_by_attribute",
        'fetch(lambda row: str(row["currency"]))',
        "fetch(lambda row: str(row.currency))",
        "attr-defined",
    ),
]


def _schema() -> str:
    wallets = Table(
        "wallets",
        MetaData(),
        Column("id", UUID(), primary_key=True),
        Column("version", BIGINT(), nullable=False),
        Column("state", VARCHAR(24), nullable=False),
        Column("owner_id", UUID(), nullable=False),
        Column("currency", CHAR(3), nullable=False),
        Column("balance", NUMERIC(19, 4), nullable=False),
        Column("created_date", TIMESTAMP(), nullable=False),
        Column("updated_date", TIMESTAMP(), nullable=False),
    )
    alerts = Table(
        "alerts",
        MetaData(),
        Column("id", UUID(), primary_key=True),
        Column("message", VARCHAR(80), nullable=False),
    )
    return render(
        [alerts, wallets],
        framework=[],
        migrations_fingerprint="x",
        module=None,
        dialect="postgresql",
        config=SchemagenConfig(
            forced_types=(
                ForcedType(
                    converter="typed_records_app.converters.CurrencyConverter",
                    include_types=r"CHAR\(3\)",
                ),
            )
        ),
    )


@pytest.fixture(scope="module")
def checked(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[tuple[int, str]]]:
    """Write the application, run mypy once over all of it, and hand back each file's errors
    as (line, error code)."""
    import sys

    from mypy import api

    root = tmp_path_factory.mktemp("typed_records")
    app = root / "typed_records_app"
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "model.py").write_text(MODEL)
    (app / "converters.py").write_text(CONVERTERS)
    sys.path.insert(0, str(root))
    try:
        (app / "schema.py").write_text(_schema())
    finally:
        sys.path.remove(str(root))
        for name in [m for m in sys.modules if m.startswith("typed_records_app")]:
            del sys.modules[name]
    files = {"right": RIGHT}
    for name, old, new, _ in WRONG:
        assert RIGHT.count(old) == 1, name
        files[name] = RIGHT.replace(old, new)
    for name, source in files.items():
        (app / f"repo_{name}.py").write_text(textwrap.dedent(source))

    stdout, stderr, _ = api.run(
        [
            "--strict",
            "--show-error-codes",
            "--no-error-summary",
            "--cache-dir",
            str(root / ".mypy_cache"),
            "--config-file",
            "",
            str(app),
        ]
    )
    assert not stderr, stderr
    errors: dict[str, list[tuple[int, str]]] = {name: [] for name in files}
    errors["other"] = []
    for line in stdout.splitlines():
        found = re.match(r".*?typed_records_app[/\\](\w+)\.py:(\d+): error: .*\[([\w-]+)\]$", line)
        if found is None:
            continue
        module, number, code = found.groups()
        name = module.removeprefix("repo_") if module.startswith("repo_") else "other"
        errors.setdefault(name, []).append((int(number), code))
    errors["_stdout"] = [(0, stdout)]
    return errors


def test_the_generated_schema_and_the_right_repository_are_clean(
    checked: dict[str, list[tuple[int, str]]],
) -> None:
    assert checked["other"] == [], checked["_stdout"][0][1]
    assert checked["right"] == [], checked["_stdout"][0][1]


@pytest.mark.parametrize(("name", "old", "new", "code"), WRONG, ids=[w[0] for w in WRONG])
def test_each_mistake_is_a_type_error_on_its_line(
    checked: dict[str, list[tuple[int, str]]], name: str, old: str, new: str, code: str
) -> None:
    source = textwrap.dedent(RIGHT.replace(old, new))
    # The line the change is on - or, for a key the record lacks or lost, the call it is in:
    # mypy reports a TypedDict call's keys where the call starts.
    keys = code == "typeddict-unknown-key" or not new.strip()
    where = "return WalletsRecord(" if keys else new.strip().splitlines()[0]
    line = next(i for i, text in enumerate(source.splitlines(), 1) if where in text)
    assert checked[name], f"mypy accepted {name}:\n{checked['_stdout'][0][1]}"
    assert (line, code) in checked[name], (checked[name], checked["_stdout"][0][1])


def test_the_record_holds_the_converter_s_own_type(tmp_path: Path) -> None:
    """The column is CHAR(3); the record says Currency, because the converter gives one."""
    import sys

    app = tmp_path / "typed_records_app"
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "model.py").write_text(MODEL)
    (app / "converters.py").write_text(CONVERTERS)
    sys.path.insert(0, str(tmp_path))
    try:
        text = _schema()
    finally:
        sys.path.remove(str(tmp_path))
        for name in [m for m in sys.modules if m.startswith("typed_records_app")]:
            del sys.modules[name]
    assert "    currency: Currency\n" in text
    assert "from typed_records_app.model import Currency" in text
