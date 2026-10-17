"""Parity scan area C, pure logic: the Python twin of ParityCPureProbeTest.java."""

from __future__ import annotations

import io
import logging
import tempfile
import time
import uuid
from pathlib import Path

from sqlalchemy import create_engine

from ekbatan.config import (
    DataSourceConfig,
    ShardGroupConfig,
    ShardingConfig,
    ShardMemberConfig,
)
from ekbatan.config_files import load_sharding_config
from ekbatan.persistence import DatabaseRegistry, TransactionManager
from ekbatan.sharding import (
    DEFAULT_SHARD,
    EmbeddedBitsShardingStrategy,
    ShardIdentifier,
    read_shard,
    sharded_uuid,
    uuid_instant,
)

MARKER = "s3cret-PROBE-marker"
DIR = Path(__file__).parent


def p(key: str, value: object) -> None:
    print(f"PROBE {key}: {value}", flush=True)


def sid(s: ShardIdentifier | None) -> str:
    return "none" if s is None else f"{s.group}:{s.member}"


def err(fn) -> str:
    try:
        fn()
        return "ok"
    except BaseException as e:  # noqa: BLE001
        return f"error: {e}"


def has_marker(s: str | None) -> str:
    return "yes" if s and MARKER in s else "no"


def jbool(b: bool) -> str:
    return "true" if b else "false"


# ------------------------------------------------------------------ ids


def ids() -> None:
    shards = [(0, 0), (1, 2), (255, 63), (128, 32), (7, 0), (0, 63), (255, 0)]
    lines = []
    for g, m in shards:
        u = sharded_uuid(ShardIdentifier(g, m))
        p(f"id.mint.{g}_{m}.version", u.version)
        # java.util.UUID.variant() returns 2 for the RFC 4122 variant
        p(f"id.mint.{g}_{m}.variant", 2 if u.variant == uuid.RFC_4122 else u.variant)
        p(f"id.mint.{g}_{m}.decoded_self", sid(read_shard(u)))
        lines.append(f"{g} {m} {u}")
    (DIR / "py-ids.txt").write_text("\n".join(lines) + "\n")
    java = DIR / "java-ids.txt"
    if java.exists():
        strat = EmbeddedBitsShardingStrategy()
        for line in java.read_text().splitlines():
            g, m, text = line.split(" ")
            p(f"id.cross.{g}_{m}.decoded_by_other", sid(strat.shard_for_id(uuid.UUID(text))))
    fixed = uuid.UUID("01890a5d-ac96-774b-bcce-b302099a8057")
    p("id.fixed_v7.shard", sid(EmbeddedBitsShardingStrategy().shard_for_id(fixed)))
    p("id.fixed_v7.instant_ms", int(uuid_instant(fixed).timestamp() * 1000))
    v4 = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
    p("id.v4.strategy_by_id", sid(EmbeddedBitsShardingStrategy().shard_for_id(v4)))
    p("id.v4.raw_decode", f"ok {sid(read_shard(v4))}")
    seen = {sharded_uuid(ShardIdentifier(3, 4)) for _ in range(10000)}
    p("id.unique_of_10000", len(seen))
    before = int(time.time() * 1000)
    t = int(uuid_instant(sharded_uuid(ShardIdentifier(1, 1))).timestamp() * 1000)
    p("id.instant_is_now", "yes" if abs(t - before) < 1000 else "no")


# ------------------------------------------------------------------ shard identifier


def shard_identifiers() -> None:
    p("sid.of_256_0", err(lambda: ShardIdentifier.of(256, 0)))
    p("sid.of_0_64", err(lambda: ShardIdentifier.of(0, 64)))
    p("sid.of_-1_0", err(lambda: ShardIdentifier.of(-1, 0)))
    p("sid.of_255_63", err(lambda: ShardIdentifier.of(255, 63)))
    p("sid.str_1_2", str(ShardIdentifier.of(1, 2)))
    p("sid.equal", jbool(ShardIdentifier.of(1, 2) == ShardIdentifier.of(1, 2)))
    p("sid.hash_equal", jbool(hash(ShardIdentifier.of(1, 2)) == hash(ShardIdentifier.of(1, 2))))
    p("sid.default", sid(DEFAULT_SHARD))


