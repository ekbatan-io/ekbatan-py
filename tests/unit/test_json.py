"""The JSON of params and payloads is the text Java's Ekbatan writes, and reads back exactly.

The expected strings were written by Ekbatan's default Jackson 3 mapper
(``JsonMapper.builder().build()``) - see ``parity-probes/G-domain-model/JsonParityProbeTest.java``
and ``JsonFormatsParityProbeTest.java``, which check the same thing over 50,000 values.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum

import pytest

from ekbatan._json import canonical, decode, dumps, json_name, loads
from ekbatan.domain import ModelEvent
from ekbatan.errors import ValidationError
from ekbatan.ids import Id, ShardedId, TypedValue
from ekbatan.sharding import ShardIdentifier

MODEL = "01941f29-7c7b-7abc-8042-123456789abc"
OWNER = uuid.UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")
NOTE = 'café € </tag> "q" \\ \n \u0001'


class WalletState(Enum):
    OPENED = "opened"
    CLOSED = "closed"


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str


@dataclass(frozen=True, eq=False)
class WalletNumber(TypedValue[str]):
    pass


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


#: What Java writes for the same event, a class with Ekbatan's usual `@JsonCreator`.
JAVA_EVENT = (
    '{"modelId":"01941f29-7c7b-7abc-8042-123456789abc","modelName":"Wallet","amount":10.50,'
    '"zero":0.00,"sci":1E+3,"tiny":1E-7,"huge":123456789012345678901234567890.123456789,'
    '"negative":-0.5,"ownerId":"f47ac10b-58cc-4372-a567-0e02b2c3d479",'
    '"at":"2025-01-01T00:00:00Z","atMicros":"2025-01-01T00:00:00.123456Z",'
    '"atMillis":"2025-01-01T00:00:00.120Z","atOffset":"2025-01-01T02:00:00+02:00",'
    '"localAt":"2025-01-01T10:00:00","day":"2025-01-31","clock":"10:15:30","state":"OPENED",'
    '"note":"café € </tag> \\"q\\" \\\\ \\n \\u0001","missing":null,"count":1152921504606846976,'
    '"ratio":0.1,"flag":true,"tags":["b","a"],"attrs":{"z":1,"a":2,"10":3,"9":4},'
    '"nested":{"amount":5.00,"currency":"EUR"},"raw":"AQL/"}'
)


def test_an_event_is_written_byte_for_byte_as_java_writes_it():
    assert dumps(ProbeEvent(model_id=MODEL).payload()) == JAVA_EVENT


@dataclass(frozen=True)
class DepositParams:
    wallet_id: ShardedId
    amount: Decimal
    requested_at: datetime
    memo: str | None


def test_params_take_javas_field_names_in_declaration_order():
    params = DepositParams(
        ShardedId.of(MODEL), Decimal("10.50"), datetime(2025, 1, 1, 0, 0, 0, 120000, UTC), None
    )
    # A Java record's components, in order; the typed id as Jackson writes Java's ShardedId.
    assert dumps(params) == (
        '{"walletId":{"id":"01941f29-7c7b-7abc-8042-123456789abc",'
        '"value":"01941f29-7c7b-7abc-8042-123456789abc"},"amount":10.50,'
        '"requestedAt":"2025-01-01T00:00:00.120Z","memo":null}'
    )


@dataclass(frozen=True, kw_only=True)
class IdsEvent(ModelEvent["Wallet"]):
    wallet_id: ShardedId = field(default_factory=lambda: ShardedId.of(MODEL))
    alert_id: Id = field(default_factory=lambda: Id.of(OWNER))
    wallet_number: WalletNumber = field(default_factory=lambda: WalletNumber("W-1"))
    shard: ShardIdentifier = field(default_factory=lambda: ShardIdentifier(1, 2))


def test_typed_ids_and_values_take_the_shape_jackson_gives_javas():
    assert dumps(IdsEvent(model_id=MODEL).payload()) == (
        '{"modelId":"01941f29-7c7b-7abc-8042-123456789abc","modelName":"Wallet",'
        '"walletId":{"id":"01941f29-7c7b-7abc-8042-123456789abc",'
        '"value":"01941f29-7c7b-7abc-8042-123456789abc"},'
        '"alertId":{"id":"f47ac10b-58cc-4372-a567-0e02b2c3d479",'
        '"value":"f47ac10b-58cc-4372-a567-0e02b2c3d479"},'
        '"walletNumber":{"value":"W-1"},"shard":{"group":1,"member":2}}'
    )


def test_typed_ids_and_values_read_back_from_that_shape():
    back = decode(IdsEvent, loads(dumps(IdsEvent(model_id=MODEL).payload())))
    assert back.wallet_id == ShardedId.of(MODEL)
    assert type(back.wallet_id) is ShardedId
    assert back.alert_id == Id.of(OWNER)
    assert back.wallet_number == WalletNumber("W-1")


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("new_balance", "newBalance"),
        ("amount", "amount"),
        ("owner_id", "ownerId"),
        ("iso_4217_code", "iso4217Code"),
        ("alreadyCamel", "alreadyCamel"),
        ("_private", "_private"),
        ("from_", "from_"),
    ],
)
def test_json_name_is_javas_spelling_of_the_field(name, key):
    assert json_name(name) == key


def test_two_fields_with_one_json_name_are_refused():
    @dataclass
    class Clash:
        new_balance: int
        newBalance: int

    with pytest.raises(ValidationError, match="both written as 'newBalance'"):
        dumps(Clash(1, 2))


def test_a_map_keeps_its_own_keys_in_insertion_order():
    assert dumps({"z": 1, "new_balance": 2, 3: "three"}) == '{"z":1,"new_balance":2,"3":"three"}'


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Decimal("10.50"), "10.50"),
        (Decimal("1E+3"), "1E+3"),
        (Decimal("1000"), "1000"),
        (Decimal("0.0000001"), "1E-7"),
        (Decimal("-0.000001"), "-0.000001"),
        (Decimal("0E-10"), "0E-10"),
        (Decimal("-0.00"), "0.00"),
        (0.1, "0.1"),
        (100.0, "100.0"),
        (1e7, "1.0E7"),
        (9999999.0, "9999999.0"),
        (0.001, "0.001"),
        (9.99e-4, "9.99E-4"),
        (1e20, "1.0E20"),
        (-0.0, "-0.0"),
        (5e-324, "4.9E-324"),
        (float("nan"), '"NaN"'),
        (float("-inf"), '"-Infinity"'),
        (1 << 70, "1180591620717411303424"),
    ],
)
def test_numbers_are_written_as_java_writes_them(value, text):
    assert dumps(value) == text


def test_a_decimal_that_java_cannot_hold_is_refused():
    with pytest.raises(ValidationError, match="BigDecimal"):
        dumps(Decimal("NaN"))


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (datetime(2025, 1, 1, 0, 0, 0, 100000, UTC), '"2025-01-01T00:00:00.100Z"'),
        (datetime(2025, 1, 1, 0, 0, 0, 123400, UTC), '"2025-01-01T00:00:00.123400Z"'),
        (datetime(2025, 1, 1, 10, 0, 0, 120000), '"2025-01-01T10:00:00.12"'),
        (
            datetime(2025, 1, 1, 2, 0, 0, 120000, timezone(timedelta(hours=2))),
            '"2025-01-01T02:00:00.12+02:00"',
        ),
        (
            datetime(2025, 1, 1, 2, 0, tzinfo=timezone(timedelta(hours=5, minutes=30, seconds=15))),
            '"2025-01-01T02:00:00+05:30:15"',
        ),
        (time(0, 0), '"00:00:00"'),
        (time(10, 15, 30, 120000), '"10:15:30.12"'),
        (time(10, 15, 30, 500000, timezone(timedelta(hours=2))), '"10:15:30.500+02:00"'),
        (date(1, 1, 31), '"0001-01-31"'),
        (timedelta(milliseconds=1500), '"PT1.5S"'),
        (timedelta(milliseconds=-500), '"PT-0.5S"'),
        (timedelta(hours=26, minutes=3), '"PT26H3M"'),
        (timedelta(0), '"PT0S"'),
    ],
)
def test_times_are_written_as_java_writes_them(value, text):
    assert dumps(value) == text


def test_strings_escape_as_jackson_escapes_them():
    controls = "".join(chr(c) for c in range(0x20)) + "\u007f\u2028/😀"
    assert dumps(controls) == (
        '"\\u0000\\u0001\\u0002\\u0003\\u0004\\u0005\\u0006\\u0007\\b\\t\\n\\u000B\\f\\r'
        "\\u000E\\u000F\\u0010\\u0011\\u0012\\u0013\\u0014\\u0015\\u0016\\u0017\\u0018\\u0019"
        '\\u001A\\u001B\\u001C\\u001D\\u001E\\u001F\u007f\u2028/😀"'
    )


def test_bytes_are_base64():
    assert dumps(bytes([1, 2, 255])) == '"AQL/"'
    assert dumps(b"") == '""'


def test_canonical_changes_nothing_the_second_time():
    once = canonical(ProbeEvent(model_id=MODEL).payload())
    assert canonical(once) == once
    assert dumps(once) == JAVA_EVENT


def test_json_numbers_are_read_as_decimals_so_no_digit_is_lost():
    read = loads('{"a":10.50,"b":123456789012345678901234567890.123456789,"c":1E+3,"d":7}')
    assert str(read["a"]) == "10.50"
    assert str(read["b"]) == "123456789012345678901234567890.123456789"
    assert str(read["c"]) == "1E+3"
    assert read["d"] == 7 and type(read["d"]) is int


def test_an_event_java_wrote_reads_back_as_its_declared_types():
    back = decode(ProbeEvent, loads(JAVA_EVENT))
    expected = ProbeEvent(model_id=MODEL)
    for name in (
        "amount", "zero", "sci", "tiny", "huge", "negative", "owner_id", "at", "at_micros",
        "at_millis", "at_offset", "local_at", "day", "clock", "state", "note", "missing",
        "count", "ratio", "flag", "tags", "attrs", "nested", "raw",
    ):  # fmt: skip
        got, want = getattr(back, name), getattr(expected, name)
        assert got == want, name
        assert type(got) is type(want), name
    assert str(back.amount) == "10.50", "scale kept"
    assert back.local_at is not None and back.local_at.tzinfo is None, "a LocalDateTime stays local"
    assert back.at is not None and back.at.utcoffset() == timedelta(0)
    assert back.at_offset is not None and back.at_offset.utcoffset() == timedelta(hours=2)


def test_a_round_trip_restores_dates_times_bytes_and_durations():
    @dataclass(frozen=True)
    class Times:
        day: date
        clock: time
        raw: bytes
        wait: timedelta
        local: datetime

    value = Times(date(2025, 1, 31), time(10, 15, 30), b"\x00\xff", timedelta(seconds=-1.5),
                  datetime(2025, 1, 1, 10, 0))  # fmt: skip
    assert decode(Times, loads(dumps(value))) == value


def test_a_missing_key_leaves_the_field_none_as_jackson_leaves_it_null():
    @dataclass(frozen=True, kw_only=True)
    class Small(ModelEvent["Wallet"]):
        amount: Decimal
        note: str
        kind: str = "deposit"

    back = decode(Small, {"modelId": "m"})
    assert back.amount is None
    assert back.note is None
    assert back.kind == "deposit", "a declared default is kept"


def test_a_value_that_cannot_be_read_names_the_field():
    @dataclass(frozen=True)
    class Small:
        amount: Decimal

    with pytest.raises(ValidationError, match=re.escape("Small.amount: cannot read 'abc'")):
        decode(Small, {"amount": "abc"})


def test_a_number_read_into_a_text_field_becomes_its_text():
    @dataclass(frozen=True)
    class Small:
        note: str

    assert decode(Small, loads('{"note":5}')).note == "5"
    assert decode(Small, loads('{"note":1.50}')).note == "1.50"
