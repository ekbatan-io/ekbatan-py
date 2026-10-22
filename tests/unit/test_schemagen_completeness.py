"""schemagen's completeness, without a database: the readers of the database's own text, and
what the writer makes of what they read.

The round trips that prove each of these on a real database are in
`tests/integration/test_schemagen_round_trip.py`; the ledger is
`docs/SCHEMAGEN-COMPLETENESS-LEDGER.md`.
"""

from __future__ import annotations

import pytest

from ekbatan.schemagen._reflect import (
    index_parts,
    mysql_table_parts,
    split_top_level,
    sqlite_table_parts,
    unquote,
)

# -- index text -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statement", "elements", "tail"),
    [
        ("CREATE INDEX i ON t (a)", ["a"], ""),
        (
            "CREATE INDEX t_mixed ON public.t USING btree (a, ((b + 1)) DESC, "
            'lower(name) COLLATE "C")',
            ["a", "((b + 1)) DESC", 'lower(name) COLLATE "C"'],
            "",
        ),
        (
            "CREATE UNIQUE INDEX u ON public.t USING btree (lower(name), a) WHERE (a IS NOT NULL)",
            ["lower(name)", "a"],
            "WHERE (a IS NOT NULL)",
        ),
        (
            "CREATE INDEX i ON public.t USING btree (a) INCLUDE (b, name)",
            ["a"],
            "INCLUDE (b, name)",
        ),
        # A table whose name holds what the reader looks for.
        ('CREATE INDEX i ON "t (on, x)" (a, b)', ["a", "b"], ""),
        ('CREATE INDEX "x ON y" ON t ("a,b" DESC)', ['"a,b" DESC'], ""),
        ("CREATE INDEX i\nON t\n(a)\nWHERE a > 0", ["a"], "WHERE a > 0"),
        ("CREATE INDEX i ON t (substr(name, 1, 2), 'x,y')", ["substr(name, 1, 2)", "'x,y'"], ""),
        ("CREATE INDEX i ON ONLY public.m USING btree (a)", ["a"], ""),
    ],
)
def test_an_index_s_elements_are_read_as_the_database_wrote_them(
    statement: str, elements: list[str], tail: str
) -> None:
    assert index_parts(statement) == (elements, tail)


def test_text_that_is_no_index_reads_as_nothing() -> None:
    assert index_parts("CREATE INDEX i") is None


def test_splitting_respects_parentheses_and_every_kind_of_quote() -> None:
    assert split_top_level("a, f(b, c), 'x,y', \"p,q\", `r,s`, [t,u]") == [
        "a",
        "f(b, c)",
        "'x,y'",
        '"p,q"',
        "`r,s`",
        "[t,u]",
    ]


@pytest.mark.parametrize(
    ("token", "name"),
    [('"a ""b"""', 'a "b"'), ("`a``b`", "a`b"), ("[a b]", "a b"), ("plain", "plain")],
)
def test_unquote(token: str, name: str) -> None:
    assert unquote(token) == name


# -- SQLite's table text ----------------------------------------------------------------------


def test_sqlite_column_collations_are_read_and_a_default_saying_collate_is_not() -> None:
    parts = sqlite_table_parts(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, a VARCHAR(20) COLLATE NOCASE, "
        'b TEXT DEFAULT \'x COLLATE y\', "c d" TEXT NOT NULL COLLATE "RTRIM", '
        "CONSTRAINT k CHECK (a <> 'COLLATE'))"
    )
    assert parts.collations == {"a": "NOCASE", "c d": "RTRIM"}


@pytest.mark.parametrize(
    ("statement", "alias", "options"),
    [
        ("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)", "id", ("AUTOINCREMENT",)),
        ("CREATE TABLE t (id integer primary key, v TEXT)", "id", ()),
        ("CREATE TABLE t (id INTEGER, v TEXT, PRIMARY KEY (id))", "id", ()),
        ("CREATE TABLE t (id INTEGER, v TEXT, PRIMARY KEY (id AUTOINCREMENT))", "id", ()),
        # Not the rowid's alias: another type, DESC, two columns, or no rowid at all.
        ("CREATE TABLE t (id INT PRIMARY KEY)", None, ()),
        ("CREATE TABLE t (id BIGINT PRIMARY KEY)", None, ()),
        ("CREATE TABLE t (id INTEGER PRIMARY KEY DESC)", None, ()),
        ("CREATE TABLE t (a INTEGER, b INTEGER, PRIMARY KEY (a, b))", None, ()),
        ("CREATE TABLE t (k INTEGER PRIMARY KEY, v INT) WITHOUT ROWID", None, ("WITHOUT ROWID",)),
        ("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT) STRICT", "id", ("STRICT",)),
        (
            "CREATE TABLE t (k TEXT PRIMARY KEY) STRICT, WITHOUT ROWID",
            None,
            ("STRICT", "WITHOUT ROWID"),
        ),
    ],
)
def test_sqlite_rowid_alias_and_table_options(
    statement: str, alias: str | None, options: tuple[str, ...]
) -> None:
    parts = sqlite_table_parts(statement)
    assert parts.rowid_alias == alias
    expected = options
    if "AUTOINCREMENT)" in statement:
        expected = ("AUTOINCREMENT",)
    assert parts.options == expected


# -- what a column given as the database spells its type reads back as -------------------------


