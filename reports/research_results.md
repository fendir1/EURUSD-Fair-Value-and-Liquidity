# Research results

Results pending execution and validation.

<!-- NOTEBOOK_04:BEGIN -->
### Notebook 04 — development baselines

Implemented zero-change and trailing-return forecasts, plus training-only
imputation, scaling, and Ridge regression.

Price-only and price-plus-size Ridge models use identical training and
validation observations. Price and size coverage are reported separately.
Alpha is fixed at 10.0; no tuning was performed.

A small development sample is selected from current-price coverage.
The pilot holdout is not loaded or evaluated.

Validation includes MAE, RMSE, descriptive calibration, training-defined
forecast buckets, spread-scale comparisons, hourly diagnostics, and
same-source-record endpoint diagnostics.

Automated execution checks: 45 passed in 3.92s.
Private execution reports contain the measured development metrics.
Notebook 04 owner review remains pending.

These short-sample comparisons do not establish statistical significance,
full-history stability, or achievable trading profits.

Full walk-forward and final-test research results remain pending execution
and validation.
<!-- NOTEBOOK_04:END -->

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

<!-- NOTEBOOK_06:BEGIN -->
### Notebook 06 — January development walk-forward pilot

Implemented rolling chronological folds using the preceding
3 calendar days for training and
one following UTC day for evaluation.

Each fold independently fits preprocessing, Ridge models, regime thresholds,
and training-prediction bucket boundaries. Label intervals reaching fold
boundaries are purged.

Model feature families use identical training and evaluation observations.
Completed folds are resumable with artifact integrity checks.

Reports include aggregate and date/session/regime/freshness diagnostics,
calibration, forecast buckets, spread-scale comparisons, and paired
moving-block intervals using ordered evaluation-date loss totals.

Intervals are exploratory given the short development sample and assumptions
about time dependence. Subgroup intervals are not estimated.

Executed correctness checks: 60 passed in 5.16s.
Forecast uniqueness, coverage, chronology, and protected-boundary checks passed.
Notebook 06 owner review remains pending.

The reserved pilot holdout was not evaluated.
This is not full-history final-test validation.
Broader ingestion, multi-year stability, final-test dates, H001 testing,
and sustained-wide-spread episode analysis remain pending.
<!-- NOTEBOOK_06:END -->

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
