"""The layout, read from a config file - YAML, TOML, JSON or INI.

A file produces the same :class:`~ekbatan.config.ShardingConfig` that code builds. Every check
stays in the config classes; this module only reads the file, fills in environment variables and
hands the result over::

    from ekbatan.config_files import load_sharding_config

    layout = load_sharding_config("ekbatan.yaml")

**The layout is Java's**, under ``ekbatan.sharding``, so one file reads the same in both ports.
Keys may be written kebab-case or camelCase, as Java's ``PropertyKeyNormalizer`` accepts
(``default-shard`` or ``defaultShard``, ``primary-config`` or ``primaryConfig``); a datasource's
own keys also snake_case (``pool_size``). Names inside ``data-source-properties`` are a driver's
and are never changed. Anything else in the file is left alone; a key under ``ekbatan.sharding``
that means nothing is refused, with its path.

**Environment variables** use each format's own style:

- YAML, TOML and JSON: ``${DB_PASSWORD}``, or ``${DB_PORT:5432}`` with a default - Spring's
  syntax. ``$${`` is a literal ``${``.
- INI: ``%(ENV_DB_PASSWORD)s`` - Supervisor's convention. ``%(name)s`` keeps INI's meaning, another
  key in the same section, and ``%%`` is a literal ``%``.

A variable that is not set and has no default stops the load, naming the variable; one that is
set but empty is used as given. A filled-in value is inserted as it is and never read again.

**Types.** YAML, TOML and JSON say what each value is. INI says nothing, so the datasource's own
keys are turned into their declared types (``pool_size = 20`` is the number 20), and inside
``data-source-properties`` a quoted value is text while an unquoted number or ``true``/``false``
becomes one. A value filled in from an environment variable is text in ``data-source-properties``.

**No error shows a value**: each names the key's path or the variable instead. The INI, YAML and
JSON readers quote the offending line in their own messages, so those are always rephrased.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, get_type_hints

from .config import (
    DataSourceConfig,
    ShardGroupConfig,
    ShardingConfig,
    ShardMemberConfig,
)
from .errors import ValidationError
from .sharding import ShardIdentifier

__all__ = ["load_sharding_config"]


_PREFIX = ("ekbatan", "sharding")

_READERS = {".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".json": "json", ".ini": "ini"}


def load_sharding_config(
    path: str | os.PathLike[str], *, environ: Mapping[str, str] | None = None
) -> ShardingConfig:
    """The layout under ``ekbatan.sharding`` in the file at ``path``.

    The reader is chosen by the file's extension: ``.yaml``/``.yml``, ``.toml``, ``.json`` or
    ``.ini``. ``environ`` is where variables are looked up, ``os.environ`` when left out.
    """
    file = Path(path)
    kind = _READERS.get(file.suffix.lower())
    if kind is None:
        raise ValidationError(
            f"{file.name}: cannot tell the format from the extension; use .yaml, .yml, .toml, "
            ".json or .ini"
        )
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as error:
        raise ValidationError(f"{file}: cannot be read ({error.strerror})") from None
    variables = os.environ if environ is None else environ
    if kind == "ini":
        tree = _read_ini(text, file.name, variables)
    else:
        tree = _fill(_read_structured(kind, text, file.name), file.name, variables)
    node: Any = tree
    walked = []
    for step in _PREFIX:
        walked.append(step)
        if not isinstance(node, Mapping) or step not in node:
            raise ValidationError(f"{file.name}: no '{'.'.join(walked)}' in the file")
        node = node[step]
    return _sharding(node, f"{file.name}: ekbatan.sharding")


# -- reading ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Text:
    """A value that arrived as text with no type: from INI, or from an environment variable.
    ``quoted`` says it is meant as text whatever it looks like."""

    value: str
    quoted: bool


def _read_structured(kind: str, text: str, name: str) -> Any:
    # Each reader's own message can quote the line it stopped at, which may hold a secret; only
    # the position is kept.
    if kind == "yaml":
        import yaml

        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as error:
            mark = getattr(error, "problem_mark", None)
            where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
            raise ValidationError(f"{name}: not valid YAML{where}") from None
    if kind == "toml":
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            line, column = getattr(error, "lineno", None), getattr(error, "colno", None)
            where = f" at line {line}, column {column}" if line else ""
            raise ValidationError(f"{name}: not valid TOML{where}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ValidationError(
            f"{name}: not valid JSON at line {error.lineno}, column {error.colno}"
        ) from None


#: `${NAME}` or `${NAME:default}`, and `$${` for a literal `${`.
_DOLLAR = re.compile(r"\$\$\{|\$\{([^}:]*)(?::([^}]*))?\}|\$\{")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_INI_ENV = re.compile(r"%\(ENV_")


def _fill(node: Any, path: str, variables: Mapping[str, str]) -> Any:
    """``${...}`` filled in everywhere in a YAML, TOML or JSON tree."""
    if isinstance(node, Mapping):
        return {key: _fill(value, f"{path}.{key}", variables) for key, value in node.items()}
    if isinstance(node, list):
        return [_fill(value, f"{path}[{i}]", variables) for i, value in enumerate(node)]
    if not isinstance(node, str):
        return node
    if _INI_ENV.search(node):
        raise ValidationError(
            f"{path}: '%(ENV_...)s' is the INI style; in this file an environment variable is "
            "written '${NAME}'"
        )
    if "${" not in node:
        return node

    def one(match: re.Match[str]) -> str:
        if match.group(0) == "$${":
            return "${"
        name, default = match.group(1), match.group(2)
        if name is None or not _ENV_NAME.fullmatch(name):
            raise ValidationError(f"{path}: a '${{' that is not a placeholder like ${{NAME}}")
        if name in variables:
            return variables[name]
        if default is not None:
            return default
        raise ValidationError(
            f"{path}: environment variable {name!r} is not set, and the placeholder has no default"
        )

    return _Text(_DOLLAR.sub(one, node), quoted=True)


class _EnvironmentInterpolation(configparser.Interpolation):
    """INI's own ``%(name)s`` - another key in the same section - plus ``%(ENV_NAME)s``, the
    environment variable ``NAME``. ``%%`` is a literal ``%``. A filled-in value is inserted as it
    is: a ``%`` in a password from the environment is never read again."""

    _TOKEN = re.compile(r"%%|%\(([^)]*)\)s|%")

    def __init__(self, variables: Mapping[str, str], name: str) -> None:
        self._variables = variables
        self._name = name

    def before_get(
        self,
        parser: Any,
        section: Any,
        option: str,
        value: str,
        defaults: Any,
    ) -> str:
        return self._filled(parser, str(section), option, value, 0)

    def _filled(self, parser: Any, section: str, option: str, value: str, _depth: int) -> str:
        where = f"{self._name}: [{section}] {option}"
        if _depth > 10:
            raise ValidationError(f"{where}: '%(...)s' references go round in a circle")

        def one(match: re.Match[str]) -> str:
            token = match.group(0)
            if token == "%%":
                return "%"
            reference = match.group(1)
            if reference is None:
                raise ValidationError(
                    f"{where}: a '%' must be written '%%', or start a placeholder like %(name)s"
                )
            if reference.startswith("ENV_"):
                variable = reference[4:]
                if variable in self._variables:
                    return self._variables[variable]
                raise ValidationError(
                    f"{where}: environment variable {variable!r} is not set; INI placeholders "
                    "have no default"
                )
            key = parser.optionxform(reference)
            if not parser.has_option(section, key):
                raise ValidationError(
                    f"{where}: '%({reference})s' names no key in [{section}]; for an environment "
                    f"variable write '%(ENV_{reference})s'"
                )
            raw = parser.get(section, key, raw=True)
            return self._filled(parser, section, key, raw, _depth + 1)

        return self._TOKEN.sub(one, value)


def _read_ini(text: str, name: str, variables: Mapping[str, str]) -> Any:
    parser = configparser.ConfigParser(
        interpolation=_EnvironmentInterpolation(variables, name),
        # Every section is its own: INI's [DEFAULT] would otherwise copy its keys into all of them.
        default_section="\0no default section",
    )
    parser.optionxform = str  # type: ignore[assignment,method-assign]  # keys keep their case
    try:
        parser.read_string(text, source=name)
    except configparser.Error as error:
        # Some of these quote the offending line, which may hold a secret: the line number only.
        line = getattr(error, "lineno", None)
        where = f" at line {line}" if line else ""
        raise ValidationError(f"{name}: not a valid INI file{where}") from None

    tree: dict[str, Any] = {}
    for section in parser.sections():
        node = tree
        for step in section.split("."):
            child = node.setdefault(step, {})
            if not isinstance(child, dict):
                raise ValidationError(f"{name}: [{section}] collides with a key of the same name")
            node = child
        for key in parser.options(section):
            raw = parser.get(section, key, raw=True)
            if "${" in raw:
                raise ValidationError(
                    f"{name}: [{section}] {key}: '${{...}}' is the YAML/TOML/JSON style; in an INI "
                    "file an environment variable is written '%(ENV_NAME)s'"
                )
            value = parser.get(section, key)
            filled = "%(ENV_" in raw
            quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in "'\""
            if key in node and isinstance(node[key], dict):
                raise ValidationError(f"{name}: [{section}] {key} collides with a section")
            node[key] = _Text(value[1:-1] if quoted else value, quoted=quoted or filled)
    return _lists(tree, name)


def _lists(node: Any, path: str) -> Any:
    """INI numbers its list items as sections (``groups.0``, ``groups.1``); those become lists."""
    if not isinstance(node, dict):
        return node
    converted = {key: _lists(value, f"{path}.{key}") for key, value in node.items()}
    if converted and all(key.isdigit() for key in converted):
        numbers = sorted(int(key) for key in converted)
        if numbers != list(range(len(numbers))):
            raise ValidationError(f"{path}: list items must be numbered 0, 1, 2, ... with no gaps")
        return [converted[str(i)] for i in numbers]
    return converted


# -- building ---------------------------------------------------------------------------------


def _canonical(key: str) -> str:
    """A key's snake_case form: ``pool-size``, ``poolSize`` and ``pool_size`` are one key."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key.replace("-", "_")).lower()


