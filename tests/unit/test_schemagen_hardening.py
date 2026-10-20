"""schemagen's edges, without a database: every way the file can be wrong, every way a forced
type can be written wrong, and what the writer does with names, text and types nobody planned.

The happy paths are in `test_schemagen_config.py`; the same subjects against real databases are
in `tests/integration/test_schemagen_hardening.py`. The ledger is `docs/SCHEMAGEN-TEST-LEDGER.md`.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import (
    CHAR,
    DATE,
    INTEGER,
    TEXT,
    VARCHAR,
    Column,
    DateTime,
    FetchedValue,
    Interval,
    LargeBinary,
    MetaData,
    String,
    Table,
    Unicode,
    Uuid,
    text,
)
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.types import NullType, UserDefinedType

import ekbatan.schemagen as schemagen
from ekbatan.errors import ValidationError
from ekbatan.schemagen import (
    DEFAULT_FORCED_TYPES,
    ForcedType,
    SchemagenConfig,
    generate,
    load_config,
    render,
)
from ekbatan.schemagen._config import DEFAULT_IMAGE
from ekbatan.schemagen._types import emit_type, resolve, type_names

PASSWORD = "S3cret-hardening-Pw-4417"

# -- the configuration file: every way it can be wrong (ledger C-*) -------------------------


def _file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(f"[tool.ekbatan.schemagen]\n{body}\n")
    return path


def _refused(tmp_path: Path, body: str) -> str:
    with pytest.raises(ValidationError) as raised:
        load_config(_file(tmp_path, body))
    return str(raised.value)


TOP_KEYS = [
    "migrations",
    "out",
    "url",
    "image",
    "schemas",
    "includes",
    "excludes",
    "views",
    "forced-types",
    "default-forced-types",
]
FORCED_KEYS = [
    "name",
    "converter",
    "sqlalchemy-type",
    "include-expression",
    "include-types",
    "exclude-expression",
    "exclude-types",
    "nullability",
]


@pytest.mark.parametrize("key", TOP_KEYS)
def test_a_typo_in_any_key_is_refused_naming_it_and_the_known_ones(
    tmp_path: Path, key: str
) -> None:
    """A misspelt setting that silently did nothing would generate the wrong module and say
    nothing; refused, it names itself and lists what is known."""
    typo = key[:-1] + key[-1].upper() + "x"

    message = _refused(tmp_path, f'{typo} = "a"')

    assert f"unknown key(s) ['{typo}']" in message
    assert str(sorted(TOP_KEYS)) in message


@pytest.mark.parametrize("key", [*FORCED_KEYS, "dialects"])
def test_a_typo_in_any_forced_type_key_is_refused(tmp_path: Path, key: str) -> None:
    """`dialects` too: only the built-in forced types use it, and a project cannot set it."""
    typo = key if key == "dialects" else key + "s"

    message = _refused(
        tmp_path,
        "[[tool.ekbatan.schemagen.forced-types]]\n"
        f'name = "Text"\ninclude-types = "X"\n{typo} = "a"',
    )

    assert f"forced-types[0]: unknown key(s) ['{typo}']" in message
    assert str(sorted(FORCED_KEYS)) in message


@pytest.mark.parametrize("key", ["migrations", "out", "image", "includes", "excludes"])
@pytest.mark.parametrize("value", ["1", "true", '["a"]', "{ a = 1 }"])
def test_a_text_setting_given_anything_but_text_is_refused(
    tmp_path: Path, key: str, value: str
) -> None:
    assert f"{key} must be a string, got " in _refused(tmp_path, f"{key} = {value}")


@pytest.mark.parametrize("value", ['["{url}"]', '{{ a = "{url}" }}', "1"])
def test_a_url_of_the_wrong_kind_is_refused_without_printing_it(tmp_path: Path, value: str) -> None:
    """A url can carry a password, and a refused one used to be printed back in the error."""
    url = f"postgresql://app:{PASSWORD}@db/app"

    message = _refused(tmp_path, "url = " + value.format(url=url))

    assert "url must be a string" in message
    assert PASSWORD not in message and "app:" not in message


@pytest.mark.parametrize("key", ["views", "default-forced-types"])
@pytest.mark.parametrize("value", ['"false"', "0", "1", '["true"]'])
def test_a_switch_given_anything_but_true_or_false_is_refused(
    tmp_path: Path, key: str, value: str
) -> None:
    """`"false"` is the dangerous one: as a non-empty string it would read as true."""
    assert f"{key} must be true or false, got " in _refused(tmp_path, f"{key} = {value}")


def test_schemas_may_be_one_name_written_bare(tmp_path: Path) -> None:
    config = load_config(_file(tmp_path, 'schemas = "eventlog"'))

    assert config is not None and config.schemas == ("eventlog",)


@pytest.mark.parametrize("value", ["[1]", "5", '[["a"]]', "true"])
def test_schemas_that_are_not_names_are_refused(tmp_path: Path, value: str) -> None:
    assert "schemas must be a list of names" in _refused(tmp_path, f"schemas = {value}")


@pytest.mark.parametrize(
    ("key", "label"),
    [("includes", "includes"), ("excludes", "excludes")],
)
def test_a_bad_table_pattern_is_refused(tmp_path: Path, key: str, label: str) -> None:
    assert f"{label} '[a' is not a regular expression" in _refused(tmp_path, f'{key} = "[a"')


@pytest.mark.parametrize(
    "key", ["include-expression", "include-types", "exclude-expression", "exclude-types"]
)
def test_a_bad_forced_type_pattern_is_refused_naming_the_key(tmp_path: Path, key: str) -> None:
    include = "" if key.startswith("include") else 'include-types = "X"\n'
    message = _refused(
        tmp_path,
        f'[[tool.ekbatan.schemagen.forced-types]]\nname = "Text"\n{include}{key} = "(a"',
    )

    assert f"has a bad {key} '(a'" in message
    assert "forced-types[0]" in message


@pytest.mark.parametrize("key", FORCED_KEYS)
def test_a_forced_type_value_that_is_not_text_is_refused(tmp_path: Path, key: str) -> None:
    message = _refused(tmp_path, f"[[tool.ekbatan.schemagen.forced-types]]\n{key} = 1")

    assert f"forced-types[0]: {key} must be a string, got 1" in message


@pytest.mark.parametrize(
    ("targets", "named"),
    [
        ('name = "Text"\nconverter = "a.B"', "name='Text', converter='a.B'"),
        ('name = "Text"\nsqlalchemy-type = "a.B"', "name='Text', sqlalchemy-type='a.B'"),
        ('converter = "a.B"\nsqlalchemy-type = "a.C"', "converter='a.B', sqlalchemy-type='a.C'"),
        (
            'name = "Text"\nconverter = "a.B"\nsqlalchemy-type = "a.C"',
            "name='Text', converter='a.B', sqlalchemy-type='a.C'",
        ),
        ("", "none"),
    ],
    ids=[
        "name+converter",
        "name+sqlalchemy-type",
        "converter+sqlalchemy-type",
        "all three",
        "none",
    ],
)
def test_a_forced_type_needs_exactly_one_target(tmp_path: Path, targets: str, named: str) -> None:
    message = _refused(
        tmp_path, f'[[tool.ekbatan.schemagen.forced-types]]\n{targets}\ninclude-types = "X"'
    )

    assert f"needs exactly one of name, converter and sqlalchemy-type, got {named}" in message


@pytest.mark.parametrize(
    "rest",
    ['exclude-expression = "a"', 'exclude-types = "a"', 'nullability = "NULL"', ""],
    ids=["only an exclude-expression", "only an exclude-types", "only nullability", "nothing"],
)
def test_a_forced_type_without_an_include_is_refused(tmp_path: Path, rest: str) -> None:
    """An exclude alone, or a nullability alone, still means every other column."""
    message = _refused(tmp_path, f'[[tool.ekbatan.schemagen.forced-types]]\nname = "Text"\n{rest}')

    assert "needs include-expression or include-types" in message


@pytest.mark.parametrize("extra", ["", 'converter = "a.B"\n'], ids=["alone", "beside converter"])
def test_the_old_user_type_key_is_explained_even_beside_a_new_one(
    tmp_path: Path, extra: str
) -> None:
    message = _refused(
        tmp_path,
        f'[[tool.ekbatan.schemagen.forced-types]]\nuser-type = "a.M"\n{extra}include-types = "X"',
    )

    assert "user-type is now two keys" in message


@pytest.mark.parametrize("value", ["not_null", "None", "", "NOTNULL", "all"])
def test_nullability_is_one_of_three_words_in_capitals(tmp_path: Path, value: str) -> None:
    message = _refused(
        tmp_path,
        f'[[tool.ekbatan.schemagen.forced-types]]\nname = "Text"\ninclude-types = "X"\n'
        f'nullability = "{value}"',
    )

    assert f"nullability {value!r}; use ALL, NULL or NOT_NULL" in message


def test_forced_types_written_as_one_table_are_refused(tmp_path: Path) -> None:
    """`[tool.ekbatan.schemagen.forced-types]` with single brackets is a table, not a list of
    them - an easy slip, and it would otherwise be read as nothing."""
    message = _refused(
        tmp_path, '[tool.ekbatan.schemagen.forced-types]\nname = "Text"\ninclude-types = "X"'
    )

    assert "forced-types must be an array of tables" in message


def test_a_forced_type_that_is_not_a_table_is_refused(tmp_path: Path) -> None:
    assert "forced-types[1]: must be a table, got 'Text'" in _refused(
        tmp_path, 'forced-types = [{ name = "Text", include-types = "X" }, "Text"]'
    )


def test_the_error_names_the_file_and_which_forced_type(tmp_path: Path) -> None:
    message = _refused(
        tmp_path,
        '[[tool.ekbatan.schemagen.forced-types]]\nname = "Text"\ninclude-types = "X"\n\n'
        '[[tool.ekbatan.schemagen.forced-types]]\nname = "Text"\ninclude-types = "("',
    )

    assert message.startswith(f"[tool.ekbatan.schemagen] in {tmp_path / 'pyproject.toml'}, ")
    assert "forced-types[1]:" in message


def test_a_named_file_that_is_missing_is_an_error_and_the_default_one_is_not(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="no configuration file at"):
        load_config(tmp_path / "nope.toml")

    assert load_config(tmp_path / "pyproject.toml", required=False) is None


def test_a_file_that_is_not_toml_is_refused_naming_it(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text("[tool.ekbatan.schemagen\nout = 'x'\n")

    with pytest.raises(ValidationError, match=r"pyproject.toml is not valid TOML: "):
        load_config(path)


def test_a_directory_named_as_the_file_is_an_error_not_a_traceback(tmp_path: Path) -> None:
    """`--config .` is a slip anyone makes; it was an `IsADirectoryError` traceback."""
    with pytest.raises(ValidationError, match=f"cannot read {tmp_path}"):
        load_config(tmp_path)


def test_a_file_that_is_not_utf_8_is_an_error_not_a_traceback(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_bytes(b'[tool.ekbatan.schemagen]\nout = "caf\xe9.py"\n')

    with pytest.raises(ValidationError, match="is not UTF-8"):
        load_config(path)


@pytest.mark.parametrize(
    "body",
    [
        f'[tool.ekbatan]\nschemagen = "postgresql://app:{PASSWORD}@db/app"',
        "[tool]\nekbatan = 1",
        "tool = 1",
    ],
    ids=["schemagen a string", "ekbatan a number", "tool a number"],
)
def test_a_section_that_is_not_a_table_is_refused_as_such(tmp_path: Path, body: str) -> None:
    """Not "unknown key(s) [...]" - the letters of a string - and not an AttributeError. And
    the value is not shown: a url put there by mistake would carry its password."""
    path = tmp_path / "pyproject.toml"
    path.write_text(body + "\n")

    with pytest.raises(ValidationError, match="must be a table, got a ") as raised:
        load_config(path)
    assert PASSWORD not in str(raised.value)


def test_paths_are_relative_to_the_file_and_absolute_ones_are_kept(tmp_path: Path) -> None:
    elsewhere = tmp_path / "abs" / "migrations"
    config = load_config(_file(tmp_path, f'migrations = "{elsewhere}"\nout = "../gen/schema.py"'))

    assert config is not None
    assert config.migrations == elsewhere
    assert config.out == tmp_path / ".." / "gen" / "schema.py"


def test_an_empty_image_or_includes_falls_back_to_the_default(tmp_path: Path) -> None:
    """As written today: an empty string is no setting, not "match nothing". Pinned so that a
    change is a decision (see DECISIONS-TO-REVIEW, schemagen tests)."""
    config = load_config(_file(tmp_path, 'image = ""\nincludes = ""'))

    assert config is not None
    assert (config.image, config.includes) == (DEFAULT_IMAGE, ".*")


def test_a_target_is_what_the_columns_become_as_written() -> None:
    assert ForcedType(name="Text", include_types="X").target == "Text"
    assert ForcedType(converter="a.B(1)", include_types="X").target == "a.B(1)"
    assert ForcedType(sqlalchemy_type="a:C", include_types="X").target == "a:C"


# -- how a forced type picks its columns: the parts the other file leaves (ledger G-*) --------


def _takes(forced: ForcedType, *, nullable: bool = True, dialect: str | None = "mysql") -> bool:
    return forced.matches(
        dialect=dialect, names=["note", "t.note"], types=("TEXT",), nullable=nullable
    )


@pytest.mark.parametrize(
    ("nullability", "on_null", "on_not_null"),
    [("ALL", True, True), ("NULL", True, False), ("NOT_NULL", False, True)],
)
def test_nullability_picks_by_null_and_not_null(
    nullability: Any, on_null: bool, on_not_null: bool
) -> None:
    forced = ForcedType(name="Text", include_types="TEXT", nullability=nullability)

    assert _takes(forced, nullable=True) is on_null
    assert _takes(forced, nullable=False) is on_not_null


def test_exclude_types_takes_a_column_back_whatever_its_name() -> None:
    assert not _takes(ForcedType(name="Text", include_expression=".*", exclude_types="text"))
    assert _takes(ForcedType(name="Text", include_expression=".*", exclude_types="BLOB"))


def test_a_forced_type_for_some_databases_takes_nothing_from_the_others() -> None:
    """How the built-ins differ per database. With no database known (`render()` without a
    dialect), only the forced types for every database apply."""
    pg_only = ForcedType(name="Text", include_types="TEXT", dialects=("postgresql",))

    assert _takes(pg_only, dialect="postgresql")
    assert not _takes(pg_only, dialect="mysql")
    assert not _takes(pg_only, dialect=None)


def test_render_without_a_dialect_applies_only_the_defaults_for_every_database() -> None:
    table = Table(
        "t",
        MetaData(),
        Column("id", INTEGER(), primary_key=True),
        Column("doc", postgresql.JSONB()),
        Column("at", DateTime()),
    )

    written = render([table], framework=[], migrations_fingerprint="x", module=None)

    assert 'Column("doc", postgresql.JSONB(none_as_null=True))' in written
    assert 'Column("at", timestamp_column())' in written


@pytest.mark.parametrize(
    ("reflected", "raw", "compiler", "spellings"),
    [
        (postgresql.TIME(timezone=True), "time with time zone", postgresql.dialect(), {"TIMETZ"}),
        (CHAR(36), "character(36)", postgresql.dialect(), {"CHARACTER(36)", "CHAR(36)", "CHAR"}),
        (
            mysql.INTEGER(unsigned=True),
            "int unsigned",
            mysql.dialect(),
            {"INT UNSIGNED", "INTEGER UNSIGNED"},
        ),
        (mysql.TINYINT(display_width=1), "tinyint(1)", mysql.dialect(), {"TINYINT(1)", "TINYINT"}),
        (
            postgresql.NUMERIC(19, 4),
            "numeric(19,4)",
            postgresql.dialect(),
            {"NUMERIC(19,4)", "NUMERIC(19, 4)", "NUMERIC"},
        ),
        (
            postgresql.TIMESTAMP(precision=3),
            "timestamp(3) without time zone",
            postgresql.dialect(),
            {"TIMESTAMP(3) WITHOUT TIME ZONE", "TIMESTAMP(3)", "TIMESTAMP"},
        ),
    ],
    ids=["timetz", "character", "int unsigned", "tinyint(1)", "numeric spaces", "timestamp(3)"],
)
def test_each_spelling_a_pattern_may_be_written_against_is_offered(
    reflected: Any, raw: str, compiler: Any, spellings: set[str]
) -> None:
    assert spellings <= set(type_names(reflected, raw, compiler))


def test_a_type_with_no_compiled_form_is_matched_by_its_spelling_alone() -> None:
    assert type_names(NullType(), "xml", postgresql.dialect()) == ("XML",)


# -- what a forced type turns a column into: the refusals the other file leaves ---------------


@pytest.fixture
def user_types(tmp_path: Path) -> Iterator[str]:
    """Two modules of the user's, each with a `MoneyConverter`, and odd things beside them."""
    package = tmp_path / "hardening_user_types"
    package.mkdir()
    (package / "__init__.py").write_text("")
    converter = (
        "class MoneyConverter:\n"
        "    def __init__(self, places=2):\n"
        "        self.places = places\n"
        "    def from_db(self, value) -> object:\n"
        "        return value\n"
        "    def to_db(self, value):\n"
        "        return value\n"
    )
    (package / "a.py").write_text(
        converter + "\nNUMBER = 3\n\n"
        "from sqlalchemy import Numeric\n"
        "from sqlalchemy.types import TypeDecorator\n\n"
        "class Money(TypeDecorator):\n"
        "    impl = Numeric\n"
        "    cache_ok = True\n"
        "    def __init__(self, scale=2):\n"
        "        super().__init__(precision=19, scale=scale)\n\n"
        "class Outer:\n"
        "    Inner = Money\n"
    )
    (package / "b.py").write_text(converter)
    sys.path.insert(0, str(tmp_path))
    yield "hardening_user_types"
    sys.path.remove(str(tmp_path))
    for name in [m for m in sys.modules if m.startswith("hardening_user_types")]:
        sys.modules.pop(name)


