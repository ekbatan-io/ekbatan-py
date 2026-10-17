# Area C: transactions, database registry, sharding and config

Part of the [behaviour parity ledger](../BEHAVIOUR-PARITY-LEDGER.md). Run on 2026-10-17.

**Scope.** What a user sees from Java's `persistence/` (`TransactionManager`, `Transaction`,
`ConnectionProvider`, `DataSources`), `shard/` (`DatabaseRegistry`, `ShardIdentifier`,
`ShardedUUID`, `EmbeddedBitsShardingStrategy`, `UnknownShardPolicy`) and `config/`
(`DataSourceConfig`, `ShardingConfig` and its YAML binding), against Python's `persistence.py`,
`sharding.py`, `config.py`, `config_files.py`, `_credentials.py` and `_redact.py`. Also the
repository's `db()` / `readonlyDb()` / `txDb()` / `txDbElseDb()`, because that is where Java
exposes them. `concurrent/` (keyed locks) is left to area E, the jOOQ converters to area A. Every
row below was run in both ports: pure logic as a unit test in `ekbatan-core` and
`pure_probe.py`; database behaviour on one PostgreSQL container, as a test in
`ekbatan-integration-tests-postgres-sharded` and `tx_probe.py`. Ids minted by each port were fed
to the other. The password proofs log in as a real role whose password is a made-up marker, with
DEBUG logging on in both ports, and the full output of both runs was searched for it: it appears
0 times in either.

Probes and outputs: [`parity-probes/C-transactions-sharding-config/`](../../parity-probes/C-transactions-sharding-config/)
(`ParityCPureProbeTest.java` / `pure_probe.py` -> `pure.*.out`; `ParityCTxProbeTest.java` /
`tx_probe.py` -> `tx.*.out`). In the Probe column, `P:` is a key in `pure.*.out`, `T:` one in `tx.*.out`.

## Table

