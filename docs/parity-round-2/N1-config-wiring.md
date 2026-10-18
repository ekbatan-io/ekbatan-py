# N1: config files and wiring

**Scope and what was run.** What a Java developer writes under `ekbatan:` and what they get, in
Java (Spring Boot 4.0.6, the real config loading) and in Python (`load_ekbatan_config`). Not
repeated: round 1's area C rows (layout, policy spellings, driver-setting types, missing
group/member numbers, unknown keys in the layout, password redaction) and the known Draculas 8, 9,
10, 13, 16, 17, 18, 19. 29 small YAML files (`parity-probes/round2-N1/scenarios/`) were fed to
both ports unchanged, apart from the datasource's own key names (`jdbcUrl`/`maximumPoolSize` in
Java, `url`/`poolSize` in Python). Java: a throwaway JUnit test boots a real `SpringApplication` on
each file (`spring.config.location`), then calls the starter's own binding methods
(`EkbatanCoreConfiguration.ekbatanShardingConfig`, `ekbatanJobsConfig`,
`ekbatanLocalEventHandlerConfig`, the `EkbatanProperties` binder) and reads the property the
handling-job condition reads. Python: `load_ekbatan_config` on the same files. One more run sets
per-key environment variables. Also cross-fed: a real Python example's `ekbatan.yaml` to Java and a
real Java example's `application.yml` to Python; each refuses the other's datasource keys (`url`
vs `jdbcUrl`), as Dracula 9 already says. Quarkus and Micronaut were not run (`read`): they use the
same `PropertyKeyNormalizer` and Jackson binding, but their placeholder and environment rules are
SmallRye's and Micronaut's. Probe: `config_probe.py`; Java copy `N1ConfigProbeTest.java.txt`;
outputs `config.java.out`, `config.python.out`, `config.diff`. No password is printed: the probes
print only marker strings or a length.

| # | Difference (plain, one line) | Java | Python | Verdict | Why (one line) | Evidence |
|---|---|---|---|---|---|---|
| 1 | A nested placeholder `${A:${B:x}}` gives a wrong value with no error | resolves it: `x` | `${B:x}` when A is unset, `value}` when A is set; no error | fix | gives a wrong password or url with no error; a common Spring pattern; cheap | `s12` (+ inline run: `'set-a}'`, `'${B:fb}'`) |
| 2 | A quoted number or boolean (`poolSize: "20"`, `enabled: "true"`) is refused | accepted (20, true) | refused: "must be a whole number" / "must be true or false" | fix | Helm and envsubst templates quote values; a placeholder `${X:20}` already reads as 20, a quoted `"20"` should too; safe (it fails loudly today) | `s08`, `s09` |
| 3 | `${some.other.key}` refers to another key in the file | resolved (`${ekbatan.namespace}` -> `ns`) | refused: "environment variable 'ekbatan.namespace' is not set" | leave | fails loudly at startup; YAML anchors (`&url` / `*url`) do the same job in Python | `s13` |
| 4 | A bare number as a duration (`polling-interval: 10`) | 10 seconds (Jackson's rule) | refused, asks for `PT10S` | leave | fails loudly, and the message names the fix; seconds is an accident of Jackson's code | `s01`, `s02` |
| 5 | An empty value or empty section (`polling-interval:`, `jobs:`, `namespace:`) | treated as absent (namespace becomes `""`) | refused: "must be a group of keys" / "needs a value" | leave | fails loudly; an empty namespace in Java is worse than a refusal | `s06`, `s07`, `s18`, `s19` |
| 6 | Environment variables per key (`EKBATAN_JOBS_POLLINGINTERVAL=PT7S`) | only `EKBATAN_NAMESPACE` and the handling switch work; pool size and job settings are silently ignored | none work (Dracula 13) | leave | Java's starter mostly ignores them too, so the gap is two keys, not all of them | `s99_env_override` |
| 7 | `local_event_handler:` (snake_case parent) | the handling job is created, but its settings are dropped (`enabled=false`, defaults) | read in full | leave | a Java-side inconsistency, not a Python problem | `s17` |
| 8 | The same key written twice in two spellings (`polling-interval` and `pollingInterval`) | silently keeps the first | refused, names the key | leave | Python's refusal is safer | `s15` |
| 9 | snake_case and Capitalised keys (`polling_interval`, `PollingInterval`) | refused (unknown property) | accepted | leave | Python accepts more; a Java file still loads | `s16`, `s24` |
| 10 | A nested driver setting (`dataSourceProperties: {ssl: {mode: require}}`) | flattened to one name, `ssl.mode` | passed on as a group, `{'ssl': {'mode': 'require'}}` | leave | Python drivers take groups (PyMySQL's `ssl`); JDBC takes dotted names | `s20` |
| 11 | How to write a literal `${` | `\${` (Spring's escape); `$${` fails | `$${`; `\${` fails | leave | rare; each fails loudly on the other's form | `s25`, `s26` |
| 12 | A zero job interval: the message shows the value as `0:00:00` | `PT0S` | `0:00:00` (same sentence otherwise) | leave | message wording only | `s05` |

## Fix details

**1. Nested placeholders.** Java: Spring's placeholder resolver (not Ekbatan code); the starter
reads each value through `Environment.getProperty`
(`ekbatan-di/spring/autoconfigure/.../EkbatanCoreConfiguration.java:177`, `readSubtree`), which
resolves nested defaults. Python: `src/ekbatan/config_files.py:207` (`_DOLLAR`) stops a default at
its first `}`, so `${A:${B:x}}` leaves a stray `}` or an unfilled `${B:x}` in the value. To
match: resolve from the innermost placeholder out (or at least refuse a `${` inside a default).
About 15 lines plus tests; INI is not affected.

**2. Quoted numbers and booleans.** Java: Spring hands Ekbatan every value as text, and Jackson
turns `"20"` into 20 and `"true"` into true (`EkbatanCoreConfiguration.java:99`, `:156`). Python:
`src/ekbatan/config_files.py:496` (`_typed`) turns text into a number only when it came from INI or
a placeholder; a YAML/TOML/JSON string is refused at `:519`-`:531`. To match: apply the same strict
text rule (digits only; `true`/`false`) to a plain string. About 5 lines. The decided rule "a text
field refuses a number" (`username: 1234`) is the other direction and stays.

## Totals

24 checked: 10 same, 2 fix, 10 leave, 0 needs you; 2 known (the cross-fed example files, Dracula 9)
not re-reported.

Same in both: no-section defaults; `10s` refused; ISO durations in any case (`pt10s`, `P1DT2H`,
`PT0.5S`); `enabled: TRUE` and `enabled: yes`; an unset variable with no default stops the load,
naming the variable; a batch size of 0 stops startup (Java when the job is built, Python when the
file is read); `${X:}` gives an empty value; `poolSize: 1e1` refused; `handling.enabled: false`
with its tuning kept.
