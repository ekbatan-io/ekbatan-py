package io.ekbatan.core.test.repository;

import io.ekbatan.core.repository.Repository;
import io.ekbatan.core.repository.RepositoryRegistry;
import io.ekbatan.core.shard.NoShardingStrategy;
import io.ekbatan.core.shard.ShardingStrategy;
import io.ekbatan.core.test.model.Dummy;
import java.util.Collection;
import java.util.List;
import org.junit.jupiter.api.Test;

/** Parity probe (area A, RepositoryRegistry). Temporary; no database. */
public class ProbeRegistryTest {

    static final class Fake implements Repository<Dummy> {
        final String name;

        Fake(String name) {
            this.name = name;
        }

        public ShardingStrategy<?> shardingStrategy() {
            return new NoShardingStrategy<>();
        }

        public Dummy add(Dummy m) {
            return m;
        }

        public void addNoResult(Dummy m) {}

        public List<Dummy> addAll(Collection<Dummy> m) {
            return List.copyOf(m);
        }

        public void addAllNoResult(Collection<Dummy> m) {}

        public Dummy update(Dummy m) {
            return m;
        }

        public void updateNoResult(Dummy m) {}

        public List<Dummy> updateAll(Collection<Dummy> m) {
            return List.copyOf(m);
        }

        public void updateAllNoResult(Collection<Dummy> m) {}

        public List<Dummy> findAll() {
            return List.of();
        }

        public String toString() {
            return name;
        }
    }

    static void p(String k, Object v) {
        System.out.println("PROBE registry." + k + ": " + v);
    }

    @Test
    void probe() {
        var one = new Fake("first");
        var two = new Fake("second");
        try {
            var reg = RepositoryRegistry.Builder.repositoryRegistry()
                    .withModelRepository(Dummy.class, one)
                    .withModelRepository(Dummy.class, two)
                    .build();
            p("two_repos_one_model", "no error -> " + reg.repository(Dummy.class));
        } catch (RuntimeException e) {
            p("two_repos_one_model.err", e.getClass().getSimpleName());
        }
        try {
            var reg = RepositoryRegistry.Builder.repositoryRegistry()
                    .withModelRepository(Dummy.class, one)
                    .withModelRepository(Dummy.class, one)
                    .build();
            p("same_repo_twice", "no error -> " + reg.repository(Dummy.class));
        } catch (RuntimeException e) {
            p("same_repo_twice.err", e.getClass().getSimpleName());
        }
        var empty = RepositoryRegistry.Builder.repositoryRegistry().build();
        try {
            p("lookup_unregistered", "no error -> " + empty.repository(Dummy.class));
        } catch (RuntimeException e) {
            p("lookup_unregistered.err", e.getClass().getSimpleName());
        }
    }
}
