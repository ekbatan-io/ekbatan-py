"""Configuration: which databases exist, and which one is the default.

Everything here is validated where it is written. A pool size that only fails on the first
request, or a default shard that only fails after every pool has been opened, has moved the
error away from the mistake - and the person reading the traceback has no way back to the line
that caused it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ValidationError
from .sharding import ShardIdentifier

__all__ = ["DataSourceConfig", "ShardConfig", "ShardingConfig"]

_DIALECTS = {"postgresql": "postgres", "mysql": "mysql", "mariadb": "mariadb", "sqlite": "sqlite"}


@dataclass(frozen=True, kw_only=True)
class DataSourceConfig:
    """One connection pool.

    ``pool_size`` and ``max_overflow`` together are what Hikari calls a maximum pool size; the
    split is SQLAlchemy's and is left visible rather than papered over, because an operator
    tuning a pool needs to see the knobs their own tooling reports on.
    """

    url: str
    pool_size: int = 10
    max_overflow: int = 0
    pool_timeout: float = 30.0
    pool_recycle: int = 1800
    pool_pre_ping: bool = True
    connect_args: dict[str, object] = field(default_factory=dict)
    echo: bool = False

    def __post_init__(self) -> None:
        if not self.url or not self.url.strip():
            raise ValidationError("url is required")
        if self.pool_size < 1:
            raise ValidationError(f"pool_size must be at least 1, got {self.pool_size}")
        if self.max_overflow < 0:
            raise ValidationError(f"max_overflow cannot be negative, got {self.max_overflow}")
        if self.pool_timeout <= 0:
            raise ValidationError(f"pool_timeout must be positive, got {self.pool_timeout}")
        # Unlike some pool implementations this refuses rather than logging and carrying on.
        # A knob that silently disables itself is a knob an operator believes is set.
        self.dialect  # noqa: B018 - raises here if the URL names something unsupported

    @property
    def dialect(self) -> str:
        scheme = self.url.split(":", 1)[0].split("+", 1)[0].strip().lower()
        try:
            return _DIALECTS[scheme]
        except KeyError:
            raise ValidationError(
                f"unsupported database {scheme!r}; this framework speaks "
                f"{', '.join(sorted(set(_DIALECTS.values())))}"
            ) from None


@dataclass(frozen=True, kw_only=True)
class ShardConfig:
    """One member of the layout: a writer, and optionally a reader beside it."""

    shard: ShardIdentifier
    primary: DataSourceConfig
    replica: DataSourceConfig | None = None
    name: str | None = None

    @property
    def label(self) -> str:
        return self.name or str(self.shard)


@dataclass(frozen=True, kw_only=True)
class ShardingConfig:
    """The whole layout, and which shard work goes to when nothing says otherwise."""

    shards: tuple[ShardConfig, ...]
    default_shard: ShardIdentifier

    def __post_init__(self) -> None:
        if not self.shards:
            raise ValidationError("at least one shard is required")

        seen: set[ShardIdentifier] = set()
        for shard in self.shards:
            if shard.shard in seen:
                raise ValidationError(f"shard {shard.shard} is configured twice")
            seen.add(shard.shard)

        # Checked before a single engine is built. Run afterwards, the realistic mistake here -
        # a mistyped default - opens every pool and then throws, and nothing holds a reference
        # to them afterwards. This check reads configuration only, so running it first is free.
        if self.default_shard not in seen:
            raise ValidationError(
                f"default_shard {self.default_shard} names no configured database; "
                f"configured: {sorted(str(s) for s in seen)}"
            )

        dialects = {s.primary.dialect for s in self.shards}
        if len(dialects) > 1:
            raise ValidationError(
                f"every shard must speak the same dialect, found {sorted(dialects)}"
            )

    @property
    def dialect(self) -> str:
        return self.shards[0].primary.dialect

    @classmethod
    def single(cls, url: str, **pool: object) -> ShardingConfig:
        """The common case: one database, no sharding to think about."""
        shard = ShardIdentifier(0, 0)
        return cls(
            shards=(ShardConfig(shard=shard, primary=DataSourceConfig(url=url, **pool)),),  # type: ignore[arg-type]
            default_shard=shard,
        )
