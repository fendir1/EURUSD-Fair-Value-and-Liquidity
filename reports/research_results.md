# Research results

Measured results are recorded in the generated completion section below.

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
## Completed research run

Run ID: `66d2e5a9ff903fe48113c6ea`.

Development evaluation: 2017-01-01 through 2025-10-01, end exclusive.
Final test: 2025-10-01 through 2026-10-01, end exclusive.

Training: up to 365 preceding calendar days; annual development refits.
Final-test models were frozen before scoring and held fixed during the final test.

### Measured final-test results

| horizon_seconds | model | rows | mae_pips | rmse_pips | mae_gain_vs_zero_pips | rmse_gain_vs_zero_pips |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | ridge_price | 21836932.000000 | 0.057179 | 0.144103 | -0.000760 | 0.000018 |
| 1 | ridge_price_size | 21836932.000000 | 0.057664 | 0.144049 | -0.001246 | 0.000073 |
| 1 | trailing | 21836932.000000 | 0.094768 | 0.202062 | -0.038350 | -0.057940 |
| 1 | zero | 21836932.000000 | 0.056418 | 0.144122 | 0.000000 | 0.000000 |
| 5 | ridge_price | 21586600.000000 | 0.179594 | 0.328191 | -0.000721 | -0.000007 |
| 5 | ridge_price_size | 21586600.000000 | 0.179710 | 0.328135 | -0.000837 | 0.000049 |
| 5 | trailing | 21586600.000000 | 0.279336 | 0.463739 | -0.100462 | -0.135555 |
| 5 | zero | 21586600.000000 | 0.178874 | 0.328184 | 0.000000 | 0.000000 |
| 30 | ridge_price | 20221145.000000 | 0.528643 | 0.828497 | -0.000468 | -0.000034 |
| 30 | ridge_price_size | 20221145.000000 | 0.528705 | 0.828519 | -0.000530 | -0.000056 |
| 30 | trailing | 20221145.000000 | 0.772103 | 1.171220 | -0.243928 | -0.342756 |
| 30 | zero | 20221145.000000 | 0.528175 | 0.828463 | 0.000000 | 0.000000 |
| 60 | ridge_price | 19152419.000000 | 0.780088 | 1.197397 | -0.000398 | -0.000052 |
| 60 | ridge_price_size | 19152419.000000 | 0.780176 | 1.197447 | -0.000486 | -0.000102 |
| 60 | trailing | 19152419.000000 | 1.128350 | 1.695817 | -0.348660 | -0.498472 |
| 60 | zero | 19152419.000000 | 0.779690 | 1.197345 | 0.000000 | 0.000000 |

### Displayed-size comparison

Positive differences mean the size model has higher error.

| horizon_seconds | delta_mae_size_minus_price_pips | delta_rmse_size_minus_price_pips |
| --- | --- | --- |
| 1 | 0.000486 | -0.000055 |
| 5 | 0.000116 | -0.000055 |
| 30 | 0.000062 | 0.000022 |
| 60 | 0.000088 | 0.000050 |

### Measured findings

- 1-second horizon: lowest MAE = `zero`; lowest RMSE = `ridge_price_size`.
- 5-second horizon: lowest MAE = `zero`; lowest RMSE = `ridge_price_size`.
- 30-second horizon: lowest MAE = `zero`; lowest RMSE = `zero`.
- 60-second horizon: lowest MAE = `zero`; lowest RMSE = `zero`.
- Adding displayed-size predictors reduced final-test MAE at 0/4 horizons and RMSE at 2/4 horizons.
- Point-estimate rankings must be read alongside the paired block intervals.

### Interpretation and pricing relevance

The results measure forecast errors and conditional liquidity states.
Any reliable improvement would motivate further pricing research.
Weak, unstable, or negative improvements do not support adding forecast-driven
pricing adjustments on the evidence of this experiment.

The regime tables describe conditions under transparent training-derived rules.
They do not establish that a stressed label is a data error or a causal predictor.

Forecast magnitudes are compared with prevailing spreads in the saved tables.
This comparison does not establish executable returns.

### Validation

- Project tests: 79 passed in 8.77s.
- Real cross-source continuous-versus-block calculation passed.
- Canonical source integrity and dataset row conservation passed.
- Training targets were purged at every evaluation boundary.
- Model comparisons used matched observations.
- Final-test protocol and model checksums were frozen before scoring.
- Owner review of Notebook 09 remains pending.

### Wide-spread investigation

Qualifying raw wide quotes: 478.
Observed episodes: 100.
Episodes exceeding 20 minutes: 0.
Automatic exclusions: zero.

Duration is measured conservatively between observed qualifying quotes and
requires no intervening non-wide record or gap above 30 seconds.

### Limitations


- This is a research prototype using one Dukascopy/JForex quote feed.
- Displayed sizes are not executed trade volumes; their export denomination remains unresolved.
- Europe/Helsinki timestamp interpretation retains its documented verification limits.
- True side-specific quote ages are unavailable.
- Thirty-second snapshot eligibility is a research assumption; held-snapshot diagnostics are reported.
- Annual fixed-window fits do not establish performance under alternative refit policies.
- Full-history fitting uses mean imputation; pilot fitting used median imputation.
- Liquidity thresholds and forecast buckets use a deterministic five-minute training sample.
- Bootstrap intervals are conditional on fitted forecasts and may not capture all dependence.
- Subgroup and multiple-model comparisons are descriptive.
- H001 remains untested and is not a validated finding.
- No execution, costs, queue position, fill probability, inventory policy, or market-making P&L is modeled.
- Predictive accuracy does not establish achievable trading profits.


### Future work

H001, alternative training/refit policies, stronger source-timezone confirmation,
and executable-price diagnostics are future experiments. None is represented
as completed here.
<!-- COMPLETION_RESULTS_END -->
