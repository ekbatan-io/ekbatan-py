"""Area G twin probe: Model / Entity / ModelEvent behaviour (Java's ModelTest, EntityTest,
ModelEventTest, plus untested corners). Twin: ModelParityProbeTest.java. Prints PROBE lines."""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from ekbatan.domain import Entity, EntityBuilder, GenericState, Model, ModelBuilder, ModelEvent
from ekbatan.ids import Id


def p(key, value):
    print(f"PROBE {key}: {value}")


def err(fn):
    try:
        r = fn()
        return f"OK {r}"
    except Exception as e:  # noqa: BLE001
        return f"ERR {type(e).__name__}: {e}"


class SampleModelBuilder(ModelBuilder["SampleModel", "Id[SampleModel]", "GenericState"]):
    def name(self, v):
        return self.set(name=v)

    def label(self, v):
        return self.set(label=v)


@dataclass(frozen=True, kw_only=True)
class SampleEvent(ModelEvent["SampleModel"]):
    pass


@dataclass(frozen=True, kw_only=True)
class SampleModel(Model[Id["SampleModel"], GenericState, SampleModelBuilder]):
    # Java's twin: `label` checked in the constructor, `name` not checked at all.
    label: str = "L"
    name: str = "N"


@dataclass(frozen=True, kw_only=True)
class OtherModel(Model[Id["OtherModel"], GenericState, ModelBuilder]):
    pass


class SampleEntityBuilder(EntityBuilder["SampleEntity", "Id[SampleEntity]", "GenericState"]):
    pass


@dataclass(frozen=True, kw_only=True)
class SampleEntity(Entity[Id["SampleEntity"], GenericState, SampleEntityBuilder]):
    pass


ID = Id.of("0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b")
ID2 = Id.of("0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c")
T0 = datetime(2025, 1, 1, tzinfo=UTC)
T6 = datetime(2025, 6, 1, tzinfo=UTC)


def base():
    return SampleModel.builder().id(ID).state(GenericState.ACTIVE).created_date(T0)


def ts(d):
    # Render as Java's Instant.toString would for comparison: UTC, 'Z', no zone object.
    if d.tzinfo is not None:
        d = d.astimezone(UTC).replace(tzinfo=None)
    return d.isoformat() + "Z"


m = base().with_initial_version().build()
p("model.withInitialVersion", m.version)
p("model.increaseVersion", m.copy().increase_version().build().version)
p("model.explicitVersion5", base().version(5).build().version)
p("model.version0", err(lambda: base().version(0).build()))
p("model.versionNeg1", err(lambda: base().version(-1).build()))
p("model.withInitialVersionAfter5", base().version(5).with_initial_version().build().version)
p("model.versionNotSet", err(lambda: base().build().version))
p("model.increaseVersionWhenUnset", err(lambda: base().increase_version().build().version))
big = 9223372036854775807
p("model.versionLongMax.nextVersion", err(lambda: base().version(big).build().next_version().version))

# missing base fields: setter never called
p("model.missingId", err(lambda: SampleModel.builder().state(GenericState.ACTIVE).created_date(T0).with_initial_version().build()))
p("model.missingState", err(lambda: SampleModel.builder().id(ID).created_date(T0).with_initial_version().build()))
p("model.missingCreatedDate", err(lambda: SampleModel.builder().id(ID).state(GenericState.ACTIVE).with_initial_version().build()))
# explicit null
p("model.nullId", err(lambda: base().id(None).with_initial_version().build()))
p("model.nullState", err(lambda: base().state(None).with_initial_version().build()))
p("model.nullCreatedDate", err(lambda: base().created_date(None).with_initial_version().build()))
p("model.nullVersion", err(lambda: base().version(None).build()))
p("model.nullEventsList", err(lambda: base().with_initial_version().events(None).build()))
p("model.nullEventInList", err(lambda: len(base().with_initial_version().with_event(None).build().events)))
# business fields
p("model.nullCheckedField", err(lambda: base().with_initial_version().label(None).build().label))
p("model.nullUncheckedField", err(lambda: base().with_initial_version().name(None).build().name))

# events
ev = SampleEvent(model_id=str(ID))
p("model.oneEvent", len(base().with_initial_version().with_event(ev).build().events))
p("model.twoEvents", len(base().with_initial_version().with_event(ev).with_event(SampleEvent(model_id=str(ID))).build().events))
e1 = SampleEvent(model_id=str(ID))
rep = base().with_initial_version().with_event(ev).with_event(ev).events([e1]).build()
p("model.eventsReplace.size", len(rep.events))
p("model.eventsReplace.same", rep.events[0] is e1)
p("model.eventsImmutable", err(lambda: m.events.append(ev)))
b = base().with_initial_version()
first = b.build()
second = b.with_event(ev).build()
p("model.eventsSnapshot", f"{len(first.events)},{len(second.events)}")
p("model.noEventsEmpty", len(m.events))
src = [ev]
viaList = base().with_initial_version().events(src).build()
src.append(ev)
p("model.callerListMutatedAfterBuild", len(viaList.events))

