# Area G: domain model - behaviour parity, run in both ports

Part of [the behaviour-parity ledger](../BEHAVIOUR-PARITY-LEDGER.md).

**Scope.** Models, entities and their builders (Java's `ModelTest`, `EntityTest`,
`ModelEventTest`, `IdTest`, `TypedValueTest`, `ShardIdentifierTest` ported scenario by scenario,
plus corners no test pins), events, `Id` / `ShardedId` / the sharded-UUID bit layout, typed
values, the JSON of events and params, JSON read back, clocks, and what a generated builder does.
Every row was settled on 2026-10-17 by running the same scenario in both ports: temporary JUnit
tests in a throwaway copy of the Java repo (`ekbatan-core`, no database) and Python scripts, both
printing `PROBE` lines with the same keys. The generated-builder rows ran a Java class compiled
with the real `ekbatan-annotation-processor` (`javac -processorpath`) against a Python module
whose builders `python -m ekbatan.codegen` wrote. Ids and JSON were fed across: each port parsed
the ids and the event JSON the other port wrote. Sources and outputs are in
[`parity-probes/G-domain-model/`](../../parity-probes/G-domain-model/): `*ParityProbeTest.java`,
`CodegenParityProbe.java` (+ `run-codegen-java.sh`), `*_probe.py`, `gpkg/`, `<probe>.java.out`,
`<probe>.python.out`, and the exchanged files (`java_ids.txt`, `python_ids.txt`,
`java_event.json`, `python_event.json`, ...).

How to read it: exception type names differ by language (`NullPointerException` /
`IllegalArgumentException` vs `ValidationError`, Dracula 57), so a row is `same` when the same
call is refused at the same point for the same reason, whatever the wording. The JSON rows measure
the real write path in each port: Java = the application's `ObjectMapper` (a default Jackson 3.1.3
one, as Ekbatan's own tests use) doing `valueToTree`, then `JSONBObjectNodeConverter` writing the
text; Python = `canonical(event.payload())` written by `_json.dumps` (the engine's
`json_serializer`). Read back = converter + `treeToValue` (what `EventHandlingJob` does) against
`_json.loads` + `_json.decode` (what the dispatcher does). The main probe event is declared in
Python with Java's camelCase field names, so format can be compared without the known naming
difference in the way. No database was involved: what a JSONB or MySQL JSON column then does to
the text is not measured here.

