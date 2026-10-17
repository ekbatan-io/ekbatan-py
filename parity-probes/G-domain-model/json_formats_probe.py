"""Group J twin probe (EKB-0290): the values JsonFormatsParityProbeTest.java wrote with
Ekbatan's default Jackson 3 mapper, written again by ekbatan._json.dumps and compared byte for
byte, then Java's text read back by ekbatan._json.decode and compared with the input."""

from __future__ import annotations

import gzip
import struct
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ekbatan._json import decode, dumps, loads

HERE = Path(__file__).parent
EPOCH = datetime(1970, 1, 1)


def value(kind: str, raw: str) -> tuple[Any, type]:
    parts = raw.split(",") if raw else []
    if kind == "dbl":
        return struct.unpack(">d", bytes.fromhex(raw.rjust(16, "0")))[0], float
    if kind == "bd":
        unscaled, scale = int(parts[0]), int(parts[1])
        digits = tuple(int(c) for c in str(abs(unscaled)))
        return Decimal((int(unscaled < 0), digits, -scale)), Decimal
    if kind in ("instant", "ldt", "odt"):
        sec, micros = int(parts[0]), int(parts[1])
        naive = EPOCH + timedelta(seconds=sec, microseconds=micros)
        if kind == "ldt":
            return naive, datetime
        aware = naive.replace(tzinfo=UTC)
        if kind == "instant":
            return aware, datetime
        return aware.astimezone(timezone(timedelta(seconds=int(parts[2])))), datetime
    if kind in ("lt", "ot"):
        nod = int(parts[0]) // 1000
        sec, micro = divmod(nod, 1_000_000)
        t = time(sec // 3600, sec // 60 % 60, sec % 60, micro)
        if kind == "ot":
            t = t.replace(tzinfo=timezone(timedelta(seconds=int(parts[1]))))
        return t, time
    if kind == "ld":
        return date(1970, 1, 1) + timedelta(days=int(raw)), date
    if kind == "dur":
        return timedelta(seconds=int(parts[0]), microseconds=int(parts[1])), timedelta
    if kind == "str":
        return "".join(chr(int(c)) for c in parts), str
    if kind == "bytes":
        return bytes.fromhex(raw), bytes
    raise ValueError(kind)


same: Counter[str] = Counter()
total: Counter[str] = Counter()
read: Counter[str] = Counter()
shown: Counter[str] = Counter()
# The Java probe writes java_formats.txt; it is kept gzipped (2 MB of text otherwise).
plain = HERE / "java_formats.txt"
TEXT = plain.read_text() if plain.exists() else gzip.decompress((HERE / "java_formats.txt.gz").read_bytes()).decode()
for line in TEXT.splitlines():
    kind, raw, java = line.split("\t")
    v, hint = value(kind, raw)
    total[kind] += 1
    mine = dumps(v)
    if mine == java:
        same[kind] += 1
    elif shown[kind] < 3:
        shown[kind] += 1
        print(f"MISMATCH {kind} {raw}: java {java} python {mine}")

    @dataclass
    class Box:
        v: Any

    Box.__annotations__["v"] = hint
    back = decode(Box, {"v": loads(java)}).v
    if kind == "dbl":
        ok = back == v or (back != back and v != v)
        ok = ok and str(back) == str(v)
    elif kind in ("odt", "ot"):
        ok = back == v and back.utcoffset() == v.utcoffset()
    else:
        ok = back == v and type(back) is type(v)
    read[kind] += ok
    if not ok and shown[kind] < 6:
        shown[kind] += 1
        print(f"READ MISMATCH {kind} {raw}: java {java} read {back!r} want {v!r}")

for kind in total:
    print(f"PROBE formats.{kind}.write: {same[kind]}/{total[kind]} same as Java")
    if kind == "odt":
        # An aware datetime at offset zero is an Instant here (Python has one type for both):
        # Java's OffsetDateTime at Z trims the fraction (.4Z), its Instant groups it (.400Z).
        zero = sum(1 for ln in TEXT.splitlines()
                   if ln.startswith("odt\t") and ln.split("\t")[1].endswith(",0"))
        print(f"PROBE formats.odt.offsetZero: {zero} of them at offset zero, written as an Instant")
    print(f"PROBE formats.{kind}.read: {read[kind]}/{total[kind]} read back exactly")