@pytest.mark.parametrize(
    ("kind", "target", "message"),
    [
        ("name", "String(36", "is not a name, or a name with arguments"),
        ("name", 'String(**{"length": 3})', "no ** arguments"),
        ("name", "String(*[3])", "arguments must be plain values"),
        ("name", "mysql.", "is not a type name"),
        ("name", "Te-xt", "is not a type name"),
        ("name", "sqlite.NOPE", "is not a SQLAlchemy type"),
        ("name", "String(length=1, nope=2)", "TypeError"),
        ("name", "json_column(True)", "takes no arguments"),
        ("converter", "{p}.a.NUMBER", "NUMBER has no from_db() and to_db()"),
        ("converter", "{p}.a:Outer.Inner", "must be a dotted path to a converter"),
        ("converter", "{p}.a.MoneyConverter(1, 2, 3)", "could not be built: TypeError"),
        ("sqlalchemy-type", "{p}.a.Money(1, 2, 3)", "could not be built: TypeError"),
        ("sqlalchemy-type", "{p}.a:Outer.Inner", "must be a dotted path to a type"),
        ("sqlalchemy-type", "{p}.a.Money(scale=x)", "arguments must be plain values"),
        ("sqlalchemy-type", ".a.Money", "must be a dotted path"),
    ],
)
def test_a_target_written_wrong_is_refused_naming_itself(
    user_types: str, kind: str, target: str, message: str
) -> None:
    written = target.replace("{p}", user_types)
    forced = ForcedType(**{kind.replace("-", "_"): written, "include_types": "X"})  # type: ignore[arg-type]

    with pytest.raises(ValidationError) as raised:
        resolve(forced)

    assert repr(written) in str(raised.value)
    assert message in str(raised.value)


