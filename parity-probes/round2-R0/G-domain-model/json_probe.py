"""Area G twin probe: JSON of events and params, byte for byte, and JSON input tolerance.
Twin: JsonParityProbeTest.java.

Path measured is the real one: canonical(event.payload()) is what the outbox hands the column,
and the engine's json_serializer is ekbatan._json.dumps. Reading back is _json.loads (the
engine's json_deserializer) then _json.decode, as the event dispatcher does.

The events are declared with Python's usual snake_case field names; since EKB-0290 they are
written under Java's camelCase names, so the text can be compared with Java's byte for byte."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path

from ekbatan._json import canonical, decode, dumps, loads
from ekbatan.domain import ModelEvent
from ekbatan.ids import ShardedId, TypedValue
from ekbatan.sharding import ShardIdentifier

HERE = Path(__file__).parent


def p(key, value):
    print(f"PROBE {key}: {value}")


def err(fn):
    try:
        return f"OK {fn()}"
    except Exception as e:  # noqa: BLE001
        m = str(e).split("\n")[0][:160]
        return f"ERR {type(e).__name__}: {m}"


class WalletState(Enum):
    OPENED = "OPENED"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str


@dataclass(frozen=True, eq=False)
class WalletNumber(TypedValue[str]):
    pass


MODEL = "01941f29-7c7b-7abc-8042-123456789abc"
OWNER = uuid.UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")
NOTE = 'café € </tag> "q" \\ \n \u0001'


@dataclass(frozen=True, kw_only=True)
class ProbeEvent(ModelEvent["Wallet"]):
    amount: Decimal | None = Decimal("10.50")
    zero: Decimal | None = Decimal("0.00")
    sci: Decimal | None = Decimal("1E+3")
    tiny: Decimal | None = Decimal("0.0000001")
    huge: Decimal | None = Decimal("123456789012345678901234567890.123456789")
    negative: Decimal | None = Decimal("-0.5")
    owner_id: uuid.UUID | None = OWNER
    at: datetime | None = datetime(2025, 1, 1, tzinfo=UTC)
    at_micros: datetime | None = datetime(2025, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)
    at_millis: datetime | None = datetime(2025, 1, 1, 0, 0, 0, 120000, tzinfo=UTC)
    at_offset: datetime | None = datetime(2025, 1, 1, 2, 0, tzinfo=timezone(timedelta(hours=2)))
    local_at: datetime | None = datetime(2025, 1, 1, 10, 0)
    day: date | None = date(2025, 1, 31)
    clock: time | None = time(10, 15, 30)
    state: WalletState | None = WalletState.OPENED
    note: str | None = NOTE
    missing: str | None = None
    count: int = 1 << 60
    ratio: float = 0.1
    flag: bool = True
    tags: list[str] = field(default_factory=lambda: ["b", "a"])
    attrs: dict[str, int] = field(default_factory=lambda: {"z": 1, "a": 2, "10": 3, "9": 4})
    nested: Money | None = Money(Decimal("5.00"), "EUR")
    raw: bytes | None = bytes([1, 2, 255])


@dataclass(frozen=True, kw_only=True)
class ProbeEventSnake(ModelEvent["Wallet"]):
    amount: Decimal = Decimal("10.50")
    owner_id: uuid.UUID = OWNER
    at_micros: datetime = datetime(2025, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)
    new_balance: Decimal = Decimal("20.00")


@dataclass(frozen=True, kw_only=True)
class IdsEvent(ModelEvent["Wallet"]):
    wallet_id: ShardedId = ShardedId.of(MODEL)
    wallet_number: WalletNumber = WalletNumber("W-1")
    shard: ShardIdentifier = ShardIdentifier(1, 2)


@dataclass(frozen=True, kw_only=True)
class SmallEvent(ModelEvent["Wallet"]):
    amount: Decimal
    note: str


@dataclass(frozen=True)
class DepositParams:
    wallet_id: uuid.UUID
    amount: Decimal
    requested_at: datetime
    memo: str | None


def write(payload):
    # canonical() is what the outbox stores; dumps() is the engine's json_serializer.
    return dumps(canonical(payload))


def dec(d):
    if d is None:
        return "None"
    if not isinstance(d, Decimal):
        return f"{type(d).__name__}:{d}"
    return f"{format(d, 'f')} (scale {-d.as_tuple().exponent})"


def inst(d):
    if d is None:
        return "None"
    if not isinstance(d, datetime):
        return f"{type(d).__name__}:{d}"
    # As Java prints an Instant.
    return canonical(d if d.tzinfo is not None else d.replace(tzinfo=UTC))


def plain(v, kind):
    if v is None:
        return "None"
    if not isinstance(v, kind):
        return f"{type(v).__name__}:{v}"
    return v.isoformat()


def local(d):
    """As Java prints a LocalDateTime: seconds left off when they and the fraction are zero."""
    if not isinstance(d, datetime) or d.tzinfo is not None:
        return f"{type(d).__name__}:{d}"
    return d.strftime("%Y-%m-%dT%H:%M") if d.second == 0 and d.microsecond == 0 else canonical(d)


def describe(e):
    out = [
        f"modelId={e.model_id}",
        f"modelName={e.model_name}",
        f"amount={dec(e.amount)}",
        f"zero={dec(e.zero)}",
        f"sci={dec(e.sci)}",
        f"tiny={dec(e.tiny)}",
        f"huge={dec(e.huge)}",
        f"negative={dec(e.negative)}",
        f"ownerId={e.owner_id}",
        f"at={inst(e.at)}",
        f"atMicros={inst(e.at_micros)}",
        f"atMillis={inst(e.at_millis)}",
        f"atOffset={inst(e.at_offset)}",
        f"localAt={local(e.local_at)}",
        f"day={plain(e.day, date)}",
        f"clock={plain(e.clock, time)}",
        f"state={e.state.name if isinstance(e.state, Enum) else e.state}",
        "note=" + ("None" if e.note is None else "same" if e.note == NOTE else "changed"),
        f"missing={e.missing}",
        f"count={e.count}",
        f"ratio={e.ratio}",
        f"flag={e.flag}",
        "tags=[" + ", ".join(e.tags) + "]",
        "attrs={" + ", ".join(f"{k}={v}" for k, v in e.attrs.items()) + "}",
        "nested=" + ("None" if e.nested is None else f"{dec(e.nested.amount)} {e.nested.currency}" if isinstance(e.nested, Money) else f"{type(e.nested).__name__}:{e.nested}"),
        "raw=" + ("None" if e.raw is None else e.raw.hex() if isinstance(e.raw, bytes) else f"{type(e.raw).__name__}:{e.raw}"),
    ]
    return " | ".join(out)


def read_back(text, target=ProbeEvent):
    return decode(target, loads(text))


def small(e):
    kind = "None" if e.note is None else "text" if isinstance(e.note, str) else type(e.note).__name__
    return f"modelId={e.model_id} amount={dec(e.amount)} note={e.note} ({kind})"


event = ProbeEvent(model_id=MODEL)
text = write(event.payload())
p("json.event", text)
p("json.event.pythonDefaultNames", write(ProbeEventSnake(model_id=MODEL).payload()))
keys = list(loads(text).keys())
p("json.event.keyOrder", "[" + ", ".join(keys) + "]")
for k, v in loads(text).items():
    p(f"json.field.{k}", write({"v": canonical(event.payload())[k]}))
(HERE / "python_event.json").write_text(text)
(HERE / "python_event_snake.json").write_text(write(ProbeEventSnake(model_id=MODEL).payload()))

p("json.idsEvent", write(IdsEvent(model_id=MODEL).payload()))

params = DepositParams(uuid.UUID(MODEL), Decimal("10.50"), datetime(2025, 1, 1, 0, 0, 0, 120000, tzinfo=UTC), None)
p("json.params", write(params))

p("json.ownRoundTrip", err(lambda: describe(read_back(text))))
jf = HERE / "java_event.json"
if jf.exists():
    jtext = jf.read_text()
    p("json.readOtherPort", err(lambda: describe(read_back(jtext))))
    jdata = loads(jtext)
    jdata.pop("raw")
    p("json.readOtherPort.withoutRaw", err(lambda: describe(decode(ProbeEvent, jdata))))
else:
    p("json.readOtherPort", "java_event.json missing")
p("json.readOtherPortDefaultNames", "n/a (Java writes only its field names)")


def rs(t):
    return small(read_back(t, SmallEvent))


p("in.exact", err(lambda: rs('{"modelId":"m","modelName":"Wallet","amount":"1.50","note":"n"}')))
p("in.unknownField", err(lambda: rs('{"modelId":"m","modelName":"Wallet","amount":"1.50","note":"n","extra":1}')))
p("in.missingFields", err(lambda: rs('{"modelId":"m","modelName":"Wallet"}')))
p("in.missingModelId", err(lambda: rs('{"modelName":"Wallet","amount":"1.50","note":"n"}')))
p("in.missingModelName", err(lambda: rs('{"modelId":"m","amount":"1.50","note":"n"}')))
p("in.otherModelName", err(lambda: read_back('{"modelId":"m","modelName":"Other","amount":"1.50","note":"n"}', SmallEvent).model_name))
p("in.amountNumber", err(lambda: rs('{"modelId":"m","modelName":"Wallet","amount":1.50,"note":"n"}')))
p("in.amountHugeNumber", err(lambda: rs('{"modelId":"m","modelName":"Wallet","amount":123456789012345678901234567890.123456789,"note":"n"}')))
p("in.amountInt", err(lambda: rs('{"modelId":"m","modelName":"Wallet","amount":7,"note":"n"}')))
p("in.amountBadString", err(lambda: rs('{"modelId":"m","modelName":"Wallet","amount":"abc","note":"n"}')))
p("in.noteNumber", err(lambda: rs('{"modelId":"m","modelName":"Wallet","amount":"1","note":5}')))
p("in.nullAmount", err(lambda: rs('{"modelId":"m","modelName":"Wallet","amount":null,"note":"n"}')))
p("in.duplicateKey", err(lambda: rs('{"modelId":"m","modelName":"Wallet","amount":"1","amount":"2","note":"n"}')))
p("in.snakeCaseKeys", err(lambda: rs('{"model_id":"m","modelId":"m","modelName":"Wallet","amount":"1","new_note":"n"}')))
