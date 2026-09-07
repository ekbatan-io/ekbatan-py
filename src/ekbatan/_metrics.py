"""Counters and histograms, if anyone is listening.

The same bargain as :mod:`ekbatan._tracing`: OpenTelemetry is not a dependency, so this looks
for one at import, uses it if it is there, and costs an attribute lookup if it is not.

The instrument names, units and attribute keys match the Java implementation's. That is the
whole point of them - an operator whose dashboard reads
``ekbatan.events.handled{outcome="expired_postfailure"}`` is reading across both ports, and a
name invented here would quietly show only half the fleet.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["Counter", "Histogram", "counter", "histogram", "metrics_enabled"]

try:  # pragma: no cover - depends on what is installed
    import opentelemetry.metrics as _otel

    _METER: Any = _otel.get_meter("io.ekbatan.events.localeventhandler")
except ImportError:  # pragma: no cover
    _METER = None


def metrics_enabled() -> bool:
    return _METER is not None


class Counter:
    """A count of things that happened, or nothing at all."""

    __slots__ = ("_instrument",)

    def __init__(self, instrument: Any = None) -> None:
        self._instrument = instrument

    def add(self, amount: int, attributes: Mapping[str, str] | None = None) -> None:
        if self._instrument is not None and amount:
            self._instrument.add(amount, dict(attributes or {}))


class Histogram:
    """A distribution of measurements, or nothing at all."""

    __slots__ = ("_instrument",)

    def __init__(self, instrument: Any = None) -> None:
        self._instrument = instrument

    def record(self, value: float, attributes: Mapping[str, str] | None = None) -> None:
        if self._instrument is not None:
            self._instrument.record(value, dict(attributes or {}))


def counter(name: str, description: str, unit: str) -> Counter:
    if _METER is None:
        return Counter()
    return Counter(_METER.create_counter(name, unit=unit, description=description))


def histogram(name: str, description: str, unit: str) -> Histogram:
    if _METER is None:
        return Histogram()
    return Histogram(_METER.create_histogram(name, unit=unit, description=description))
