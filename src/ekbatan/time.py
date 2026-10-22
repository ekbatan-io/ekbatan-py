"""Which kind of time a ``datetime`` holds: three labels, checked by a type checker.

Python has one ``datetime`` class for three different things. Java has three classes, and a
Java service reading what this one writes needs to know which it is. These labels say it::

    from ekbatan.time import Instant, LocalDateTime, OffsetDateTime

- :data:`Instant` - **a moment**, held as UTC digits with no zone attached (naive). What the
  clock gives (``clock.instant()``) and what a zone-less ``TIMESTAMP`` / ``DATETIME`` column
  holds. Java's ``Instant``; in JSON it is written with ``Z``.
- :data:`LocalDateTime` - **a wall date and time**, naive, belonging to no moment until someone
  says where: "the shop opens at 09:00 on 1 March". Java's ``LocalDateTime``; in JSON it is
  written with no zone.
- :data:`OffsetDateTime` - **a moment with its offset**, an aware ``datetime``. Java's
  ``OffsetDateTime``; in JSON it is written with its offset.

Plain ``date`` and ``time`` stay Python's own (Java's ``LocalDate`` and ``LocalTime``).

They are ``typing.NewType`` labels: at run time a value is a plain ``datetime`` and costs
nothing, and ``Instant(x)`` returns ``x`` unchanged - it converts nothing and checks nothing. A
type checker keeps them apart: an ``Instant`` passed where a ``LocalDateTime`` is expected is an
error, and so is a plain ``datetime`` where an ``Instant`` is. Arithmetic keeps the label:
``instant + timedelta(days=1)`` is still an ``Instant``.

Where the label is read at run time is JSON: an event's or a params dataclass's ``datetime``
field is written as the kind it is declared, and a field declared plain ``datetime`` is
refused, because nothing would say which of the three it is.
"""

from __future__ import annotations

from datetime import datetime
from typing import NewType

__all__ = ["Instant", "LocalDateTime", "OffsetDateTime"]

#: A moment: UTC digits with no zone attached. What ``clock.instant()`` gives. Java's ``Instant``.
Instant = NewType("Instant", datetime)

#: A wall date and time, no zone. Java's ``LocalDateTime``.
LocalDateTime = NewType("LocalDateTime", datetime)

#: A moment with its offset: an aware ``datetime``. Java's ``OffsetDateTime``.
OffsetDateTime = NewType("OffsetDateTime", datetime)
