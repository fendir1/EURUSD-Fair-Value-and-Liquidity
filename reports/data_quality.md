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

<!-- BEGIN STALENESS_30S -->
## Current snapshot eligibility policy

Raymond Fendi selected a maximum accepted snapshot age of 30 seconds.
Ages up to and including 30 seconds are accepted when prices are valid.
Source gaps exceeding 30 seconds still break return continuity.

January was regenerated from the same ingestion generation:
1,550,700 usable-price rows under the
five-second rule versus 1,693,556
under the thirty-second rule. The original generation is retained.

The earlier Notebook 02 coverage figures describe the five-second baseline.
The shared configuration now specifies thirty seconds for future processing.
Full-history regeneration remains pending.

Automated validation: 23 passed in 3.35s; the exact thirty-second
boundary check passed. This records execution evidence, not a new owner
code-review confirmation.

The proposed ten-second-return replacement was superseded; one-second-return volatility is retained.
Measured results are recorded in the generated completion section below.
<!-- END STALENESS_30S -->

<!-- NOTEBOOK_03:BEGIN -->
### Notebook 03 — labels and pilot splits

Implemented exact 1-, 5-, 30-, and 60-second forward mid-price changes in
pips, with backward-constructed endpoint states and explicit price-path
validity checks.

Snapshot ages up to 30 seconds are accepted. A source gap strictly greater
than 30 seconds breaks the price path. Wide spreads are not automatically
excluded.

The owner confirmed retention of volatility calculated from one-second
returns. The earlier proposed ten-second-return implementation is superseded.

Same-source-record endpoints are identified separately. These observations
may have zero labels because the sampled snapshot was carried forward.

Pilot split dates are selected chronologically from observed current-price
coverage. Label intervals reaching the next period are purged. January's
pilot holdout is not the full-history final test.

Execution checks: 35 passed in 2.73s; the independently generated day and month
have identical overlapping label rows. Target timing, source causality,
staleness, grid conservation, and split-purge checks passed.

Notebook 03 owner review remains pending. Full-history processing,
final-test dates, model fitting, predictive evaluation, and sustained
wide-spread episode analysis remain pending.

Measured results are recorded in the generated completion section below.
<!-- NOTEBOOK_03:END -->

<!-- NOTEBOOK_05:BEGIN -->
### Notebook 05 — transparent liquidity regimes

Implemented trailing spread, volatility, minimum-side displayed size,
and source-snapshot activity metrics with a 60-second lookback.

Thresholds are fitted on the saved development training window only.
High spread or volatility takes precedence over low depth or activity.
Current invalid prices are unavailable; incomplete metric sets are unclassified.
A zero current displayed size on either side is a thin condition when
the complete classification is available.

Continuous metrics and overlapping rule flags are preserved.
A stressed classification is not a data exclusion.

The unchanged Notebook 04 forecasts are evaluated by current regime.
Grouped errors reconcile with the original aggregate errors.
The pilot holdout is not loaded or evaluated.

Executed correctness checks: 52 passed in 6.63s.
Real-data processing-boundary and future-append checks passed.
Notebook 05 owner review remains pending.

H001, displayed size conditional on a preceding price move, is recorded
in reports/research_hypotheses.md for later testing. No dedicated H001
experiment was executed here.

Size denomination and true quote ages remain unresolved.
The sustained-wide-spread episode investigation remains pending.
Full walk-forward uncertainty and final-test results remain pending.
<!-- NOTEBOOK_05:END -->

<!-- NOTEBOOK_07:BEGIN -->
### Notebook 07 — full-history coverage and scaling preparation

Completed bounded-memory source audits for 7 selected exports.
The audit measures actual UTC coverage, daily/yearly record counts,
quality flags, source-order gaps, adjacent exact-export duplicates,
and possible cross-file range overlaps.

Original CSVs are preserved. Changed source content has a distinct audit
identity. Completed audit outputs are verified before reuse.

Wide spreads and market-closure gaps are reported without automatic exclusion.
Source-range overlaps do not prove identical records.

Executed correctness checks: 66 passed in 5.34s.
Source integrity and daily/source row conservation passed.

The research sequence now includes full-history coverage and scale planning
before final interpretation. Multi-source combination, longer-window
training, multi-year evaluation, and untouched final testing remain pending.

Resource projections are planning estimates, not measured full-history
output sizes or peak-memory benchmarks.

H001 remains untested. Notebook 07 owner review remains pending.
<!-- NOTEBOOK_07:END -->

<!-- NOTEBOOK_08:BEGIN -->
### Notebook 08 — full-history canonical ingestion

Converted the audited, nonoverlapping exports to resumable source Parquet
generations using the existing validated ingestion implementation.

The canonical collection references each selected source partition once.
Small immutable overlays correct first-quote gap context at later source
boundaries. Original source partitions and pilot generations are preserved.

No price, size, locked-quote, or wide-spread observations were removed.

Portable collection paths are repository-relative. A separate ignored local
binding supports existing feature-generation APIs.

Executed correctness checks: 73 passed in 7.67s.
Canonical row counts, quality flags, yearly counts, source boundaries,
partition schemas, and integrity checks passed.

A separate full-history final-test date reservation was recorded using the
last 12 complete UTC calendar months. No predictive final evaluation
was executed. The January pilot plan remains separate.

Full-history features, labels, longer-window development evaluation,
H001 testing, final model freezing, and final interpretation remain pending.
Notebook 08 owner review remains pending.
<!-- NOTEBOOK_08:END -->

<!-- COMPLETION_RESULTS_START -->
## Completion-stage quality checks

Canonical quotes: 293,093,358.
Research clock rows: 339,033,600.
Research range: 2016-01-03T00:00:00+00:00 to 2026-10-01T00:00:00+00:00, end exclusive.

Cross-source context and continuous-versus-block checks passed.
Wide-spread investigation: {"wide_quote_rows": 478, "episodes": 100, "episodes_over_20_minutes": 0, "maximum_observed_duration_seconds": 337.115, "automatic_exclusions": 0}.

Wide spreads, locked quotes, and zero-size observations remain in canonical data.
Feature and label eligibility is separate from raw quality flags.
Coverage tables report the resulting populations.

Timestamp and displayed-size limitations remain documented in the research results.
<!-- COMPLETION_RESULTS_END -->
