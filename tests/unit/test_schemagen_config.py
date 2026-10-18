"""schemagen's settings and matching, without a database.

What a project writes in `[tool.ekbatan.schemagen]`, how a forced type picks its columns, what
it turns them into, and how the type table writes a type - each checked in milliseconds. The
same things against real databases are in `tests/integration/test_schemagen*.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import CHAR, VARCHAR, Column, DateTime, MetaData, Table
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
user-type = "app.types:Money(4)"
include-types = "DECIMAL.*"
exclude-expression = "rate"
nullability = "NOT_NULL"
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
            user_type="app.types:Money(4)",
            include_types="DECIMAL.*",
            exclude_expression="rate",
            nullability="NOT_NULL",
        ),
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
            '[[tool.ekbatan.schemagen.forced-types]]\nname = "Text"\nuser-type = "a.B"\n'
            'include-types = "X"',
            "exactly one of name and user-type",
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
        "name and user-type",
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


def test_ekbatan_s_defaults_are_per_database_as_java_s_builds_are() -> None:
    """The CHAR(36) uuid guess only when reading MySQL or MariaDB; JSONB only on PostgreSQL."""
    guess = DEFAULT_FORCED_TYPES[-1]

    assert _matches(guess, "owner_id", dialect="mysql")
    assert _matches(guess, "owner_id", dialect="mariadb")
    assert not _matches(guess, "owner_id", dialect="postgresql")
    assert not _matches(guess, "owner_id", dialect="sqlite")


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
        ("myapp.Money", "your own type goes in user-type"),
        ("String(len(x))", "arguments must be plain values"),
        ("MetaData", "not a column type"),
    ],
)
def test_a_wrong_name_is_refused_naming_itself(name: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        resolve(ForcedType(name=name, include_types="X"))


@pytest.fixture
def user_types(tmp_path: Path) -> Path:
    """A package of the user's own types, importable as `myapp_types_*`."""
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
    sys.path.insert(0, str(tmp_path))
    yield package
    sys.path.remove(str(tmp_path))
    for name in [m for m in sys.modules if m.startswith("unit_user_types")]:
        sys.modules.pop(name)


@pytest.mark.parametrize(
    ("user_type", "source"),
    [
        ("unit_user_types.money.Money", "Money()"),
        ("unit_user_types.money:Money", "Money()"),
        ("unit_user_types.money.Money(4)", "Money(4)"),
        ("unit_user_types.money.cents", "cents()"),
        ("unit_user_types.money.AMOUNT", "AMOUNT"),
    ],
    ids=["class", "colon form", "with arguments", "a function", "an instance"],
)
def test_a_user_type_is_imported_by_its_dotted_path(
    user_types: Path, user_type: str, source: str
) -> None:
    emitted = resolve(ForcedType(user_type=user_type, include_types="X"))

    assert emitted.source == source
    assert emitted.imports == {("unit_user_types.money", source.split("(")[0])}


@pytest.mark.parametrize(
    ("user_type", "message"),
    [
        ("unit_user_types.nope.Money", "cannot import unit_user_types.nope"),
        ("unit_user_types.money.Nope", "has no Nope"),
        ("unit_user_types.money.NOT_A_TYPE", "is not a type"),
        ("unit_user_types.money.AMOUNT(2)", "is a type instance"),
        ("Money", "must be a dotted path"),
    ],
)
def test_a_wrong_user_type_is_refused_naming_itself(
    user_types: Path, user_type: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        resolve(ForcedType(user_type=user_type, include_types="X"))


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
    assert 'Column("id", uuid_column(), primary_key=True)' in text
    assert 'Column("flag", BOOLEAN(), nullable=False)' in text
    assert 'Column("at", timestamp_column())' in text
    assert "EVENTS = outbox_table(metadata)" in text


def test_the_uuid_guess_is_mysql_s_and_mariadb_s_only() -> None:
    """Round 2, N2 #2: Java forces CHAR(36) named like an id, on MySQL only. On PostgreSQL a
    uuid has its own type, so a 36-character text id is text."""
    table = Table(
        "t", MetaData(), Column("id", CHAR(36), primary_key=True), Column("ext_id", VARCHAR(36))
    )

    on_postgres = render(
        [table], framework=[], migrations_fingerprint="x", module=None, dialect="postgresql"
    )
    on_mysql = render(
        [table], framework=[], migrations_fingerprint="x", module=None, dialect="mysql"
    )

    assert 'Column("id", CHAR(length=36), primary_key=True)' in on_postgres
    assert 'Column("ext_id", VARCHAR(length=36))' in on_postgres
    assert 'Column("id", uuid_column(), primary_key=True)' in on_mysql
    assert 'Column("ext_id", VARCHAR(length=36))' in on_mysql, "VARCHAR is never guessed"


def test_a_user_type_named_like_a_sqlalchemy_type_is_aliased(
    user_types: Path, tmp_path: Path
) -> None:
    (user_types / "clash.py").write_text(
        "from sqlalchemy import Text\n\nclass JSON(Text):\n    pass\n"
    )
    table = Table("t", MetaData(), Column("a", VARCHAR(5)), Column("b", postgresql.JSON()))
    config = SchemagenConfig(
        forced_types=(ForcedType(user_type="unit_user_types.clash.JSON", include_expression="a"),)
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