def _config_name(name: str) -> str:
    """A ``configs`` entry's name in Java's form: ``primary-config`` is ``primaryConfig``."""
    return re.sub(r"-([a-zA-Z0-9])", lambda m: m.group(1).upper(), name)


def _entries(node: Any, path: str, allowed: set[str]) -> dict[str, tuple[str, Any]]:
    """``node``'s keys by their canonical form, each refused unless ``allowed``."""
    if not isinstance(node, Mapping):
        raise ValidationError(f"{path}: must be a group of keys")
    found: dict[str, tuple[str, Any]] = {}
    for key, value in node.items():
        canonical = _canonical(str(key))
        if canonical not in allowed:
            raise ValidationError(
                f"{path}: unknown key {str(key)!r}; known here: {', '.join(sorted(allowed))}"
            )
        if canonical in found:
            raise ValidationError(f"{path}: {str(key)!r} is given twice, in two spellings")
        found[canonical] = (str(key), value)
    return found


def _sharding(node: Any, path: str) -> ShardingConfig:
    entries = _entries(node, path, {"default_shard", "groups", "unknown_shard_policy"})
    if "default_shard" not in entries:
        raise ValidationError(f"{path}: default-shard is required")
    if "groups" not in entries:
        raise ValidationError(f"{path}: groups is required")
    key, shard = entries["default_shard"]
    shard_entries = _entries(shard, f"{path}.{key}", {"group", "member"})
    default = ShardIdentifier(
        _number(shard_entries, "group", f"{path}.{key}"),
        _number(shard_entries, "member", f"{path}.{key}"),
    )
    groups_key, groups = entries["groups"]
    if not isinstance(groups, list):
        raise ValidationError(f"{path}.{groups_key}: must be a list")
    settings: dict[str, Any] = {}
    if "unknown_shard_policy" in entries:
        policy_key, policy = entries["unknown_shard_policy"]
        settings["unknown_shard_policy"] = (
            _text(policy, f"{path}.{policy_key}").lower().replace("-", "_")
        )
    return _validated(
        path,
        lambda: ShardingConfig(
            default_shard=default,
            groups=tuple(
                _group(group, f"{path}.{groups_key}[{i}]") for i, group in enumerate(groups)
            ),
            **settings,
        ),
    )


