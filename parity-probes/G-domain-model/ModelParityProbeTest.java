package io.ekbatan.core.domain;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Callable;
import org.junit.jupiter.api.Test;

/** Area G twin probe: Model / Entity / ModelEvent. Twin: model_probe.py. Temporary. */
class ModelParityProbeTest {

    static void p(String key, Object value) {
        System.out.println("PROBE " + key + ": " + fmt(value));
    }

    static String fmt(Object v) {
        if (v instanceof Boolean b) return b ? "True" : "False";
        return String.valueOf(v);
    }

    static String err(Callable<Object> fn) {
        try {
            return "OK " + fmt(fn.call());
        } catch (Throwable e) {
            return "ERR " + e.getClass().getSimpleName() + ": " + e.getMessage();
        }
    }

    static class SampleEvent extends ModelEvent<SampleModel> {
        SampleEvent(String id) {
            super(id, SampleModel.class);
        }
    }

    static class SampleModel extends Model<SampleModel, Id<SampleModel>, GenericState> {
        final String label;
        final String name;

        SampleModel(Builder b) {
            super(b);
            this.label = io.ekbatan.core.internal.Validate.notNull(b.label, "label cannot be null");
            this.name = b.name;
        }

        @Override
        public Builder copy() {
            return Builder.sampleModel().copyBase(this).label(label).name(name);
        }

        static class Builder extends Model.Builder<Id<SampleModel>, Builder, SampleModel, GenericState> {
            String label = "L";
            String name = "N";

            static Builder sampleModel() {
                return new Builder();
            }

            Builder label(String v) {
                this.label = v;
                return this;
            }

            Builder name(String v) {
                this.name = v;
                return this;
            }

            @Override
            public SampleModel build() {
                return new SampleModel(this);
            }
        }
    }

    static class OtherModel extends Model<OtherModel, Id<OtherModel>, GenericState> {
        OtherModel(Builder b) {
            super(b);
        }

        @Override
        public Builder copy() {
            return new Builder().copyBase(this);
        }

        static class Builder extends Model.Builder<Id<OtherModel>, Builder, OtherModel, GenericState> {
            @Override
            public OtherModel build() {
                return new OtherModel(this);
            }
        }
    }

    static class SampleEntity extends Entity<SampleEntity, Id<SampleEntity>, GenericState> {
        SampleEntity(Builder b) {
            super(b);
        }

        @Override
        public Builder copy() {
            return new Builder().copyBase(this);
        }

        static class Builder extends Entity.Builder<Id<SampleEntity>, Builder, SampleEntity, GenericState> {
            @Override
            public SampleEntity build() {
                return new SampleEntity(this);
            }
        }
    }

    static final UUID U = UUID.fromString("0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b");
    static final UUID U2 = UUID.fromString("0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c");
    static final Id<SampleModel> ID = Id.of(SampleModel.class, U);
    static final Id<SampleModel> ID2 = Id.of(SampleModel.class, U2);
    static final Instant T0 = Instant.parse("2025-01-01T00:00:00Z");
    static final Instant T6 = Instant.parse("2025-06-01T00:00:00Z");

    static SampleModel.Builder base() {
        return SampleModel.Builder.sampleModel().id(ID).state(GenericState.ACTIVE).createdDate(T0);
    }