# timestamps
p("model.createdDate.micros", ts(base().created_date(datetime(2025, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)).with_initial_version().build().created_date))
p("model.createdDate.plus2", ts(base().created_date(datetime(2025, 1, 1, 2, 0, tzinfo=timezone(timedelta(hours=2)))).with_initial_version().build().created_date))
p("model.createdDate.hasZone", m.created_date.tzinfo is not None)
p("model.updatedDefaultsToCreated", m.updated_date == m.created_date)
p("model.updatedExplicit", ts(base().updated_date(T6).with_initial_version().build().updated_date))
p("model.updatedBeforeCreated", err(lambda: ts(base().updated_date(datetime(2020, 1, 1, tzinfo=UTC)).with_initial_version().build().updated_date)))
p("model.createdNaiveInput", err(lambda: ts(base().created_date(datetime(2025, 1, 1)).with_initial_version().build().created_date)))
p("model.createdDateVsClockNow.compare", err(lambda: m.created_date < datetime.now(UTC)))

# copy
orig = base().updated_date(T6).version(3).with_event(ev).build()
cp = orig.copy().build()
p("model.copy.fields", f"{cp.id == orig.id},{cp.state == orig.state},{cp.created_date == orig.created_date},{cp.updated_date == orig.updated_date},{cp.version},{len(cp.events)}")
o2 = base().with_initial_version().with_event(ev).build()
c2 = o2.copy().with_event(ev).build()
p("model.copy.eventsIndependent", f"{len(o2.events)},{len(c2.events)}")
p("model.copy.businessFields", f"{orig.copy().build().label},{base().with_initial_version().label('X').build().copy().build().label}")
p("model.copy.isNewInstance", cp is not orig)
p("model.copyOverride", orig.copy().state(GenericState.DELETED).build().state.name)

# equality
a = base().with_initial_version().build()
bb = base().with_initial_version().build()
p("eq.sameFields", a == bb)
p("eq.ignoresEvents", base().with_initial_version().with_event(ev).build() == a)
p("eq.diffId", base().id(ID2).with_initial_version().build() == a)
p("eq.diffVersion", base().version(2).build() == a)
p("eq.diffState", base().state(GenericState.DELETED).with_initial_version().build() == a)
p("eq.diffCreated", base().created_date(T6).with_initial_version().build() == a)
p("eq.diffUpdated", base().updated_date(T6).with_initial_version().build() == a)
p("eq.diffBusinessField", base().label("other").with_initial_version().build() == a)
other = OtherModel(id=ID, state=GenericState.ACTIVE, created_date=T0, version=1)
p("eq.otherClassSameBase", other == a)
p("eq.hashById", hash(base().created_date(T6).with_initial_version().build()) == hash(a))
p("eq.hashEqualsIdHash", hash(a) == hash(ID))
p("eq.inSetDifferentVersions", len({a, base().version(2).build()}))
p("model.isModel", a.is_model)

nv = base().with_initial_version().with_event(ev).updated_date(T6).build().next_version()
p("model.nextVersion", f"{nv.version},{nv.id == ID},{len(nv.events)},{ts(nv.updated_date)}")
p("model.mutateField", err(lambda: setattr(a, "version", 9)))
p("model.isDeleted.deletedState", base().state(GenericState.DELETED).with_initial_version().build().is_deleted)

# entity
ebase = lambda: SampleEntity.builder().id(ID).state(GenericState.ACTIVE)  # noqa: E731
en = ebase().with_initial_version().build()
p("entity.withInitialVersion", en.version)
p("entity.increaseVersion", en.copy().increase_version().build().version)
p("entity.version0", err(lambda: ebase().version(0).build()))
p("entity.missingId", err(lambda: SampleEntity.builder().state(GenericState.ACTIVE).with_initial_version().build()))
p("entity.nullId", err(lambda: ebase().id(None).with_initial_version().build()))
p("entity.nullState", err(lambda: ebase().state(None).with_initial_version().build()))
p("entity.versionNotSet", err(lambda: ebase().build().version))
p("entity.eq.same", ebase().with_initial_version().build() == en)
p("entity.eq.diffState", ebase().state(GenericState.DELETED).with_initial_version().build() == en)
p("entity.eq.diffVersion", ebase().version(2).build() == en)
p("entity.hashById", hash(ebase().version(7).build()) == hash(en))
p("entity.isModel", en.is_model)
nve = en.next_version()
p("entity.nextVersion", f"{nve.version},{nve.id == ID}")
p("entity.copyOverride", en.copy().state(GenericState.DELETED).build().state.name)

# events
p("event.modelIdAndName", f"{ev.model_id},{ev.model_name}")
p("event.nullModelId", err(lambda: SampleEvent(model_id=None)))
p("event.blankModelId", err(lambda: SampleEvent(model_id="")))
p("event.whitespaceModelId", err(lambda: SampleEvent(model_id="   ")))
p("event.equalSameFields", SampleEvent(model_id="a") == SampleEvent(model_id="a"))
p("event.equalDifferentModelId", SampleEvent(model_id="a") == SampleEvent(model_id="b"))
p("event.modelIdNotTrimmed", repr(SampleEvent(model_id=" a ").model_id))
p("event.mutate", err(lambda: setattr(ev, "model_id", "x")))
