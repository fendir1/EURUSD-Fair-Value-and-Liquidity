# EURUSD Fair-Value and Liquidity Research

## Purpose and business relevance

Investigate short-horizon EURUSD price changes, liquidity deterioration, and possible electronic-pricing implications.

## Data and units

Dukascopy/JForex Level 1 exports. EURUSD pip size is 0.0001. Source timezone/DST interpretation and displayed-size denomination remain unresolved.

## Observed information and limitations

This is a research prototype. Displayed sizes are not executed trade volumes. A single feed does not represent the entire FX market. Predictive accuracy does not establish achievable trading profits.

## Repository structure

notebooks/: research steps; src/: reusable modules; configs/: settings; data/: local datasets and safe synthetic samples; tests/: correctness checks; reports/: findings; artifacts/: generated execution evidence and models.

## Setup

Notebook 00 uses Python's standard library. Research dependencies and the editable package will be established before Notebook 01 imports project code.

## Notebook execution order

00 environment/data audit → 01 ingestion/quality → 02 sampling/features → 03 labels/splits → 04 baselines → 05 regimes → 06 walk-forward evaluation → 07 interpretation.

## Methodology

Currently implemented: bounded source inspection and prefix-quality diagnostics. Causal sampling, labels, models, and evaluation remain pending.

## Validation

Notebook 00 records execution observations. Third-party imports, research tests, full-history quality, and owner review remain pending.

## Results

Results pending execution and validation.

## Development approach

Development approach: This is an AI-assisted research project.
Raymond Fendi defined the business objectives and provided FX domain
direction. OpenAI Codex assisted with implementation, testing, and
documentation. CONTRIBUTIONS.md records completed design decisions,
reviews, and validation.

## Next steps

Review Notebook 00 outputs, resolve source assumptions, prepare the package, and begin synthetic and bounded ingestion checks.

<!-- BEGIN NOTEBOOK01 -->
## Implemented ingestion workflow

The project now provides an editable src-layout package, synthetic
quotes, bounded CSV-to-Parquet ingestion, UTC year/month partitions,
source provenance, resumable batch manifests, and quality diagnostics.

From an active Jupyter kernel, installation uses its Python executable
with `-m pip install --editable <repository>[test]`.

Notebook 01 runs focused tests, a synthetic example, a pilot day, and
the available January 2016 data. Later notebooks consume the selected
manifest recorded in ignored local configuration.

Historical quotes are preserved with quality flags. Unassignable
timestamps are stored separately. Overlapping pilot and month datasets
must not be concatenated.

Environment versions are recorded locally after successful execution.
Owner review remains pending.

Results pending execution and validation.
<!-- END NOTEBOOK01 -->

<!-- BEGIN NOTEBOOK02 -->
## Implemented sampling and features

Notebook 02 creates a one-second UTC grid from manifest-selected quotes.
Sampling is backward-looking, with explicit staleness and continuity
checks. Prices become unavailable when the latest state is unusable.

Features include prices, spreads, displayed sizes, imbalance,
size-weighted quote displacement, trailing price changes and log returns,
realized volatility, snapshot activity, spread/size changes, time features,
and bounded observed price-change ages.

See reports/feature_dictionary.md for units and window definitions.
The active month feature manifest is recorded in ignored local configuration.

Tests cover financial units, temporal alignment, future-append causality,
processing boundaries, DST-aware local times, and checkpoint recovery.

Results pending execution and validation.
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
Results pending execution and validation.
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

Results pending execution and validation.
<!-- NOTEBOOK_03:END -->

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