| # | Behaviour | Java | Python | Verdict | Probe |
|---|---|---|---|---|---|
| 1 | Sharded id layout: v7, RFC variant, group 8 bits + member 6 bits | as described | same | same | P: `id.mint.*` |
| 2 | Java-minted id read by Python, 7 shards incl. 255:63, 0:63, 255:0 | - | same shard each | same | P: `id.cross.*` (python.out) |
| 3 | Python-minted id read by Java, same 7 shards | same shard each | - | same | P: `id.cross.*` (java.out) |
| 4 | Fixed v7 id: shard and millisecond timestamp | 243:14, 1688096058518 | same | same | P: `id.fixed_v7.*` |
| 5 | Non-v7 id through the strategy | no shard | no shard | same | P: `id.v4.strategy_by_id` |
| 6 | Raw decode of a non-v7 id | `ShardedUUID.from(v4)` gives 156:22 | `read_shard` gives none | differs | P: `id.v4.raw_decode` |
| 7 | Ids unique (10,000), timestamp is now | yes | yes | same | P: `id.unique_of_10000`, `id.instant_is_now` |
| 8 | Shard range 0..255 / 0..63, outside refused | refused | refused (other wording) | same | P: `sid.of_*` |
| 9 | Printed shard | `ShardIdentifier(1, 2)` | `1:2` | differs | P: `sid.str_1_2` |
| 10 | Shard equality, hash, default 0:0 | yes | yes | same | P: `sid.equal`, `sid.hash_equal`, `sid.default` |
| 11 | Policy spelled `fail`, `FAIL`, `Fail`, `use_default_shard`, `USE-DEFAULT-SHARD`, `use-default-shard` | read | read | same | P: `policy.yaml.*` |
| 12 | Policy spelled `useDefaultShard` / `UseDefaultShard` | read | refused | differs | P: `policy.yaml.useDefaultShard` |
| 13 | Policy `' fail '`, `''`, empty value | fail / default / default | all refused | differs | P: `policy.yaml.padded_fail`, `empty_string`, `null_value` |
| 14 | Policy left out | use default shard | same | same | P: `policy.yaml.omitted` |
| 15 | Unknown policy value | refused, names both values | same | same | P: `policy.yaml.shrug*` |
| 16 | `null` policy on the registry builder | default | default | same | P: `reg.null_policy` |
| 17 | `null` policy on the config builder | default | refused | differs | P: `policy.config_builder_null` |
| 18 | Full YAML layout loads to the same values (shards, names, pools, replica, named configs, absent name/username) | yes | yes | same | P: `yaml.*` (first 14) |
| 19 | Driver setting values from YAML | all text (`"10"`, `"true"`) | typed (10, true) | differs | P: `yaml.dsp.*`, `dsc.dsp.number_kept_as` |
| 20 | Driver setting names kept as written | yes | yes | same | P: `yaml.dsp.names_kept` |
| 21 | YAML group or member with no number | taken as 0 | refused | differs | P: `yaml.group_number_missing*`, `yaml.member_number_missing` |
| 22 | Unknown YAML key refused, key named | yes | yes | same | P: `yaml.unknown_key*` |
| 23 | No primary, no groups, no default shard, group without name | refused | refused | same | P: `yaml.no_primary`, `no_groups`, `no_default_shard`, `group_no_name` |
| 24 | Layout with a default shard that names nothing | loads; registry refuses (before pools) | refused at load | differs | P: `yaml.default_names_nothing`, `reg.from_config.default_names_nothing` |
| 25 | Layout with a shard twice | loads; registry keeps one silently | refused | differs | P: `yaml.duplicate_shard`, `reg.from_config.duplicate_shard*` |
| 26 | Layout mixing PostgreSQL and MySQL | loads | refused | differs | P: `yaml.mixed_dialects` |
| 27 | kebab-case keys (`default-shard`) | read by the DI path's normaliser (core binding alone refuses) | read | same | P: `yaml.kebab_keys.*` |
| 28 | A bad value in a YAML number key is repeated in the error | yes, value shown | no | differs | P: `yaml.bad_pool_value.marker_in_error` |
| 29 | A password given as a map or list is not repeated in the error | not shown | not shown | same | P: `yaml.password_as_*` |
| 30 | Datasource defaults: at most 10 connections, username and password optional | yes | yes | same | P: `dsc.default.*` |
| 31 | Pool size below 1 refused | yes | yes (other name) | same | P: `dsc.pool_*` |
| 32 | Blank or missing url refused | yes | yes | same | P: `dsc.url_*` |
| 33 | Blank username refused; empty password and password-only accepted | yes | yes | same | P: `dsc.username_*`, `dsc.password_*` |
| 34 | Dialect read from the url; unreadable url and Oracle refused | yes | yes | same | P: `dsc.dialect.*` (not sqlite) |
| 35 | SQLite url | refused | accepted | differs | P: `dsc.dialect.sqlite` |
| 36 | `user` / `PASSWORD` in driver settings refused | yes | yes | same | P: `dsc.dsp.user`, `dsc.dsp.PASSWORD` |
| 37 | `passwd` in driver settings | accepted | refused | differs | P: `dsc.dsp.passwd` |
| 38 | Driver setting with no value or blank name refused | yes | yes | same | P: `dsc.dsp.null_value`, `blank_name` |
| 39 | Driver setting holding a group or a list | refused | accepted | differs | P: `dsc.dsp.nested`, `dsc.dsp.list` |
| 40 | Login in the url (`?password=`, `u:pw@`, `?user=`, `?sslpassword=`) refused, secret not in the error | yes | yes | same | P: `dsc.url_*` |
| 41 | Printed datasource, member, layout never show the password | never (no `toString`) | never (redacted) | same | P: `dsc.*printed.marker` |
| 42 | Registry with no database refused | yes | yes | same | P: `reg.empty` |
| 43 | Two databases and no default refused; one database is the default by itself | yes | yes | same | P: `reg.two_no_default`, `reg.one_no_default.default` |
| 44 | Asking for an unregistered shard's manager | refused | refused (other wording) | same | P: `reg.manager_*` |
| 45 | Effective shard: registered -> itself; unknown -> default with one WARN per call from `io.ekbatan.core.shard.DatabaseRegistry` | yes | yes | same | P: `reg.effective.*` |
| 46 | Effective shard, unknown, policy fail | refused | refused | same | P: `reg.effective.unknown_fail_policy` |
| 47 | Effective shard of `null` | WARN + default; refused under fail | default, silent, also under fail | differs | P: `reg.effective.null_*` |
| 48 | Registry keeps insertion order | yes | yes | same | P: `reg.shards_order` |
| 49 | From config: no replica shares the primary's pool; a replica gets its own | yes | yes | same | P: `reg.from_config.*_pool` |
| 50 | Closing the registry twice | fine | fine | same | P: `reg.close_twice` |
| 51 | Commit: others see nothing before, the row after | yes | yes | same | T: `tx.uncommitted.*`, `tx.commit.*` |
| 52 | Exception in the block: rolled back | yes | yes | same | T: `tx.rollback.*row_kept` |
| 53 | The exception that comes out of `inTransaction(...)` / `transaction()` | a new `RuntimeException` wrapping it, even an unchecked one | the same object | differs | T: `tx.rollback.runtime.propagated_same_object`, `tx.failed_rollback.propagated` |
| 54 | The exception that comes out of `inTransactionChecked(...)` | the same object | the same object | same | T: `tx.rollback.runtime.checked_form_same_object` |
| 55 | `Error` / `BaseException` in the block: rolled back, passed on unchanged | yes | yes | same | T: `tx.rollback.base_error.*` |
| 56 | Checked exceptions | `inTransaction` wraps, `inTransactionChecked` does not | none in Python | read | T: `tx.checked.*` - Python has no checked exceptions |
| 57 | Current transaction: none outside, the block's own inside, none after | yes | yes | same | T: `tx.current.*` |
| 58 | Another thread started inside a transaction | sees none | sees none | same | T: `tx.current.other_thread_inside` |
| 59 | Outside a transaction each statement commits by itself | yes | yes | same | T: `tx.autocommit.*` |
| 60 | Rollback log line | WARN, same logger, `[shard=(0,0)]: IllegalStateException` | same level and logger, `[shard=0:0]: ValueError` | differs | T: `tx.log.*` |
| 61 | Transaction inside a transaction: connection, sees outer's rows | second connection, sees nothing | same (fixed in EKB-0301) | same | T: `tx.nested.same_connection`, `inner_sees_outer_uncommitted` |
| 62 | Inner committed, then outer fails | inner row kept | same (fixed in EKB-0301) | same | T: `tx.nested.*after_outer_rollback` |
| 63 | Inner fails, outer catches it and commits | inner row gone | same (fixed in EKB-0301) | same | T: `tx.nested.inner_failure_caught.*` |
| 64 | Inner writes a row the outer has locked | blocks (lock timeout here; forever without one) | same (fixed in EKB-0301) | same | T: `tx.nested.inner_updates_row_outer_locked` |
| 65 | After the inner block, current is the outer | yes | yes | same | T: `tx.nested.current_after_inner_is_outer` |
| 66 | Two managers for the same shard (here different databases): the second inside the first's transaction | sees none; opens its own on its own database | same (fixed in EKB-0300) | same | T: `tx.two_managers_same_shard.*` |
| 67 | Rollback fails (connection killed): original error out, connection thrown away and logged, row gone, next transaction fine | yes | yes | same | T: `tx.failed_rollback.*` (message differs by row 53) |
| 68 | Commit fails (deferred unique): error with SQLSTATE 23505, nothing kept, next transaction fine | yes | yes | same | T: `tx.commit_failure.*` |
| 69 | Replica configured: repository read-only access reads the replica, also inside a transaction | yes | yes | same | T: `rep.secondary_database`, `rep.repo_readonly_*` |
| 70 | No replica: read-only access reads the primary | yes | yes | same | T: `rep.no_replica.secondary_database` |
| 71 | Repository `db()` inside a transaction: other connection, does not see its rows | yes | yes | same | T: `rep.repo_db_inside_tx_*` |
| 72 | `txDbElseDb` joins inside, commits by itself outside; `txDb` present inside, empty outside | yes | yes | same | T: `rep.repo_txDb*` |
| 73 | Manager-level read-only access inside a transaction | no such call; `registry.secondary` reads the replica | `manager.connection(readonly=True)` joins the transaction on the primary | differs | T: `rep.manager_level_readonly_inside_tx_database` |
| 74 | Repository access with an explicit unregistered shard | `db`/`readonlyDb`: null context; `txDb`: refused | default shard, with a WARN | differs | T: `acc.*_unknown_shard` |
| 75 | Repository access by an id from an unregistered shard | default + WARN | default + WARN | same | T: `acc.db_by_id_unknown_shard*` |
| 76 | Open connections after one query, pool of 3 | 3 | 1 | differs | T: `pool.open_connections_after_one_query` |
| 77 | Connections under load never exceed the configured max | 3 | 3 | same | T: `pool.max_connections_under_load` |
| 78 | Driver settings reach the server (application name) | yes | yes | same | T: `pool.driver_setting_reaches_server` |
| 79 | Wrong password: time to fail | about 30 s (pool retries) | under 5 s (at once) | differs | T: `red.wrong_password.seconds_to_fail` |
| 80 | Real login with the marker password works; the wrong-password error never holds it | yes | yes | same | T: `red.login_*`, `red.wrong_password.*` |
| 81 | Printed manager, pool, url, registry never show the password | never | never | same | T: `red.printed_forms.marker` |
| 82 | All logs at DEBUG (Hikari / SQLAlchemy pool and engine) never hold the password | never; 0 in the whole run output | never; 0 in the whole run output | same | T: `red.all_captured_logs.*` |
| 83 | No username: driver logs in as the OS user; password not in the error | yes | yes | same | T: `red.no_username.logs_in_as_os_user`, `marker_in_error` |
| 84 | No username: a warning | WARN "No username is configured" | none | differs | T: `red.no_username.warned` |
| 85 | Pool knobs: Java has no connection-timeout setting, Python has no `minimumIdle` / `idleTimeout` / leak detection | - | - | read | API shape: the settings do not exist on the other side ([Dracula 10](../DRACULAS.md)) |

