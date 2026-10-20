"""schemagen's settings and matching, without a database.

What a project writes in `[tool.ekbatan.schemagen]`, how a forced type picks its columns, what
it turns them into, and how the type table writes a type - each checked in milliseconds. The
same things against real databases are in `tests/integration/test_schemagen*.py`.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import CHAR, INTEGER, VARCHAR, Column, DateTime, MetaData, Table
from sqlalchemy.dialects import mysql, postgresql

from ekbatan.errors import ValidationError
from ekbatan.schemagen import (
    DEFAULT_FORCED_TYPES,
    ForcedType,
    SchemagenConfig,
    load_config,
    render,
)
from ekbatan.schemagen._types import emit_type, resolve, type_names

# -- the configuration file -----------------------------------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nname = "x"\n\n' + body)
    return path


def test_the_whole_table_is_read(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[tool.ekbatan.schemagen]
migrations = "src/app/migrations"
out = "src/app/schema.py"
image = "mysql:8.4"
schemas = ["eventlog"]
includes = "wallets|alerts"
excludes = "legacy_.*"
views = false
default-forced-types = false

[[tool.ekbatan.schemagen.forced-types]]
name = "Text"
include-expression = ".*\\\\.notes"

[[tool.ekbatan.schemagen.forced-types]]
converter = "app.converters:MoneyConverter(4)"
include-types = "DECIMAL.*"
exclude-expression = "rate"
nullability = "NOT_NULL"

[[tool.ekbatan.schemagen.forced-types]]
sqlalchemy-type = "pgvector.sqlalchemy.Vector(3)"
include-types = "VECTOR.*"
""",
    )
    config = load_config(path)

    assert config is not None
    assert config.migrations == tmp_path / "src/app/migrations", "relative to the file"
    assert config.out == tmp_path / "src/app/schema.py"
    assert config.image == "mysql:8.4"
    assert config.schemas == ("eventlog",)
    assert (config.includes, config.excludes, config.views) == (
        "wallets|alerts",
        "legacy_.*",
        False,
    )
    assert config.default_forced_types is False
    assert config.forced_types == (
        ForcedType(name="Text", include_expression=r".*\.notes"),
        ForcedType(
            converter="app.converters:MoneyConverter(4)",
            include_types="DECIMAL.*",
            exclude_expression="rate",
            nullability="NOT_NULL",
        ),
        ForcedType(sqlalchemy_type="pgvector.sqlalchemy.Vector(3)", include_types="VECTOR.*"),
    )
    assert config.all_forced_types == config.forced_types, "defaults off: only the user's"


def test_the_user_s_forced_types_come_before_ekbatan_s(tmp_path: Path) -> None:
    """As in jOOQ, the first forced type that matches wins - and the user's are first."""
    mine = ForcedType(name="Text", include_types="JSON")
    config = SchemagenConfig(forced_types=(mine,))

    assert config.all_forced_types == (mine, *DEFAULT_FORCED_TYPES)


def test_a_file_with_no_table_is_ignored_unless_named(tmp_path: Path) -> None:
    path = _write(tmp_path, "[tool.ruff]\nline-length = 100\n")

    assert load_config(path, required=False) is None
    with pytest.raises(ValidationError, match=r"no \[tool.ekbatan.schemagen\] table"):
        load_config(path)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('migratons = "m"', r"unknown key\(s\) \['migratons'\]"),
        ("views = 1", "views must be true or false"),
        ("schemas = [1]", "schemas must be a list of names"),
        ('includes = "("', "includes '\\(' is not a regular expression"),
        ('[[tool.ekbatan.schemagen.forced-types]]\nname = "Text"', "needs include-expression"),
        (
            '[[tool.ekbatan.schemagen.forced-types]]\nname = "Text"\nconverter = "a.B"\n'
            'include-types = "X"',
            "exactly one of name, converter and sqlalchemy-type, got name='Text', converter='a.B'",
        ),
        (
            '[[tool.ekbatan.schemagen.forced-types]]\ninclude-types = "X"',
            "exactly one of name, converter and sqlalchemy-type, got none",
        ),
        (
            '[[tool.ekbatan.schemagen.forced-types]]\nuser-type = "a.Money"\ninclude-types = "X"',
            "user-type is now two keys. Use converter for your own class with from_db and "
            "to_db, or sqlalchemy-type for a ready-made SQLAlchemy type",
        ),
        (
            '[[tool.ekbatan.schemagen.forced-types]]\nname = "Text"\ninclude-type = "X"',
            r"unknown key\(s\) \['include-type'\]",
        ),
        (
            '[[tool.ekbatan.schemagen.forced-types]]\nname = "Text"\ninclude-types = "("',
            "bad include-types",
        ),
        (
            '[[tool.ekbatan.schemagen.forced-types]]\nname = "Text"\ninclude-types = "X"\n'
            'nullability = "SOME"',
            "nullability 'SOME'",
        ),
    ],
    ids=[
        "typo",
        "views not a bool",
        "schemas not names",
        "bad regex",
        "no include",
        "name and converter",
        "no target",
        "the old user-type key",
        "typo in a forced type",
        "bad forced regex",
        "bad nullability",
    ],
)
def test_a_mistake_in_the_file_is_refused_by_name(tmp_path: Path, body: str, message: str) -> None:
    path = _write(tmp_path, f"[tool.ekbatan.schemagen]\n{body}\n")

    with pytest.raises(ValidationError, match=message):
        load_config(path)


