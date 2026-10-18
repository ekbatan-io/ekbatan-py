# Website without Java: inventory (B1)

Every line on the website and in the `docs/` copies of website pages that named Java, jOOQ,
Gradle, Maven, Flyway, Spring, Quarkus, Micronaut, Jackson, Redisson, the JVM, Hikari or JDBC,
taken from `main` at `97d726a` (line numbers are that commit's), plus the example files the
setup guide shows. Decisions: **remove** (the sentence is gone or says it without Java), **moved**
(the fact now lives on [Running beside a Java service](beside-java.md) and a short link is left),
**keep** (a working-together fact, a real name such as the `io.ekbatan.flyway.FlywayMigrator`
logger, or a code comment the page does not show). The schemagen page had none.

Before: 701 lines. Removed: 583. Moved: 90. Kept: 28.

| file | line | kind | decision | note |
|---|---|---|---|---|
| `docs/README.md` | 8 | comparison | remove | same ideas hold in the Java port dropped; beside-java linked under Running it |
| `docs/README.md` | 30 | maintainer-link | keep | repository index's "About the port" list (maintainer files), not a website copy |
| `docs/README.md` | 34 | maintainer-link | keep | same list |
| `docs/README.md` | 36 | maintainer-link | keep | same list |
| `docs/concepts/README.md` | 17 | working-together | moved | interoperate on the same tables -> one line + link to beside-java |
| `docs/concepts/actions.md` | 100 | comparison | remove | same three mechanisms Java uses |
| `docs/concepts/actions.md` | 102 | comparison | remove | ScopedValue |
| `docs/concepts/actions.md` | 112 | comparison | remove | as in Java |
| `docs/concepts/actions.md` | 171 | comparison | remove | getCause(); reason kept as raise...from |
| `docs/concepts/actions.md` | 172 | comparison | remove | Java has no equivalent |
| `docs/concepts/actions.md` | 174 | comparison | remove | as Java's is |
| `docs/concepts/models-and-entities.md` | 94 | justification | remove | reason restated without Java |
| `docs/concepts/models-and-entities.md` | 111 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `docs/concepts/models-and-entities.md` | 138 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `docs/concepts/models-and-entities.md` | 145 | justification | remove | reason restated without Java |
| `docs/concepts/models-and-entities.md` | 151 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `docs/concepts/models-and-entities.md` | 152 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `docs/concepts/models-and-entities.md` | 161 | justification | remove | reason restated without Java |
| `docs/concepts/models-and-entities.md` | 162 | justification | remove | reason restated without Java |
| `docs/concepts/models-and-entities.md` | 169 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `docs/concepts/models-and-entities.md` | 180 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `docs/concepts/models-and-entities.md` | 188 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `docs/concepts/models-and-entities.md` | 190 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `docs/concepts/models-and-entities.md` | 191 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `docs/concepts/models-and-entities.md` | 193 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `docs/concepts/models-and-entities.md` | 198 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `docs/concepts/models-and-entities.md` | 207 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `docs/concepts/models-and-entities.md` | 212 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `docs/concepts/models-and-entities.md` | 214 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `docs/concepts/models-and-entities.md` | 218 | justification | remove | reason restated without Java |
| `docs/concepts/models-and-entities.md` | 221 | justification | remove | reason restated without Java |
| `docs/concepts/models-and-entities.md` | 229 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `docs/concepts/models-and-entities.md` | 234 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `docs/concepts/models-and-entities.md` | 255 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `docs/concepts/models-and-entities.md` | 265 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `docs/concepts/models-and-entities.md` | 270 | justification | remove | reason restated without Java |
| `docs/concepts/models-and-entities.md` | 271 | justification | remove | reason restated without Java |
| `docs/concepts/models-and-entities.md` | 275 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `docs/concepts/models-and-entities.md` | 390 | justification | remove | reason restated without Java |
| `docs/concepts/models-and-entities.md` | 413 | justification | remove | reason restated without Java |
| `docs/concepts/models-and-entities.md` | 455 | justification | remove | reason restated without Java |
| `docs/concepts/models-and-entities.md` | 458 | justification | remove | reason restated without Java |
| `docs/concepts/outbox.md` | 82 | comparison | remove | as Java's builder |
| `docs/concepts/outbox.md` | 88 | working-together | moved | columns fixed; link to beside-java#the-shared-tables |
| `docs/concepts/outbox.md` | 89 | working-together | moved | same |
| `docs/concepts/outbox.md` | 103 | working-together | moved | payload JSON shape kept in Python terms; link #event-payloads |
| `docs/concepts/outbox.md` | 104 | comparison | remove | Java's field names -> camelCase |
| `docs/concepts/outbox.md` | 105 | comparison | remove | java.time forms -> ISO-8601 text |
| `docs/concepts/outbox.md` | 106 | working-together | moved | modelId/modelName reason -> beside-java |
| `docs/concepts/outbox.md` | 126 | working-together | keep | Kafka Connect is a Java program; transform JARs; link #streaming-the-events |
| `docs/concepts/outbox.md` | 127 | working-together | moved | ekbatan-debezium-smt artifact name now only on beside-java |
| `docs/concepts/sagas.md` | 65 | comparison | remove | no counterpart in the Java page |
| `docs/concepts/sharding.md` | 62 | working-together | moved | id layout shared; link #ids |
| `docs/concepts/sharding.md` | 115 | comparison | remove | Java forks a virtual thread |
| `docs/concepts/sharding.md` | 121 | comparison | remove | real difference from Java |
| `docs/database/README.md` | 28 | working-together | moved | section now points to beside-java |
| `docs/database/README.md` | 30 | working-together | moved |  |
| `docs/database/README.md` | 34 | working-together | moved |  |
| `docs/database/config-files.md` | 22 | working-together | moved | same keys; link to #the-config-file |
| `docs/database/config-files.md` | 48 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/config-files.md` | 111 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/config-files.md` | 125 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/config-files.md` | 144 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/config-files.md` | 149 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/config-files.md` | 150 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/config-files.md` | 164 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/config-files.md` | 167 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/keyed-locks.md` | 45 | comparison | remove |  |
| `docs/database/keyed-locks.md` | 102 | comparison | remove |  |
| `docs/database/keyed-locks.md` | 139 | comparison | remove |  |
| `docs/database/keyed-locks.md` | 142 | comparison | remove |  |
| `docs/database/keyed-locks.md` | 168 | working-together | moved | table row now links beside-java#keyed-locks |
| `docs/database/keyed-locks.md` | 170 | comparison | remove |  |
| `docs/database/keyed-locks.md` | 184 | working-together | moved | Redis format kept in Python terms; sharing -> beside-java#keyed-locks |
| `docs/database/keyed-locks.md` | 186 | working-together | moved | channel detail lives on beside-java |
| `docs/database/keyed-locks.md` | 198 | working-together | moved | hash sharing lives on beside-java |
| `docs/database/keyed-locks.md` | 199 | working-together | moved | hash sharing lives on beside-java |
| `docs/database/keyed-locks.md` | 201 | working-together | moved | lock name kept; Java part on beside-java |
| `docs/database/keyed-locks.md` | 212 | comparison | remove |  |
| `docs/database/keyed-locks.md` | 261 | comparison | remove |  |
| `docs/database/keyed-locks.md` | 291 | comparison | remove |  |
| `docs/database/keyed-locks.md` | 292 | comparison | remove |  |
| `docs/database/keyed-locks.md` | 299 | comparison | remove |  |
| `docs/database/mariadb.md` | 124 | working-together | moved | native UUID sharing -> beside-java#the-shared-tables |
| `docs/database/mariadb.md` | 127 | working-together | moved | checked fact kept without Java, link left |
| `docs/database/mariadb.md` | 138 | comparison | remove | reason kept in plain words |
| `docs/database/mariadb.md` | 139 | tool-name | remove |  |
| `docs/database/migrations.md` | 5 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 6 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 30 | working-together | keep | logger name io.ekbatan.flyway.FlywayMigrator shared with a Java service; Flyway comparison removed |
| `docs/database/migrations.md` | 31 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 38 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 39 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 75 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 78 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 80 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 87 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 98 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 102 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 106 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 129 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 157 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 162 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 166 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 179 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 189 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 195 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 211 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 212 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 219 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 232 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 245 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 255 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 275 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 294 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 324 | working-together | keep | logger name io.ekbatan.flyway.FlywayMigrator shared with a Java service; Flyway comparison removed |
| `docs/database/migrations.md` | 329 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 390 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 392 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 394 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 397 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 399 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 401 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 403 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/migrations.md` | 404 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/multi-database.md` | 12 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/multi-database.md` | 13 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/multi-database.md` | 55 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/multi-database.md` | 65 | working-together | moved | fact on beside-java; short link left |
| `docs/database/multi-database.md` | 86 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/multi-database.md` | 88 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/multi-database.md` | 99 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/multi-database.md` | 108 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/multi-database.md` | 112 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/multi-database.md` | 157 | working-together | moved | fact on beside-java; short link left |
| `docs/database/mysql.md` | 109 | comparison | remove |  |
| `docs/database/mysql.md` | 111 | justification | remove | now: add it in your migration, and why |
| `docs/database/mysql.md` | 131 | comparison | remove |  |
| `docs/database/mysql.md` | 133 | comparison | remove |  |
| `docs/database/mysql.md` | 139 | tool-name | remove |  |
| `docs/database/postgresql.md` | 94 | working-together | moved | eventlog schema -> beside-java#what-to-set |
| `docs/database/repositories.md` | 19 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/repositories.md` | 23 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/repositories.md` | 32 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/repositories.md` | 57 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/repositories.md` | 61 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/repositories.md` | 146 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/repositories.md` | 161 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/repositories.md` | 198 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/repositories.md` | 214 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/repositories.md` | 234 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/repositories.md` | 254 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/sharding.md` | 42 | comparison | remove |  |
| `docs/database/sharding.md` | 75 | working-together | moved | one sentence + link beside-java#the-config-file |
| `docs/database/sharding.md` | 101 | comparison | remove |  |
| `docs/database/sharding.md` | 115 | comparison | remove |  |
| `docs/database/sharding.md` | 144 | tool-name | remove | restated in plain words |
| `docs/database/sharding.md` | 216 | comparison | remove |  |
| `docs/database/sharding.md` | 276 | working-together | moved | one sentence + link beside-java#ids |
| `docs/database/sharding.md` | 289 | working-together | moved | order explained in plain words; one sentence says Java sorts the same |
| `docs/database/sharding.md` | 291 | working-together | moved |  |
| `docs/database/sharding.md` | 367 | comparison | remove |  |
| `docs/database/sharding.md` | 426 | comparison | remove |  |
| `docs/database/sharding.md` | 432 | comparison | remove |  |
| `docs/database/sqlite.md` | 3 | comparison | remove |  |
| `docs/database/sqlite.md` | 122 | comparison | remove |  |
| `docs/database/tables.md` | 9 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables.md` | 52 | working-together | moved | fact now on beside-java; short link left |
| `docs/database/tables.md` | 84 | working-together | moved | fact now on beside-java; short link left |
| `docs/database/tables/event-notifications.md` | 156 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/event-notifications.md` | 168 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/event-notifications.md` | 170 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/event-notifications.md` | 228 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/event-notifications.md` | 230 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/events.md` | 17 | comparison | remove | rewritten without Java |
| `docs/database/tables/events.md` | 116 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/events.md` | 117 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/events.md` | 118 | working-together | moved | fact now on beside-java; short link left |
| `docs/database/tables/events.md` | 119 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/events.md` | 124 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/events.md` | 128 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/events.md` | 136 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/events.md` | 151 | working-together | moved | fact now on beside-java; short link left |
| `docs/database/tables/events.md` | 167 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/events.md` | 177 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/events.md` | 198 | comparison | remove | rewritten without Java |
| `docs/database/tables/scheduled-tasks.md` | 14 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/scheduled-tasks.md` | 17 | working-together | moved | fact now on beside-java; short link left |
| `docs/database/tables/scheduled-tasks.md` | 75 | comparison | remove | rewritten without Java |
| `docs/database/tables/scheduled-tasks.md` | 115 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/scheduled-tasks.md` | 117 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/scheduled-tasks.md` | 137 | working-together | moved | fact now on beside-java; short link left |
| `docs/database/tables/scheduled-tasks.md` | 142 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/scheduled-tasks.md` | 146 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/scheduled-tasks.md` | 155 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/tables/scheduled-tasks.md` | 159 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/database/transaction-manager.md` | 56 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/transaction-manager.md` | 65 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/transaction-manager.md` | 69 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/transaction-manager.md` | 94 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/transaction-manager.md` | 123 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/transaction-manager.md` | 173 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/transaction-manager.md` | 174 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/database/transaction-manager.md` | 200 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/events/event-streaming.md` | 6 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `docs/events/event-streaming.md` | 7 | working-together | keep | SMT JARs come from the Java project; one paragraph + link |
| `docs/events/event-streaming.md` | 11 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/event-streaming.md` | 14 | comparison | remove | rewritten without Java |
| `docs/events/event-streaming.md` | 23 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `docs/events/event-streaming.md` | 68 | comparison | remove | rewritten without Java |
| `docs/events/event-streaming.md` | 77 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `docs/events/event-streaming.md` | 84 | working-together | keep | SMT JARs come from the Java project; one paragraph + link |
| `docs/events/event-streaming.md` | 131 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/event-streaming.md` | 207 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `docs/events/event-streaming.md` | 209 | working-together | keep | SMT JARs come from the Java project; one paragraph + link |
| `docs/events/event-streaming.md` | 320 | working-together | keep | SMT JARs come from the Java project; one paragraph + link |
| `docs/events/event-streaming.md` | 328 | working-together | moved | fact now on beside-java; short link left |
| `docs/events/event-streaming.md` | 329 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/event-streaming.md` | 471 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/event-streaming.md` | 501 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `docs/events/local-event-handler.md` | 73 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 156 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 158 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `docs/events/local-event-handler.md` | 175 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 178 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 189 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 211 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `docs/events/local-event-handler.md` | 221 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 223 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 225 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 254 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 262 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 263 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 272 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 274 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `docs/events/local-event-handler.md` | 278 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 279 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 292 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 302 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 306 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 331 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 359 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/events/local-event-handler.md` | 442 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/getting-started.md` | 48 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `docs/getting-started.md` | 49 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `docs/getting-started.md` | 55 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `docs/getting-started.md` | 58 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `docs/getting-started.md` | 71 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `docs/getting-started.md` | 72 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `docs/getting-started.md` | 93 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `docs/getting-started.md` | 109 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `docs/getting-started.md` | 112 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `docs/getting-started.md` | 156 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `docs/getting-started.md` | 212 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `docs/jobs/distributed-jobs.md` | 103 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `docs/jobs/distributed-jobs.md` | 165 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/jobs/distributed-jobs.md` | 169 | comparison | remove | rewritten without Java |
| `docs/jobs/distributed-jobs.md` | 184 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `docs/jobs/distributed-jobs.md` | 186 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/jobs/distributed-jobs.md` | 191 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `docs/jobs/distributed-jobs.md` | 193 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `docs/jobs/distributed-jobs.md` | 194 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `docs/jobs/distributed-jobs.md` | 244 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/jobs/distributed-jobs.md` | 273 | working-together | moved | fact now on beside-java; short link left |
| `docs/jobs/distributed-jobs.md` | 281 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/jobs/distributed-jobs.md` | 282 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `docs/jobs/distributed-jobs.md` | 286 | comparison | remove | rewritten without Java |
| `docs/runtime/README.md` | 6 | comparison | remove |  |
| `docs/runtime/README.md` | 12 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 24 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 31 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 47 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 49 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 69 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 93 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 102 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 110 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 128 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 145 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 147 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 152 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 156 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 158 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 161 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 168 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 208 | comparison | remove |  |
| `docs/runtime/concurrency.md` | 296 | comparison | remove |  |
| `docs/runtime/observability.md` | 4 | comparison | remove |  |
| `docs/runtime/observability.md` | 10 | working-together | moved | one sentence + link to beside-java#traces-and-logs |
| `docs/runtime/observability.md` | 44 | comparison | remove |  |
| `docs/runtime/observability.md` | 67 | comparison | remove | heading now matches website |
| `docs/runtime/observability.md` | 77 | comparison | remove |  |
| `docs/runtime/observability.md` | 83 | working-together | moved | scope sharing on beside-java |
| `docs/runtime/observability.md` | 84 | working-together | moved |  |
| `docs/runtime/observability.md` | 85 | working-together | moved |  |
| `docs/runtime/observability.md` | 89 | working-together | moved | one sentence why + link to beside-java#traces-and-logs |
| `docs/runtime/observability.md` | 90 | working-together | moved |  |
| `docs/runtime/observability.md` | 106 | working-together | keep | a logger name, shared with Java logging config |
| `docs/runtime/observability.md` | 108 | comparison | remove |  |
| `docs/runtime/observability.md` | 109 | comparison | remove |  |
| `docs/runtime/observability.md` | 111 | comparison | remove |  |
| `docs/runtime/observability.md` | 112 | comparison | remove |  |
| `docs/wiring-fastapi.md` | 76 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `docs/wiring-fastapi.md` | 146 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/wiring-fastapi.md` | 174 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/wiring.md` | 79 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/wiring.md` | 110 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `docs/wiring.md` | 123 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `docs/wiring.md` | 134 | justification | remove | Java comparison removed; behaviour stated on its own |
| `docs/wiring.md` | 150 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/wiring.md` | 165 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `docs/wiring.md` | 256 | example-comment | remove | Java comparison removed; behaviour stated on its own |
| `docs/wiring.md` | 273 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `docs/wiring.md` | 306 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/app.py` | 21 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/app.py` | 64 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/app.py` | 79 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/app.py` | 158 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/app.py` | 194 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/app.py` | 209 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/ekbatan.yaml` | 1 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/ekbatan.yaml` | 24 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/migrations/V0004__alerts.sql` | 1 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/repository.py` | 7 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/repository.py` | 14 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/repository.py` | 50 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/repository.py` | 74 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/repository.py` | 159 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/test_fastapi_wallet_rest_mariadb.py` | 9 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/test_fastapi_wallet_rest_mariadb.py` | 120 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mariadb/src/fastapi_wallet_rest_mariadb/test_fastapi_wallet_rest_mariadb.py` | 286 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/app.py` | 21 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/app.py` | 64 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/app.py` | 79 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/app.py` | 158 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/app.py` | 194 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/app.py` | 209 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/ekbatan.yaml` | 1 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/ekbatan.yaml` | 24 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/migrations/V0004__alerts.sql` | 1 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/repository.py` | 7 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/repository.py` | 14 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/repository.py` | 50 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/repository.py` | 74 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/repository.py` | 159 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/test_fastapi_wallet_rest_mysql.py` | 9 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/test_fastapi_wallet_rest_mysql.py` | 116 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/test_fastapi_wallet_rest_mysql.py` | 134 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/test_fastapi_wallet_rest_mysql.py` | 135 | working-together | keep | MySQL JSON number; a Java reader sees the same (points to beside-java page) |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-mysql/src/fastapi_wallet_rest_mysql/test_fastapi_wallet_rest_mysql.py` | 285 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/api.py` | 9 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/api.py` | 185 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/app.py` | 21 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/app.py` | 64 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/app.py` | 79 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/app.py` | 158 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/app.py` | 194 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/app.py` | 209 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/ekbatan.yaml` | 1 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/migrations/V0004__alerts.sql` | 1 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/repository.py` | 7 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/repository.py` | 14 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/repository.py` | 50 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/repository.py` | 74 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/repository.py` | 159 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/test_fastapi_wallet_rest_pg.py` | 9 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/test_fastapi_wallet_rest_pg.py` | 113 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `ekbatan-py-examples/fastapi-wallet-rest-uv-pg/src/fastapi_wallet_rest_pg/test_fastapi_wallet_rest_pg.py` | 279 | comparison | remove | example comment shown by the setup guide; reason restated without Java |
| `website/src/components/Footer.astro` | 2 | not-shown-comment | keep | code comment |
| `website/src/components/Footer.astro` | 64 | link | remove | The Java port link now "Running beside Java", to the new page |
| `website/src/components/Nav.astro` | 2 | not-shown-comment | keep | code comment |
| `website/src/pages/index.astro` | 5 | not-shown-comment | keep | frontmatter code comment |
| `website/src/pages/learn/actions.mdx` | 125 | comparison | remove | ScopedValue |
| `website/src/pages/learn/actions.mdx` | 185 | comparison | remove | divergence from Java; also the claim was wrong (isinstance) - fixed to exact class per src |
| `website/src/pages/learn/complete-project-setup.mdx` | 311 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/complete-project-setup.mdx` | 341 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/complete-project-setup.mdx` | 357 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/complete-project-setup.mdx` | 521 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `website/src/pages/learn/complete-project-setup.mdx` | 531 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `website/src/pages/learn/complete-project-setup.mdx` | 1009 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `website/src/pages/learn/consuming-events.mdx` | 113 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/consuming-events.mdx` | 197 | working-together | moved | Debezium JARs from the Java project; one sentence + link to beside-java#streaming-the-events |
| `website/src/pages/learn/consuming-events.mdx` | 198 | working-together | moved | Debezium JARs from the Java project; one sentence + link to beside-java#streaming-the-events |
| `website/src/pages/learn/consuming-events.mdx` | 232 | comparison | remove | schema explained on its own; eventlog option kept |
| `website/src/pages/learn/going-to-production.mdx` | 12 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/going-to-production.mdx` | 36 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/going-to-production.mdx` | 44 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/going-to-production.mdx` | 59 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/going-to-production.mdx` | 107 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/going-to-production.mdx` | 112 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/going-to-production.mdx` | 119 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `website/src/pages/learn/going-to-production.mdx` | 124 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/index.astro` | 2 | not-shown-comment | keep | frontmatter code comment |
| `website/src/pages/learn/index.astro` | 3 | not-shown-comment | keep | frontmatter code comment |
| `website/src/pages/learn/install.mdx` | 5 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `website/src/pages/learn/install.mdx` | 43 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/install.mdx` | 44 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `website/src/pages/learn/install.mdx` | 66 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `website/src/pages/learn/install.mdx` | 79 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `website/src/pages/learn/install.mdx` | 80 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `website/src/pages/learn/install.mdx` | 91 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `website/src/pages/learn/install.mdx` | 104 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `website/src/pages/learn/models-and-entities.mdx` | 103 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/learn/models-and-entities.mdx` | 113 | justification | remove | reason restated without Java |
| `website/src/pages/learn/models-and-entities.mdx` | 114 | justification | remove | reason restated without Java |
| `website/src/pages/learn/models-and-entities.mdx` | 121 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/learn/models-and-entities.mdx` | 132 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/learn/models-and-entities.mdx` | 140 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/learn/models-and-entities.mdx` | 142 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `website/src/pages/learn/models-and-entities.mdx` | 143 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `website/src/pages/learn/models-and-entities.mdx` | 145 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `website/src/pages/learn/models-and-entities.mdx` | 150 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `website/src/pages/learn/models-and-entities.mdx` | 159 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/learn/models-and-entities.mdx` | 164 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `website/src/pages/learn/models-and-entities.mdx` | 166 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/learn/models-and-entities.mdx` | 257 | justification | remove | reason restated without Java |
| `website/src/pages/learn/models-and-entities.mdx` | 280 | justification | remove | reason restated without Java |
| `website/src/pages/learn/models-and-entities.mdx` | 352 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/learn/models-and-entities.mdx` | 353 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/learn/outbox.mdx` | 143 | comparison | remove | MariaDB native UUID: kept as Python fact (native-UUID table works) |
| `website/src/pages/learn/outbox.mdx` | 145 | comparison | remove | Java DDL |
| `website/src/pages/learn/outbox.mdx` | 199 | working-together | keep | Kafka Connect is a Java program; link #streaming-the-events |
| `website/src/pages/learn/outbox.mdx` | 200 | comparison | remove | artifact a Java one does |
| `website/src/pages/learn/sharding-strategies.mdx` | 69 | working-together | moved | link #ids |
| `website/src/pages/learn/sharding.mdx` | 37 | working-together | moved | shard bits shared; link to beside-java#ids |
| `website/src/pages/learn/sharding.mdx` | 211 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/wiring-a-service.mdx` | 10 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `website/src/pages/learn/wiring-a-service.mdx` | 57 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `website/src/pages/learn/wiring-a-service.mdx` | 81 | tool-name | remove | Java tool named as a comparison; reason restated in plain words |
| `website/src/pages/learn/your-first-action.mdx` | 55 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/your-first-action.mdx` | 57 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/learn/your-first-action.mdx` | 212 | working-together | moved | payload JSON a Java consumer reads; parenthetical + link to beside-java#event-payloads |
| `website/src/pages/learn/your-first-action.mdx` | 232 | comparison | remove | Java comparison; behaviour stated on its own terms |
| `website/src/pages/reference/action-executor.mdx` | 22 | comparison | remove | execute(... .class ...) |
| `website/src/pages/reference/action-executor.mdx` | 47 | justification | remove | the way Java names them; reason (six args) kept |
| `website/src/pages/reference/action-executor.mdx` | 56 | comparison | remove | as Java's builder |
| `website/src/pages/reference/action-executor.mdx` | 132 | comparison | remove | persistActionEvents |
| `website/src/pages/reference/action-plan.mdx` | 33 | comparison | remove | hasChanges() |
| `website/src/pages/reference/action.mdx` | 47 | comparison | remove | @EkbatanAction / DI |
| `website/src/pages/reference/action.mdx` | 118 | comparison | remove | as in Java |
| `website/src/pages/reference/action.mdx` | 128 | comparison | remove | as in Java |
| `website/src/pages/reference/action.mdx` | 159 | comparison | remove | getCause() |
| `website/src/pages/reference/action.mdx` | 161 | comparison | remove | as Java's is |
| `website/src/pages/reference/config-files.mdx` | 27 | working-together | moved | same keys; link to #the-config-file |
| `website/src/pages/reference/config-files.mdx` | 53 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/config-files.mdx` | 116 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/config-files.mdx` | 130 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/config-files.mdx` | 149 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/config-files.mdx` | 154 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/config-files.mdx` | 155 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/config-files.mdx` | 169 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/config-files.mdx` | 172 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/dialects/mariadb.mdx` | 126 | working-together | moved | native UUID sharing -> beside-java#the-shared-tables |
| `website/src/pages/reference/dialects/mariadb.mdx` | 129 | working-together | moved | checked fact kept without Java, link left |
| `website/src/pages/reference/dialects/mariadb.mdx` | 140 | comparison | remove | reason kept in plain words |
| `website/src/pages/reference/dialects/mariadb.mdx` | 141 | tool-name | remove |  |
| `website/src/pages/reference/dialects/mysql.mdx` | 111 | comparison | remove |  |
| `website/src/pages/reference/dialects/mysql.mdx` | 113 | justification | remove | now: add it in your migration, and why |
| `website/src/pages/reference/dialects/mysql.mdx` | 133 | comparison | remove |  |
| `website/src/pages/reference/dialects/mysql.mdx` | 135 | comparison | remove |  |
| `website/src/pages/reference/dialects/mysql.mdx` | 141 | tool-name | remove |  |
| `website/src/pages/reference/dialects/postgresql.mdx` | 96 | working-together | moved | eventlog schema -> beside-java#what-to-set |
| `website/src/pages/reference/dialects/sqlite.mdx` | 8 | comparison | remove |  |
| `website/src/pages/reference/dialects/sqlite.mdx` | 124 | comparison | remove |  |
| `website/src/pages/reference/entity.mdx` | 29 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/reference/entity.mdx` | 125 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/reference/events/local-event-handler.mdx` | 72 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 154 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 156 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `website/src/pages/reference/events/local-event-handler.mdx` | 173 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 176 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 187 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 209 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `website/src/pages/reference/events/local-event-handler.mdx` | 219 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 221 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 223 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 252 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 260 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 261 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 270 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 272 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `website/src/pages/reference/events/local-event-handler.mdx` | 276 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 277 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 290 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 300 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 304 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 329 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/local-event-handler.mdx` | 392 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/streaming.mdx` | 9 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `website/src/pages/reference/events/streaming.mdx` | 10 | working-together | keep | SMT JARs come from the Java project; one paragraph + link |
| `website/src/pages/reference/events/streaming.mdx` | 14 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/streaming.mdx` | 17 | comparison | remove | rewritten without Java |
| `website/src/pages/reference/events/streaming.mdx` | 26 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `website/src/pages/reference/events/streaming.mdx` | 71 | comparison | remove | rewritten without Java |
| `website/src/pages/reference/events/streaming.mdx` | 80 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `website/src/pages/reference/events/streaming.mdx` | 87 | working-together | keep | SMT JARs come from the Java project; one paragraph + link |
| `website/src/pages/reference/events/streaming.mdx` | 135 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/streaming.mdx` | 212 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `website/src/pages/reference/events/streaming.mdx` | 214 | working-together | keep | SMT JARs come from the Java project; one paragraph + link |
| `website/src/pages/reference/events/streaming.mdx` | 325 | working-together | keep | SMT JARs come from the Java project; one paragraph + link |
| `website/src/pages/reference/events/streaming.mdx` | 333 | working-together | moved | fact now on beside-java; short link left |
| `website/src/pages/reference/events/streaming.mdx` | 334 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/streaming.mdx` | 476 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/events/streaming.mdx` | 506 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `website/src/pages/reference/index.astro` | 6 | not-shown-comment | keep | code comment |
| `website/src/pages/reference/index.astro` | 9 | not-shown-comment | keep | code comment |
| `website/src/pages/reference/index.astro` | 12 | not-shown-comment | keep | code comment |
| `website/src/pages/reference/jobs/distributed-jobs.mdx` | 104 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `website/src/pages/reference/jobs/distributed-jobs.mdx` | 166 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/jobs/distributed-jobs.mdx` | 170 | comparison | remove | rewritten without Java |
| `website/src/pages/reference/jobs/distributed-jobs.mdx` | 185 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `website/src/pages/reference/jobs/distributed-jobs.mdx` | 187 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/jobs/distributed-jobs.mdx` | 192 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `website/src/pages/reference/jobs/distributed-jobs.mdx` | 194 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `website/src/pages/reference/jobs/distributed-jobs.mdx` | 195 | comparison | remove | Java runtime/tooling comparison; Python behaviour stated alone |
| `website/src/pages/reference/jobs/distributed-jobs.mdx` | 245 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/jobs/distributed-jobs.mdx` | 274 | working-together | moved | fact now on beside-java; short link left |
| `website/src/pages/reference/jobs/distributed-jobs.mdx` | 282 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/jobs/distributed-jobs.mdx` | 283 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/jobs/distributed-jobs.mdx` | 287 | comparison | remove | rewritten without Java |
| `website/src/pages/reference/keyed-locks.mdx` | 46 | comparison | remove |  |
| `website/src/pages/reference/keyed-locks.mdx` | 103 | comparison | remove |  |
| `website/src/pages/reference/keyed-locks.mdx` | 140 | comparison | remove |  |
| `website/src/pages/reference/keyed-locks.mdx` | 143 | comparison | remove |  |
| `website/src/pages/reference/keyed-locks.mdx` | 169 | working-together | moved | table row now links beside-java#keyed-locks |
| `website/src/pages/reference/keyed-locks.mdx` | 171 | comparison | remove |  |
| `website/src/pages/reference/keyed-locks.mdx` | 185 | working-together | moved | Redis format kept in Python terms; sharing -> beside-java#keyed-locks |
| `website/src/pages/reference/keyed-locks.mdx` | 187 | working-together | moved | channel detail lives on beside-java |
| `website/src/pages/reference/keyed-locks.mdx` | 199 | working-together | moved | hash sharing lives on beside-java |
| `website/src/pages/reference/keyed-locks.mdx` | 200 | working-together | moved | hash sharing lives on beside-java |
| `website/src/pages/reference/keyed-locks.mdx` | 202 | working-together | moved | lock name kept; Java part on beside-java |
| `website/src/pages/reference/keyed-locks.mdx` | 213 | comparison | remove |  |
| `website/src/pages/reference/keyed-locks.mdx` | 262 | comparison | remove |  |
| `website/src/pages/reference/keyed-locks.mdx` | 292 | comparison | remove |  |
| `website/src/pages/reference/keyed-locks.mdx` | 293 | comparison | remove |  |
| `website/src/pages/reference/keyed-locks.mdx` | 300 | comparison | remove |  |
| `website/src/pages/reference/migrations.mdx` | 8 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 9 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 33 | working-together | keep | logger name io.ekbatan.flyway.FlywayMigrator shared with a Java service; Flyway comparison removed |
| `website/src/pages/reference/migrations.mdx` | 34 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 41 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 42 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 78 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 81 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 83 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 90 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 101 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 105 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 109 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 132 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 160 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 165 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 169 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 182 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 192 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 198 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 214 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 215 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 222 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 235 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 248 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 258 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 278 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 297 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 327 | working-together | keep | logger name io.ekbatan.flyway.FlywayMigrator shared with a Java service; Flyway comparison removed |
| `website/src/pages/reference/migrations.mdx` | 332 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 393 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 395 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 397 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 400 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 402 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 404 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 406 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/migrations.mdx` | 407 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/model-event.mdx` | 23 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/reference/model-event.mdx` | 44 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/reference/model-event.mdx` | 68 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `website/src/pages/reference/model-event.mdx` | 72 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `website/src/pages/reference/model-event.mdx` | 76 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `website/src/pages/reference/model-event.mdx` | 78 | working-together | moved | payload JSON a Java consumer reads; one sentence + link to beside-java#event-payloads |
| `website/src/pages/reference/model.mdx` | 18 | justification | remove | reason restated without Java |
| `website/src/pages/reference/model.mdx` | 25 | justification | remove | reason restated without Java |
| `website/src/pages/reference/model.mdx` | 36 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/reference/model.mdx` | 51 | justification | remove | reason restated without Java |
| `website/src/pages/reference/model.mdx` | 54 | justification | remove | reason restated without Java |
| `website/src/pages/reference/model.mdx` | 62 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/reference/model.mdx` | 67 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/reference/model.mdx` | 88 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/reference/model.mdx` | 99 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/reference/model.mdx` | 104 | justification | remove | reason restated without Java |
| `website/src/pages/reference/model.mdx` | 105 | justification | remove | reason restated without Java |
| `website/src/pages/reference/model.mdx` | 109 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/reference/model.mdx` | 128 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/reference/model.mdx` | 186 | comparison | remove | 'as Java does' dropped; behaviour stated alone |
| `website/src/pages/reference/model.mdx` | 221 | justification | remove | reason restated without Java |
| `website/src/pages/reference/model.mdx` | 223 | justification | remove | reason restated without Java |
| `website/src/pages/reference/multi-database.mdx` | 14 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/multi-database.mdx` | 15 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/multi-database.mdx` | 57 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/multi-database.mdx` | 67 | working-together | moved | fact on beside-java; short link left |
| `website/src/pages/reference/multi-database.mdx` | 88 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/multi-database.mdx` | 90 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/multi-database.mdx` | 101 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/multi-database.mdx` | 110 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/multi-database.mdx` | 114 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/multi-database.mdx` | 159 | working-together | moved | fact on beside-java; short link left |
| `website/src/pages/reference/repository.mdx` | 20 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/repository.mdx` | 24 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/repository.mdx` | 33 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/repository.mdx` | 58 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/repository.mdx` | 62 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/repository.mdx` | 149 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/repository.mdx` | 186 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/repository.mdx` | 202 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/repository.mdx` | 222 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/repository.mdx` | 242 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/runtime/concurrency.mdx` | 26 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 33 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 49 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 51 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 71 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 95 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 104 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 112 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 130 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 147 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 149 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 154 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 158 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 160 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 163 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 170 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 210 | comparison | remove |  |
| `website/src/pages/reference/runtime/concurrency.mdx` | 298 | comparison | remove |  |
| `website/src/pages/reference/runtime/observability.mdx` | 5 | comparison | remove |  |
| `website/src/pages/reference/runtime/observability.mdx` | 10 | working-together | moved | one sentence + link to beside-java#traces-and-logs |
| `website/src/pages/reference/runtime/observability.mdx` | 44 | comparison | remove |  |
| `website/src/pages/reference/runtime/observability.mdx` | 77 | comparison | remove |  |
| `website/src/pages/reference/runtime/observability.mdx` | 83 | working-together | moved | scope sharing on beside-java |
| `website/src/pages/reference/runtime/observability.mdx` | 84 | working-together | moved |  |
| `website/src/pages/reference/runtime/observability.mdx` | 85 | working-together | moved |  |
| `website/src/pages/reference/runtime/observability.mdx` | 89 | working-together | moved | one sentence why + link to beside-java#traces-and-logs |
| `website/src/pages/reference/runtime/observability.mdx` | 90 | working-together | moved |  |
| `website/src/pages/reference/runtime/observability.mdx` | 106 | working-together | keep | a logger name, shared with Java logging config |
| `website/src/pages/reference/runtime/observability.mdx` | 108 | comparison | remove |  |
| `website/src/pages/reference/runtime/observability.mdx` | 109 | comparison | remove |  |
| `website/src/pages/reference/runtime/observability.mdx` | 111 | comparison | remove |  |
| `website/src/pages/reference/runtime/observability.mdx` | 112 | comparison | remove |  |
| `website/src/pages/reference/sharding.mdx` | 43 | comparison | remove |  |
| `website/src/pages/reference/sharding.mdx` | 76 | working-together | moved | one sentence + link beside-java#the-config-file |
| `website/src/pages/reference/sharding.mdx` | 102 | comparison | remove |  |
| `website/src/pages/reference/sharding.mdx` | 116 | comparison | remove |  |
| `website/src/pages/reference/sharding.mdx` | 145 | tool-name | remove | restated in plain words |
| `website/src/pages/reference/sharding.mdx` | 213 | working-together | moved | one sentence + link beside-java#ids |
| `website/src/pages/reference/sharding.mdx` | 226 | working-together | moved | order explained in plain words; one sentence says Java sorts the same |
| `website/src/pages/reference/sharding.mdx` | 228 | working-together | moved |  |
| `website/src/pages/reference/sharding.mdx` | 246 | comparison | remove |  |
| `website/src/pages/reference/sharding.mdx` | 368 | comparison | remove |  |
| `website/src/pages/reference/sharding.mdx` | 427 | comparison | remove |  |
| `website/src/pages/reference/tables/event-notifications.mdx` | 171 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/event-notifications.mdx` | 173 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/events.mdx` | 18 | comparison | remove | rewritten without Java |
| `website/src/pages/reference/tables/events.mdx` | 111 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/events.mdx` | 118 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/events.mdx` | 119 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/events.mdx` | 120 | working-together | moved | fact now on beside-java; short link left |
| `website/src/pages/reference/tables/events.mdx` | 121 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/events.mdx` | 126 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/events.mdx` | 130 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/events.mdx` | 140 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/events.mdx` | 155 | working-together | moved | fact now on beside-java; short link left |
| `website/src/pages/reference/tables/events.mdx` | 171 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/index.mdx` | 12 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/index.mdx` | 61 | working-together | moved | fact now on beside-java; short link left |
| `website/src/pages/reference/tables/scheduled-tasks.mdx` | 16 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/scheduled-tasks.mdx` | 19 | working-together | moved | fact now on beside-java; short link left |
| `website/src/pages/reference/tables/scheduled-tasks.mdx` | 115 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/scheduled-tasks.mdx` | 117 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/scheduled-tasks.mdx` | 137 | working-together | moved | fact now on beside-java; short link left |
| `website/src/pages/reference/tables/scheduled-tasks.mdx` | 144 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/tables/scheduled-tasks.mdx` | 148 | comparison | remove | 'Java does X, here Y' - rewritten on its own terms |
| `website/src/pages/reference/transaction-manager.mdx` | 56 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/transaction-manager.mdx` | 65 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/transaction-manager.mdx` | 69 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/transaction-manager.mdx` | 94 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/transaction-manager.mdx` | 129 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/transaction-manager.mdx` | 178 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/transaction-manager.mdx` | 179 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/transaction-manager.mdx` | 205 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/wiring-fastapi.mdx` | 77 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/wiring-fastapi.mdx` | 147 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/wiring-fastapi.mdx` | 175 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/wiring.mdx` | 80 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/wiring.mdx` | 111 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/wiring.mdx` | 124 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/wiring.mdx` | 135 | justification | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/wiring.mdx` | 151 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/wiring.mdx` | 166 | tool-name | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/wiring.mdx` | 257 | example-comment | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/wiring.mdx` | 274 | comparison | remove | Java comparison removed; behaviour stated on its own |
| `website/src/pages/reference/wiring.mdx` | 307 | tool-name | remove | Java comparison removed; behaviour stated on its own |