def test_two_converters_of_one_name_from_two_modules_are_both_imported(user_types: str) -> None:
    """Neither shadows the other: the second is imported under its module's name."""
    table = Table(
        "t",
        MetaData(),
        Column("id", INTEGER(), primary_key=True),
        Column("price", TEXT()),
        Column("cost", TEXT()),
    )
    config = SchemagenConfig(
        forced_types=(
            ForcedType(converter=f"{user_types}.a.MoneyConverter(1)", include_expression="price"),
            ForcedType(converter=f"{user_types}.b.MoneyConverter(2)", include_expression="cost"),
        )
    )

    written = render([table], framework=[], migrations_fingerprint="x", module=None, config=config)
    namespace = _run(written)

    assert f"from {user_types}.a import MoneyConverter\n" in written
    assert f"from {user_types}.b import MoneyConverter as {user_types}_b_MoneyConverter" in written
    price, cost = namespace["T"].c.price.type.converter, namespace["T"].c.cost.type.converter
    assert (type(price).__module__, price.places) == (f"{user_types}.a", 1)
    assert (type(cost).__module__, cost.places) == (f"{user_types}.b", 2)


# -- the type table, on types no database reflects ------------------------------------------


class _Unbuildable(UserDefinedType[Any]):
    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "WEIRD"

    def __repr__(self) -> str:
        return "Weird()"


