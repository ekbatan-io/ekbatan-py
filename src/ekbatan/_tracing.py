"""Spans, under the same instrumentation scope as the Java implementation's.

OpenTelemetry's API is a dependency, as it is of Java's `ekbatan-core`: it is a few small
modules, and on its own it does nothing - spans go nowhere until the application installs an
SDK, exactly as Java's `GlobalOpenTelemetry` stays a no-op until one is registered.

The scope is Java's, `io.ekbatan.core`, and so are the span names and attributes, so one trace -
and one filter on it - covers both. The version is this package's own, as Java's is its
artifact's.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from importlib import metadata
from typing import Any

import opentelemetry.trace as _otel

__all__ = ["SCOPE", "Span", "span"]

#: Java: `GlobalOpenTelemetry.get().getTracer("io.ekbatan.core", "1.1.2")`.
SCOPE = "io.ekbatan.core"


def _version() -> str:
    try:
        return metadata.version("ekbatan")
    except metadata.PackageNotFoundError:  # pragma: no cover - running from a bare checkout
        return "0"


_TRACER: Any = _otel.get_tracer(SCOPE, _version())


class Span:
    """What a caller can do to a span, whether or not one exists."""

    __slots__ = ("_span",)

    def __init__(self, underlying: Any = None) -> None:
        self._span = underlying

    def set(self, attributes: Mapping[str, Any]) -> None:
        """Attributes under the names they are written with.

        Spelled out by the caller rather than assembled here, because the names are the
        contract: an operator filtering on ``ekbatan.action.name`` is filtering across both
        implementations, and a key this module invented would quietly not be there.
        """
        if self._span is not None:
            for key, value in attributes.items():
                self._span.set_attribute(key, value)

    def event(self, name: str, attributes: Mapping[str, Any]) -> None:
        if self._span is not None:
            self._span.add_event(name, dict(attributes))

    def failed(self, error: BaseException, *, outcome: str | None = None) -> None:
        """Mark this span failed.

        Only the attribute. An exception that escapes the ``with`` is already recorded by
        OpenTelemetry, which sets the status and adds the event itself - doing it here as well
        put the same traceback on the span twice, which is how a trace ends up looking like two
        failures. What it cannot know is the port's own outcome attribute, so that is all this
        adds.
        """
        if self._span is not None and outcome is not None:
            self._span.set_attribute(outcome, "error")


@contextmanager
def span(name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[Span]:
    """Open a span - which does nothing at all until an SDK is installed."""
    with _TRACER.start_as_current_span(name) as underlying:
        wrapped = Span(underlying)
        if attributes:
            wrapped.set(attributes)
        yield wrapped