| # | Behaviour | Java | Python | Verdict | Probe |
|---|---|---|---|---|---|
| 1 | `withInitialVersion()` gives version 1 | 1 | 1 | same | model.withInitialVersion |
| 2 | `increaseVersion()` adds one | 2 | 2 | same | model.increaseVersion |
| 3 | Explicit version kept; `withInitialVersion()` after it resets to 1 | 5 / 1 | 5 / 1 | same | model.explicitVersion5, model.withInitialVersionAfter5 |
| 4 | Version 0 or negative refused at build (Model and Entity) | refused | refused | same | model.version0, model.versionNeg1, entity.version0 |
| 5 | Version never set | refused: "version cannot be null" | refused: "version cannot be null" | same (fixed in EKB-0292) | model.versionNotSet, entity.versionNotSet |
| 6 | `increaseVersion()` when no version was set | NPE | refused: "version cannot be null" | same (fixed in EKB-0292); Java's is an NPE from `version + 1` | model.increaseVersionWhenUnset |
| 7 | `nextVersion()` at the largest 64-bit version | refused (wraps negative) | 9223372036854775808 | differs | model.versionLongMax.nextVersion |
| 8 | Base field (`id`, `state`, `createdDate`) whose setter was never called | "id cannot be null" | "id cannot be null" | same (fixed in EKB-0292) | model.missingId, model.missingState, model.missingCreatedDate, entity.missingId |
| 9 | Base field set to null | "id/state/createdDate/version cannot be null" | same messages | same | model.nullId, model.nullState, model.nullCreatedDate, model.nullVersion, entity.nullId, entity.nullState |
| 10 | `events(null)` refused | refused | refused | same | model.nullEventsList |
| 11 | A null event in the list | refused at build | kept as an event | differs | model.nullEventInList |
| 12 | Business field the model checks itself, set to null | refused | refused | same | model.nullCheckedField |
| 13 | Business field the model does not check, set to null | accepted | refused | differs | model.nullUncheckedField |
| 14 | `withEvent` adds, and accumulates | 1, 2 | 1, 2 | same | model.oneEvent, model.twoEvents |
| 15 | `events(list)` replaces what was added; keeps the same object | yes | yes | same | model.eventsReplace.* |
| 16 | A model's event list cannot be changed | refused | refused | same | model.eventsImmutable |
| 17 | A builder used again after `build()` does not change the first model | 0, 1 | 0, 1 | same | model.eventsSnapshot |
| 18 | Changing the caller's list after `build()` does not change the model | 1 | 1 | same | model.callerListMutatedAfterBuild |
| 19 | No events by default | 0 | 0 | same | model.noEventsEmpty |
| 20 | `createdDate` kept to the microsecond | `.123456Z` | `.123456Z` | same | model.createdDate.micros (Java also cuts nanos: model.createdDate.nanos) |
| 21 | A `+02:00` time is stored as the same instant | `00:00:00Z` | `00:00:00Z` | same | model.createdDate.plus2 |
| 22 | The model's timestamp can be compared with the clock's now | true | true | same (fixed in EKB-0294) | model.createdDateVsClockNow.compare, model.createdDate.hasZone |
| 23 | A time with no zone passed as `createdDate` | cannot happen (`Instant`) | taken as UTC | read: Java's type has no zone-less form | model.createdNaiveInput |
| 24 | `updatedDate` defaults to `createdDate`; explicit one kept; earlier than created accepted | yes | yes | same | model.updatedDefaultsToCreated, model.updatedExplicit, model.updatedBeforeCreated |
| 25 | `copy()` carries id, state, both dates, version, events, business fields | yes | yes | same | model.copy.fields, model.copy.businessFields, model.copy.isNewInstance |
| 26 | Events added on a copy do not reach the original | 1, 2 | 1, 2 | same | model.copy.eventsIndependent |
| 27 | A field changed on a copy | DELETED | DELETED | same | model.copyOverride, entity.copyOverride |
| 28 | Model equality: id, state, version, both dates; events and business fields ignored | yes | yes | same | eq.* |
| 29 | A different model class with the same base fields is not equal | false | false | same | eq.otherClassSameBase |
| 30 | Hash is the id's; two versions of one row are two set entries | yes | yes | same | eq.hashById, eq.hashEqualsIdHash, eq.inSetDifferentVersions |
| 31 | `isModel` | Model true, Entity false | same | same | model.isModel, entity.isModel |
| 32 | `nextVersion()`: version+1, same id, events kept, `updatedDate` untouched | 2, same, 1, kept | same | same | model.nextVersion, entity.nextVersion |
| 33 | Entity: version rules, null checks, equality (id, state, version), hash by id | as Model | as Model | same | entity.* |
| 34 | Changing a field of a built model | compile error | `FrozenInstanceError` | read: Java refuses at compile time | model.mutateField |
| 35 | `is_deleted` on a model | none | yes | read: Python-only method (Dracula 67) | model.isDeleted.deletedState |
| 36 | `copy()` on a model that names no builder | always compiles (abstract) | `EkbatanError` | read: compile-time in Java (Dracula 45) | - |
| 37 | Event carries `modelId` and the model's bare class name | yes | yes | same | event.modelIdAndName |
| 38 | Event with null, empty or blank `modelId` refused; `modelId` not trimmed | yes | yes | same | event.nullModelId, event.blankModelId, event.whitespaceModelId, event.modelIdNotTrimmed |
| 39 | Two events with the same fields are equal | false (identity) | true, even for different `modelId` | differs | event.equalSameFields, event.equalDifferentModelId |
| 40 | Generated builder builds a full model; copy keeps and changes fields; builder reuse | yes | yes | same | codegen.build, codegen.copy*, codegen.builderReuse |
| 41 | Generated builder, business-field setter never called | model's own check: "ownerId cannot be null" | model's own check: "owner_id cannot be null" | same (fixed in EKB-0292) | codegen.missingBusinessField, codegen.missingBaseField |
| 42 | Generated builder, unchecked field never set | null | "memo cannot be null" (field not typed `X \| None`) | differs only by Dracula 53 (a field not typed `X \| None` is checked); the `TypeError` is gone (EKB-0292) | codegen.uncheckedFieldUnset |
| 43 | Generated builder, setter given null | refused at build | refused at build | same | codegen.nullSetterThenBuild |
| 44 | Generated builder, setter that does not exist | compile error | `AttributeError` at run time | read: compile-time in Java | codegen.unknownSetter |
| 45 | Sharded-UUID layout: hand-built ids give the same shard and millisecond in both | same values | same values | same | fixed.* |
| 46 | `generate(shard)`: version 7, variant 2, shard read back, time = now | yes | yes | same | gen.0_0 ... gen.255_63 |
| 47 | Ids one port minted read back to the right shard in the other | yes | yes | same | cross.otherPort.* (java_ids.txt, python_ids.txt) |
| 48 | 2000 ids in a row: distinct, time never goes back, not strictly increasing inside one ms | yes | yes | same | gen.batch* |
| 49 | Shard of a version 4 UUID | `(149, 39)` | refused | differs | v4.shard, readShard.v4 |
| 50 | Time read out of a version 4 UUID | 268808061933772 (meaningless) | None | differs | v4.instantMs |
| 51 | `Id.random` is a version 4 UUID, variant 2, unique | yes | yes | same | id.random.* |
| 52 | Text accepted by `Id.of` / `ShardedId.of` | `1-1-1-1-1` yes; no dashes, `{...}`, `urn:uuid:` no | the reverse | differs | id.of.*, shardedId.of.* |
| 53 | Empty, garbage or null text refused; upper case read as lower | yes | yes | same | id.of.empty, id.of.garbage, id.of.null, id.of.upper |
| 54 | `Id` / `ShardedId` print as the bare uuid | yes | yes | same | id.toString, shardedId.toString |
| 55 | Id equality: same uuid equal; `Id` never equals `ShardedId` or a raw UUID; two models' ids with one uuid equal; hash = uuid's | yes | yes | same | id.equals.*, id.hash.equalsUuidHash |
| 56 | Ordering of ids whose top bit is set | `80000000-...` sorts first (signed) | `80000000-...` sorts first (signed) | same (fixed in EKB-0293) | order.id.*, order.shardedId.msbHighBitVsZero |
| 57 | Ordering of version 7 ids minted in one ms | by low bits, same order | same | same | order.v7.sameMsDifferentLsb |
| 58 | Typed value prints as | `WalletNumber{x}` | `x` | differs | typed.toString |
| 59 | Typed value of null refused; equal by type and value; other type not equal; hash by value | yes | yes | same | typed.null, typed.equals.same, typed.equals.otherType, typed.hash.same |
| 60 | Typed value subclass equals its parent with the same value | false | true | differs | typed.equals.subclass |
| 61 | Typed values compared with `<` | not `Comparable` | allowed | read: compile-time in Java (Dracula 65) | typed.compare |
| 62 | Shard coordinates outside 0..255 / 0..63 refused; 255/63 accepted; `DEFAULT` is (0, 0) | yes | yes | same | shard.of.*, shard.default |
| 63 | A shard prints as | `ShardIdentifier(1, 2)` | `1:2` | differs | shard.toString |
| 64 | Event and params JSON key names | Java field names | Java field names: `newBalance` | same (fixed in EKB-0290) | json.event.pythonDefaultNames |
| 65 | JSON key order | declaration order; map entries as inserted | declaration order; map entries as inserted | same (fixed in EKB-0290) | json.event.keyOrder, json.event |
| 66 | Decimal in JSON | number, as `toString`: `10.50`, `1E+3`, `1E-7` | number, as `toString`: `10.50`, `1E+3`, `1E-7` | same (fixed in EKB-0290) | json.field.amount, .sci, .tiny, .huge |
| 67 | `Instant` / aware datetime in JSON | shortest: `...00Z`, `...00.120Z` | shortest: `...00Z`, `...00.120Z` | same (fixed in EKB-0290) | json.field.at, .atMillis (.atMicros same) |
| 68 | Time with an offset in JSON | offset kept: `02:00:00+02:00` | offset kept: `02:00:00+02:00` | same (fixed in EKB-0290); at offset zero Python writes the `Instant` form (limit, DECISIONS) | json.field.atOffset |
| 69 | Time with no zone in JSON | `2025-01-01T10:00:00` | `2025-01-01T10:00:00` | same (fixed in EKB-0290) | json.field.localAt |
| 70 | Date and time-of-day in JSON | `"2025-01-31"`, `"10:15:30"` | same | same | json.field.day, json.field.clock |
| 71 | UUID, enum (by name), long, boolean, null, list, nested object keys, text escaping (`\u0001`, `\"`, `é` raw, `</` not escaped) | same text | same text | same | json.field.ownerId, .state, .count, .flag, .missing, .tags, .note, .nested |
| 72 | Float in JSON | number `0.1` | number `0.1` | same (fixed in EKB-0290) | json.field.ratio |
| 73 | Bytes in JSON | base64 `"AQL/"` | base64 `"AQL/"` | same (fixed in EKB-0290) | json.field.raw |
| 74 | Typed id / typed value inside an event | `{"id":..,"value":..}` / `{"value":"W-1"}` | `{"id":..,"value":..}` / `{"value":"W-1"}` | same (fixed in EKB-0290) | json.idsEvent |
| 75 | `ShardIdentifier` inside an event; `modelId` and `modelName` in the payload | `{"group":1,"member":2}`; both present | same | same | json.idsEvent, json.field.modelId, json.field.modelName |
| 76 | Action params JSON | `{"walletId":..,"amount":10.50,"requestedAt":"...120Z","memo":null}` | `{"walletId":..,"amount":10.50,"requestedAt":"...120Z","memo":null}` | same (fixed in EKB-0290) | json.params |
| 77 | An event read back by its own port | decimals lose scale and digits (`10.5`, huge cut); date, time, bytes come back typed | decimals exact; date, time, bytes, zone-less time come back typed | date/time/bytes same (fixed in EKB-0290); Java's decimal loss is a Java bug (DECISIONS, "Noticed in the Java") | json.ownRoundTrip |
| 78 | An event read by the other port | Python's: all exact, but its hex bytes refused | Java's: all exact, typed | Python same as Java reading its own (fixed in EKB-0290); Java's decimal loss is a Java bug | json.readOtherPort, json.readOtherPort.withoutRaw |
| 79 | Unknown key on read | ignored | ignored | same | in.unknownField |
| 80 | Missing key on read | field left null | field left `None` (or its default) | same (fixed in EKB-0290) | in.missingFields, in.snakeCaseKeys |
| 81 | Missing `modelId` on read refused; `modelName` missing or wrong is ignored (taken from the class) | yes | yes | same | in.missingModelId, in.missingModelName, in.otherModelName |
| 82 | Decimal sent as a JSON number: scale and digits lost (`1.50` -> `1.5`, huge cut); integer kept | yes | exact: `1.50`, every digit kept | Java loses digits (Java bug, DECISIONS); Python exact since EKB-0290 | in.amountNumber, in.amountHugeNumber, in.amountInt |
| 83 | Bad decimal text refused; explicit null accepted; duplicate key: last wins | yes | yes | same | in.amountBadString, in.nullAmount, in.duplicateKey |
| 84 | A number sent for a text field | turned into text `"5"` | turned into text `"5"` | same (fixed in EKB-0290) | in.noteNumber |
| 85 | System clock: microseconds (this Mac), UTC, near the wall clock | yes | yes | same | system.* |
| 86 | Virtual clock starts in real time; advancing a running clock refused | yes | yes | same | virtual.default.*, virtual.resumeFrom.advance |
| 87 | `pauseAt`: frozen, two reads equal; advance 1.5 s, 1 us, -10 s; offset input stored as the instant | yes | yes | same | virtual.pauseAt.*, virtual.advance* |
| 88 | `resumeFrom` runs from the anchor; `pause` freezes; `resume` back to real time | yes | yes | same | virtual.resumeFrom.nearAnchor, virtual.pause.frozen, virtual.resume.realTime |
| 89 | A virtual clock started at a fixed instant | nearest is `new VirtualClock(Clock.fixed(t))`: reads t, refuses `advance` | `VirtualClock(t)`: reads t, `advance` works | differs | virtual.constructedAtInstant.* |
| 90 | Clock instant with nanoseconds | kept by the clock (cut by the model, row 20) | cannot be held | read: `datetime` has microseconds only | virtual.nanosInstant |
| 91 | `count_statements` | none | Python-only test helper | read: no Java counterpart (`accounting.py`) | - |

