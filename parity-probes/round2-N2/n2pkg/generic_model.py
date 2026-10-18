"""Area N2: a generic model - Java's @AutoBuilder supports one (Box<T>)."""

from __future__ import annotations

from dataclasses import dataclass

from ekbatan.domain import GenericState, Model
from ekbatan.ids import Id

from .generic_builders import BoxBuilder


@dataclass(frozen=True, kw_only=True)
class Box[T](Model[Id["Box"], GenericState, BoxBuilder]):
    content: T
