"""Spans, if anyone is listening.

OpenTelemetry is not a dependency. A persistence framework that drags a tracing stack into
every project that installs it has made a decision it had no standing to make - so this looks
for one at import, uses it if it is there, and costs a couple of attribute lookups if it is
not.

The span names and attributes match the Java implementation's, so one trace can span both.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

__all__ = ["Span", "span", "tracing_enabled"]

try:  # pragma: no cover - depends on what is installed
    from opentelemetry import trace as _otel

    _TRACER: Any = _otel.get_tracer("ekbatan", "0.1.0")
except ImportError:  # pragma: no cover
    _TRACER = None


def tracing_enabled() -> bool:
    return _TRACER is not None


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
        if self._span is not None:
            from opentelemetry.trace import Status, StatusCode

            if outcome is not None:
                self._span.set_attribute(outcome, "error")
            self._span.set_status(Status(StatusCode.ERROR, str(error)))
            self._span.record_exception(error)


_NOWHERE = Span()


@contextmanager
def span(name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[Span]:
    """Open a span, or do nothing at all."""
    if _TRACER is None:
        yield _NOWHERE
        return
    with _TRACER.start_as_current_span(name) as underlying:
        wrapped = Span(underlying)
        if attributes:
            wrapped.set(attributes)
        yield wrapped