def test_a_flag_wins_over_the_file() -> None:
    config = SchemagenConfig(image="mysql:8.4", excludes="a", views=True)

    changed = config.with_overrides(image="mariadb:11.4", excludes=None, views=False)

    assert (changed.image, changed.excludes, changed.views) == ("mariadb:11.4", "a", False)


def test_the_url_is_never_in_the_config_s_repr() -> None:
    """A url can carry a password; a config printed in a traceback must not."""
    config = SchemagenConfig(url="postgresql://u:S3cret-not-for-logs@h/db")

    assert "S3cret" not in repr(config)


# -- how a forced type picks its columns ---------------------------------------------------


def _matches(
    forced: ForcedType,
    column: str = "id",
    types: tuple[str, ...] = ("CHAR(36)", "CHAR"),
    *,
    table: str = "wallets",
    dialect: str = "mysql",
    nullable: bool = True,
) -> bool:
    return forced.matches(
        dialect=dialect, names=[column, f"{table}.{column}"], types=types, nullable=nullable
    )


def test_an_expression_matches_the_column_or_table_dot_column_whole() -> None:
    """Java's `.*\\.id|.*_id` works unchanged: `wallets.id` or `owner_id` - and nothing that
    merely contains the text."""
    uuid = ForcedType(name="uuid_column", include_expression=r".*\.id|.*_id")

    assert _matches(uuid, "id")
    assert _matches(uuid, "owner_id")
    assert not _matches(uuid, "identity")
    assert not _matches(uuid, "id_card")


def test_types_match_whole_and_ignore_case() -> None:
    forced = ForcedType(name="uuid_column", include_types=r"CHAR\(36\)")

    assert _matches(forced, types=("char(36)",))
    assert not _matches(forced, types=("VARCHAR(36)", "VARCHAR"))


def test_both_includes_must_match_and_an_exclude_takes_a_column_back() -> None:
    forced = ForcedType(
        name="uuid_column",
        include_types=r"CHAR\(36\)",
        include_expression=r".*_id",
        exclude_expression="legacy_id",
    )

    assert _matches(forced, "owner_id")
    assert not _matches(forced, "owner_id", types=("CHAR(3)",))
    assert not _matches(forced, "legacy_id")


def test_nullability_limits_a_forced_type() -> None:
    forced = ForcedType(name="Text", include_types="CHAR.*", nullability="NOT_NULL")

    assert _matches(forced, nullable=False)
    assert not _matches(forced, nullable=True)


def test_ekbatan_s_defaults_are_per_database_and_never_take_a_char_36() -> None:
    """JSONB only on PostgreSQL. And no default takes a CHAR(36) id on any database: whether it
    holds a uuid is the project's to say, as each Java MySQL build says it."""
    jsonb = next(f for f in DEFAULT_FORCED_TYPES if f.include_types == "JSONB")

    assert _matches(jsonb, "doc", types=("JSONB",), dialect="postgresql")
    assert not _matches(jsonb, "doc", types=("JSONB",), dialect="mysql")
    for dialect in ("postgresql", "mysql", "mariadb", "sqlite"):
        for column in ("id", "owner_id"):
            assert not any(
                _matches(f, column, types=("CHAR(36)", "CHAR"), dialect=dialect)
                for f in DEFAULT_FORCED_TYPES
            ), (dialect, column)