## Differences

Ordered as in the table. Surprise is for a Java developer.

**6. Raw decode of a non-v7 id.** Java's `ShardedUUID.from(uuid).resolveShardIdentifier()` reads
bits out of any UUID; Python's `read_shard` returns none for anything not v7. Through the strategy
both say "no shard". Java `ShardedUUID.java:78`; Python `sharding.py:115-124`. Python could
match by dropping the check (cheap), but the check is safer. Known: Dracula 60. Surprise: low.

**9. Printed shard.** `ShardIdentifier(1, 2)` against `1:2`, in messages and logs too (rows 44-47,
60). Java `ShardIdentifier.java:74`; Python `sharding.py:70`. Matching is one line. Known: Dracula
66. Surprise: low.

**12. `useDefaultShard` is refused.** Java strips everything but letters, so `useDefaultShard` -
the spelling Java's own error message asks for - is read; Python only lower-cases and turns `-`
into `_`, so camelCase is refused. Java `UnknownShardPolicy.java:45-47`; Python
`config_files.py:401`, `config.py:291`. Python could match by normalising as Java does (cheap).
Not in the Draculas (58 is only "string, not enum"). Surprise: medium - a Java `application.yml`
written as Java documents it fails here.

**13. Padded and empty policy.** Java trims (`' fail '` is fail) and reads blank or empty as the
default; Python refuses all three. Same files as 12; same cheap fix. Surprise: low.