class _NotAType(UserDefinedType[Any]):
    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "ODD"

    def __repr__(self) -> str:
        return "MetaData()"


@pytest.mark.parametrize(
    ("reflected", "source"),
    [
        (LargeBinary(), "sqlalchemy.LargeBinary()"),
        (Uuid(), "sqlalchemy.Uuid()"),
        (String(10), "sqlalchemy.VARCHAR(length=10)"),
        (Unicode(10), "sqlalchemy.VARCHAR(length=10)"),
        (Interval(), "sqlalchemy.Interval()"),
        (_Unbuildable(), None),
        (_NotAType(), None),
        (postgresql.ARRAY(NullType()), None),
    ],
    ids=[
        "generic LargeBinary",
        "generic Uuid",
        "generic String",
        "Unicode",
        "a type of sqlalchemy's own with no rule",
        "a repr that does not build",
        "a repr that builds something else",
        "an array of a type with no spelling",
    ],
)
def test_types_no_database_reflects_are_written_faithfully_or_not_at_all(
    reflected: Any, source: str | None
) -> None:
    """Only `render()` on tables built by hand meets these. "Not at all" is `None`: the caller
    then writes the database's spelling, or `NullType()` with a note - never a guess."""
    emitted = emit_type(reflected, None)

    assert (emitted.source if emitted else None) == source


