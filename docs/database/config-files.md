# Config files

Load Ekbatan's settings - the databases, the namespace, the jobs and the event handler - from a
YAML, TOML, JSON or INI file. You get the same objects you would build in code, with environment
variables filled in, and no error ever shows a value.

There are two loaders:

- `load_sharding_config` reads only the *layout*: the shards and their connection settings. It
  returns a `ShardingConfig`, the same object you would build in code.
- `load_ekbatan_config` reads the whole `ekbatan` section: the layout, plus the namespace, the
  jobs and the local event handler.

A file gets the same checks as code. A url with a login in it, a blank username, or a driver
setting named `password` is refused exactly as it is when you build the config in code.

**Sync**

```python
from ekbatan import DatabaseRegistry
from ekbatan.config_files import load_sharding_config

layout = load_sharding_config("ekbatan.yaml")
registry = DatabaseRegistry.from_config(layout)
```

**Async**

```python
from ekbatan.aio import AsyncDatabaseRegistry
from ekbatan.config_files import load_sharding_config

layout = load_sharding_config("ekbatan.yaml")
registry = AsyncDatabaseRegistry.from_config(layout)    # the same file, the async driver
```

The format comes from the file's extension: `.yaml` or `.yml`, `.toml`, `.json`, or `.ini`. No
library beyond PyYAML is involved: TOML, JSON and INI are read by Python's own `tomllib`, `json` and
`configparser`.

## The layout

The databases sit under `ekbatan.sharding`, as a list of groups of shards. Each shard is a
*member* of a group, with one or more named connection settings.

```yaml
ekbatan:
  sharding:
    default-shard: {group: 0, member: 0}
    groups:
      - group: 0
        name: global
        members:
          - member: 0
            configs:
              primary-config:
                url: postgresql+psycopg://db:5432/wallet
                username: app
                password: ${DB_PASSWORD}
                pool-size: 20
                data-source-properties:
                  sslmode: require
              jobs-config:
                url: postgresql+psycopg://db:5432/wallet
                username: app
                password: ${DB_PASSWORD}
                pool-size: 8
```

- Keys may be kebab-case or camelCase: `default-shard` or `defaultShard`, `primary-config` or
  `primaryConfig`. The keys of one connection's settings (`url`, `pool-size` and the rest) may
  also be snake_case, as they are named in code: `pool_size`.
- `data-source-properties` holds extra settings for the database driver, here PostgreSQL's
  `sslmode`. Its names are the driver's own, and are kept exactly as written.
- Anything outside `ekbatan.sharding` is left alone, so the file can hold your own settings too.
  A key under it that means nothing is refused, and the error gives its path.