def test_the_timestamp_default_takes_zone_less_timestamps_only() -> None:
    """Java's `TIMESTAMP` forced type does not match `timestamp with time zone`, which keeps
    its zone (an OffsetDateTime there). The spellings are the ones schemagen offers a pattern."""
    stamp = DEFAULT_FORCED_TYPES[0]
    pg = postgresql.dialect()

    plain = type_names(postgresql.TIMESTAMP(), "timestamp(3) without time zone", pg)
    zoned = type_names(postgresql.TIMESTAMP(timezone=True), "timestamp with time zone", pg)

    assert "TIMESTAMP" in plain
    assert "TIMESTAMPTZ" in zoned and "TIMESTAMP" not in zoned
    assert _matches(stamp, "at", types=plain, dialect="postgresql")
    assert not _matches(stamp, "at", types=zoned, dialect="postgresql")
    assert _matches(
        stamp, "at", types=type_names(mysql.DATETIME(fsp=6), "datetime(6)", mysql.dialect())
    )


def test_a_type_is_offered_in_every_spelling() -> None:
    names = type_names(VARCHAR(36), "character varying(36)", postgresql.dialect())

    assert {"CHARACTER VARYING(36)", "VARCHAR(36)", "VARCHAR", "CHARACTER VARYING"} <= set(names)
    assert "NUMERIC(19,4)" in type_names(postgresql.NUMERIC(19, 4), None, postgresql.dialect())


# -- what a forced type turns a column into ------------------------------------------------


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("uuid_column", "uuid_column()"),
        ("json_column()", "json_column()"),
        ("Text", "Text()"),
        ("String(36)", "String(36)"),
        ("Numeric(19, 4)", "Numeric(19, 4)"),
        ("Numeric(precision=19, scale=4)", "Numeric(precision=19, scale=4)"),
        ("postgresql.INET", "postgresql.INET()"),
        ("mysql.TINYINT(unsigned=True)", "mysql.TINYINT(unsigned=True)"),
    ],
)
def test_a_name_is_a_helper_or_a_sqlalchemy_type(name: str, source: str) -> None:
    assert resolve(ForcedType(name=name, include_types="X")).source == source


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("Txet", "is not a SQLAlchemy type"),
        ("uuid_column(1)", "takes no arguments"),
        ("myapp.Money", "your own class goes in converter, a library's type in sqlalchemy-type"),
        ("String(len(x))", "arguments must be plain values"),
        ("MetaData", "not a column type"),
    ],
)
def test_a_wrong_name_is_refused_naming_itself(name: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        resolve(ForcedType(name=name, include_types="X"))


@pytest.fixture
def user_types(tmp_path: Path) -> Iterator[Path]:
    """A package of the user's own types and converters, importable as `unit_user_types`."""
    package = tmp_path / "unit_user_types"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "money.py").write_text(
        "from decimal import Decimal\n"
        "from sqlalchemy import Numeric\n"
        "from sqlalchemy.types import TypeDecorator\n\n"
        "class Money(TypeDecorator):\n"
        "    impl = Numeric\n"
        "    cache_ok = True\n"
        "    def __init__(self, scale: int = 2):\n"
        "        super().__init__(precision=19, scale=scale)\n\n"
        "def cents():\n"
        "    return Numeric(19, 2)\n\n"
        "AMOUNT = Numeric(19, 4)\n"
        "NOT_A_TYPE = 3\n"
    )
    (package / "converters.py").write_text(
        "from decimal import Decimal\n"
        "from sqlalchemy import Numeric\n\n"
        "class MoneyConverter:\n"
        "    def __init__(self, places: int = 2):\n"
        "        self.places = places\n"
        "    def from_db(self, value) -> Decimal:\n"
        "        return value\n"
        "    def to_db(self, value):\n"
        "        return value\n\n"
        "def money(places: int = 2):\n"
        "    return MoneyConverter(places)\n\n"
        "SHARED = MoneyConverter()\n\n"
        "class OnlyFromDb:\n"
        "    def from_db(self, value):\n"
        "        return value\n\n"
        "def not_a_converter():\n"
        "    return 3\n\n"
        "def needs_two(a, b):\n"
        "    return MoneyConverter()\n\n"
        "TYPE = Numeric(19, 2)\n"
    )
    sys.path.insert(0, str(tmp_path))
    yield package
    sys.path.remove(str(tmp_path))
    for name in [m for m in sys.modules if m.startswith("unit_user_types")]:
        sys.modules.pop(name)