# -- writing the module: names, text, defaults (ledger F-*) ----------------------------------


def _render(*tables: Table, dialect: str = "postgresql") -> str:
    return render(
        list(tables), framework=[], migrations_fingerprint="x", module=None, dialect=dialect
    )


def _run(written: str) -> dict[str, Any]:
    namespace: dict[str, Any] = {"__file__": "schema.py"}
    exec(compile(written, "schema.py", "exec"), namespace)
    return namespace


def _one(name: str, *columns: Column[Any]) -> Table:
    return Table(name, MetaData(), Column("id", INTEGER(), primary_key=True), *columns)


@pytest.mark.parametrize(
    ("table", "constant"),
    [
        ("2024_sales", "T_2024_SALES"),
        ("order items", "ORDER_ITEMS"),
        ("order-items", "ORDER_ITEMS"),
        ("class", "CLASS_"),
        ("caf\u00e9", "CAF\u00c9"),
    ],
    ids=["starts with a digit", "a space", "a hyphen", "a keyword", "not ascii"],
)
def test_any_table_name_is_exported_under_a_python_name(table: str, constant: str) -> None:
    namespace = _run(_render(_one(table)))

    assert namespace[constant].name == table
    assert constant in namespace["__all__"]


def test_two_tables_exported_under_one_name_are_refused_naming_both() -> None:
    with pytest.raises(
        ValidationError, match="order_items and order-items would both be exported as ORDER_ITEMS"
    ):
        _render(_one("order-items"), _one("order_items"))


