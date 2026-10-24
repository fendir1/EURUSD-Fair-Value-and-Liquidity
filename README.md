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
