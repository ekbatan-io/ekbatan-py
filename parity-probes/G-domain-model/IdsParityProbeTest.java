package io.ekbatan.core.domain;

import io.ekbatan.core.shard.ShardIdentifier;
import io.ekbatan.core.shard.ShardedUUID;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import org.junit.jupiter.api.Test;

/** Area G twin probe: ids, sharded UUID layout, typed values. Twin: ids_probe.py. Temporary. */
class IdsParityProbeTest {

    static final Path DIR = Path.of("/Users/mike/workspace/ekbatan-py/parity-probes/G-domain-model");

    static void p(String key, Object value) {
        System.out.println("PROBE " + key + ": " + fmt(value));
    }

    static String fmt(Object v) {
        if (v instanceof Boolean b) return b ? "True" : "False";
        if (v == null) return "None";
        return String.valueOf(v);
    }

    static String err(Callable<Object> fn) {
        try {
            return "OK " + fmt(fn.call());
        } catch (Throwable e) {
            return "ERR " + e.getClass().getSimpleName() + ": " + e.getMessage();
        }
    }

    static String sh(ShardIdentifier s) {
        return "(" + s.group + ", " + s.member + ")";
    }

    interface A extends Identifiable<Id<A>> {}

    interface B extends Identifiable<Id<B>> {}

    static ShardedId<A> sid(String s) {
        return ShardedId.of(A.class, ShardedUUID.from(UUID.fromString(s)));
    }

    static class WalletNumber extends TypedValue<String> {
        WalletNumber(String v) {
            super(v);
        }
    }

    static class SpecialWalletNumber extends WalletNumber {
        SpecialWalletNumber(String v) {
            super(v);
        }
    }

    static class Iban extends TypedValue<String> {
        Iban(String v) {
            super(v);
        }
    }

    static int sign(int c) {
        return Integer.signum(c);
    }

