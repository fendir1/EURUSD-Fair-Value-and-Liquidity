Origin: Raymond Fendi proposed that a price move accompanied by substantial
available size might carry information about continuation.

Timing: formulated after reviewing the Notebook 04 pilot results.
This is exploratory, not a hypothesis registered before that pilot.

## Research question

Does the side, relative magnitude, and persistence of displayed size improve
forecasts conditional on a preceding price move and liquidity conditions?

## Proposed first specification

- Preceding move: 30-second trailing mid-price change.
- Primary future horizon: 5 seconds.
- Nonzero preceding moves define a direction; zero moves are reported separately.
- Direction-aligned imbalance:
  sign(preceding move) multiplied by current size imbalance.
- Persistence candidate: direction-aligned mean imbalance over the preceding
  10 sampled seconds, including the current sample, with valid size coverage.
- Relative supporting-side size: bid size for an upward preceding move,
  ask size for a downward move, normalized using the corresponding
  training-only side-size median.

The continuation hypothesis is that the expected direction-signed future
price change increases with direction-aligned size support, conditional on
preceding move magnitude, spread, volatility, quote activity, and staleness.

Substantial opposing-side size may instead be associated with resistance
or absorption. These are hypotheses, not established mechanisms.

## Planned comparison safeguards

Before execution, freeze the exact implementation, development dates,
numerical zero tolerance, controls, and limited comparison budget.

Use identical training and evaluation observations for each ablation.
Fit normalization and any thresholds on training data only.

Evaluate incremental forecast errors and conditional continuation estimates.
Use time-block uncertainty and report stability across development periods.
Report freshness and carried-forward-snapshot sensitivity.


## Observability limits

Displayed size is available quoted quantity, not executed trade volume.
Changes in quoted size do not identify executions, cancellations,
replenishment, participant identity, or intent.

Persistence means persistence observed in this feed.
Export size denomination remains unresolved.

## Execution and review