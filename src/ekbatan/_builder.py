"""The shared half of the builders for the framework's plain data classes.

Each builder keeps its own typed setters - that is what a checker reads - and hands the value to
:meth:`FieldBuilder._set`. What they share is ``build()``: every required field named, in one
refusal, before the constructor is reached. Left to the dataclass, a missing field is a
``TypeError`` about a keyword argument, which names the constructor rather than the setter that
was never called.
"""

from __future__ import annotations

from typing import Any, ClassVar, Self, cast

from .errors import ValidationError


class FieldBuilder[T]:
    """Collects fields by name, then builds ``_target`` from them."""

    __slots__ = ("_given",)

    #: The class ``build()`` constructs.
    _target: ClassVar[type[Any]]
    #: Fields ``build()`` refuses to go without, in the order they are named when missing.
    _required: ClassVar[tuple[str, ...]] = ()
    #: Values used for a field whose setter was never called, where the dataclass has none.
    _defaults: ClassVar[dict[str, Any]] = {}

    def __init__(self) -> None:
        self._given: dict[str, Any] = {}

    def _set(self, name: str, value: Any) -> Self:
        self._given[name] = value
        return self

    def build(self) -> T:
        missing = [name for name in self._required if self._given.get(name) is None]
        if missing:
            verb = "is" if len(missing) == 1 else "are"
            raise ValidationError(f"{', '.join(missing)} {verb} required")
        return cast("T", self._target(**{**self._defaults, **self._given}))