**17. `null` policy on the config builder.** Java's `ShardingConfig` builder treats `null` as the
default; Python's `ShardingConfigBuilder.unknown_shard_policy(None)` is refused. (The registry
builder matches, row 16.) Java `ShardingConfig.java:44-45`; Python `config.py:291`, builder
`config.py:595-600`. Cheap to match. Surprise: low.

**19. Driver setting values keep their type.** Java turns every value into text; Python passes
10 and `true` as they are. A recorded decision: PyMySQL fails on `"10"` and reads `"false"` as
true. Java `DataSourceConfig.java:239-251`; Python `config.py:393` (`_checked_properties`). Matching
would break PyMySQL. Known: Dracula C "data_source_properties keep their types". Surprise: low.

**21. Missing group or member number.** Java's builders default `group` and `member` to 0, so a
YAML member with no `member:` is shard x:0 without a word - and can collide with a real x:0.
Python refuses it. Java `ShardGroupConfig.java:39`, `ShardMemberConfig.java:92`; Python
`config_files.py:607-609`. Python could match (cheap) but its refusal is safer; Java could add
the check. Not in the Draculas. Surprise: low.

**24-26. Layout checks.** Java's `ShardingConfig` accepts a default shard that names nothing
(the registry refuses it later, before opening pools), a shard twice (the registry silently keeps
the second and the first pool is left open), and mixed databases. Python refuses all three when the
config is built. Java `ShardingConfig.java:40-46`, `DatabaseRegistry.java:180-215`; Python
`config.py:297-318`. Java could add the checks. Known: Dracula 19. Surprise: low.

