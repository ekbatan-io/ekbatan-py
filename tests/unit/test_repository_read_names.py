"""A repository's reads carry Java's names, in snake_case - Dracula 48, the user's decision.

Java's ``findById`` / ``getById`` / ``findAllByIds`` / ``findAll`` / ``existsById`` / ``count`` /
``countWhere`` / ``findOneWhere`` / ``findAllWhere`` / ``existsWhere``, on the sync repository and
on its ``ekbatan.aio`` twin alike. The names the port used before are gone, with no alias.

What stays Python's: a missing row is ``None`` rather than an ``Optional``, and each ``*_where``
method takes one condition or several, and an optional ``shard=``. Java's take exactly one
condition, so none of them can be called with none - ``count()`` and ``find_all()`` are the reads
with no condition, as in Java.
"""

from __future__ import annotations

import inspect

import pytest

from ekbatan.aio.repository import (
    AbstractAsyncRepository,
    AsyncEntityRepository,
    AsyncModelRepository,
    AsyncRepository,
)
from ekbatan.repository import AbstractRepository, EntityRepository, ModelRepository, Repository

JAVA_READS = (
    "find_by_id",
    "get_by_id",
    "find_all_by_ids",
    "find_all",
    "exists_by_id",
    "count",
    "count_where",
    "find_one_where",
    "find_all_where",
    "exists_where",
)
OLD_READS = ("find", "get", "find_many", "find_where", "all", "exists")
WHERE_READS = ("count_where", "find_one_where", "find_all_where", "exists_where")

REPOSITORIES = (
    AbstractRepository,
    ModelRepository,
    EntityRepository,
    AbstractAsyncRepository,
    AsyncModelRepository,
    AsyncEntityRepository,
)


def name(cls: type) -> str:
    return cls.__name__


@pytest.mark.parametrize("cls", REPOSITORIES, ids=name)
def test_a_repository_reads_by_javas_names(cls):
    assert [n for n in JAVA_READS if not callable(getattr(cls, n, None))] == []


@pytest.mark.parametrize("cls", REPOSITORIES, ids=name)
def test_the_names_used_before_are_gone(cls):
    assert [n for n in OLD_READS if hasattr(cls, n)] == []


@pytest.mark.parametrize("protocol", [Repository, AsyncRepository], ids=name)
def test_what_the_executor_asks_of_a_repository_is_find_all(protocol):
    assert "find_all" in protocol.__abstractmethods__
    assert "all" not in protocol.__abstractmethods__


@pytest.mark.parametrize("cls", REPOSITORIES, ids=name)
def test_count_takes_no_condition_and_count_where_does(cls):
    kinds = [p.kind for p in inspect.signature(cls.count).parameters.values()]
    assert inspect.Parameter.VAR_POSITIONAL not in kinds, "count() counts every live row"


@pytest.mark.parametrize("cls", REPOSITORIES, ids=name)
@pytest.mark.parametrize("read", WHERE_READS)
def test_each_where_read_needs_one_condition_and_takes_more(cls, read):
    first, more, *rest = list(inspect.signature(getattr(cls, read)).parameters.values())[1:]
    assert first.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert first.default is inspect.Parameter.empty, "a condition is required, as in Java"
    assert more.kind is inspect.Parameter.VAR_POSITIONAL
    assert [p.name for p in rest] == ["shard"]
