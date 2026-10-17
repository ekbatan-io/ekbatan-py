"""Configuration: which databases exist, and which one is the default.

Everything here is validated where it is written. A pool size that only fails on the first
request, or a default shard that only fails after every pool has been opened, has moved the
error away from the mistake - and the person reading the traceback has no way back to the line
that caused it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self

from ._credentials import require_none_in
from ._redact import HIDDEN, redact_url
from .errors import ValidationError
from .sharding import ShardIdentifier

if TYPE_CHECKING:
    from sqlalchemy.engine import URL

__all__ = [
    "DataSourceConfig",
    "DataSourceConfigBuilder",
    "ShardGroupConfig",
    "ShardGroupConfigBuilder",
    "ShardMemberConfig",
    "ShardMemberConfigBuilder",
    "ShardingConfig",
    "ShardingConfigBuilder",
    "split_login",
]

_DIALECTS = {"postgresql": "postgres", "mysql": "mysql", "mariadb": "mariadb", "sqlite": "sqlite"}


@dataclass(frozen=True, kw_only=True, repr=False)
class DataSourceConfig:
    """One connection pool.

    ``pool_size`` and ``max_overflow`` together are what Hikari calls a maximum pool size; the
    split is SQLAlchemy's and is left visible rather than papered over, because an operator
    tuning a pool needs to see the knobs their own tooling reports on.

    **The login lives in** ``username`` **and** ``password``, **never in the url** - as in Java.
    A url is logged and repeated, so one carrying a user name or a secret is refused, and the
    message says where each goes instead. Both fields are optional: leave ``username`` out only
    when the driver supplies the user, ``password`` for a login that has none. A blank username is
    refused - it is far more often an unset variable than a choice - while the empty string stays
    a valid password.

    ``data_source_properties`` are extra settings handed to the driver exactly as given - a number
    stays a number, a group stays a group - such as a client key's ``sslpassword`` or PyMySQL's
    ``ssl`` group. ``user``, ``password`` and PyMySQL's ``passwd`` are refused there, in any case:
    they would silently win over ``username`` and ``password``.

    **It never prints its password**, nor any ``data_source_properties`` value: every one of those
    is treated as a secret. The same goes for the printed form of everything that holds one, a
    layout or a migration result.
    """

    url: str
    username: str | None = None
    password: str | None = None
    pool_size: int = 10
    max_overflow: int = 0
    pool_timeout: float = 30.0
    pool_recycle: int = 1800
    pool_pre_ping: bool = True
    data_source_properties: Mapping[str, object] = field(default_factory=dict)
    echo: bool = False

    def __post_init__(self) -> None:
        if not self.url or not self.url.strip():
            raise ValidationError("url is required")
        if self.username is not None and not self.username.strip():
            raise ValidationError(
                "username cannot be empty; leave it out when the driver supplies the user"
            )
        object.__setattr__(
            self,
            "data_source_properties",
            MappingProxyType(_checked_properties(self.data_source_properties)),
        )
        if self.pool_size < 1:
            raise ValidationError(f"pool_size must be at least 1, got {self.pool_size}")
        if self.max_overflow < 0:
            raise ValidationError(f"max_overflow cannot be negative, got {self.max_overflow}")
        if self.pool_timeout <= 0:
            raise ValidationError(f"pool_timeout must be positive, got {self.pool_timeout}")
        # Unlike some pool implementations this refuses rather than logging and carrying on.
        # A knob that silently disables itself is a knob an operator believes is set.
        dialect = self.dialect  # raises here if the URL names something unsupported
        require_none_in(self.url)
        if dialect == "sqlite" and (self.username is not None or self.password is not None):
            raise ValidationError(
                "sqlite has no login: leave username and password out of a sqlite url's config"
            )

    def __repr__(self) -> str:
        shown = []
        for each in fields(self):
            value: Any = getattr(self, each.name)
            if each.name == "url":
                value = redact_url(value)
            elif each.name == "password" and value is not None:
                value = HIDDEN
            elif each.name == "data_source_properties":
                value = dict.fromkeys(value, HIDDEN)
            shown.append(f"{each.name}={value!r}")
        return f"DataSourceConfig({', '.join(shown)})"

    def sqlalchemy_url(self) -> URL:
        """The url with ``username`` and ``password`` set on it: what the engine and the migrator
        connect with. A SQLAlchemy ``URL`` object, which prints its password as ``***``.

        Setting them on the url, rather than handing the driver ``user`` and ``password``, lets
        each SQLAlchemy dialect pass them under the name its driver reads.
        """
        from sqlalchemy.engine import make_url

        url = make_url(self.url)
        if self.username is not None:
            url = url.set(username=self.username)
        if self.password is not None:
            url = url.set(password=self.password)
        return url

    @classmethod
    def builder(cls) -> DataSourceConfigBuilder:
        return DataSourceConfigBuilder()

    @property
    def dialect(self) -> str:
        """Which database this URL names, or a refusal that says which kind of mistake it is.

        The two are separated as Java separates them: a URL nothing can be read out of is a
        typo, and a URL naming Oracle is a decision about what this framework supports. They
        want different fixes, and one message covering both sends the reader looking in the
        wrong place.
        """
        # Neither refusal repeats the url: one that cannot be read may hold its password
        # anywhere, and the scheme in front of `://` is all a reader needs to find the mistake.
        head, separator, _ = self.url.partition("://")
        if not separator:
            raise ValidationError(
                "cannot tell which database the url names: it has no '://'. A url looks like "
                "'postgresql+psycopg://host:5432/db'"
            )
        scheme = head.split("+", 1)[0].strip().lower()
        if scheme.startswith("jdbc:"):
            # The likeliest mistake anyone moving between the two ports makes, and left alone
            # it reports as "unsupported database 'jdbc:postgresql'", which sends the reader to
            # look for a driver rather than at the string.
            shown = f" ({scheme}://...)" if re.fullmatch(r"jdbc:[a-z0-9]+", scheme) else ""
            raise ValidationError(
                f"the url is a JDBC url{shown}, which is the Java port's form. Here a url looks "
                f"like 'postgresql+psycopg://host:5432/db', with the login in username and "
                f"password."
            )
        try:
            return _DIALECTS[scheme]
        except KeyError:
            # Shown only when it is a plain scheme name: garbled text in front of `://` could
            # be a password.
            named = f" {scheme!r}" if re.fullmatch(r"[a-z][a-z0-9_.-]*", scheme) else ""
            raise ValidationError(
                f"unsupported database{named}; this framework speaks "
                f"{', '.join(sorted(set(_DIALECTS.values())))}"
            ) from None


@dataclass(frozen=True, kw_only=True)
class ShardMemberConfig:
    """One member of a shard group, and every database it has - Java's ``ShardMemberConfig``.

    All of them live in one named map. ``"primaryConfig"`` is reserved and required,
    ``"secondaryConfig"`` is reserved and optional, and any other name - ``"jobsConfig"``,
    ``"lockConfig"``, ``"analyticsConfig"`` - is the application's, read back with
    :meth:`config_for`. The keys are Java's, spelling included, so one layout describes both
    ports::

        ShardMemberConfig(
            member=0,
            name="global-eu-1",
            configs={
                "primaryConfig": DataSourceConfig(url=..., pool_size=20),
                "secondaryConfig": DataSourceConfig(url=...),
                "lockConfig": DataSourceConfig(url=..., pool_size=50),
            },
        )
    """

    #: Reserved key for the required primary datasource.
    PRIMARY_CONFIG: ClassVar[str] = "primaryConfig"
    #: Reserved key for the optional secondary (read-replica) datasource.
    SECONDARY_CONFIG: ClassVar[str] = "secondaryConfig"

    member: int
    configs: Mapping[str, DataSourceConfig]
    name: str | None = None

    def __post_init__(self) -> None:
        if self.PRIMARY_CONFIG not in self.configs:
            raise ValidationError(f"configs.{self.PRIMARY_CONFIG} is required")
        for key, value in self.configs.items():
            if not isinstance(key, str) or not key.strip():
                raise ValidationError(f"a config name cannot be blank, got {key!r}")
            if not isinstance(value, DataSourceConfig):
                raise ValidationError(
                    f"configs.{key} must be a DataSourceConfig, got {type(value).__name__}"
                )
        # A copy nobody else holds, read-only - Java's `Map.copyOf`.
        object.__setattr__(self, "configs", MappingProxyType(dict(self.configs)))

    @classmethod
    def builder(cls) -> ShardMemberConfigBuilder:
        return ShardMemberConfigBuilder()

    def primary_config(self) -> DataSourceConfig:
        """The writer. Always present - construction refused a member without one."""
        return self.configs[self.PRIMARY_CONFIG]

    def secondary_config(self) -> DataSourceConfig | None:
        """The read replica, or ``None`` - in which case reads use :meth:`primary_config`."""
        return self.configs.get(self.SECONDARY_CONFIG)

    def config_for(self, name: str) -> DataSourceConfig | None:
        """The datasource registered under ``name``, or ``None``.

        The reserved names answer like any other, so this is one lookup over every config on
        the member.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("name cannot be blank")
        return self.configs.get(name)


