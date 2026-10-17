package io.ekbatan.test.keyed_lock_provider;

import static io.ekbatan.keyedlock.redis.RedisKeyedLockProvider.Builder.redisKeyedLockProvider;

import java.time.Duration;
import org.junit.jupiter.api.Test;
import org.redisson.Redisson;
import org.redisson.config.Config;

/**
 * Temporary: drives Ekbatan's Java Redis lock through three scenarios while Redis MONITOR
 * records every command, so ekbatan-py can pin its copy of Redisson's behaviour to the real one.
 */
class RedissonRecordingTest {

    static void mark(org.redisson.api.RedissonClient r, String name) {
        // A command that shows up in MONITOR, to split the recording into scenarios.
        r.getBucket("rec:marker:" + name).isExists();
    }

    @Test
    void record() throws Exception {
        var cfg = new Config();
        cfg.useSingleServer().setAddress("redis://localhost:56479");
        var redisson = Redisson.create(cfg);
        try {
            var provider = redisKeyedLockProvider().redissonClient(redisson).build();
            var hold = Duration.ofSeconds(30);

            mark(redisson, "acquire-release");
            try (var lease = provider.acquire("rec:one", hold)) {
                mark(redisson, "while-held");
            }

            mark(redisson, "re-entry");
            try (var outer = provider.acquire("rec:two", hold)) {
                try (var inner = provider.acquire("rec:two", hold)) {
                    mark(redisson, "inner-held");
                }
                mark(redisson, "inner-closed");
            }

            mark(redisson, "try-acquire");
            try (var lease = provider.tryAcquire("rec:three", Duration.ZERO, hold).orElseThrow()) {
                mark(redisson, "try-held");
            }
            mark(redisson, "end");
        } finally {
            redisson.shutdown();
        }
    }
}