# ------------------------------------------------------------------ yaml helpers

TMP = Path(tempfile.mkdtemp())


def minimal_yaml(extra: str) -> str:
    return (
        extra
        + """defaultShard:
  group: 0
  member: 0
groups:
  - group: 0
    name: global
    members:
      - member: 0
        configs:
          primaryConfig:
            url: postgresql+psycopg://primary:5432/db
            username: app
"""
    )


def as_sharding(text: str) -> str:
    """Nest a layout under ekbatan.sharding, as Python's loader reads it."""
    body = "".join("    " + line + "\n" if line else "\n" for line in text.splitlines())
    return "ekbatan:\n  sharding:\n" + body


_n = [0]


def load(text: str) -> ShardingConfig:
    _n[0] += 1
    f = TMP / f"c{_n[0]}.yaml"
    f.write_text(as_sharding(text))
    return load_sharding_config(f, environ={})


def load_result(text: str) -> str:
    try:
        load(text)
        return "ok"
    except Exception:  # noqa: BLE001
        return "error"


def load_message(text: str) -> str:
    try:
        load(text)
        return "ok"
    except Exception as e:  # noqa: BLE001
        return str(e).splitlines()[0]


# ------------------------------------------------------------------ policy


def policy() -> None:
    spellings = {
        "fail": "fail",
        "FAIL": "FAIL",
        "Fail": "Fail",
        "useDefaultShard": "useDefaultShard",
        "use_default_shard": "use_default_shard",
        "USE-DEFAULT-SHARD": "USE-DEFAULT-SHARD",
        "use-default-shard": "use-default-shard",
        "UseDefaultShard": "UseDefaultShard",
        "padded_fail": "' fail '",
        "empty_string": "''",
        "null_value": "",
        "shrug": "shrug",
    }
    for key, written in spellings.items():
        try:
            r = load(minimal_yaml(f"unknownShardPolicy: {written}\n")).unknown_shard_policy
        except Exception:  # noqa: BLE001
            r = "error"
        p(f"policy.yaml.{key}", r)
    p("policy.yaml.omitted", load(minimal_yaml("")).unknown_shard_policy)
    msg = load_message(minimal_yaml("unknownShardPolicy: shrug\n"))
    p("policy.yaml.shrug.msg_names_both_values", "yes" if "fail" in msg else "no")
    member = ShardMemberConfig.builder().member(0).primary_config(
        DataSourceConfig(url="postgresql+psycopg://h/db")
    ).build()
    group = ShardGroupConfig.builder().group(0).name("g").with_member(member).build()
    try:
        r = (
            ShardingConfig.builder()
            .default_shard(DEFAULT_SHARD)
            .with_group(group)
            .unknown_shard_policy(None)  # type: ignore[arg-type]
            .build()
            .unknown_shard_policy
        )
    except Exception as e:  # noqa: BLE001
        r = f"error: {e}"
    p("policy.config_builder_null", r)
    p(
        "policy.config_builder_omitted",
        ShardingConfig.builder().default_shard(DEFAULT_SHARD).with_group(group).build().unknown_shard_policy,
    )


# ------------------------------------------------------------------ yaml

