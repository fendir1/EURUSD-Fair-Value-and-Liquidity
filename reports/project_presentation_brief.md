# EURUSD Fair-Value and Liquidity Research — presentation brief

## Slide 1 — Research question
Can Level 1 quotes and displayed sizes improve short-horizon mid-price forecasts
and describe deteriorating liquidity conditions?

Audience: electronic FX market-making research.
Scope: research prototype; no profitability claim.

## Slide 2 — Dataset
- 293,093,358 canonical EURUSD quote records.
- Source coverage: 2016-01-03T22:00:01.446000+00:00 to 2026-10-16T20:59:59.899000+00:00.
- Complete-month research evaluation ends October 1, 2026.
- One-second sampling; maximum snapshot age 30 seconds.
- Single-feed limitations and unresolved displayed-size denomination.

## Slide 3 — Leakage controls
- Backward-looking quotes and causal features.
- Exact-horizon targets and gap rejection.
- Training-only preprocessing and regime thresholds.
- Purged training targets.
- Matched model comparison populations.
- Frozen 12-month final test.

## Slide 4 — Models
- Zero-change benchmark.
- Trailing-return benchmark.
- Price-only Ridge.
- Price-plus-size Ridge.
- Up to 365 days of training; no parameter search.
- Mean imputation supports bounded full-history fitting.

## Slide 5 — Measured final-test results
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

## Slide 6 — Does displayed size help?
| horizon_seconds | delta_mae_size_minus_price_pips | delta_rmse_size_minus_price_pips |
| --- | --- | --- |
| 1 | 0.000486 | -0.000055 |
| 5 | 0.000116 | -0.000055 |
| 30 | 0.000062 | 0.000022 |
| 60 | 0.000088 | 0.000050 |

- 1-second horizon: lowest MAE = `zero`; lowest RMSE = `ridge_price_size`.
- 5-second horizon: lowest MAE = `zero`; lowest RMSE = `ridge_price_size`.
- 30-second horizon: lowest MAE = `zero`; lowest RMSE = `zero`.
- 60-second horizon: lowest MAE = `zero`; lowest RMSE = `zero`.
- Adding displayed-size predictors reduced final-test MAE at 0/4 horizons and RMSE at 2/4 horizons.
- Point-estimate rankings must be read alongside the paired block intervals.

Use `displayed_size_contribution.png` and the paired block interval table.
Discuss economic magnitude and stability, not only the sign of an error difference.

## Slide 7 — Liquidity and stability
Use:
- `annual_stability.png`;
- `by_regime.csv`;
- `by_session.csv`;
- `by_snapshot_age.csv`;
- `annual_dataset_coverage.csv`.

Training-derived regime labels describe relative conditions.
They are not universal labels or automatic exclusions.

## Slide 8 — Pricing implications and limitations
Forecast performance alone does not justify a live pricing adjustment.
Any proposed adjustment would need a separate execution, latency, cost,
liquidity, inventory, and risk assessment.


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


## Slide 9 — Conclusion
Present the measured ranking and uncertainty, including negative findings.
Distinguish the evidence from interpretation.

H001 remains an optional future hypothesis, not a completed finding.

## Slide 10 — Development and validation
Development approach: This is an AI-assisted research project.
Raymond Fendi defined the business objectives and provided FX domain
direction. OpenAI Codex assisted with implementation, testing, and
documentation. CONTRIBUTIONS.md records completed design decisions,
reviews, and validation.

Automated validation: 79 passed in 8.77s.
Owner review of the final notebook remains pending.