@dataclass(frozen=True, kw_only=True)
class ShardGroupConfig:
    """A group of members - Java's ``ShardGroupConfig``. The ``group`` of a
    :class:`~ekbatan.sharding.ShardIdentifier` is this group's number."""

    group: int
    name: str
    members: tuple[ShardMemberConfig, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValidationError("name is required")
        object.__setattr__(self, "members", tuple(self.members))
        if not self.members:
            raise ValidationError("at least one member is required")

    @classmethod
    def builder(cls) -> ShardGroupConfigBuilder:
        return ShardGroupConfigBuilder()


@dataclass(frozen=True, kw_only=True)
class ShardingConfig:
    """The whole layout, and which shard work goes to when nothing says otherwise.

    Java's ``ShardingConfig``: groups of members, each member one ``(group, member)`` shard.
    """

    default_shard: ShardIdentifier
    groups: tuple[ShardGroupConfig, ...]
    #: What to do with a shard this deployment has not configured.
    #:
    #: ``"use_default_shard"`` routes it to :attr:`default_shard`, which is what both ports have
    #: always done: a deployment holding a subset of the layout can read ids minted by one
    #: holding more, instead of refusing them. It is also how a mistyped shard identifier writes
    #: to the wrong database without saying so - which is why the fallback now logs a warning
    #: either way, and why this setting exists.
    #:
    #: ``"fail"`` raises instead. For a deployment that holds the whole layout, where an
    #: unregistered shard can only be a bug.
    unknown_shard_policy: Literal["use_default_shard", "fail"] = "use_default_shard"

    def __post_init__(self) -> None:
        if self.default_shard is None:
            raise ValidationError("default_shard is required")
        object.__setattr__(self, "groups", tuple(self.groups))
        if not self.groups:
            raise ValidationError("at least one group is required")

        object.__setattr__(
            self, "unknown_shard_policy", _unknown_shard_policy(self.unknown_shard_policy)
        )

        # Java does not check this: a repeated (group, member) there builds a second pool,
        # overwrites the first in the registry's map, and leaves it open with nothing holding it.
        seen: set[ShardIdentifier] = set()
        for identifier, _ in self.members():
            if identifier in seen:
                raise ValidationError(f"shard {identifier} is configured twice")
            seen.add(identifier)

        # Checked before a single engine is built. Run afterwards, the realistic mistake here -
        # a mistyped default - opens every pool and then throws, and nothing holds a reference
        # to them afterwards. This check reads configuration only, so running it first is free.
        if self.default_shard not in seen:
            raise ValidationError(
                f"default_shard {self.default_shard} names no configured database; "
                f"configured: {sorted(str(s) for s in seen)}"
            )

        dialects = {member.primary_config().dialect for _, member in self.members()}
        if len(dialects) > 1:
            raise ValidationError(
                f"every shard must speak the same dialect, found {sorted(dialects)}"
            )

    def members(self) -> Iterator[tuple[ShardIdentifier, ShardMemberConfig]]:
        """Every member, with the shard it is - group by group, in the order configured.

        Java walks ``groups`` and then ``members`` inline wherever it needs this; it is written
        once here because four places in this port need it.
        """
        for group in self.groups:
            for member in group.members:
                yield ShardIdentifier(group.group, member.member), member

    def member(self, shard: ShardIdentifier) -> ShardMemberConfig:
        """The member that is ``shard``. Java's DI modules carry this as ``findDefaultMember``."""
        for identifier, member in self.members():
            if identifier == shard:
                return member
        raise ValidationError(f"no member is configured for shard {shard}")

    @classmethod
    def builder(cls) -> ShardingConfigBuilder:
        return ShardingConfigBuilder()

    @property
    def dialect(self) -> str:
        return self.groups[0].members[0].primary_config().dialect

    @classmethod
    def single(cls, url: str, **settings: object) -> ShardingConfig:
        """The common case: one database, no sharding to think about.

        ``settings`` are :class:`DataSourceConfig`'s own - ``username``, ``password``,
        ``pool_size``, ``data_source_properties`` and the rest::

            ShardingConfig.single("postgresql+psycopg://db/wallet", username="app", password=pw)
        """
        primary = DataSourceConfig(url=url, **settings)  # type: ignore[arg-type]
        return cls(
            default_shard=ShardIdentifier(0, 0),
            groups=(
                ShardGroupConfig(
                    group=0,
                    name="default",
                    members=(
                        ShardMemberConfig(
                            member=0, configs={ShardMemberConfig.PRIMARY_CONFIG: primary}
                        ),
                    ),
                ),
            ),
        )


def _unknown_shard_policy(value: str | None) -> Literal["use_default_shard", "fail"]:
    """Read a policy as Java's ``UnknownShardPolicy.from`` does.

    Case and separators do not matter - everything but letters is dropped - so ``fail``,
    ``FAIL``, ``useDefaultShard``, ``use_default_shard`` and ``USE-DEFAULT-SHARD`` all read.
    ``None``, empty or blank is the default, ``"use_default_shard"``.
    """
    if value is None or not value.strip():
        return "use_default_shard"
    normalised = re.sub(r"[^a-z]", "", value.strip().lower())
    if normalised == "usedefaultshard":
        return "use_default_shard"
    if normalised == "fail":
        return "fail"
    raise ValidationError(
        f"unknown_shard_policy must be 'useDefaultShard' or 'fail', not {value!r}"
    )


def split_login(url: str) -> tuple[str, str | None, str | None]:
    """``url`` without its login, and the user and password it carried.

    For a url that comes from somewhere this package does not control - a throwaway container,
    a command-line flag - and has to become a :class:`DataSourceConfig`, which refuses a url with
    a login in it::

        url, username, password = split_login(container.get_connection_url())
        config = DataSourceConfig(url=url, username=username, password=password)
    """
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    # `URL.set` leaves a field alone when it is given None, so the login is cleared this way.
    plain = parsed._replace(username=None, password=None).render_as_string(hide_password=False)
    return plain, parsed.username, parsed.password


#: Settings that would silently win over ``username`` or ``password``, and the field to use instead.
_LOGIN_SETTINGS = {"user": "username", "password": "password", "passwd": "password"}


def _checked_properties(properties: Mapping[str, object]) -> dict[str, object]:
    """``data_source_properties``, checked, as a copy nobody else holds - Java's rules.

    A group or a list stays as it is: some driver settings take one (PyMySQL's ``ssl``,
    ``auth_plugin_map``), and Java refuses them only because Spring splits dotted names into
    groups, which Python's config readers do not. A value is never turned into text: PyMySQL
    fails on ``connect_timeout="10"`` and reads ``autocommit="false"`` as true.
    """
    if properties is None or not isinstance(properties, Mapping):
        raise ValidationError("data_source_properties must be a mapping of setting name to value")
    checked: dict[str, object] = {}
    for name, value in properties.items():
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("a data_source_properties name cannot be blank")
        login = _LOGIN_SETTINGS.get(name.lower())
        if login is not None:
            raise ValidationError(
                f"{name!r} does not belong in data_source_properties, where it would silently "
                f"win over {login}; set {login} instead"
            )
        if value is None:
            raise ValidationError(f"data_source_properties {name!r} has no value")
        checked[name] = value
    return checked


class DataSourceConfigBuilder:
    """Fluent builder for :class:`DataSourceConfig`. Obtain via :meth:`DataSourceConfig.builder`.

    The setters are the pool's own names - SQLAlchemy's, where Java's are Hikari's - and a
    setter left uncalled leaves the dataclass default in place.
    """

    __slots__ = ("_given",)

    def __init__(self) -> None:
        self._given: dict[str, Any] = {}

    def url(self, url: str) -> Self:
        self._given["url"] = url
        return self

    def username(self, username: str | None) -> Self:
        """The user to log in as. Leave it out (or pass None) only when the driver supplies the
        user; it cannot be blank."""
        self._given["username"] = username
        return self

    def password(self, password: str | None) -> Self:
        """The login password. Leave it out (or pass None) for a login that has none; the empty
        string is a valid password."""
        self._given["password"] = password
        return self

    def pool_size(self, pool_size: int) -> Self:
        self._given["pool_size"] = pool_size
        return self

    def max_overflow(self, max_overflow: int) -> Self:
        self._given["max_overflow"] = max_overflow
        return self

    def pool_timeout(self, pool_timeout: float) -> Self:
        self._given["pool_timeout"] = pool_timeout
        return self

    def pool_recycle(self, pool_recycle: int) -> Self:
        self._given["pool_recycle"] = pool_recycle
        return self

    def pool_pre_ping(self, pool_pre_ping: bool) -> Self:
        self._given["pool_pre_ping"] = pool_pre_ping
        return self

    def data_source_properties(self, data_source_properties: Mapping[str, object]) -> Self:
        """The extra settings handed to the driver, replacing any set before. Values reach the
        driver as given; ``user``, ``password`` and ``passwd`` are refused, in any case."""
        self._given["data_source_properties"] = dict(data_source_properties)
        return self

    def echo(self, echo: bool) -> Self:
        self._given["echo"] = echo
        return self

    def build(self) -> DataSourceConfig:
        if "url" not in self._given:
            raise ValidationError("url is required")
        return DataSourceConfig(**self._given)


class ShardMemberConfigBuilder:
    """Fluent builder for :class:`ShardMemberConfig`. Obtain via :meth:`ShardMemberConfig.builder`.

    ``primary_config``, ``secondary_config`` and ``with_config`` each put one entry in the
    ``configs`` map; ``configs`` replaces the whole map. The same four as Java's.
    """

    __slots__ = ("_configs", "_member", "_name")

    def __init__(self) -> None:
        self._member: int | None = None
        self._name: str | None = None
        self._configs: dict[str, DataSourceConfig] = {}

    def member(self, member: int) -> Self:
        self._member = member
        return self

    def name(self, name: str) -> Self:
        if name is None:
            raise ValidationError("name cannot be None")
        self._name = name
        return self

    def primary_config(self, primary_config: DataSourceConfig) -> Self:
        self._configs[ShardMemberConfig.PRIMARY_CONFIG] = primary_config
        return self

    def secondary_config(self, secondary_config: DataSourceConfig) -> Self:
        self._configs[ShardMemberConfig.SECONDARY_CONFIG] = secondary_config
        return self

    def with_config(self, name: str, config: DataSourceConfig) -> Self:
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("name cannot be blank")
        self._configs[name] = config
        return self

    def configs(self, configs: Mapping[str, DataSourceConfig]) -> Self:
        self._configs = dict(configs)
        return self

    def build(self) -> ShardMemberConfig:
        if self._member is None:
            raise ValidationError("member is required")
        return ShardMemberConfig(member=self._member, configs=self._configs, name=self._name)


class ShardGroupConfigBuilder:
    """Fluent builder for :class:`ShardGroupConfig`. Obtain via :meth:`ShardGroupConfig.builder`.

    ``with_member`` appends one; ``members`` replaces the list.
    """

    __slots__ = ("_group", "_members", "_name")

    def __init__(self) -> None:
        self._group: int | None = None
        self._name: str | None = None
        self._members: list[ShardMemberConfig] = []

    def group(self, group: int) -> Self:
        self._group = group
        return self

    def name(self, name: str) -> Self:
        self._name = name
        return self

    def members(self, members: Iterable[ShardMemberConfig]) -> Self:
        self._members = list(members)
        return self

    def with_member(self, member: ShardMemberConfig) -> Self:
        self._members.append(member)
        return self

    def build(self) -> ShardGroupConfig:
        if self._group is None:
            raise ValidationError("group is required")
        return ShardGroupConfig(
            group=self._group,
            name=self._name,  # type: ignore[arg-type]  # None is refused by ShardGroupConfig
            members=tuple(self._members),
        )


class ShardingConfigBuilder:
    """Fluent builder for :class:`ShardingConfig`. Obtain via :meth:`ShardingConfig.builder`.

    ``with_group`` appends one; ``groups`` replaces the list.
    """

    __slots__ = ("_default_shard", "_groups", "_unknown_shard_policy")

    def __init__(self) -> None:
        self._default_shard: ShardIdentifier | None = None
        self._groups: list[ShardGroupConfig] = []
        self._unknown_shard_policy: Literal["use_default_shard", "fail"] = "use_default_shard"

    def default_shard(self, default_shard: ShardIdentifier) -> Self:
        self._default_shard = default_shard
        return self

    def groups(self, groups: Iterable[ShardGroupConfig]) -> Self:
        self._groups = list(groups)
        return self

    def with_group(self, group: ShardGroupConfig) -> Self:
        self._groups.append(group)
        return self

    def unknown_shard_policy(self, unknown_shard_policy: str | None) -> Self:
        """Spelled any way Java reads it; ``None`` or blank means the default, as Java's null."""
        self._unknown_shard_policy = unknown_shard_policy  # type: ignore[assignment]
        return self

    def build(self) -> ShardingConfig:
        return ShardingConfig(
            default_shard=self._default_shard,  # type: ignore[arg-type]  # None is refused
            groups=tuple(self._groups),
            unknown_shard_policy=self._unknown_shard_policy,
        )