FULL = """defaultShard:
  group: 0
  member: 0
groups:
  - group: 0
    name: global
    members:
      - member: 0
        name: global-eu-1
        configs:
          primaryConfig:
            url: postgresql+psycopg://primary-eu-1:5432/db
            username: app
            poolSize: 20
            dataSourceProperties:
              ApplicationName: orders-service
              ha.enableJMX: true
              connectTimeout: 10
          secondaryConfig:
            url: postgresql+psycopg://replica-eu-1:5432/db
            username: app
          lockConfig:
            url: postgresql+psycopg://locks-eu-1:5432/db
            username: app
  - group: 1
    name: mexico
    members:
      - member: 3
        configs:
          primaryConfig:
            url: postgresql+psycopg://primary-mx-1:5432/db
"""


def yaml() -> None:
    c = load(FULL)
    p("yaml.default_shard", sid(c.default_shard))
    p("yaml.groups", len(c.groups))
    p("yaml.group1.number", c.groups[1].group)
    p("yaml.group0.name", c.groups[0].name)
    p("yaml.member0.name", c.groups[0].members[0].name or "none")
    p("yaml.member_mx.name", c.groups[1].members[0].name or "none")
    p("yaml.member_mx.number", c.groups[1].members[0].member)
    primary = c.groups[0].members[0].primary_config()
    p("yaml.primary.max_pool", primary.pool_size + primary.max_overflow)
    p("yaml.secondary.present", jbool(c.groups[0].members[0].secondary_config() is not None))
    sec = c.groups[0].members[0].secondary_config()
    p("yaml.secondary.max_pool_default", sec.pool_size + sec.max_overflow)
    p("yaml.lock_config.present", jbool(c.groups[0].members[0].config_for("lockConfig") is not None))
    p("yaml.mx.secondary.present", jbool(c.groups[1].members[0].secondary_config() is not None))
    p("yaml.mx.username", c.groups[1].members[0].primary_config().username or "none")
    v = primary.data_source_properties["connectTimeout"]
    p("yaml.dsp.connectTimeout", f"{v} ({'text' if isinstance(v, str) else 'number'})")
    b = primary.data_source_properties["ha.enableJMX"]
    shown_b = b if isinstance(b, str) else jbool(b)
    p("yaml.dsp.ha.enableJMX", f"{shown_b} ({'text' if isinstance(b, str) else 'bool'})")
    p("yaml.dsp.names_kept", "[" + ", ".join(primary.data_source_properties) + "]")

    no_group = FULL.replace("  - group: 1\n    name: mexico", "  - name: mexico")
    p("yaml.group_number_missing", load_result(no_group))
    try:
        p("yaml.group_number_missing.value", load(no_group).groups[1].group)
    except Exception:  # noqa: BLE001
        p("yaml.group_number_missing.value", "error")
    p(
        "yaml.member_number_missing",
        load_result(FULL.replace("      - member: 3\n        configs:", "      - configs:")),
    )
    typo = FULL.replace("    members:\n      - member: 3", "    memmbers:\n      - member: 3")
    p("yaml.unknown_key", load_result(typo))
    p("yaml.unknown_key.names_key", "yes" if "memmbers" in load_message(typo) else "no")
    no_primary = """defaultShard: {group: 0, member: 0}
groups:
  - group: 0
    name: g
    members:
      - member: 0
        configs:
          secondaryConfig:
            url: postgresql+psycopg://h/db
"""
    p(
        "yaml.no_primary",
        "error: configs.primaryConfig is required"
        if "configs.primaryConfig is required" in load_message(no_primary)
        else "other",
    )
    p(
        "yaml.no_groups",
        "error: at least one group is required"
        if "at least one group is required"
        in load_message("defaultShard: {group: 0, member: 0}\ngroups: []\n")
        else "other: " + load_message("defaultShard: {group: 0, member: 0}\ngroups: []\n"),
    )
    p(
        "yaml.no_default_shard",
        load_result(minimal_yaml("").replace("defaultShard:\n  group: 0\n  member: 0\n", "")),
    )
    p("yaml.group_no_name", load_result(minimal_yaml("").replace("    name: global\n", "")))
    p(
        "yaml.default_names_nothing",
        load_result(
            minimal_yaml("").replace(
                "defaultShard:\n  group: 0\n  member: 0", "defaultShard:\n  group: 9\n  member: 9"
            )
        ),
    )
    p(
        "yaml.duplicate_shard",
        load_result(
            minimal_yaml("")
            + """  - group: 0
    name: again
    members:
      - member: 0
        configs:
          primaryConfig:
            url: postgresql+psycopg://other:5432/db
"""
        ),
    )
    p(
        "yaml.mixed_dialects",
        load_result(
            minimal_yaml("")
            + """  - group: 1
    name: my
    members:
      - member: 0
        configs:
          primaryConfig:
            url: mysql+pymysql://other:3306/db
"""
        ),
    )
    p("yaml.kebab_keys.core_binding", load_result(minimal_yaml("").replace("defaultShard", "default-shard")))
    p("yaml.kebab_keys.di_normalizer", "n/a (the loader itself accepts kebab-case)")
    p(
        "yaml.bad_pool_value.marker_in_error",
        has_marker(load_message(minimal_yaml("").replace("username: app", f"username: app\n            poolSize: {MARKER}"))),
    )
    p(
        "yaml.password_as_map.marker_in_error",
        has_marker(load_message(minimal_yaml("").replace("username: app", f"username: app\n            password: {{x: {MARKER}}}"))),
    )
    p(
        "yaml.password_as_list.marker_in_error",
        has_marker(load_message(minimal_yaml("").replace("username: app", f"username: app\n            password: [{MARKER}]"))),
    )
    p(
        "yaml.password_ok.result",
        load_result(minimal_yaml("").replace("username: app", f"username: app\n            password: {MARKER}")),
    )


