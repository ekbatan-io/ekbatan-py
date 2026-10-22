"""Which class a repository reads its rows into: the record its generics name.

`ModelRepository[Wallet, WalletsRecord, ShardedId[Wallet]]` says it, so every read builds a
`WalletsRecord` per row before `from_record` sees it - found by walking the class's generic
bases once, through any generic class of the application's own in between. Where the generics
name no record class - a `TypedDict`, a `Mapping`, nothing - the rows are plain `dict`s.
"""

from __future__ import annotations

import types
from collections.abc import Mapping
from typing import Any, TypedDict

import pytest

from ekbatan import EntityRepository, ModelRepository
from ekbatan.aio import AsyncEntityRepository, AsyncModelRepository
from ekbatan.repository import AbstractRepository, _record_type_of


class ThingsRecord(dict[str, Any]):
    __slots__ = ()


class OldStyle(TypedDict):
    id: int


class Direct(ModelRepository[Any, ThingsRecord, Any]):
    pass


class Inherited(Direct):
    pass


class Generic[R: Mapping[str, Any]](ModelRepository[Any, R, Any]):
    pass


class Through(Generic[ThingsRecord]):
    pass


class Mixin:
    pass


class WithMixin(Mixin, EntityRepository[Any, ThingsRecord, Any]):
    pass


def _made(base: Any) -> type:
    return types.new_class("Made", (base,))


@pytest.mark.parametrize(
    ("repository", "record"),
    [
        (Direct, ThingsRecord),
        (Inherited, ThingsRecord),
        (Through, ThingsRecord),
        (WithMixin, ThingsRecord),
        (_made(AsyncModelRepository[Any, ThingsRecord, Any]), ThingsRecord),
        (_made(AsyncEntityRepository[Any, ThingsRecord, Any]), ThingsRecord),
        (_made(AbstractRepository[Any, ThingsRecord, Any]), ThingsRecord),
        (_made(ModelRepository[Any, OldStyle, Any]), dict),
        (_made(ModelRepository[Any, Mapping[str, Any], Any]), dict),
        (_made(ModelRepository[Any, dict[str, Any], Any]), dict),
        (Generic, dict),
    ],
)
def test_the_record_is_read_off_the_generics(repository: Any, record: type) -> None:
    assert _record_type_of(repository) is record