@pytest.mark.parametrize(
    "value",
    [
        'a "quoted" word',
        "a back\\slash",
        "line one\nline two",
        "a carriage\rreturn",
        "windows\r\nlines",
        "a\ttab",
        "a bell\x07 and an escape\x1b",
        "a vertical\x0btab and a form\x0cfeed",
        "a nul\x00byte",
        "the end \x7f",
        "\u2028a line separator",
    ],
    ids=[
        "quotes",
        "backslash",
        "newline",
        "carriage return",
        "crlf",
        "tab",
        "bell and escape",
        "vt and ff",
        "nul",
        "del",
        "u2028",
    ],
)
def test_any_text_in_a_comment_or_default_survives_into_the_module(value: str) -> None:
    """A column comment, a table comment, a server default and a column name are written as
    string literals. `\\r` once ended the literal mid-line, and the module did not parse."""
    table = Table(
        "t",
        MetaData(),
        Column("id", INTEGER(), primary_key=True),
        Column(f"c {value}", TEXT(), comment=value, server_default=text(f"'{value}'")),
        comment=value,
    )

    built = _run(_render(table))["T"]

    column = built.c[f"c {value}"]
    assert column.comment == value
    assert built.comment == value
    assert column.server_default.arg.text == f"'{value}'"


@pytest.mark.parametrize(
    "default",
    [FetchedValue(), text("NULL"), text(" null ")],
    ids=["not text", "NULL", "null, spaced"],
)
def test_a_default_that_is_not_one_is_not_written(default: Any) -> None:
    """MariaDB reports `NULL` as the default of every nullable column; a `FetchedValue` has
    no text to write."""
    written = _render(_one("t", Column("note", TEXT(), server_default=default)))

    assert 'Column("note", sqlalchemy.TEXT())' in written


def test_a_sequence_default_in_another_spelling_is_kept_as_written() -> None:
    """`nextval(('s'::text)::regclass)`, as older dumps write it, names no sequence schemagen
    can read: the default is kept as text, and no `Sequence` is guessed."""
    written = _render(
        _one("t", Column("n", INTEGER(), server_default=text("nextval(('s'::text)::regclass)")))
    )

    assert "Sequence(" not in written
    assert "server_default=sqlalchemy.text(\"nextval(('s'::text)::regclass)\")" in written


def test_a_sequence_in_another_schema_is_declared_there() -> None:
    written = _render(
        _one(
            "t",
            Column("n", INTEGER(), server_default=text("nextval('audit.\"t_n_seq\"'::regclass)")),
        )
    )

    assert 'sqlalchemy.Sequence("t_n_seq", schema="audit")' in written


@pytest.mark.parametrize(
    ("first", "column"),
    [("date", DATE()), ("text", TEXT()), ("varchar", VARCHAR(5))],
)
def test_a_table_named_like_a_type_does_not_break_a_later_table(first: str, column: Any) -> None:
    """`DATE = Table("date", ...)` once replaced `from sqlalchemy import DATE`, and the next
    table that wrote `DATE()` called a Table. The module now writes `sqlalchemy.DATE()`. Every
    name schemagen can emit is in `test_schemagen_table_names.py`."""
    namespace = _run(_render(_one(first), _one("zz_later", Column("c", column))))

    assert type(namespace["ZZ_LATER"].c.c.type) is type(column)


@pytest.mark.parametrize("table", ["migrations_fingerprint", "schema_format"])
def test_a_table_named_like_the_module_s_own_constant_is_exported_with_an_underscore(
    table: str,
) -> None:
    """`MIGRATIONS_FINGERPRINT = Table(...)` would replace the fingerprint, and the stale check
    would always fire; so such a table is exported as `MIGRATIONS_FINGERPRINT_`, as a keyword
    is (`class` -> `CLASS_`)."""
    namespace = _run(_render(_one(table)))

    assert namespace["MIGRATIONS_FINGERPRINT"] == "x"
    assert namespace["SCHEMA_FORMAT"] == schemagen.SCHEMA_FORMAT
    assert namespace[f"{table.upper()}_"].name == table
    assert f"{table.upper()}_" in namespace["__all__"]


# -- the throwaway database: which one an image names (ledger F-4, lines 276-306) -------------


class _FakeContainer:
    started: ClassVar[list[tuple[str, str, dict[str, Any]]]] = []
    stopped: ClassVar[list[str]] = []
    url = ""

    def __init__(self, image: str, **options: Any) -> None:
        self.image = image
        type(self).started.append((type(self).__name__, image, options))

    def start(self) -> None:
        pass

    def stop(self) -> None:
        type(self).stopped.append(self.image)

    def get_connection_url(self) -> str:
        return self.url


class _FakeMysql(_FakeContainer):
    url = "mysql+pymysql://root:pw@127.0.0.1:3306/test"


class _FakePostgres(_FakeContainer):
    url = "postgresql+psycopg://test:pw@127.0.0.1:5432/test"


@pytest.fixture
def containers(monkeypatch: pytest.MonkeyPatch) -> type[_FakeContainer]:
    """testcontainers' classes, replaced by ones that record what they were asked to start."""
    import warnings

    import testcontainers.community.mysql as mysql_module

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import testcontainers.postgres as postgres_module

    _FakeContainer.started, _FakeContainer.stopped = [], []
    monkeypatch.setattr(mysql_module, "MySqlContainer", _FakeMysql)
    monkeypatch.setattr(postgres_module, "PostgresContainer", _FakePostgres)
    return _FakeContainer