@pytest.mark.parametrize(
    ("sqlalchemy_type", "source"),
    [
        ("unit_user_types.money.Money", "Money()"),
        ("unit_user_types.money:Money", "Money()"),
        ("unit_user_types.money.Money(4)", "Money(4)"),
        ("unit_user_types.money.cents", "cents()"),
        ("unit_user_types.money.AMOUNT", "AMOUNT"),
    ],
    ids=["class", "colon form", "with arguments", "a function", "an instance"],
)
def test_a_sqlalchemy_type_is_imported_by_its_dotted_path(
    user_types: Path, sqlalchemy_type: str, source: str
) -> None:
    emitted = resolve(ForcedType(sqlalchemy_type=sqlalchemy_type, include_types="X"))

    assert emitted.source == source
    assert emitted.imports == {("unit_user_types.money", source.split("(")[0])}


@pytest.mark.parametrize(
    ("sqlalchemy_type", "message"),
    [
        ("unit_user_types.nope.Money", "cannot import unit_user_types.nope"),
        ("unit_user_types.money.Nope", "has no Nope"),
        ("unit_user_types.money.NOT_A_TYPE", "is not a type"),
        ("unit_user_types.money.AMOUNT(2)", "is a type instance"),
        ("Money", "must be a dotted path"),
        (
            "unit_user_types.converters.MoneyConverter",
            "has from_db and to_db, so it goes in converter",
        ),
    ],
)
def test_a_wrong_sqlalchemy_type_is_refused_naming_itself(
    user_types: Path, sqlalchemy_type: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        resolve(ForcedType(sqlalchemy_type=sqlalchemy_type, include_types="X"))


@pytest.mark.parametrize(
    ("converter", "source"),
    [
        ("unit_user_types.converters.MoneyConverter", "MoneyConverter()"),
        ("unit_user_types.converters:MoneyConverter", "MoneyConverter()"),
        ("unit_user_types.converters.MoneyConverter(4)", "MoneyConverter(4)"),
        ("unit_user_types.converters.MoneyConverter(places=4)", "MoneyConverter(places=4)"),
        ("unit_user_types.converters.money(3)", "money(3)"),
        ("unit_user_types.converters.SHARED", "SHARED"),
    ],
    ids=["class", "colon form", "with arguments", "keyword", "a function", "an instance"],
)
def test_a_converter_is_imported_by_its_dotted_path(
    user_types: Path, converter: str, source: str
) -> None:
    emitted = resolve(ForcedType(converter=converter, include_types="X"))

    assert emitted.source == source
    assert emitted.imports == {("unit_user_types.converters", source.split("(")[0])}


@pytest.mark.parametrize(
    ("converter", "message"),
    [
        ("unit_user_types.nope.MoneyConverter", "cannot import unit_user_types.nope"),
        ("unit_user_types.converters.Nope", "has no Nope"),
        (
            "unit_user_types.converters.OnlyFromDb",
            r"OnlyFromDb has no from_db\(\) and to_db\(\). A converter is any class with "
            r"from_db\(value\) and to_db\(value\)",
        ),
        ("unit_user_types.converters.not_a_converter", "builds int, which has no from_db"),
        ("unit_user_types.converters.needs_two", "could not be built: TypeError"),
        ("unit_user_types.converters.SHARED(2)", "is a converter already; drop the"),
        ("unit_user_types.converters.TYPE", "a SQLAlchemy type goes in sqlalchemy-type"),
        ("unit_user_types.money.Money", "a SQLAlchemy type goes in sqlalchemy-type"),
        ("MoneyConverter", "must be a dotted path to a converter"),
        ("unit_user_types.converters.MoneyConverter(x)", "arguments must be plain values"),
    ],
    ids=[
        "no module",
        "no name",
        "lacks to_db",
        "factory returns something else",
        "cannot be built",
        "instance called",
        "a type instance",
        "a TypeDecorator",
        "not dotted",
        "not a literal",
    ],
)
def test_a_wrong_converter_is_refused_naming_itself(
    user_types: Path, converter: str, message: str
) -> None:
    with pytest.raises(
        ValidationError, match=re.escape(f"the forced converter {converter!r}")
    ) as raised:
        resolve(ForcedType(converter=converter, include_types="X"))
    assert raised.match(message)


# -- the type table, on types built by hand ------------------------------------------------


@pytest.mark.parametrize(
    ("reflected", "source"),
    [
        (mysql.BIGINT(display_width=20), "BIGINT()"),
        (mysql.TINYINT(display_width=1), "BOOLEAN()"),
        (mysql.TINYINT(display_width=1, unsigned=True), "mysql.TINYINT(unsigned=True)"),
        (mysql.INTEGER(display_width=10, unsigned=True), "mysql.INTEGER(unsigned=True)"),
        (mysql.DOUBLE(asdecimal=True), "DOUBLE()"),
        (mysql.ENUM("NEW", "DONE"), 'Enum("NEW", "DONE")'),
        (postgresql.ENUM("a", "b", name="mood"), 'Enum("a", "b", name="mood")'),
        (postgresql.ARRAY(postgresql.TIMESTAMP()), "postgresql.ARRAY(TIMESTAMP())"),
        (postgresql.BYTEA(), "LargeBinary()"),
        (postgresql.DOUBLE_PRECISION(precision=53), "DOUBLE_PRECISION()"),
        (
            mysql.LONGTEXT(charset="utf8mb4", collation="utf8mb4_bin"),
            'mysql.LONGTEXT(charset="utf8mb4", collation="utf8mb4_bin")',
        ),
        (mysql.YEAR(display_width=4), "mysql.YEAR()"),
        (postgresql.INET(), "postgresql.INET()"),
    ],
)
def test_the_type_table_writes_an_importable_faithful_type(reflected: object, source: str) -> None:
    emitted = emit_type(reflected, "mysql")  # type: ignore[arg-type]

    assert emitted is not None and emitted.source == source


def test_render_needs_no_database_and_imports_what_it_used() -> None:
    """The emitter is separable from the pipeline. Before the type table, a dialect type was
    imported from `sqlalchemy`, which does not have it (round 2, N2 #4)."""
    table = Table(
        "t",
        MetaData(),
        Column("id", CHAR(36), primary_key=True),
        Column("flag", mysql.TINYINT(display_width=1), nullable=False),
        Column("note", mysql.LONGTEXT()),
        Column("at", DateTime()),
    )
    text = render(
        [table], framework=["events"], migrations_fingerprint="x", module=None, dialect="mysql"
    )

    namespace: dict[str, object] = {}
    body = text.replace("check_fingerprint(__file__", "check_fingerprint('x'")
    exec(compile(body, "schema.py", "exec"), namespace)
    assert "from sqlalchemy.dialects import mysql" in text
    assert 'Column("id", CHAR(length=36), primary_key=True)' in text
    assert 'Column("flag", BOOLEAN(), nullable=False)' in text
    assert 'Column("at", timestamp_column())' in text
    assert "EVENTS = outbox_table(metadata)" in text


def test_a_char_36_id_stays_text_unless_a_forced_type_says_it_is_a_uuid() -> None:
    """Nothing in MySQL or MariaDB says a CHAR(36) holds a uuid, so schemagen does not guess:
    an id-like name is not enough, on any database. A project that stores uuids that way says
    so with its own forced type - and only then is the column a uuid."""
    table = Table(
        "t", MetaData(), Column("id", CHAR(36), primary_key=True), Column("ext_id", VARCHAR(36))
    )
    uuid_ids = SchemagenConfig(
        forced_types=(
            ForcedType(
                name="uuid_column", include_types=r"CHAR\(36\)", include_expression=r".*\.id|.*_id"
            ),
        )
    )

    for dialect in ("postgresql", "mysql", "mariadb"):
        plain = render(
            [table], framework=[], migrations_fingerprint="x", module=None, dialect=dialect
        )
        assert 'Column("id", CHAR(length=36), primary_key=True)' in plain, dialect
        assert 'Column("ext_id", VARCHAR(length=36))' in plain, dialect

    forced = render(
        [table],
        framework=[],
        migrations_fingerprint="x",
        module=None,
        dialect="mysql",
        config=uuid_ids,
    )
    assert 'Column("id", uuid_column(), primary_key=True)' in forced
    assert 'Column("ext_id", VARCHAR(length=36))' in forced, "the rule names CHAR(36) only"


def test_a_sqlalchemy_type_named_like_sqlalchemy_s_own_is_aliased(
    user_types: Path, tmp_path: Path
) -> None:
    (user_types / "clash.py").write_text(
        "from sqlalchemy import Text\n\nclass JSON(Text):\n    pass\n"
    )
    table = Table("t", MetaData(), Column("a", VARCHAR(5)), Column("b", postgresql.JSON()))
    config = SchemagenConfig(
        forced_types=(
            ForcedType(sqlalchemy_type="unit_user_types.clash.JSON", include_expression="a"),
        )
    )

    text = render(
        [table],
        framework=[],
        migrations_fingerprint="x",
        module=None,
        dialect="postgresql",
        config=config,
    )

    assert "from unit_user_types.clash import JSON as unit_user_types_clash_JSON" in text
    assert 'Column("a", unit_user_types_clash_JSON())' in text
    assert 'Column("b", JSON(none_as_null=True))' in text, "SQLAlchemy's JSON is still SQLAlchemy's"


def _rendered(*forced: ForcedType, dialect: str = "postgresql", **columns: object) -> str:
    table = Table("t", MetaData(), Column("id", INTEGER(), primary_key=True))
    for name, type_ in columns.items():
        table.append_column(Column(name, type_))  # type: ignore[arg-type]
    return render(
        [table],
        framework=[],
        migrations_fingerprint="x",
        module=None,
        dialect=dialect,
        config=SchemagenConfig(forced_types=forced),
    )


def test_a_converter_keeps_the_column_s_own_type(user_types: Path) -> None:
    """The column's database type stays; the converter is wrapped around it, and the module
    imports both the helper and the converter - and runs."""
    text = _rendered(
        ForcedType(
            converter="unit_user_types.converters.MoneyConverter(4)",
            include_expression=r".*\.amount",
        ),
        amount=postgresql.NUMERIC(19, 4),
        rate=postgresql.NUMERIC(19, 4),
    )

    assert 'Column("amount", converted(NUMERIC(precision=19, scale=4), MoneyConverter(4)))' in text
    assert 'Column("rate", NUMERIC(precision=19, scale=4))' in text, "only the named column"
    assert "from ekbatan.schema import converted" in text
    assert "from unit_user_types.converters import MoneyConverter" in text
    namespace: dict[str, object] = {"__file__": "schema.py"}
    exec(compile(text, "schema.py", "exec"), namespace)
    amount = namespace["T"].c.amount.type  # type: ignore[attr-defined]
    assert repr(amount).startswith("converted(NUMERIC(precision=19, scale=4), <")


def test_a_converter_wraps_what_the_column_would_otherwise_be(user_types: Path) -> None:
    """Under a converter, the column is what it would be without it: Ekbatan's timestamp and
    json helpers, or the user's own `name` listed after it. A second converter is not stacked."""
    text = _rendered(
        ForcedType(converter="unit_user_types.converters.SHARED", include_types="TIMESTAMP"),
        ForcedType(converter="unit_user_types.converters.money", include_expression="body"),
        ForcedType(converter="unit_user_types.converters.money(5)", include_expression="body"),
        ForcedType(name="Text", include_expression="body"),
        at=DateTime(),
        body=VARCHAR(10),
    )

    assert 'Column("at", converted(timestamp_column(), SHARED))' in text
    assert 'Column("body", converted(Text(), money()))' in text


def test_a_converter_named_like_a_sqlalchemy_type_is_aliased(
    user_types: Path,
) -> None:
    (user_types / "clash.py").write_text(
        "class JSON:\n"
        "    def from_db(self, value) -> str:\n"
        "        return value\n"
        "    def to_db(self, value):\n"
        "        return value\n"
    )
    text = _rendered(
        ForcedType(converter="unit_user_types.clash.JSON", include_expression="doc"),
        doc=postgresql.JSON(),
    )

    assert "from unit_user_types.clash import JSON as unit_user_types_clash_JSON" in text
    assert (
        'Column("doc", converted(JSON(none_as_null=True), unit_user_types_clash_JSON()))' in text
    ), "SQLAlchemy's JSON inside, the user's outside"


def test_a_type_with_no_spelling_is_noted_only_when_rendering_by_hand(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A real run always has the database's spelling and writes `sql_type(...)`; `render()` on a
    table built by hand may not, and says so rather than guessing."""
    from sqlalchemy.types import NullType

    with caplog.at_level("WARNING", logger="io.ekbatan.schemagen"):
        text = _rendered(odd=NullType())

    assert 'Column("odd", NullType())' in text
    assert "t.odd is NullType, a type with no known spelling" in caplog.text