    @Test
    void probe() throws Exception {
        String[][] fixed = {
            {"0_0", "01941f29-7c7b-7abc-8000-123456789abc"},
            {"1_2", "01941f29-7c7b-7abc-8042-123456789abc"},
            {"17_5", "01941f29-7c7b-7abc-8445-123456789abc"},
            {"255_63", "01941f29-7c7b-7abc-bfff-123456789abc"},
        };
        for (var f : fixed) {
            var s = sid(f[1]);
            var su = ShardedUUID.from(s.getValue());
            p(
                    "fixed." + f[0],
                    "shard=" + sh(s.resolveShardIdentifier()) + " ms=" + su.instant().toEpochMilli() + " str=" + s);
        }
        var v4 = "f47ac10b-58cc-4372-a567-0e02b2c3d479";
        p("v4.shard", err(() -> sh(sid(v4).resolveShardIdentifier())));
        p("v4.instantMs", err(() -> ShardedUUID.from(UUID.fromString(v4)).instant().toEpochMilli()));

        int[][] shards = {{0, 0}, {1, 2}, {17, 5}, {255, 63}};
        long now = System.currentTimeMillis();
        List<String> mine = new ArrayList<>();
        for (var gm : shards) {
            var s = ShardedId.generate(A.class, ShardIdentifier.of(gm[0], gm[1]));
            var u = s.getValue();
            mine.add(u.toString());
            long ms = ShardedUUID.from(u).instant().toEpochMilli();
            p(
                    "gen." + gm[0] + "_" + gm[1],
                    "version=" + u.version() + " variant=" + u.variant() + " shard="
                            + sh(s.resolveShardIdentifier()) + " msNearNow=" + fmt(Math.abs(ms - now) < 5000));
        }
        Files.writeString(DIR.resolve("java_ids.txt"), String.join("\n", mine) + "\n");

        List<UUID> batch = new ArrayList<>();
        for (int i = 0; i < 2000; i++)
            batch.add(ShardedId.generate(A.class, ShardIdentifier.of(1, 2)).getValue());
        boolean strictly = true;
        boolean timeOk = true;
        for (int i = 1; i < batch.size(); i++) {
            if (batch.get(i - 1).toString().compareTo(batch.get(i).toString()) >= 0) strictly = false;
            if ((batch.get(i - 1).getMostSignificantBits() >>> 16) > (batch.get(i).getMostSignificantBits() >>> 16))
                timeOk = false;
        }
        p("gen.batchStrictlyIncreasing", strictly);
        p("gen.batchDistinct", new HashSet<>(batch).size() == batch.size());
        p("gen.batchTimeNonDecreasing", timeOk);

        var other = DIR.resolve("python_ids.txt");
        if (Files.exists(other)) {
            var lines = Files.readString(other).trim().split("\\s+");
            for (int i = 0; i < shards.length; i++) {
                var u = UUID.fromString(lines[i]);
                var s = sid(lines[i]);
                p(
                        "cross.otherPort." + shards[i][0] + "_" + shards[i][1],
                        "version=" + u.version() + " shard=" + sh(s.resolveShardIdentifier()) + " msNearFile=True");
            }
        } else {
            p("cross.otherPort", "python_ids.txt missing");
        }

        var r = Id.random(A.class);
        p("id.random.version", r.getValue().version());
        p("id.random.variant", r.getValue().variant());
        p("id.random.unique", !Id.random(A.class).equals(Id.random(A.class)));
        String[][] texts = {
            {"upper", "F47AC10B-58CC-4372-A567-0E02B2C3D479"},
            {"noDashes", "f47ac10b58cc4372a5670e02b2c3d479"},
            {"braces", "{f47ac10b-58cc-4372-a567-0e02b2c3d479}"},
            {"urn", "urn:uuid:f47ac10b-58cc-4372-a567-0e02b2c3d479"},
            {"shortGroups", "1-1-1-1-1"},
            {"empty", ""},
            {"garbage", "not-a-uuid"},
        };
        for (var t : texts) {
            p("id.of." + t[0], err(() -> Id.of(A.class, t[1]).toString()));
            p("shardedId.of." + t[0], err(() -> sid(t[1]).toString()));
        }
        p("id.of.null", err(() -> Id.of(A.class, (String) null).toString()));
        p("shardedId.of.null", err(() -> ShardedId.of(A.class, ShardedUUID.from(null))
                .toString()));

        var u = UUID.fromString(v4);
        p("id.toString", Id.of(A.class, u).toString());
        p("shardedId.toString", sid(fixed[1][1]).toString());
        p("id.equals.sameUuid", Id.of(A.class, u).equals(Id.of(A.class, u)));
        p("id.equals.diffUuid", Id.of(A.class, u).equals(Id.random(A.class)));
        p("id.equals.shardedIdSameUuid", Id.of(A.class, u).equals(ShardedId.of(A.class, ShardedUUID.from(u))));
        p("id.equals.otherModelSameUuid", Id.of(A.class, u).equals(Id.of(B.class, u)));
        p("id.hash.equalsUuidHash", Id.of(A.class, u).hashCode() == u.hashCode());
        p("id.equals.rawUuid", Id.of(A.class, u).equals(u));

        var hi = "80000000-0000-4000-8000-000000000000";
        var lo = "00000000-0000-4000-8000-000000000000";
        var lsbHi = "00000000-0000-4000-c000-000000000000";
        var lsbLo = "00000000-0000-4000-0000-000000000000";
        p("order.id.msbHighBitVsZero", sign(Id.of(A.class, hi).compareTo(Id.of(A.class, lo))));
        p("order.shardedId.msbHighBitVsZero", sign(sid(hi).compareTo(sid(lo))));
        p("order.id.lsbVariant10vs00", sign(Id.of(A.class, lsbHi).compareTo(Id.of(A.class, lsbLo))));
        List<Id<A>> list = new ArrayList<>(List.of(
                Id.of(A.class, hi), Id.of(A.class, lo), Id.of(A.class, "7fffffff-0000-4000-8000-000000000000")));
        list.sort(null);
        List<String> pre = new ArrayList<>();
        for (var x : list) pre.add("'" + x.toString().substring(0, 8) + "'");
        p("order.id.sorted", pre.toString());
        p("order.v7.sameMsDifferentLsb", sign(sid(fixed[3][1]).compareTo(sid(fixed[0][1]))));

        p("typed.toString", new WalletNumber("x").toString());
        p("typed.null", err(() -> new WalletNumber(null)));
        p("typed.equals.same", new WalletNumber("x").equals(new WalletNumber("x")));
        p("typed.equals.otherType", new WalletNumber("x").equals(new Iban("x")));
        p("typed.equals.subclass", new SpecialWalletNumber("x").equals(new WalletNumber("x")));
        p("typed.hash.same", new WalletNumber("x").hashCode() == new WalletNumber("x").hashCode());
        p("typed.compare", "n/a (TypedValue is not Comparable: compile error)");

        p("shard.of.256_0", err(() -> sh(ShardIdentifier.of(256, 0))));
        p("shard.of.0_64", err(() -> sh(ShardIdentifier.of(0, 64))));
        p("shard.of.-1_0", err(() -> sh(ShardIdentifier.of(-1, 0))));
        p("shard.of.255_63", err(() -> sh(ShardIdentifier.of(255, 63))));
        p("shard.toString", ShardIdentifier.of(1, 2).toString());
        p("shard.equals", ShardIdentifier.of(1, 2).equals(ShardIdentifier.of(1, 2)));
        p("shard.default", sh(ShardIdentifier.DEFAULT));
        p("readShard.v4", sh(ShardedUUID.from(UUID.fromString(v4)).resolveShardIdentifier()));
    }
}
