"""A container's url, as a config wants it: the login taken out of the url and put in fields.

Test containers hand out urls with the login in them (`postgresql+psycopg://test:test@...`),
and a `DataSourceConfig` refuses such a url, as Java's does.
"""

from __future__ import annotations

from typing import Any

from ekbatan.config import DataSourceConfig, ShardingConfig, split_login


def data_source(url: str, **settings: Any) -> DataSourceConfig:
    """A `DataSourceConfig` for a url that may carry its login."""
    plain, username, password = split_login(url)
    return DataSourceConfig(url=plain, username=username, password=password, **settings)


def single(url: str, **settings: Any) -> ShardingConfig:
    """`ShardingConfig.single` for a url that may carry its login."""
    plain, username, password = split_login(url)
    return ShardingConfig.single(plain, username=username, password=password, **settings)
