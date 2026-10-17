# Parity fix, group J: JSON, domain model, timestamps

Part of [the parity-fix ledger](../PARITY-FIX-LEDGER.md). Items 6, 7, 8, 9, 10, 21, 24, 25, 26;
commits `EKB-0290`.. `EKB-0299`. Scan rows: [G](../behaviour-parity/G-domain-model.md),
[B](../behaviour-parity/B-actions.md) rows 8, 9, 13.

The user, 2026-10-17: "i like the thing to be compatible with java, so camel case".

| # | Item | Decision | State | Commit | Evidence |
|---|---|---|---|---|---|
| 6 | JSON key names | camelCase, as Java's field names | done | `EKB-0290` | `test_json.py::test_an_event_is_written_byte_for_byte_as_java_writes_it`, `test_params_take_javas_field_names_in_declaration_order` (fail on the old code); probe `json.event`, `json.params`, `json.idsEvent` now identical to Java's |
| 7 | JSON text | what Ekbatan's default Jackson 3 mapper writes | done | `EKB-0290` | `test_json.py` numbers / times / strings / bytes (47 of 55 fail on the old code); `json_formats_probe.py` against `JsonFormatsParityProbeTest.java`: 50,009 values, all written the same except 16 `OffsetDateTime`s at offset zero (limit, DECISIONS) |
| 8 | Decimals read back from JSON | numbers parsed as `Decimal`; Java's loss recorded for Java | done (Java bug noted) | `EKB-0290` | `test_json_numbers_are_read_as_decimals_so_no_digit_is_lost`; `test_outbox.py::test_decimals_are_json_numbers_and_keep_their_scale_except_on_mysql` on all 4 dialects; probe `json.ownRoundTrip` (Python) equals Java's `json.ownRoundTrip.withBigDecimalForFloats` |
| 9 | Dates, times, bytes read back as text | restored to the declared type | done | `EKB-0290` | `test_an_event_java_wrote_reads_back_as_its_declared_types`, `test_a_round_trip_restores_dates_times_bytes_and_durations`; `json_formats_probe.py`: 50,009 of Java's values read back exactly |
| 10 | Timestamps read back without a zone | aware UTC everywhere, as `Instant`; storage unchanged | todo | | |
| 21 | No-event row stores JSON `null` | SQL `NULL` in every framework JSON column (`json_column()` with `none_as_null`) | done | `EKB-0291` | `test_outbox.py::test_the_sentinels_payload_is_sql_null_not_json_null` fails on the old code on all 4 dialects, passes on the new; B probe `s4.payload_sql_null`, `s5.row.payload_sql_null`: `true`, as Java (and rows 8, 9: `s1.row.action_params`, `s1.row.payload_minus_ids`, `s2.payload_minus_ids` identical to Java's) |
| 24 | Never-set field raises `TypeError` | "`<field>` cannot be null" from a builder; JSON leaves it null | todo | | |
| 25 | Random ids sort differently | Java's signed `UUID.compareTo` | todo | | |
| 26 | Version never set defaults to 1 | refused: "version cannot be null" | todo | | |