## Differences

Most surprising first.

**78, 77. Decimals read back from JSON (and Java reading its own events).** *Python side fixed in `EKB-0290`: Python writes numbers as Java does and reads every number as a `Decimal`; Java's own loss is recorded under "Noticed in the Java" in DECISIONS-TO-REVIEW, with the one setting that fixes it.* Java writes a
decimal as a JSON number; when any reader parses that number it goes through a double, so
`10.50` comes back `10.5` and `123456789012345678901234567890.123456789` comes back
`123456789012345680000000000000`. That is true for Java's own `EventHandlingJob` reading Java's
payload (the converter reads the stored text into a tree with doubles) and for Python reading
Java's payload. Python writes decimals as strings, so Python reading Python, and Java reading
Python, are exact. Java's side: `SingleTableJsonEventPersister.java:132`,
`persistence/jooq/converter/JSONBObjectNodeConverter.java:22,29`,
`ekbatan-events/local-event-handler/.../EventHandlingJob.java:444`. Python's side:
`_json.py:25-28` (write), `_json.py:123-124` (read: `Decimal(str(float))`). Python could match
the wire form only by writing numbers (Dracula 1); it could stop its own loss when reading Java's
numbers cheaply with `json.loads(..., parse_float=Decimal)` in `_json.loads`. The Java loss is a
Java-side matter (`USE_BIG_DECIMAL_FOR_FLOATS` on the converter's mapper). Surprise: high.

**77, 78. ~~Dates, times and bytes in an event read back by Python come back as text.~~** *Fixed in `EKB-0290`.*
`_json.decode` rebuilds `Decimal`, `UUID`, `datetime`, enums and nested dataclasses, but not
`date`, `time` or `bytes`, so a handler gets `"2025-01-31"` where its event says `date`, even for
Python's own payload. A zone-less `datetime` comes back with a UTC zone. Java restores all of
them. Python `_json.py:109-142`. Could match: yes, cheaply - three more branches in `_revive`.
Surprise: high.

**73, 78. ~~Bytes: base64 in Java, hex in Python.~~** *Fixed in `EKB-0290`: base64.* Each port's bytes are unreadable to the other:
Java refuses Python's `"0102ff"`, Python hands back Java's `"AQL/"` as text. Java: Jackson
default. Python `_json.py:45-46`. Could match: yes, cheaply (`base64.b64encode` / decode).
Surprise: medium.

**74. ~~Typed ids and typed values inside an event.~~** *Fixed in `EKB-0290`: Python writes Java's shape.* Jackson writes a `ShardedId` field as
`{"id":"...","value":"..."}` and a `TypedValue` as `{"value":"W-1"}` (their getters); Python
writes the bare value. Java: `domain/ShardedId.java:67-75`, `domain/TypedValue.java:29-31` (no
`@JsonValue`). Python `_json.py:49-53`. Could match: yes, but the Java shape is probably an
accident, not a contract; the better fix is `@JsonValue` on the Java side. Surprise: medium.

**8, 41, 42, 80. ~~A field never set gives `TypeError`, not `ValidationError`.~~** *Fixed in `EKB-0292` (builders) and `EKB-0290` (JSON).* Building a model
without calling a required setter, or reading an event payload with a missing key, ends in the
dataclass constructor's `TypeError: missing 1 required keyword-only argument: 'id'`. Java says
"id cannot be null" (and for JSON simply leaves the field null). Code catching
`ValidationError` (or `ValueError`) does not catch it. Python `domain.py:272`, `_json.py:100`;
Java `domain/Model.java:81-87`. Could match: yes, cheaply - `build()` and `decode` can check the
missing names first, as `_builder.FieldBuilder.build` already does. Surprise: medium.

**56. ~~Id ordering.~~** *Fixed in `EKB-0293`: Java's signed order.* Java's `UUID.compareTo` compares the two halves as signed numbers, so an id
starting `8...`-`f...` sorts before one starting `0...`; Python (and PostgreSQL) compare unsigned.
Version 7 ids agree (row 57) until the year ~6400; random version 4 ids (`Id.random`) sort
differently in half the cases. Neither framework sorts ids itself. Java `domain/Id.java:69-71`,
`domain/ShardedId.java:78-80`; Python `ids.py:92-100`. Could match: yes, cheaply (compare as
signed halves) - but Python's order is the database's. Surprise: medium.

**66, 72, 76, 64, 65. ~~JSON text is not byte-identical.~~** *Fixed in `EKB-0290`: byte-identical, checked over 50,000 values.* Beyond the known names (Dracula 1):
Python sorts keys, including inside maps (Java keeps declaration / insertion order); decimals are
strings (Java numbers, in `toString` form such as `1E+3`); a float is a string (Java a number).
Java: `SingleTableJsonEventPersister.java:132,142` with Jackson 3 defaults; Python
`_json.py:25-32`, `_json.py:72`. Could match: partly - key order, floats and decimals can follow
Jackson, at the cost of the "sorted, canonical" promise in `_json.dumps`. Surprise: high for a
reader comparing rows (known: Dracula 1).

**67, 68, 69. ~~Time in JSON.~~** *Fixed in `EKB-0290`; one limit: an `OffsetDateTime` at offset zero.* Python always writes six fraction digits and always UTC with `Z`;
Java writes the shortest form, keeps an offset on an `OffsetDateTime`, and writes no zone for a
`LocalDateTime` (Python treats a naive datetime as UTC and adds `Z`). Both ports parse the
other's instants correctly (row 78). Python `_json.py:35-38`. Could match: partly - trimming
digits is cheap; offsets need the type kept. Surprise: low.

**22. ~~Model timestamps cannot be compared with `clock.now()`.~~** *Fixed in `EKB-0294`.* Known: Dracula 43. Python
`domain.py:654`, `clock.py:132-144`; Java `domain/Model.java:84-86`. Surprise: high.

**39. Event equality.** Known: Dracula 56. Python `domain.py:87`; Java `domain/ModelEvent.java:23`.
Surprise: medium.

**5, 6. ~~Version left unset.~~** *Fixed in `EKB-0292`.* Known: Dracula 52; row 6 is the same default seen through
`increase_version()`. Python `domain.py:479`, `domain.py:230-231`; Java `domain/Model.java:87`,
`domain/Model.java:238-240`. Surprise: medium.

**13, 42. Unchecked business field.** Known: Dracula 53 (Python refuses `None` in any field not
typed `X | None`). Python `domain.py:310-343`; Java `domain/Model.java:80-89`. Surprise: medium.

**11. A null event.** Java's `List.copyOf` refuses it at build; Python keeps `None` in `events`,
and it fails later in the outbox. Python `domain.py:297-299`, `domain.py:643-646`; Java
`domain/Model.java:82`. Could match: yes, cheaply. Surprise: low.

**52. Which uuid text is accepted.** Java's `UUID.fromString` accepts the short form `1-1-1-1-1`
and refuses 32 hex digits without dashes, `{...}` and `urn:uuid:`; Python's `uuid.UUID` is the
other way round. Java `domain/Id.java:34-36`; Python `ids.py:119-126`, `ids.py:164-171`. Could
match: yes (a regex before parsing), cheap. Surprise: low.

**49, 50. A version 4 uuid as a sharded id.** Known: Dracula 60. Java reads shard bits and a
timestamp out of any uuid (`shard/ShardedUUID.java:77-96`); Python refuses the shard and returns
`None` for the time (`ids.py:183-196`, `sharding.py:102-125`). Surprise: low.

**84. ~~A number sent for a text field.~~** *Fixed in `EKB-0290`.* Jackson turns it into text; `_json.decode` passes the int
through unchanged into a `str` field. Python `_json.py:113-142`. Could match: yes, cheaply.
Surprise: low.

**89. A virtual clock started at an instant.** Known: Dracula 59. Python `clock.py:63-65`; Java
`ekbatan-test-support/.../VirtualClock.java:49-52`. Surprise: medium.

**58, 60. Typed value printing and subclass equality.** Known: Dracula 65. Python `ids.py:72-106`;
Java `domain/TypedValue.java:34-49`. Surprise: low.

**63. Shard printing.** Known: Dracula 66. Python `sharding.py:69-70`; Java
`shard/ShardIdentifier.java:73-76`. Surprise: low.

**7. Version past the 64-bit limit.** Java wraps to a negative number and refuses it; Python's int
grows (the database column would refuse it on write). Python `domain.py:573-574`; Java
`domain/Model.java:238-240`. Could match: yes, cheaply; nobody will reach it. Surprise: low.

**Totals:** 91 rows - 51 same, 32 differs, 8 read, as scanned. Parity-fix group J
(`EKB-0290`-`EKB-0294`) settled rows 5, 6, 8, 22, 41, 56, 64-69, 72-74, 76, 80 and 84, and the
Python side of 77 and 78; row 82 now differs only by Java's decimal loss (a Java bug), and 42 only
by Dracula 53.
