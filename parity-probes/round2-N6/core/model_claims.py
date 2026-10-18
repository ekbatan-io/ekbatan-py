"""docs/concepts/models-and-entities.md claims (JSON table, equality, required fields, builders)."""
import dataclasses, enum
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta, timezone, UTC
from decimal import Decimal
from uuid import uuid4, UUID
from common import probe
from wallet_service_sqlite.model import Wallet, WalletState, MoneyDepositedEvent, WalletOpenedEvent, Alert
from wallet_service_sqlite.builders import WalletBuilder
from ekbatan import ModelEvent, Model, ShardedId, Id, GenericState
from ekbatan._json import dumps, decode, loads
from ekbatan.clock import SystemClock
clock = SystemClock()
now = clock.now()

# models-and-entities.md:29-46 Wallet.open as written: builder without created_date
probe("m&e.md:33 open() snippet: builder with no created_date", lambda: WalletBuilder().id(ShardedId[Wallet].random()).state(WalletState.OPENED)
      .owner_id(uuid4()).currency("EUR").balance(Decimal(1)).with_initial_version().build())
probe("m&e.md:285 builder with no version (0292)", lambda: WalletBuilder().id(ShardedId[Wallet].random()).state(WalletState.OPENED)
      .owner_id(uuid4()).currency("EUR").balance(Decimal(1)).created_date(now).build())
probe("m&e.md:285 None for a non-Optional declared field refused", lambda: WalletBuilder().id(ShardedId[Wallet].random()).state(WalletState.OPENED)
      .owner_id(None).currency("EUR").balance(Decimal(1)).with_initial_version().created_date(now).build())

w = Wallet.open(owner_id=uuid4(), currency="EUR", balance=Decimal(1), created_date=now)
w2 = w.copy().balance(Decimal(999)).build()
probe("m&e.md:117 equality ignores business fields (same id/version)", lambda: (w == w2, hash(w) == hash(w.id)))
probe("m&e.md:117 equality differs on version", lambda: w == w.next_version())
probe("m&e.md:225 updated_date falls back to created_date", lambda: w.updated_date == w.created_date)
probe("m&e.md:225 naive created_date taken as UTC, aware value kept UTC", lambda: (Wallet.open(owner_id=uuid4(), currency="E", balance=Decimal(1), created_date=datetime(2025,1,1,10)).created_date,
      Wallet.open(owner_id=uuid4(), currency="E", balance=Decimal(1), created_date=datetime(2025,1,1,12, tzinfo=timezone(timedelta(hours=2)))).created_date))
probe("m&e.md:141 blank model_id refused at event creation", lambda: MoneyDepositedEvent(model_id="", amount=Decimal(1), new_balance=Decimal(1)))

class Colour(enum.Enum):
    RED = 1
@dataclass(frozen=True, kw_only=True)
class Inner:
    some_value: int
@dataclass(frozen=True, kw_only=True)
class Ev(ModelEvent["Wallet"]):
    new_balance: Decimal
    big: Decimal
    ratio: float
    huge: float
    at_utc: datetime
    at_off: datetime
    at_naive: datetime
    day: date
    t: time
    dur: timedelta
    raw: bytes
    colour: Colour
    wid: ShardedId[Wallet]
    aid: Id[Alert]
    inner: Inner
    tags: dict
    either: int | str | None = None
ev = Ev(model_id=str(w.id), new_balance=Decimal("10.50"), big=Decimal("1E+3"), ratio=0.1, huge=1e20,
        at_utc=datetime(2025,1,1,0,0,0,120000,tzinfo=UTC), at_off=datetime(2025,1,1,2,tzinfo=timezone(timedelta(hours=2))),
        at_naive=datetime(2025,1,1,10), day=date(2025,1,2), t=time(1,2,3), dur=timedelta(seconds=1.5), raw=b"\x01\x02\xff",
        colour=Colour.RED, wid=w.id, aid=Id[Alert].random(), inner=Inner(some_value=1), tags={"z": 1, "a": 2}, either=5)
text = dumps(ev)
print("PROBE m&e.md:166 JSON written:", text)
back = decode(Ev, loads(text))
probe("m&e.md:166 round trip types", lambda: {f.name: type(getattr(back, f.name)).__name__ for f in dataclasses.fields(Ev)})
probe("m&e.md:166 round trip equal (except union)", lambda: [f.name for f in dataclasses.fields(Ev) if getattr(back, f.name) != getattr(ev, f.name)])
probe("m&e.md:166 at_off read back offset", lambda: back.at_off.isoformat())
probe("m&e.md:195 unknown key dropped, missing key -> None", lambda: decode(MoneyDepositedEvent, {"modelId": "x", "amount": 1, "zzz": 2}))
probe("m&e.md:190 every number read as Decimal (float field?)", lambda: type(decode(Ev, loads(text)).ratio).__name__)

# m&e.md:404 model with no typed builder: copy() refusal message
@dataclass(frozen=True, kw_only=True)
class Plain(Model[Id["Plain"], GenericState]):
    x: int
p = Plain(id=Id[Plain].random(), state=GenericState.ACTIVE, version=1, x=1, created_date=now)
probe("m&e.md:404 copy() without typed builder", lambda: p.copy())
probe("m&e.md:418 evolve() still works without one", lambda: p.evolve(x=2).x)
probe("m&e.md:427 inherited builder methods on WalletBuilder", lambda: [m for m in ["id","state","version","with_initial_version","increase_version","copy_base","set","with_event","events","created_date","updated_date"] if not hasattr(WalletBuilder, m)])
from wallet_service_sqlite.builders import AlertBuilder
probe("m&e.md:427 entity builder lacks with_event/created_date", lambda: [m for m in ["with_event","events","created_date","updated_date"] if hasattr(AlertBuilder, m)])