The same layout in TOML, JSON and INI. (INI writes an environment variable differently; see
[Environment variables](#environment-variables).)

```toml
[ekbatan.sharding]
default-shard = { group = 0, member = 0 }

[[ekbatan.sharding.groups]]
group = 0
name = "global"

[[ekbatan.sharding.groups.members]]
member = 0

[ekbatan.sharding.groups.members.configs.primary-config]
url = "postgresql+psycopg://db:5432/wallet"
username = "app"
password = "${DB_PASSWORD}"
pool-size = 20
```

```json
{"ekbatan": {"sharding": {
  "defaultShard": {"group": 0, "member": 0},
  "groups": [{"group": 0, "name": "global", "members": [{"member": 0, "configs": {
    "primaryConfig": {"url": "postgresql+psycopg://db:5432/wallet", "username": "app",
                      "password": "${DB_PASSWORD}", "poolSize": 20}}}]}]}}}
```

```ini
[ekbatan.sharding.default-shard]
group = 0
member = 0

[ekbatan.sharding.groups.0]
group = 0
name = global

[ekbatan.sharding.groups.0.members.0]
member = 0

[ekbatan.sharding.groups.0.members.0.configs.primaryConfig]
url = postgresql+psycopg://db:5432/wallet
username = app
password = %(ENV_DB_PASSWORD)s
pool_size = 20

[ekbatan.sharding.groups.0.members.0.configs.primaryConfig.data-source-properties]
sslmode = require
```

INI has only one level of sections. So nesting is written as a dotted section name, and a list
item as a number: `groups.0`, `groups.1`, counted from 0 with no gaps. Keys keep their case, and
an INI `[DEFAULT]` section is not copied into the other sections.

## The whole `ekbatan` section

`load_ekbatan_config` reads everything under `ekbatan` - the namespace, the shard layout, the jobs
and the local event handler - and returns one `EkbatanConfig`:

**Sync**

```python
from ekbatan.config_files import load_ekbatan_config

ekbatan = load_ekbatan_config("ekbatan.yaml")

registry = DatabaseRegistry.from_config(ekbatan.sharding)
executor_builder = ActionExecutor.builder().namespace(ekbatan.namespace)
jobs_builder = job_registry().with_config(ekbatan.jobs)
handling_builder = (
    EventHandlingJob.builder()
    .config(ekbatan.local_event_handler)
    # The job does not read the switch itself; the wiring does.
    .enabled(ekbatan.local_event_handler.handling.enabled)
)
```

**Async**

```python
from ekbatan.config_files import load_ekbatan_config

ekbatan = load_ekbatan_config("ekbatan.yaml")

registry = AsyncDatabaseRegistry.from_config(ekbatan.sharding)
executor_builder = AsyncActionExecutor.builder().namespace(ekbatan.namespace)
# The jobs, fan-out and dispatch stay sync, over a sync registry from the same layout.
events_registry = DatabaseRegistry.from_config(ekbatan.sharding)
jobs_builder = job_registry().with_config(ekbatan.jobs)
handling_builder = (
    EventHandlingJob.builder()
    .registry(events_registry)
    .config(ekbatan.local_event_handler)
    # The job does not read the switch itself; the wiring does.
    .enabled(ekbatan.local_event_handler.handling.enabled)
)
```

```yaml
ekbatan:
  namespace: example.wallet
  sharding:
    # ... as above
  jobs:
    pollingInterval: PT10S
  local-event-handler:
    handling:
      enabled: true
```

| Key | Becomes | When the file has none |
|---|---|---|
| `namespace` | `ekbatan.namespace` | `default` |
| `sharding` | `ShardingConfig` | refused: it is required |
| `jobs` | `JobsConfig`: `polling-interval`, `heartbeat-interval`, `shutdown-max-wait` | framework defaults |
| `local-event-handler` | `LocalEventHandlerConfig`: `fanout-poll-delay`, `fanout-batch-size`, `handling-poll-delay`, `handling-batch-size`, `handling-max-backoff-cap`, `handling-retention-window`, `handling.enabled` | framework defaults; handling off |

- **Durations are ISO-8601 text**: `PT10S` (10 seconds), `PT0.5S`, `PT5M` (5 minutes), `P1DT2H`
  (1 day and 2 hours). A bare number or a short form such as `10s` is refused.
- **A key under `ekbatan` that means nothing is refused**, with its path, so a misspelt
  `local-event-handler.handling.enable` fails at startup instead of leaving handling off. Keys
  outside `ekbatan` are left alone, so the file can hold the application's own settings too.
- Every example in `ekbatan-py-examples` reads its settings this way, from an `ekbatan.yaml` beside
  its `app.py`.

## Environment variables

Each format uses its own style:

| Format | Placeholder | With a default |
|---|---|---|
| YAML, TOML, JSON | `${DB_PASSWORD}` | `${DB_PORT:5432}`; `${DB_PASSWORD:}` for an empty one |
| INI | `%(ENV_DB_PASSWORD)s` - Supervisor's convention | none - INI's syntax has no default |

- **Each variable is read by its name, as its placeholder is filled in.** The loader never holds
  the whole environment, so nothing it raises or keeps can show another variable's value, a
  password among them. To look variables up in a mapping of your own instead, pass `environ=` to
  either loader, as in `load_ekbatan_config(path, environ=...)`. It is read the same way, one
  name at a time.
- **A variable that is not set, with no default, stops the load**, naming the variable. Nothing
  quietly becomes the empty string.
- **A variable that is set but empty is used as given**: an empty password is a valid one.
- **A filled-in value is inserted as it is** and never read again, so a password holding `%` or `$`
  works.
- **Escapes.** In INI, `%(name)s` keeps its standard meaning (another key in the same section),
  and `%%` is a literal `%`. In YAML, TOML and JSON, `$${` is a literal `${`.
- **The other format's style is refused**, with a hint: `${DB_PASSWORD}` in an INI file, or
  `%(ENV_DB_PASSWORD)s` in a YAML one. Otherwise it would be taken as a literal password.

## Types

YAML, TOML and JSON say what kind each value is, and a value of the wrong kind is refused, not
guessed at. `pool-size: "20"` is text, not a number, and `username: 1234` must be quoted.

INI holds only text, so:

- a connection setting's own keys become their declared types: `pool_size = 20` is the number
  20, and `pool_pre_ping = false` is false. `pool_pre_ping = yes` is refused.
- inside `data-source-properties`, a quoted value is text, while an unquoted number or
  `true`/`false` gets that type: `connect_timeout = 10` is a number, `application_name = "10"` is
  text, and `sslmode = require` is text. Only lowercase `true` and `false` count; `yes` stays
  text.

**Values from environment variables.** A connection setting's own keys turn a filled-in value
into their type, in every format: `pool-size: ${POOL:10}` gives the number 10.

Inside `data-source-properties`, a filled-in value follows the same quote rule as above:

- In YAML or INI, written without quotes, a number or `true`/`false` gets its type.
  `connect_timeout: ${CONNECT_TIMEOUT:10}` and `connect_timeout = %(ENV_CONNECT_TIMEOUT)s` both
  give the number 10, which PyMySQL needs.
- Written in quotes, it stays text: `application_name: "${APP}"` keeps `123` as `"123"`, just as
  `10` and `"10"` differ in YAML itself.
- TOML and JSON allow a placeholder only inside a quoted string, so there a filled-in value is
  always text. When a driver setting must be a number and come from the environment, use YAML or
  INI; otherwise write the number itself.

## Errors

Every error names the key's path, or the variable's name, and never a value. For example:

```
ekbatan.yaml: ekbatan.sharding.groups[0].members[0].configs.primary-config.pool-size: must be a whole number
```

The YAML, INI and JSON readers quote the offending line in their own messages, and that line may
hold a secret. So a broken file is reported by its line and column only.