def _group(node: Any, path: str) -> ShardGroupConfig:
    entries = _entries(node, path, {"group", "name", "members"})
    if "members" not in entries:
        raise ValidationError(f"{path}: members is required")
    members_key, members = entries["members"]
    if not isinstance(members, list):
        raise ValidationError(f"{path}.{members_key}: must be a list")
    name = _text(entries["name"][1], f"{path}.name") if "name" in entries else None
    return _validated(
        path,
        lambda: ShardGroupConfig(
            group=_number(entries, "group", path),
            name=name,  # type: ignore[arg-type]  # None is refused by ShardGroupConfig
            members=tuple(
                _member(member, f"{path}.{members_key}[{i}]") for i, member in enumerate(members)
            ),
        ),
    )


def _member(node: Any, path: str) -> ShardMemberConfig:
    entries = _entries(node, path, {"member", "name", "configs"})
    if "configs" not in entries:
        raise ValidationError(f"{path}: configs is required")
    configs_key, configs = entries["configs"]
    if not isinstance(configs, Mapping):
        raise ValidationError(f"{path}.{configs_key}: must be a group of named datasources")
    built: dict[str, DataSourceConfig] = {}
    for raw_name, config in configs.items():
        name = _config_name(str(raw_name))
        if name in built:
            raise ValidationError(f"{path}.{configs_key}: {name!r} is given twice")
        built[name] = _data_source(config, f"{path}.{configs_key}.{raw_name}")
    member_name = _text(entries["name"][1], f"{path}.name") if "name" in entries else None
    return _validated(
        path,
        lambda: ShardMemberConfig(
            member=_number(entries, "member", path), configs=built, name=member_name
        ),
    )