@pytest.mark.parametrize(
    ("image", "container", "scheme"),
    [
        ("postgres:17-alpine", "_FakePostgres", "postgresql+psycopg"),
        ("pgvector/pgvector:pg17", "_FakePostgres", "postgresql+psycopg"),
        ("mysql:8.4", "_FakeMysql", "mysql+pymysql"),
        ("MySQL:8.4", "_FakeMysql", "mysql+pymysql"),
        ("docker.io/library/mysql:8.4", "_FakeMysql", "mysql+pymysql"),
        ("localhost:5000/mysql:8.4", "_FakeMysql", "mysql+pymysql"),
        ("mysql@sha256:0123abcd", "_FakeMysql", "mysql+pymysql"),
        ("mariadb:11.4", "_FakeMysql", "mariadb+pymysql"),
        ("registry.example.com:443/team/mariadb:11.4", "_FakeMysql", "mariadb+pymysql"),
        ("mariadb", "_FakeMysql", "mariadb+pymysql"),
        ("localhost:5000/postgres:17", "_FakePostgres", "postgresql+psycopg"),
    ],
)
def test_the_image_names_the_database_whatever_registry_or_digest_it_comes_from(
    containers: type[_FakeContainer], image: str, container: str, scheme: str
) -> None:
    """The kind is the image's last path part before its tag or digest. A registry with a
    port (`localhost:5000/mysql:8.4`) or a pinned digest once started PostgreSQL instead."""
    stop, url = schemagen._start(image)

    assert [(c, i) for c, i, _ in containers.started] == [(container, image)]
    assert url.startswith(f"{scheme}://")
    stop()
    assert containers.stopped == [image]


def test_mysql_and_mariadb_containers_are_reached_as_root(containers: type[_FakeContainer]) -> None:
    """The migrations issue DDL, and the default `test` user may touch only `test`."""
    schemagen._start("mariadb:11.4")

    assert containers.started[0][2] == {"dialect": "pymysql", "username": "root"}


@pytest.mark.parametrize("image", ["sqlite", "SQLite", "sqlite:3"])
def test_sqlite_needs_no_container_and_its_file_is_removed(
    containers: type[_FakeContainer], image: str
) -> None:
    stop, url = schemagen._start(image)
    directory = Path(url.removeprefix("sqlite:///")).parent

    assert containers.started == []
    assert directory.is_dir()
    stop()
    assert not directory.exists()


def test_a_started_database_is_stopped_whether_generating_works_or_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stops: list[str] = []
    database = tmp_path / "db.sqlite"

    def start(image: str) -> tuple[Any, str]:
        database.unlink(missing_ok=True)
        return (lambda: stops.append(image)), f"sqlite:///{database}"

    monkeypatch.setattr(schemagen, "_start", start)
    good = tmp_path / "good"
    good.mkdir()
    (good / "V0001__t.sql").write_text("CREATE TABLE t (id INTEGER PRIMARY KEY);")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "V0001__t.sql").write_text("CREATE TABLE broken (;")

    generate(good, tmp_path / "a.py", image="sqlite-ish")
    with pytest.raises(Exception, match="syntax error"):
        generate(bad, tmp_path / "b.py", image="sqlite-ish")

    assert stops == ["sqlite-ish", "sqlite-ish"]


# -- generate's own refusals, before anything starts -----------------------------------------


def test_no_migrations_anywhere_says_where_to_set_them(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"set `migrations` in .* or pass --migrations"):
        generate(None, tmp_path / "schema.py")


def test_no_output_anywhere_says_where_to_set_it(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"no output file: set `out` .* or pass --out"):
        generate(tmp_path, None)


def test_a_wrong_forced_type_in_the_config_fails_before_the_migrations_are_looked_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schemagen, "_start", lambda image: pytest.fail("a database was started"))
    (tmp_path / "m").mkdir()

    with pytest.raises(ValidationError, match=r"'nowhere_at_all\.X'"):
        generate(
            tmp_path / "m",
            tmp_path / "schema.py",
            config=SchemagenConfig(
                forced_types=(ForcedType(converter="nowhere_at_all.X", include_types="X"),)
            ),
        )


# -- how the module finds its migrations (ledger F-8, lines 325-326) -------------------------


@pytest.mark.parametrize(
    ("climb", "kept"),
    [(0, True), (schemagen.MAX_CLIMB, True), (schemagen.MAX_CLIMB + 1, False)],
)
def test_the_module_looks_for_its_migrations_only_a_few_levels_up(
    tmp_path: Path, climb: int, kept: bool
) -> None:
    deep = tmp_path.joinpath(*[f"d{i}" for i in range(climb)])
    relative = schemagen._relative(deep, tmp_path / "migrations")

    assert (relative is not None) is kept
    if kept:
        assert (deep / str(relative)).resolve() == (tmp_path / "migrations").resolve()


