"""PARITY-PROBE F4: several shards - order, one failing, the log lines and the MDC keys.
Twin of ParityFShardsProbeTest.java."""

import logging

from common import SQL, Captured, cfg, execute, fresh, history, msg, probe

from ekbatan.config import ShardGroupConfig, ShardingConfig, ShardMemberConfig
from ekbatan.migrations import LOG, YoyoMigrator
from ekbatan.sharding import ShardIdentifier

NAMES = ["global", "mexico", "brazil"]


def layout(groups: list[int]) -> ShardingConfig:
    return ShardingConfig(
        default_shard=ShardIdentifier(0, 0),
        groups=[
            ShardGroupConfig(
                group=g,
                name=NAMES[g],
                members=[ShardMemberConfig(member=0, configs={"primaryConfig": cfg("pg", f"f4_s{g}")})],
            )
            for g in groups
        ],
    )


def report(k: str, log: Captured) -> None:
    probe(
        k + ".ekbatan_lines",
        [line.replace("io.ekbatan.flyway.FlywayMigrator", "FlywayMigrator") for line in log.lines],
    )
    # Python has no Flyway: every line is the migrator's own, so there are no "Flyway lines".
    probe(k + ".flyway_lines_present", "false")
    probe(k + ".flyway_lines_mdc", "[]")


def names(targets) -> list[str]:
    return [f"{t.shard.group}:{t.shard.member} {t.name}" for t in targets]


for g in range(3):
    fresh("pg", f"f4_s{g}")
execute("pg", "f4_s1", "CREATE TABLE existing (id INT)")

probe("targets.config_order", names(YoyoMigrator.targets(layout([0, 1, 2]))))
probe("targets.groups_given_2_0_1", names(YoyoMigrator.targets(layout([2, 0, 1]))))

with Captured() as log:
    try:
        rs = YoyoMigrator.migrate(layout([0, 1, 2]), str(SQL / "quick"))
        probe("fail.outcome", f"OK {len(rs)}")
    except Exception as e:
        probe("fail.outcome", "FAIL " + msg(e))
    report("fail", log)


class After(logging.Handler):
    seen = "none"

    def emit(self, record: logging.LogRecord) -> None:
        After.seen = f"{getattr(record, 'ekbatanShard', None)} {getattr(record, 'ekbatanTarget', None)}"


after = After()
LOG.addHandler(after)
LOG.warning("after")
LOG.removeHandler(after)
probe("fail.mdc_after", After.seen.replace("None", "null"))
for g in range(3):
    probe(f"fail.s{g}.history", history("pg", f"f4_s{g}"))

execute("pg", "f4_s1", "DROP TABLE existing")
with Captured() as log:
    rs = YoyoMigrator.migrate(layout([0, 1, 2]), str(SQL / "quick"))
    probe(
        "again.results",
        [f"{r.target.name} applied={r.migrate_result.migrations_executed}" for r in rs],
    )
    report("again", log)

fresh("pg", "f4_single")
with Captured() as log:
    YoyoMigrator.migrate_data_source(cfg("pg", "f4_single"), str(SQL / "quick"))
    report("single", log)
t = YoyoMigrator.target(cfg("pg", "f4_single"), shard=ShardIdentifier(3, 1))
probe("target.shard_no_name", f"{t.shard.group}:{t.shard.member} {t.name}")
d = YoyoMigrator.target(cfg("pg", "f4_single"))
probe("target.default", f"{d.shard.group}:{d.shard.member} {d.name}")

try:
    YoyoMigrator.builder().migrate()
    probe("refuse.no_targets", "OK")
except Exception as e:
    probe("refuse.no_targets", "FAIL " + msg(e))
try:
    YoyoMigrator.migrate_data_source(cfg("pg", "f4_single"), "  ")
    probe("refuse.blank_location", "OK")
except Exception as e:
    probe("refuse.blank_location", "FAIL " + msg(e))
try:
    YoyoMigrator.target(cfg("pg", "f4_single"), shard=ShardIdentifier(0, 0), name=" ")
    probe("refuse.blank_target_name", "OK")
except Exception as e:
    probe("refuse.blank_target_name", "FAIL " + msg(e))