#: `DataSourceConfig`'s keys and their declared types.
_FIELDS = {each.name: each for each in fields(DataSourceConfig)}
_TYPES = get_type_hints(DataSourceConfig)


def _data_source(node: Any, path: str) -> DataSourceConfig:
    entries = _entries(node, path, set(_FIELDS))
    given: dict[str, Any] = {}
    for canonical, (key, value) in entries.items():
        where = f"{path}.{key}"
        if canonical == "data_source_properties":
            given[canonical] = _properties(value, where)
        else:
            given[canonical] = _typed(value, _TYPES[canonical], where)
    if "url" not in given:
        raise ValidationError(f"{path}: url is required")
    return _validated(path, lambda: DataSourceConfig(**given))


def _properties(node: Any, path: str) -> Any:
    """``data-source-properties``, names as written; INI's text given a type by the quote rule."""
    if isinstance(node, _Text):
        if node.quoted:
            return node.value
        if node.value in ("true", "false"):
            return node.value == "true"
        if re.fullmatch(r"[+-]?\d+", node.value):
            return int(node.value)
        if re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?", node.value):
            return float(node.value)
        return node.value
    if isinstance(node, Mapping):
        return {str(key): _properties(value, f"{path}.{key}") for key, value in node.items()}
    if isinstance(node, list):
        return [_properties(value, f"{path}[{i}]") for i, value in enumerate(node)]
    return node


def _typed(value: Any, declared: Any, path: str) -> Any:
    """``value`` as the declared type of a datasource key - text turned into it strictly, and a
    value of another type refused rather than guessed at."""
    optional = declared in (str | None,)
    if value is None:
        if optional:
            return None
        raise ValidationError(f"{path}: needs a value")
    target = str if optional else declared
    if isinstance(value, _Text):
        text = value.value
        if target is str:
            return text
        if target is bool:
            if text.lower() in ("true", "false"):
                return text.lower() == "true"
            raise ValidationError(f"{path}: must be true or false")
        if target is int and re.fullmatch(r"[+-]?\d+", text.strip()):
            return int(text)
        if target is float and re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", text.strip()):
            return float(text)
        raise ValidationError(f"{path}: must be a {'whole ' if target is int else ''}number")
    # bool is an int to Python, and `pool_size: true` is a mistake, not a pool of one.
    if target is bool and isinstance(value, bool):
        return value
    if target is int and isinstance(value, int) and not isinstance(value, bool):
        return value
    if target is float and isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if target is str and isinstance(value, str):
        return value
    wanted = {
        str: "text - quote it",
        int: "a whole number",
        float: "a number",
        bool: "true or false",
    }
    raise ValidationError(f"{path}: must be {wanted.get(target, target)}")


def _number(entries: dict[str, tuple[str, Any]], key: str, path: str) -> int:
    if key not in entries:
        raise ValidationError(f"{path}: {key} is required")
    written, value = entries[key]
    result: int = _typed(value, int, f"{path}.{written}")
    return result


def _text(value: Any, path: str) -> str:
    result: str = _typed(value, str, path)
    return result


def _validated[T](path: str, build: Callable[[], T]) -> T:
    """The config classes' own refusals, with the path of the part of the file they are about.
    Their messages never hold a value."""
    try:
        return build()
    except ValidationError as refused:
        message = str(refused)
        if message.startswith(path.split(": ", 1)[0]):
            raise
        raise ValidationError(f"{path}: {message}") from None