# ------------------------------------------------------------------ data source config

PG = "postgresql+psycopg://h:5432/db"


def pg(**kw) -> DataSourceConfig:
    return DataSourceConfig(url=PG, **kw)


def data_source_configs() -> None:
    d = pg()
    p("dsc.default.max_connections", d.pool_size + d.max_overflow)
    p("dsc.default.username", d.username or "none")
    p("dsc.default.password", "set" if d.password is not None else "none")
    p("dsc.pool_0", err(lambda: pg(pool_size=0)))
    p("dsc.pool_-1", err(lambda: pg(pool_size=-1)))
    p("dsc.url_blank", err(lambda: DataSourceConfig(url="  ")))
    p("dsc.url_missing", err(lambda: DataSourceConfig.builder().build()))
    p("dsc.username_blank", err(lambda: pg(username="  ")))
    p("dsc.username_empty", err(lambda: pg(username="")))
    p("dsc.password_empty", err(lambda: pg(username="u", password="")))
    p("dsc.password_only", err(lambda: pg(password="x")))
    p("dsc.dialect.postgres", pg().dialect.upper())
    p("dsc.dialect.mysql", DataSourceConfig(url="mysql+pymysql://h:3306/db").dialect.upper())
    p("dsc.dialect.mariadb", DataSourceConfig(url="mariadb+pymysql://h:3306/db").dialect.upper())
    p("dsc.dialect.oracle", err(lambda: DataSourceConfig(url="oracle+oracledb://h:1521/x")))
    p("dsc.dialect.sqlite", err(lambda: DataSourceConfig(url="sqlite:///x.db")))
    p("dsc.dialect.garbage", err(lambda: DataSourceConfig(url="nonsense")))
    p("dsc.dsp.user", err(lambda: pg(data_source_properties={"user": "x"})))
    p("dsc.dsp.PASSWORD", err(lambda: pg(data_source_properties={"PASSWORD": "x"})))
    p("dsc.dsp.passwd", err(lambda: pg(data_source_properties={"passwd": "x"})))
    p("dsc.dsp.null_value", err(lambda: pg(data_source_properties={"ssl": None})))
    p("dsc.dsp.nested", err(lambda: pg(data_source_properties={"ssl": {"ca": "x"}})))
    p("dsc.dsp.list", err(lambda: pg(data_source_properties={"ssl": ["x"]})))
    p("dsc.dsp.blank_name", err(lambda: pg(data_source_properties={" ": "x"})))
    n = pg(data_source_properties={"connectTimeout": 10}).data_source_properties["connectTimeout"]
    p("dsc.dsp.number_kept_as", "text" if isinstance(n, str) else "number")
    q = f"{PG}?password={MARKER}"
    p("dsc.url_query_password", "error" if err(lambda: DataSourceConfig(url=q)).startswith("error") else "ok")
    p("dsc.url_query_password.marker_in_error", has_marker(err(lambda: DataSourceConfig(url=q))))
    ui = f"postgresql+psycopg://u:{MARKER}@h/db"
    p("dsc.url_userinfo_password", "error" if err(lambda: DataSourceConfig(url=ui)).startswith("error") else "ok")
    p("dsc.url_userinfo_password.marker_in_error", has_marker(err(lambda: DataSourceConfig(url=ui))))
    p(
        "dsc.url_query_user",
        "error" if err(lambda: DataSourceConfig(url=f"{PG}?user=bob")).startswith("error") else "ok",
    )
    sq = f"{PG}?sslpassword={MARKER}"
    p("dsc.url_query_sslpassword", "error" if err(lambda: DataSourceConfig(url=sq)).startswith("error") else "ok")
    p("dsc.url_query_sslpassword.marker_in_error", has_marker(err(lambda: DataSourceConfig(url=sq))))
    p(
        "dsc.dsp.password.marker_in_error",
        has_marker(err(lambda: pg(data_source_properties={"password": MARKER}))),
    )
    secret = pg(username="u", password=MARKER, data_source_properties={"sslpassword": MARKER})
    p("dsc.printed.marker", has_marker(repr(secret) + str(secret)))
    member = ShardMemberConfig(member=0, configs={"primaryConfig": secret})
    group = ShardGroupConfig(group=0, name="g", members=(member,))
    cfg = ShardingConfig(default_shard=DEFAULT_SHARD, groups=(group,))
    p("dsc.member_printed.marker", has_marker(repr(member)))
    p("dsc.layout_printed.marker", has_marker(repr(cfg)))


