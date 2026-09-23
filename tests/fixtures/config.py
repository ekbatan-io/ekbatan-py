"""Java's `shardMemberConfig()` / `shardGroupConfig()` builders, as two short functions.

The layout is Java's three levels - groups, members, a named map of datasources on each member -
and a test that spells all three out in full for every case says the same thing four times. These
keep the shape visible and the noise out.
"""

from __future__ import annotations

from ekbatan.config import DataSourceConfig, ShardGroupConfig, ShardMemberConfig


def member(
    number: int,
    primary: DataSourceConfig,
    secondary: DataSourceConfig | None = None,
    *,
    name: str | None = None,
    **others: DataSourceConfig,
) -> ShardMemberConfig:
    """``shardMemberConfig().member(n).primaryConfig(p).secondaryConfig(s).withConfig(...)``."""
    configs = {ShardMemberConfig.PRIMARY_CONFIG: primary, **others}
    if secondary is not None:
        configs[ShardMemberConfig.SECONDARY_CONFIG] = secondary
    return ShardMemberConfig(member=number, name=name, configs=configs)


def group(number: int, name: str, *members: ShardMemberConfig) -> ShardGroupConfig:
    """``shardGroupConfig().group(n).name(name).withMember(...)``."""
    return ShardGroupConfig(group=number, name=name, members=members)
