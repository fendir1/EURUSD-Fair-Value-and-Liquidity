# Feature definitions

All feature timestamps and source timestamps are UTC.

- Mid, bid, ask, and size-weighted quote price: USD per EUR.
- Spread and weighted quote displacement: pips; one pip is 0.0001.
- Displayed sizes and size changes: original provider units, unresolved.
- Size imbalance and log returns: dimensionless.
- Snapshot staleness and observed price-change ages: seconds.
- Snapshot counts: source records, not trades.

Return windows: [1, 5, 30, 60] seconds.
Volatility windows: [60, 300] seconds.
Activity windows: [1, 5, 30, 60, 300] seconds.
Spread/size-change windows: [1, 5] seconds.

Price changes compare t with t-h. Realized volatility sums squared
one-second pip changes over (t-W, t], then takes the square root.
All intervening price steps must be usable.

Snapshot counts include source records in (t-W, t], including different
updates sharing a timestamp. Insufficient initial history remains missing.

Observed bid/ask price-change ages are available only within
300 seconds. Invalid
comparisons and substantial source gaps reset knowledge. These are not
true side-specific quote ages.

London and New York sessions use local Monday–Friday hours
08:00 inclusive to 17:00 exclusive. These are research conventions.

The size-weighted quote price is a candidate feature.
No forecasting benefit or calibrated fair value has been established.

A spread above 20 pips for more than 1200 continuous seconds is the
owner's proposed investigation criterion. Its episode analysis remains
pending; it is not an exclusion rule here.

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

<!-- VOLATILITY_DECISION:BEGIN -->
### Confirmed volatility specification

Retain realized volatility from one-second mid-price changes over 60-
and 300-second trailing windows. No ten-second-return replacement is active.
<!-- VOLATILITY_DECISION:END -->
