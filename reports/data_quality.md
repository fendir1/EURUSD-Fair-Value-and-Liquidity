# Preliminary data-quality report

Scope: bounded prefixes of selected completed exports.
Timestamps remain in the unverified source clock.
No observations were excluded or source files modified.

| File | Sample records | Duplicate field rows | Crossed | Locked | Median spread, pips |
|---|---:|---:|---:|---:|---:|
| EURUSD_Ticks_2016.01.04_2017.01.02.csv | 10000 | 0 | 0 | 0 | 0.4 |
| EURUSD_Ticks_2019.01.02_2021.01.02.csv | 10000 | 0 | 0 | 0 | 0.6 |
| EURUSD_Ticks_2021.01.02_2023.01.02.csv | 10000 | 0 | 0 | 0 | 0.5 |
| EURUSD_Ticks_2023.01.02_2025.01.02.csv | 10000 | 0 | 0 | 0 | 6.2 |
| EURUSD_Ticks_2025.01.02_2026.10.17.csv | 10000 | 0 | 0 | 0 | 0.3 |

These prefixes do not establish representative liquidity conditions, full coverage, or whole-file quality.

Quality flags are separate from exclusion decisions. Repeated timestamps are not automatically duplicate observations. Missing sizes differ from zero sizes.

## Data dictionary
- **Time (EET)**: Timestamp shared by the supplied quote snapshot. Units: Formatted source-clock date/time; fractional precision inspected. UTC conversion blocked pending export timezone/DST verification.
- **Ask**: Supplied ask price. Units: USD per EUR under the EURUSD quotation convention. No price rescaling applied.
- **Bid**: Supplied bid price. Units: USD per EUR under the EURUSD quotation convention. No price rescaling applied.
- **AskVolume**: Candidate displayed size associated with the supplied ask. Units: Unresolved export denomination. Not interpreted as executed trading volume.
- **BidVolume**: Candidate displayed size associated with the supplied bid. Units: Unresolved export denomination. Not interpreted as executed trading volume.

## Unresolved assumptions
- Verify the JForex export timezone and daylight-saving convention.
- Verify the CSV displayed-size denomination and any historical changes.
- Audit newly completed exports before adding them to ingestion.
- Establish full-file coverage, ordering, duplicates, and quality later.
- Resolve cross-file overlaps and ordering before modeling.
- True side-specific quote ages are unavailable from the observed schema.
- Prefix-based file row counts are approximate planning estimates only.

<!-- BEGIN NOTEBOOK01 -->
## Notebook 01 — ingestion execution

Source timezone: Europe/Helsinki, interpreted as European EET/EEST.
Timezone evidence: selected historical DST weekend boundaries.

Requested month: 2016-01-01T00:00:00Z to
2016-02-01T00:00:00Z (exclusive end).

Observed month coverage: 2016-01-03T22:00:01.446000+00:00 to
2016-01-31T23:59:59.429000+00:00.

- Source records scanned: 44,712,939
- In-range quotes preserved: 2,163,940
- Unassignable timestamp rows preserved separately:
  0
- Valid-timestamp records outside this range:
  42,548,999
- Exact-field duplicate excess rows:
  0
- Repeated-timestamp excess rows:
  0
- Chronological gaps above the configured audit threshold:
  524
- Day versus month-slice difference rows: 0

### In-range quality flags

| Flag | Rows |
|---|---:|
| flag_adjacent_repeated_timestamp | 0 |
| flag_backward_timestamp | 0 |
| flag_crossed | 0 |
| flag_extreme_spread | 0 |
| flag_invalid_price | 0 |
| flag_invalid_size | 0 |
| flag_locked | 0 |
| flag_missing_size | 0 |
| flag_missing_timestamp | 0 |
| flag_negative_size | 0 |
| flag_nonpositive_price | 0 |
| flag_source_gap | 524 |
| flag_unparseable_timestamp | 0 |
| flag_unresolved_local_time | 0 |
| flag_zero_size | 0 |
| flag_zero_total_size | 0 |

Flags overlap and do not constitute an automatic exclusion policy.
No price- or size-based rows were silently removed.

Detailed examples and source-wide counts are retained in ignored local
diagnostics and manifests.

Displayed-size units and true side-specific quote ages remain unresolved.
This execution does not establish full-history data quality or predictive value.
<!-- END NOTEBOOK01 -->

<!-- BEGIN NOTEBOOK02 -->
## Notebook 02 — sampling coverage

- Calendar grid rows: 2,678,400
- Usable-price rows: 1,550,700
- Valid displayed-size-pair rows:
  1,550,700
- Future-source violations:
  0
- Stale prices marked usable:
  0
- Continuous-day versus block comparison: passed

Coverage includes closed periods and time before the first source quote.
It does not measure feed uptime.

Missing feature values are preserved. No model imputation or forecast
evaluation has occurred. Wide spreads are not automatically excluded.
<!-- END NOTEBOOK02 -->