def test_on_another_drive_the_module_skips_its_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows' relpath raises across drives; the module then records no path."""
    import os

    def other_drive(target: Any, base: Any) -> str:
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(os.path, "relpath", other_drive)

    assert schemagen._relative(Path("C:/app"), Path("D:/migrations")) is None


def test_a_stale_module_s_error_names_both_fingerprints_and_the_command(tmp_path: Path) -> None:
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "V0001__a.sql").write_text("CREATE TABLE a (id INTEGER);")
    actual = schemagen.fingerprint(tmp_path / "migrations")

    with pytest.raises(ValidationError) as raised:
        schemagen.check_fingerprint(
            str(tmp_path / "app" / "schema.py"), "0" * 16, "../migrations", schemagen.SCHEMA_FORMAT
        )

    message = str(raised.value)
    assert f"schema.py was generated from migrations fingerprinted {'0' * 16}" in message
    assert f"now fingerprints {actual}" in message
    assert "uv run python -m ekbatan.schemagen --migrations " in message
    assert f"--out {tmp_path / 'app' / 'schema.py'}" in message


# -- notes on stderr (lines 388-395) --------------------------------------------------------


def _bare_logging(monkeypatch: pytest.MonkeyPatch) -> logging.Logger:
    """No handler anywhere, as in a plain `python -m`; put back afterwards. Called in the test
    body: pytest adds its own capturing handlers to the root logger after fixtures run."""
    log = logging.getLogger("io.ekbatan.schemagen")
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    monkeypatch.setattr(log, "handlers", [])
    monkeypatch.setattr(log, "propagate", True)
    return log


def test_with_no_logging_set_up_a_note_prints_as_note_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bare_logging = _bare_logging(monkeypatch)
    schemagen._show_notes()
    bare_logging.warning("%s", "t.at is TIMESTAMPTZ")

    captured = capsys.readouterr()
    assert captured.err == "note: t.at is TIMESTAMPTZ\n"
    assert captured.out == ""
    assert bare_logging.propagate is False, "printed once, not again by the root logger"


def test_logging_an_application_set_up_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    bare_logging = _bare_logging(monkeypatch)
    mine = logging.NullHandler()
    logging.getLogger().handlers.append(mine)

    schemagen._show_notes()

    assert bare_logging.handlers == []
    assert logging.getLogger().handlers == [mine]


def test_importing_the_main_module_does_not_run_the_command() -> None:
    import importlib

    module = importlib.import_module("ekbatan.schemagen.__main__")

    assert module.main is schemagen.main


# -- the defaults, as documented ------------------------------------------------------------


def test_the_built_in_forced_types_are_the_four_the_docs_list() -> None:
    """docs/database/schemagen.md, "Ekbatan's built-in forced types": the table there is this."""
    assert [(f.target, f.include_types, f.dialects) for f in DEFAULT_FORCED_TYPES] == [
        ("timestamp_column", "TIMESTAMP|DATETIME", ()),
        ("json_column", "JSONB", ("postgresql",)),
        ("json_column", "JSON", ("mysql", "mariadb", "sqlite")),
        ("uuid_column", "UUID", ("postgresql",)),
    ]
    assert all(
        f.include_expression is None and f.nullability == "ALL" for f in DEFAULT_FORCED_TYPES
    )


def test_a_module_from_before_the_format_number_fails_on_import_and_says_to_regenerate(
    tmp_path: Path,
) -> None:
    """A schema.py written before typed records calls the check with three arguments. Its
    migrations are unchanged, so a build that only regenerates on a migration change keeps it -
    it must not import quietly without the records the repositories now name."""
    with pytest.raises(ValidationError) as raised:
        schemagen.check_fingerprint(str(tmp_path / "schema.py"), "0" * 16, "migrations")

    message = str(raised.value)
    assert "schema.py was written by an older schemagen (format 1" in message
    assert f"this ekbatan writes {schemagen.SCHEMA_FORMAT}" in message
    assert "`make clean && make`" in message


def test_a_module_of_another_format_fails_even_when_its_migrations_are_not_shipped(
    tmp_path: Path,
) -> None:
    """The migrations half is quiet in an installed wheel; the shape half is not, because a
    module and an ekbatan that disagree are broken there too."""
    with pytest.raises(ValidationError, match="older schemagen"):
        schemagen.check_fingerprint(
            str(tmp_path / "schema.py"), "0" * 16, None, schemagen.SCHEMA_FORMAT - 1
        )


def test_a_rendered_module_carries_the_current_format_and_passes_it_to_the_check() -> None:
    from ekbatan.schemagen._render import SCHEMA_FORMAT

    text = schemagen.render([], framework=[], migrations_fingerprint="x", module="migrations")

    assert f"SCHEMA_FORMAT = {SCHEMA_FORMAT}\n" in text
    assert (
        'check_fingerprint(__file__, MIGRATIONS_FINGERPRINT, "migrations", SCHEMA_FORMAT)' in text
    )