**28. A bad YAML value is repeated in the error.** Jackson's message for a non-number in
`maximumPoolSize` quotes the value; Python's names only the key. A password pasted into the wrong
key would reach the log in Java. Java: Jackson binding of `DataSourceConfig.Builder`; Python
`config_files.py:495` (`_typed`). Python already does the safe thing; Java would need its own
message. Surprise: low.

**35. SQLite.** Java refuses `jdbc:sqlite:`, Python accepts SQLite. Java
`DataSourceConfig.java:128-145`; Python `config.py:37` (`_DIALECTS`). Known: Dracula 18. Surprise: low.

**37. `passwd`.** Python also refuses `passwd` in driver settings (PyMySQL's name for the
password); Java accepts it. Java `DataSourceConfig.java:224-231`; Python `config.py:390`
(`_LOGIN_SETTINGS`). Python-specific on purpose. Surprise: low.

**39. Groups and lists in driver settings.** Java refuses them; Python passes them on (PyMySQL's
`ssl` is a group). A recorded decision, same file as 19. Surprise: low.

**47. Effective shard of `null`.** Java warns and routes to the default (or throws under `FAIL`);
Python returns the default silently, even under `fail`. Java's repositories never pass `null`, so
only direct callers see it. Java `DatabaseRegistry.java:107-122`; Python `persistence.py:337-338`.
Python could match (cheap), but `None` means "unsharded" there by design. Surprise: low.

**53. `inTransaction` wraps every exception.** Java's unchecked form catches `Exception`, so even
a `RuntimeException` from the block comes out as a *new* `RuntimeException` wrapping it - its
javadoc says only checked ones are wrapped. `inTransactionChecked` (what the action executor uses)
passes it unchanged. Python always re-raises the same object. Java `TransactionManager.java:137-157`;
Python `persistence.py:123-195`. This looks like a Java bug; Python should not copy it. Surprise:
low for a Python reader, but a Java `catch (MyException e)` around `inTransaction` never fires.

**60. Rollback log line.** Same level and logger name; the text says `[shard=(0,0)]` in Java and
`[shard=0:0]` in Python, and names a Java or Python exception class. Java
`TransactionManager.java:187-191`; Python `persistence.py:189-193`. Cheap to
match the shard format; log scrapers are the only ones who notice. Surprise: low.

