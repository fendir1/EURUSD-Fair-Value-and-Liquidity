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