    @Test
    void probe() {
        var m = base().withInitialVersion().build();
        p("model.withInitialVersion", m.version);
        p("model.increaseVersion", m.copy().increaseVersion().build().version);
        p("model.explicitVersion5", base().version(5L).build().version);
        p("model.version0", err(() -> base().version(0L).build()));
        p("model.versionNeg1", err(() -> base().version(-1L).build()));
        p("model.withInitialVersionAfter5", base().version(5L).withInitialVersion().build().version);
        p("model.versionNotSet", err(() -> base().build().version));
        p("model.increaseVersionWhenUnset", err(() -> base().increaseVersion().build().version));
        p(
                "model.versionLongMax.nextVersion",
                err(() -> ((SampleModel) base().version(Long.MAX_VALUE).build().nextVersion()).version));

        p("model.missingId", err(() -> SampleModel.Builder.sampleModel()
                .state(GenericState.ACTIVE)
                .createdDate(T0)
                .withInitialVersion()
                .build()));
        p("model.missingState", err(() -> SampleModel.Builder.sampleModel()
                .id(ID)
                .createdDate(T0)
                .withInitialVersion()
                .build()));
        p("model.missingCreatedDate", err(() -> SampleModel.Builder.sampleModel()
                .id(ID)
                .state(GenericState.ACTIVE)
                .withInitialVersion()
                .build()));
        p("model.nullId", err(() -> base().id(null).withInitialVersion().build()));
        p("model.nullState", err(() -> base().state(null).withInitialVersion().build()));
        p("model.nullCreatedDate", err(() -> base().createdDate(null).withInitialVersion().build()));
        p("model.nullVersion", err(() -> base().version(null).build()));
        p("model.nullEventsList", err(() -> base().withInitialVersion().events(null).build()));
        p("model.nullEventInList", err(() -> base().withInitialVersion()
                .withEvent(null)
                .build()
                .events
                .size()));
        p("model.nullCheckedField", err(() -> base().withInitialVersion().label(null).build().label));
        p("model.nullUncheckedField", err(() -> base().withInitialVersion().name(null).build().name));

        var ev = new SampleEvent(U.toString());
        p("model.oneEvent", base().withInitialVersion().withEvent(ev).build().events.size());
        p(
                "model.twoEvents",
                base().withInitialVersion()
                        .withEvent(ev)
                        .withEvent(new SampleEvent(U.toString()))
                        .build()
                        .events
                        .size());
        var e1 = new SampleEvent(U.toString());
        var rep = base().withInitialVersion()
                .withEvent(ev)
                .withEvent(ev)
                .events(List.of(e1))
                .build();
        p("model.eventsReplace.size", rep.events.size());
        p("model.eventsReplace.same", rep.events.getFirst() == e1);
        p("model.eventsImmutable", err(() -> m.events.add(ev)));
        var b = base().withInitialVersion();
        var first = b.build();
        var second = b.withEvent(ev).build();
        p("model.eventsSnapshot", first.events.size() + "," + second.events.size());
        p("model.noEventsEmpty", m.events.size());
        List<ModelEvent<SampleModel>> src = new ArrayList<>();
        src.add(ev);
        var viaList = base().withInitialVersion().events(src).build();
        src.add(ev);
        p("model.callerListMutatedAfterBuild", viaList.events.size());

        p(
                "model.createdDate.micros",
                base().createdDate(Instant.parse("2025-01-01T00:00:00.123456Z"))
                        .withInitialVersion()
                        .build()
                        .createdDate);
        p(
                "model.createdDate.plus2",
                base().createdDate(java.time.OffsetDateTime.parse("2025-01-01T02:00:00+02:00")
                                .toInstant())
                        .withInitialVersion()
                        .build()
                        .createdDate);
        p("model.createdDate.hasZone", "n/a (Instant)");
        p("model.updatedDefaultsToCreated", m.updatedDate.equals(m.createdDate));
        p("model.updatedExplicit", base().updatedDate(T6).withInitialVersion().build().updatedDate);
        p("model.updatedBeforeCreated", err(() -> base().updatedDate(Instant.parse("2020-01-01T00:00:00Z"))
                .withInitialVersion()
                .build()
                .updatedDate));
        p("model.createdNaiveInput", "n/a (Instant has no zone-less form)");
        p("model.createdDateVsClockNow.compare", err(() -> m.createdDate.isBefore(Instant.now())));
        // extra, Java-only input: nanoseconds are truncated
        p(
                "model.createdDate.nanos",
                base().createdDate(Instant.parse("2025-01-01T00:00:00.123456789Z"))
                        .withInitialVersion()
                        .build()
                        .createdDate);

        var orig = base().updatedDate(T6).version(3L).withEvent(ev).build();
        var cp = orig.copy().build();
        p(
                "model.copy.fields",
                fmt(cp.id.equals(orig.id)) + "," + fmt(cp.state.equals(orig.state)) + ","
                        + fmt(cp.createdDate.equals(orig.createdDate)) + ","
                        + fmt(cp.updatedDate.equals(orig.updatedDate)) + "," + cp.version + "," + cp.events.size());
        var o2 = base().withInitialVersion().withEvent(ev).build();
        var c2 = o2.copy().withEvent(ev).build();
        p("model.copy.eventsIndependent", o2.events.size() + "," + c2.events.size());
        p(
                "model.copy.businessFields",
                orig.copy().build().label + ","
                        + base().withInitialVersion().label("X").build().copy().build().label);
        p("model.copy.isNewInstance", cp != orig);
        p("model.copyOverride", orig.copy().state(GenericState.DELETED).build().state.name());

        var a = base().withInitialVersion().build();
        var bb = base().withInitialVersion().build();
        p("eq.sameFields", a.equals(bb));
        p("eq.ignoresEvents", base().withInitialVersion().withEvent(ev).build().equals(a));
        p("eq.diffId", base().id(ID2).withInitialVersion().build().equals(a));
        p("eq.diffVersion", base().version(2L).build().equals(a));
        p("eq.diffState", base().state(GenericState.DELETED).withInitialVersion().build().equals(a));
        p("eq.diffCreated", base().createdDate(T6).withInitialVersion().build().equals(a));
        p("eq.diffUpdated", base().updatedDate(T6).withInitialVersion().build().equals(a));
        p("eq.diffBusinessField", base().label("other").withInitialVersion().build().equals(a));
        var other = new OtherModel.Builder()
                .id(Id.of(OtherModel.class, U))
                .state(GenericState.ACTIVE)
                .createdDate(T0)
                .withInitialVersion()
                .build();
        p("eq.otherClassSameBase", other.equals(a));
        p("eq.hashById", base().createdDate(T6).withInitialVersion().build().hashCode() == a.hashCode());
        p("eq.hashEqualsIdHash", a.hashCode() == ID.hashCode());
        Set<SampleModel> set = new HashSet<>();
        set.add(a);
        set.add(base().version(2L).build());
        p("eq.inSetDifferentVersions", set.size());
        p("model.isModel", a.isModel());

        SampleModel nv = base().withInitialVersion()
                .withEvent(ev)
                .updatedDate(T6)
                .build()
                .nextVersion();
        p("model.nextVersion", nv.version + "," + fmt(nv.id.equals(ID)) + "," + nv.events.size() + "," + nv.updatedDate);
        p("model.mutateField", "n/a (final field: compile error)");
        p("model.isDeleted.deletedState", "n/a (no isDeleted on Model)");

        Id<SampleEntity> eid = Id.of(SampleEntity.class, U);
        java.util.function.Supplier<SampleEntity.Builder> ebase =
                () -> new SampleEntity.Builder().id(eid).state(GenericState.ACTIVE);
        var en = ebase.get().withInitialVersion().build();
        p("entity.withInitialVersion", en.version);
        p("entity.increaseVersion", en.copy().increaseVersion().build().version);
        p("entity.version0", err(() -> ebase.get().version(0L).build()));
        p("entity.missingId", err(() -> new SampleEntity.Builder()
                .state(GenericState.ACTIVE)
                .withInitialVersion()
                .build()));
        p("entity.nullId", err(() -> ebase.get().id(null).withInitialVersion().build()));
        p("entity.nullState", err(() -> ebase.get().state(null).withInitialVersion().build()));
        p("entity.versionNotSet", err(() -> ebase.get().build().version));
        p("entity.eq.same", ebase.get().withInitialVersion().build().equals(en));
        p("entity.eq.diffState", ebase.get().state(GenericState.DELETED).withInitialVersion().build().equals(en));
        p("entity.eq.diffVersion", ebase.get().version(2L).build().equals(en));
        p("entity.hashById", ebase.get().version(7L).build().hashCode() == en.hashCode());
        p("entity.isModel", en.isModel());
        SampleEntity nve = en.nextVersion();
        p("entity.nextVersion", nve.version + "," + fmt(nve.id.equals(eid)));
        p("entity.copyOverride", en.copy().state(GenericState.DELETED).build().state.name());

        p("event.modelIdAndName", ev.modelId + "," + ev.modelName);
        p("event.nullModelId", err(() -> new SampleEvent(null)));
        p("event.blankModelId", err(() -> new SampleEvent("")));
        p("event.whitespaceModelId", err(() -> new SampleEvent("   ")));
        p("event.equalSameFields", new SampleEvent("a").equals(new SampleEvent("a")));
        p("event.equalDifferentModelId", new SampleEvent("a").equals(new SampleEvent("b")));
        p("event.modelIdNotTrimmed", "'" + new SampleEvent(" a ").modelId + "'");
        p("event.mutate", "n/a (final field: compile error)");
    }
}