# ------------------------------------------------------------------ registry


def tm(g: int, m: int) -> TransactionManager:
    return TransactionManager(ShardIdentifier(g, m), create_engine("postgresql+psycopg://u@127.0.0.1:1/x"))


def capture(fn) -> str:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s - %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    old = root.level
    root.setLevel(logging.DEBUG)
    try:
        fn()
    finally:
        root.removeHandler(handler)
        root.setLevel(old)
    return buf.getvalue()


def sid_or_err(fn) -> str:
    try:
        return sid(fn())
    except BaseException as e:  # noqa: BLE001
        return f"error: {e}"


def registry() -> None:
    p("reg.empty", err(lambda: DatabaseRegistry.builder().build()))
    a, b = tm(0, 0), tm(1, 0)
    p("reg.two_no_default", err(lambda: DatabaseRegistry.builder().with_database(a).with_database(b).build()))
    p("reg.one_no_default.default", sid(DatabaseRegistry.builder().with_database(a).build().default_shard))
    p(
        "reg.null_policy",
        DatabaseRegistry.builder().with_database(a).with_unknown_shard_policy(None).build().unknown_shard_policy,
    )
    reg = DatabaseRegistry.builder().with_default_database(a).with_database(b).build()
    p("reg.default", sid(reg.default_shard))
    p("reg.manager_unknown", err(lambda: reg.manager(ShardIdentifier(9, 9))))
    p("reg.manager_known", sid(reg.manager(ShardIdentifier(1, 0)).shard))
    p("reg.effective.registered", sid(reg.effective_shard(ShardIdentifier(1, 0))))
    holder = []
    log = capture(lambda: holder.append(sid(reg.effective_shard(ShardIdentifier(5, 5)))))
    p("reg.effective.unknown_default_policy", holder[0])
    lines = [line for line in log.splitlines() if "is not registered" in line]
    p("reg.effective.unknown.warn_lines", len(lines))
    p("reg.effective.unknown.warn_text", lines[0] if lines else "none")
    log2 = capture(lambda: (reg.effective_shard(ShardIdentifier(5, 5)), reg.effective_shard(ShardIdentifier(5, 5))))
    p("reg.effective.unknown.warn_every_call", sum("is not registered" in x for x in log2.splitlines()))
    log3 = capture(lambda: reg.effective_shard(None))
    p("reg.effective.null_default_policy", sid_or_err(lambda: reg.effective_shard(None)))
    p("reg.effective.null.warn_lines", sum("is not registered" in x for x in log3.splitlines()))
    failing = (
        DatabaseRegistry.builder()
        .with_default_database(a)
        .with_database(b)
        .with_unknown_shard_policy("fail")
        .build()
    )
    p("reg.effective.unknown_fail_policy", err(lambda: failing.effective_shard(ShardIdentifier(5, 5))))
    p("reg.effective.null_fail_policy", sid_or_err(lambda: failing.effective_shard(None)))
    p("reg.effective.registered_fail_policy", sid(failing.effective_shard(ShardIdentifier(1, 0))))
    p("reg.shards_order", "[" + ", ".join(sid(s) for s in reg.shards) + "]")

    pg_cfg = DataSourceConfig(url="postgresql+psycopg://127.0.0.1:1/x", username="u")
    replica_cfg = DataSourceConfig(url="postgresql+psycopg://127.0.0.1:2/x", username="u")
    no_replica = ShardMemberConfig(member=0, configs={"primaryConfig": pg_cfg})
    with_replica = ShardMemberConfig(member=1, configs={"primaryConfig": pg_cfg, "secondaryConfig": replica_cfg})
    layout = ShardingConfig(
        default_shard=DEFAULT_SHARD,
        groups=(ShardGroupConfig(group=0, name="g", members=(no_replica, with_replica)),),
    )
    from_cfg = DatabaseRegistry.from_config(layout)
    t0 = from_cfg.manager(ShardIdentifier(0, 0))
    t1 = from_cfg.manager(ShardIdentifier(0, 1))
    p("reg.from_config.no_replica_shares_pool", jbool(t0.replica is t0.primary))
    p("reg.from_config.replica_own_pool", jbool(t1.replica is not t1.primary))
    p("reg.from_config.policy_default", from_cfg.unknown_shard_policy)

    def bad_default():
        return DatabaseRegistry.from_config(
            ShardingConfig(
                default_shard=ShardIdentifier(9, 9),
                groups=(ShardGroupConfig(group=0, name="g", members=(no_replica,)),),
            )
        )

    p("reg.from_config.default_names_nothing", err(bad_default))

    def dup():
        return DatabaseRegistry.from_config(
            ShardingConfig(
                default_shard=DEFAULT_SHARD,
                groups=(
                    ShardGroupConfig(group=0, name="g", members=(no_replica,)),
                    ShardGroupConfig(group=0, name="again", members=(no_replica,)),
                ),
            )
        )

    p("reg.from_config.duplicate_shard", err(lambda: dup().close()))
    try:
        r = dup()
        p("reg.from_config.duplicate_shard.registered", len(r.shards))
    except Exception:  # noqa: BLE001
        p("reg.from_config.duplicate_shard.registered", "error")

    def close_twice():
        from_cfg.close()
        from_cfg.close()

    p("reg.close_twice", err(close_twice))
    reg.close()
    failing.close()


if __name__ == "__main__":
    ids()
    shard_identifiers()
    policy()
    yaml()
    data_source_configs()
    registry()
