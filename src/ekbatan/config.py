"""Configuration: which databases exist, and which one is the default.

Everything here is validated where it is written. A pool size that only fails on the first
request, or a default shard that only fails after every pool has been opened, has moved the
error away from the mistake - and the person reading the traceback has no way back to the line
that caused it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

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
        """Which database this URL names, or a refusal that says which kind of mistake it is.

        The two are separated as Java separates them: a URL nothing can be read out of is a
        typo, and a URL naming Oracle is a decision about what this framework supports. They
        want different fixes, and one message covering both sends the reader looking in the
        wrong place.
        """
        head, separator, _ = self.url.partition("://")
        if not separator:
            raise ValidationError(
                f"cannot tell which database {self.url!r} names; a url looks like "
                f"'postgresql+psycopg://user:pass@host/db'"
            )
        scheme = head.split("+", 1)[0].strip().lower()
        if scheme.startswith("jdbc:"):
            # The likeliest mistake anyone moving between the two ports makes, and left alone
            # it reports as "unsupported database 'jdbc:postgresql'", which sends the reader to
            # look for a driver rather than at the string.
            raise ValidationError(
                f"{self.url!r} is a JDBC url, which is the Java port's form. Here a url looks "
                f"like 'postgresql+psycopg://user:pass@host/db' - the credentials live in the "
                f"url rather than in separate username and password settings."
            )
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
    #: What to do with a shard this deployment has not configured.
    #:
    #: ``"default"`` routes it to :attr:`default_shard`, which is what both ports have always
    #: done: a deployment holding a subset of the layout can read ids minted by one holding
    #: more, instead of refusing them. It is also how a mistyped shard identifier writes to the
    #: wrong database without saying so - which is why the fallback now logs a warning either
    #: way, and why this setting exists.
    #:
    #: ``"refuse"`` raises instead. For a deployment that holds the whole layout, where an
    #: unregistered shard can only be a bug.
    unknown_shard: Literal["default", "refuse"] = "default"

    def __post_init__(self) -> None:
        if not self.shards:
            raise ValidationError("at least one shard is required")

        if self.unknown_shard not in ("default", "refuse"):
            raise ValidationError(
                f"unknown_shard must be 'default' or 'refuse', not {self.unknown_shard!r}"
            )

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
