"""Counters and histograms, under the same meter as the Java implementation's.

The same bargain as :mod:`ekbatan._tracing`: OpenTelemetry's API is a dependency, as in Java,
and records nothing until the application installs an SDK.

The instrument names, units and attribute keys match the Java implementation's. That is the
whole point of them - an operator whose dashboard reads
``ekbatan.events.handled{outcome="expired_postfailure"}`` is reading across both ports, and a
name invented here would quietly show only half the fleet.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import opentelemetry.metrics as _otel

__all__ = ["Counter", "Histogram", "counter", "histogram"]

#: Java: `GlobalOpenTelemetry.get().getMeter("io.ekbatan.events.localeventhandler")`.
_METER: Any = _otel.get_meter("io.ekbatan.events.localeventhandler")


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
    return Counter(_METER.create_counter(name, unit=unit, description=description))


def histogram(name: str, description: str, unit: str) -> Histogram:
    return Histogram(_METER.create_histogram(name, unit=unit, description=description))
