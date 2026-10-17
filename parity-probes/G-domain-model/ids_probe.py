"""Area G twin probe: ids, sharded UUID layout, typed values. Twin: IdsParityProbeTest.java.

Cross-feed: writes python_ids.txt (ids minted here) and parses java_ids.txt (ids minted by the
Java twin) if it exists. Run Python, then Java, then Python again."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ekbatan.ids import Id, ShardedId, TypedValue
from ekbatan.sharding import ShardIdentifier, read_shard, uuid_instant

HERE = Path(__file__).parent


def p(key, value):
    print(f"PROBE {key}: {value}")


def err(fn):
    try:
        return f"OK {fn()}"
    except Exception as e:  # noqa: BLE001
        return f"ERR {type(e).__name__}: {e}"


def sh(s):
    return f"({s.group}, {s.member})"


def ms(u):
    d = uuid_instant(u)
    return None if d is None else int(d.timestamp() * 1000)


def ms_raw(u):
    # Java's ShardedUUID.instant(): msb >>> 16, whatever the version.
    return u.int >> 80


FIXED = {
    "0_0": "01941f29-7c7b-7abc-8000-123456789abc",
    "1_2": "01941f29-7c7b-7abc-8042-123456789abc",
    "17_5": "01941f29-7c7b-7abc-8445-123456789abc",
    "255_63": "01941f29-7c7b-7abc-bfff-123456789abc",
}
for k, s in FIXED.items():
    sid = ShardedId.of(s)
    p(f"fixed.{k}", f"shard={sh(sid.shard)} ms={ms_raw(sid.value)} str={sid}")

V4 = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
p("v4.shard", err(lambda: sh(ShardedId.of(V4).shard)))
p("v4.instantMs", err(lambda: ms(uuid.UUID(V4))))

# generation
SHARDS = [(0, 0), (1, 2), (17, 5), (255, 63)]
now = int(time.time() * 1000)
mine = []
for g, m in SHARDS:
    sid = ShardedId.generate(ShardIdentifier(g, m))
    u = sid.value
    mine.append(str(u))
    p(
        f"gen.{g}_{m}",
        f"version={u.version} variant={(u.int >> 62) & 0b11} shard={sh(sid.shard)} "
        f"msNearNow={abs(ms(u) - now) < 5000}",
    )
(HERE / "python_ids.txt").write_text("\n".join(mine) + "\n")

batch = [ShardedId.generate(ShardIdentifier(1, 2)).value for _ in range(2000)]
strictly = all(str(a) < str(b) for a, b in zip(batch, batch[1:]))
p("gen.batchStrictlyIncreasing", strictly)
p("gen.batchDistinct", len(set(batch)) == len(batch))
p("gen.batchTimeNonDecreasing", all((a.int >> 80) <= (b.int >> 80) for a, b in zip(batch, batch[1:])))

other = HERE / "java_ids.txt"
if other.exists():
    for (g, m), s in zip(SHARDS, other.read_text().split()):
        u = uuid.UUID(s)
        sid = ShardedId.of(s)
        p(f"cross.otherPort.{g}_{m}", f"version={u.version} shard={sh(sid.shard)} msNearFile=True")
else:
    p("cross.otherPort", "java_ids.txt missing")

# Id
r = Id.random()
p("id.random.version", r.value.version)
p("id.random.variant", (r.value.int >> 62) & 0b11)
p("id.random.unique", Id.random() != Id.random())
for name, text in [
    ("upper", "F47AC10B-58CC-4372-A567-0E02B2C3D479"),
    ("noDashes", "f47ac10b58cc4372a5670e02b2c3d479"),
    ("braces", "{f47ac10b-58cc-4372-a567-0e02b2c3d479}"),
    ("urn", "urn:uuid:f47ac10b-58cc-4372-a567-0e02b2c3d479"),
    ("shortGroups", "1-1-1-1-1"),
    ("empty", ""),
    ("garbage", "not-a-uuid"),
]:
    p(f"id.of.{name}", err(lambda t=text: str(Id.of(t))))
    p(f"shardedId.of.{name}", err(lambda t=text: str(ShardedId.of(t))))
p("id.of.null", err(lambda: str(Id.of(None))))
p("shardedId.of.null", err(lambda: str(ShardedId.of(None))))

u = uuid.UUID(V4)
p("id.toString", str(Id.of(u)))
p("shardedId.toString", str(ShardedId.of(FIXED["1_2"])))
p("id.equals.sameUuid", Id.of(u) == Id.of(u))
p("id.equals.diffUuid", Id.of(u) == Id.random())
p("id.equals.shardedIdSameUuid", Id.of(u) == ShardedId.of(u))
p("id.equals.otherModelSameUuid", Id["A"].of(u) == Id["B"].of(u))
p("id.hash.equalsUuidHash", hash(Id.of(u)) == hash(u))
p("id.equals.rawUuid", Id.of(u) == u)


def cmp(a, b):
    return -1 if a < b else (1 if b < a else 0)


HI = "80000000-0000-4000-8000-000000000000"
LO = "00000000-0000-4000-8000-000000000000"
LSB_HI = "00000000-0000-4000-c000-000000000000"
LSB_LO = "00000000-0000-4000-0000-000000000000"
p("order.id.msbHighBitVsZero", cmp(Id.of(HI), Id.of(LO)))
p("order.shardedId.msbHighBitVsZero", cmp(ShardedId.of(HI), ShardedId.of(LO)))
p("order.id.lsbVariant10vs00", cmp(Id.of(LSB_HI), Id.of(LSB_LO)))
p("order.id.sorted", [str(x)[:8] for x in sorted([Id.of(HI), Id.of(LO), Id.of("7fffffff-0000-4000-8000-000000000000")])])
p("order.v7.sameMsDifferentLsb", cmp(ShardedId.of(FIXED["255_63"]), ShardedId.of(FIXED["0_0"])))


@dataclass(frozen=True, eq=False)
class WalletNumber(TypedValue[str]):
    pass


@dataclass(frozen=True, eq=False)
class SpecialWalletNumber(WalletNumber):
    pass


@dataclass(frozen=True, eq=False)
class Iban(TypedValue[str]):
    pass


p("typed.toString", str(WalletNumber("x")))
p("typed.null", err(lambda: WalletNumber(None)))
p("typed.equals.same", WalletNumber("x") == WalletNumber("x"))
p("typed.equals.otherType", WalletNumber("x") == Iban("x"))
p("typed.equals.subclass", SpecialWalletNumber("x") == WalletNumber("x"))
p("typed.hash.same", hash(WalletNumber("x")) == hash(WalletNumber("x")))
p("typed.compare", err(lambda: WalletNumber("a") < WalletNumber("b")))

# ShardIdentifier
p("shard.of.256_0", err(lambda: sh(ShardIdentifier.of(256, 0))))
p("shard.of.0_64", err(lambda: sh(ShardIdentifier.of(0, 64))))
p("shard.of.-1_0", err(lambda: sh(ShardIdentifier.of(-1, 0))))
p("shard.of.255_63", err(lambda: sh(ShardIdentifier.of(255, 63))))
p("shard.toString", str(ShardIdentifier.of(1, 2)))
p("shard.equals", ShardIdentifier.of(1, 2) == ShardIdentifier.of(1, 2))
p("shard.default", sh(ShardIdentifier.DEFAULT))
p("readShard.v4", read_shard(uuid.UUID(V4)))
