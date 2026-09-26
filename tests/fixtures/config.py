"""Java's `shardMemberConfig()` / `shardGroupConfig()` builders, wrapped in two short functions.

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
    builder = ShardMemberConfig.builder().member(number).primary_config(primary)
    if secondary is not None:
        builder.secondary_config(secondary)
    if name is not None:
        builder.name(name)
    for config_name, config in others.items():
        builder.with_config(config_name, config)
    return builder.build()


def group(number: int, name: str, *members: ShardMemberConfig) -> ShardGroupConfig:
    """``shardGroupConfig().group(n).name(name).withMember(...)``."""
    return ShardGroupConfig.builder().group(number).name(name).members(members).build()
