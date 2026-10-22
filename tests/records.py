"""Reading a record class back: which key each keyword fills, and what type it holds.

A record is a ``dict`` subclass with a keyword ``__init__`` (schemagen's, or one written by hand
the same way), so the only place its columns and their types are written down at run time is
that ``__init__``. Each keyword is passed a marker of its own, and the key the marker lands
under is the column the keyword fills - so ``class_`` is found to fill ``class`` by running the
code, not by repeating the generator's naming rule.

No database: the unit suite uses this too.
"""

from __future__ import annotations

import functools
import inspect
import operator
import sys
import typing
from typing import Any


def keywords(record: type) -> dict[str, str]:
    """Each column's keyword: ``{"class": "class_", "order date": "order_date", ...}``."""
    parameters = [
        p
        for p in inspect.signature(record.__init__).parameters.values()
        if p.kind is inspect.Parameter.KEYWORD_ONLY
    ]
    markers = {p.name: object() for p in parameters}
    built: dict[str, Any] = dict.__new__(record)
    record.__init__(built, **markers)
    by_marker = {id(v): k for k, v in markers.items()}
    return {key: by_marker[id(value)] for key, value in built.items()}


def filled(record: type) -> set[str]:
    """The columns the database fills: those whose keyword defaults to ``UNSET``."""
    from ekbatan.schema import UNSET

    defaults = {
        p.name for p in inspect.signature(record.__init__).parameters.values() if p.default is UNSET
    }
    return {key for key, keyword in keywords(record).items() if keyword in defaults}


def hints(record: type, namespace: dict[str, Any] | None = None) -> dict[str, Any]:
    """Each column's type, by its key, as ``record[key]`` reads it: a column the database fills
    without the ``| Unset`` its keyword carries.

    Read in ``namespace`` - a module run by ``exec`` has no ``sys.modules`` entry - or else in
    the record's own module. Evaluated in a copy: a warning raised while a hint runs is
    registered in the evaluating frame's globals as ``__warningregistry__``, and a caller's
    namespace would grow under it.
    """
    from ekbatan.schema import Unset

    scope = dict(namespace if namespace is not None else vars(sys.modules[record.__module__]))
    annotations = record.__init__.__annotations__
    found: dict[str, Any] = {}
    for key, keyword in keywords(record).items():
        hint = annotations[keyword]
        if isinstance(hint, str):
            hint = eval(hint, scope)
        elif isinstance(hint, typing.ForwardRef):
            hint = eval(hint.__forward_arg__, scope)
        args = typing.get_args(hint)
        if Unset in args:
            kept = tuple(a for a in args if a is not Unset)
            hint = kept[0] if len(kept) == 1 else functools.reduce(operator.or_, kept)
        found[key] = hint
    return found