**61-64. A transaction inside a transaction joins.** **Fixed** (`EKB-0301`): Python now opens a
second transaction on a second connection, as Java. Before: Python joined the open one: same connection,
the inner block sees the outer's rows, an inner "commit" is undone by an outer failure, a failed
inner block whose error the outer catches still gets its rows committed, and an inner write never
blocks on the outer's lock. Java opens a second transaction on a second connection: it sees
nothing, commits on its own, rolls back on its own, and blocks on the outer's lock (forever without
a lock timeout). Java `TransactionManager.java:159-210`; Python `persistence.py:138-143`. Python
could match by opening a second connection (cheap to write; changes what every nested caller gets).
Known: Dracula 44. Row 63 is the new detail: the inner block's own failure does not roll back its
own writes. Surprise: high.

**66. Two managers for the same shard share one transaction.** **Fixed** (`EKB-0300`): the open
transaction is now kept per manager, as Java's. Before: Python keyed the open transaction
by shard, not by manager. A second `TransactionManager` for the same shard - another registry,
or one built on a member's `jobsConfig` - finds the first one's transaction, and its own
`transaction()` runs on the first's connection: here a manager for `replicadb` wrote into
`testdb`. Java keeps one `ScopedValue` per manager, so the second sees nothing and uses its own
database. Java `TransactionManager.java:56`, `:92`; Python `persistence.py:47`, `:138`, `:229`.
Python could match by keying the open transactions by manager (cheap). Not in the Draculas.
Surprise: high - statements go to a different database than the manager named.

**73. Manager-level read-only access inside a transaction.** Python's
`manager.connection(readonly=True)` joins the open transaction on the primary instead of reading
the replica. Java has no manager-level read; its `registry.secondary` reads the replica whatever
is open. The repository's `readonly_db()` matches Java (row 69). Python `persistence.py:208-235`;
used by `events/repository.py:56`. Python could make it ignore the transaction (cheap). Surprise:
low - a Python-only call, but its name promises the replica.

**74. Repository access with an explicit unregistered shard.** Java's `db(shard)` and
`readonlyDb(shard)` return `null` (the error comes at first use) and `txDb(shard)` throws; Python
applies the unknown-shard policy and gives the default with a warning. Java
`AbstractRepository.java:212-214`, `:251-253`, `:300-302`; Python `repository.py:528-535`. Python's
is the safer answer; Java's looks like a Java gap. Surprise: low.

**76. Idle connections.** After one query on a pool of 3, Java holds 3 connections open (Hikari
fills to `minimumIdle`, which defaults to the maximum) and Python holds 1 (SQLAlchemy opens on
demand). Same ceiling under load (row 77). Java `ConnectionProvider.java:119-131`; Python
`persistence.py:384` (`engine_for`). Python could pre-open connections at start (moderate work).
Known in part: Dracula 10. Surprise: medium - connection budgets on the database differ.

**79. Wrong password.** Java's pool keeps retrying until Hikari's 30 s connection timeout; Python
fails on the first refusal. Java `ConnectionProvider.java:119-131` (Hikari default
`connectionTimeout`, which `DataSourceConfig` cannot change); Python `persistence.py:384`. Matching
Java would mean adding a retry loop (moderate); Python's is the faster failure. Surprise: medium -
start-up and health checks fail 30 s later in Java.

**84. No warning without a username.** Java warns "No username is configured" when the login has
no user; Python says nothing and the driver quietly uses the OS user, as Java's does. Java
`DataSources.java:52-55`; Python `persistence.py:384` (`engine_for`). One log line to match
(cheap). Surprise: low.

## Totals

85 rows: **56 same, 27 differs, 2 read.** Of the 27 differences, 13 were already known - Draculas
10 (row 76, in part), 18 (35), 19 (24-26), 44 (61-64), 60 (6), 66 (9) and the recorded
driver-settings decision (19, 39). The other 14 are new: 12, 13, 17, 21, 28, 37, 47, 53, 60, 66, 73, 74,
79, 84 - plus the row-63 detail of Dracula 44. The ids are compatible both ways: every id minted by one port
decodes to the same shard in the other.