@pytest.mark.parametrize(
    ("kind", "spelled", "expected"),
    [
        ("postgresql", "xml", str),
        ("postgresql", "tsquery", str),
        ("postgresql", "jsonpath", str),
        ("postgresql", "xml[]", list[str]),
        ("postgresql", "xml[][]", list[list[str]]),
        # The drivers disagree (asyncpg's Point, psycopg's text): nothing to say.
        ("postgresql", "point", None),
        ("postgresql", "point[]", None),
        ("postgresql", "address", None),
        ("mysql", "point", bytes),
        ("mysql", "point SRID 4326", bytes),
        ("mysql", "geometry", bytes),
        ("mariadb", "inet6", str),
        ("mariadb", "inet4", str),
        ("mysql", "inet6", None),
        # SQLite, by its affinity rules.
        ("sqlite", "UNSIGNED BIG INT", int),
        ("sqlite", "VARYING CHARACTER(255)", str),
        ("sqlite", "CLOB", str),
        ("sqlite", "DOUBLE PRECISION", float),
        ("sqlite", "FLOATING POINT", int),  # "POINT" holds "INT": SQLite's rule, in order
        ("sqlite", "", None),
        ("sqlite", "ANY", None),
        ("sqlite", "my type(5)", None),
    ],
)
def test_a_spelled_type_reads_as_what_every_driver_gives(
    kind: str, spelled: str, expected: object
) -> None:
    from ekbatan.schemagen._records import spelled_type

    assert spelled_type(kind, spelled) == expected


# -- MySQL's and MariaDB's text of a table ----------------------------------------------------


def test_mysql_partitioning_is_read_whole_from_its_versioned_comment() -> None:
    created = (
        "CREATE TABLE `r` (\n  `id` int NOT NULL,\n  `d` date NOT NULL,\n"
        "  PRIMARY KEY (`id`,`d`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 "
        "COLLATE=utf8mb4_0900_ai_ci STATS_PERSISTENT=0\n"
        "/*!50100 PARTITION BY LIST (`id`)\n"
        "(PARTITION a VALUES IN (1,2) ENGINE = InnoDB,\n"
        " PARTITION b VALUES IN (3) ENGINE = InnoDB) */"
    )
    found = mysql_table_parts(created)
    assert found.partitioning == (
        "PARTITION BY LIST (`id`)\n(PARTITION a VALUES IN (1,2) ENGINE = InnoDB,\n"
        " PARTITION b VALUES IN (3) ENGINE = InnoDB)"
    )
    assert found.options == (
        ("ENGINE", "InnoDB"),
        ("DEFAULT CHARSET", "utf8mb4"),
        ("COLLATE", "utf8mb4_0900_ai_ci"),
        ("STATS_PERSISTENT", "0"),
    )
    assert found.unread == ""


def test_mariadb_partitioning_is_bare_and_quoted_options_keep_their_quotes() -> None:
    created = (
        "CREATE TABLE `r` (\n  `id` int(11) NOT NULL\n) ENGINE=InnoDB "
        "COMMENT='it''s, = here' /*!80016 ENCRYPTION='N' */ TABLESPACE `ts`\n"
        " PARTITION BY HASH (`id`)\nPARTITIONS 4"
    )
    found = mysql_table_parts(created)
    assert found.partitioning == "PARTITION BY HASH (`id`)\nPARTITIONS 4"
    assert found.options == (
        ("ENGINE", "InnoDB"),
        ("COMMENT", "'it''s, = here'"),
        ("ENCRYPTION", "'N'"),
    )
    # What is not NAME=value is not guessed at: the run notes it.
    assert found.unread == "TABLESPACE `ts`"


def test_a_table_with_no_options_or_partitions() -> None:
    found = mysql_table_parts("CREATE TABLE `t` (\n  `id` int NOT NULL\n)")
    assert (found.partitioning, found.options, found.unread) == (None, (), "")


def test_sqlite_foreign_keys_are_read_with_deferrable_wherever_it_is_written() -> None:
    from ekbatan.schemagen._reflect import SqliteForeignKey

    found = sqlite_table_parts(
        "CREATE TABLE c (id INTEGER PRIMARY KEY,\n"
        "  p INT REFERENCES cc (id) ON DELETE CASCADE,\n"
        "  q INT NOT NULL CONSTRAINT q_fk REFERENCES c (id) DEFERRABLE INITIALLY DEFERRED,\n"
        "  r INT REFERENCES c NOT DEFERRABLE,\n"
        '  a INT, b INT, FOREIGN KEY ("a", b) REFERENCES cp (a, b)\n'
        "    ON UPDATE CASCADE\n    DEFERRABLE INITIALLY IMMEDIATE)"
    ).foreign_keys
    assert found == (
        SqliteForeignKey(("p",), False, False),
        SqliteForeignKey(("q",), True, True),
        SqliteForeignKey(("r",), False, False),
        SqliteForeignKey(("a", "b"), True, False),
    )


def test_sqlite_generated_columns_are_read_each_on_its_own() -> None:
    found = sqlite_table_parts(
        "CREATE TABLE g (id INTEGER PRIMARY KEY, qty INT,\n"
        "  doubled INT GENERATED ALWAYS AS (qty * 2) STORED, virt INT AS (qty + 1),\n"
        "  v2 AS (f(qty, ')')) VIRTUAL, label TEXT DEFAULT 'AS (x)')"
    ).generated
    assert found == {
        "doubled": ("qty * 2", True),
        "virt": ("qty + 1", False),
        "v2": ("f(qty, ')')", False),
    }
