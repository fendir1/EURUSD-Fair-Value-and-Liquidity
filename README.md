# EURUSD Fair Value and Liquidity Research

[Read the four-page project guide](reports/EURUSD_research_plain_language_guide.pdf)

A plain language project presentation and column reference

### Objective and the decisions it could inform

We asked whether euro-dollar quotes and the quantities displayed beside them help predict price changes over the next 1, 5, 30, or 60 seconds, and identify conditions in which trading becomes harder or riskier.

A dealer normally offers one price to buy euros and another to sell them. A reliable upward forecast could support moving both quoted prices upward; signs of thinner or less stable trading could support quoting smaller amounts, widening the buy-sell gap, or requesting a fresh price. These were potential decisions to investigate, not strategies proved profitable by this project.

| Expected research outcome | Potential trading decision |
| --- | --- |
| A reliable short-term price forecast | Adjust the reference price around which buy and sell quotes are set. |
| A useful warning about liquidity | Review quote size, quote width, or whether the displayed price is fresh enough. |
| Evidence that displayed size adds information | Include size in the pricing signal only if its contribution is reliable and meaningful. |

### What the initial data contain

The starting point was seven Dukascopy exports **293,093,358 quote records**, occupying about **14.50 GB** of CSV files. Their observed times run from January 3, 2016, to October 16, 2026, in UTC.

Each tick is one combined bid-and-ask snapshot with a timestamp, two prices, and two displayed quantities. It is not a completed trade. The records arrive at irregular intervals; some consecutive records can repeat the same prices.

The workflow interprets it using Europe/Helsinki daylight-saving rules, then converts to UTC; the documented verification limits remain. The displayed-size unit has not been independently confirmed. One feed does not describe the entire currency market.

### A simple example

A bid of 1.1000 and an ask of 1.1002 imply a midpoint of 1.1001 and a spread of 2 pips. The midpoint is a convenient reference; a trader generally cannot buy and sell at that midpoint.

## How the project worked and what it found

### From irregular ticks to comparable observations

The workflow kept one research row per second, always using the latest quote already observed. Quotes older than 30 seconds were unavailable. It calculated recent price behavior and size measures, then recorded the actual future price change at each forecast horizon. Gaps interrupt calculations that require a continuous price path.

The completed research dataset has **339,033,600 clock rows**, with **235,598,561 usable current-price rows**. It ends before October 1, 2026; the remaining partial October source month was not scored.

### Four forecasting methods

**Zero change** predicts no move. **Trailing return** predicts a repeat of the recent move. **Price-only Ridge** learns a restrained linear combination of price and time features. **Price-plus-size Ridge** adds displayed-size features to the otherwise matched model.

Models learned from up to 365 preceding calendar days. Nine chronological development periods covered 2017 through September 2025. Choices were then frozen before the final test, October 2025 through September 2026; training and evaluation label intervals were kept separate.

### Final test results

| Horizon | Zero MAE | Size model MAE | Zero RMSE | Size model RMSE |
| --- | --- | --- | --- | --- |
| 1 sec | 0.056418 | 0.057664 | 0.144122 | 0.144049 |
| 5 sec | 0.178874 | 0.179710 | 0.328184 | 0.328135 |
| 30 sec | 0.528175 | 0.528705 | 0.828463 | 0.828519 |
| 60 sec | 0.779690 | 0.780176 | 1.197345 | 1.197447 |

All errors are in pips; lower is better. MAE is the average mistake size. RMSE gives larger mistakes extra weight.

**No-change had the lowest MAE at every horizon.** The size model slightly improved RMSE over no-change at 1 and 5 seconds, by about 0.05% and 0.015%; it did not improve the longer horizons. Adding size worsened the aggregate development errors, so its contribution was not consistent across development and final testing.

### What this means for trading decisions

These results do not establish a dependable practical advantage for forecast-driven quote changes. The liquidity measures can describe conditions for further investigation, but no execution, costs, fill probability, or inventory policy was tested.

## Important market data columns

Values are rounded for readability; this single example illustrates the columns and is not evidence of typical performance.

| Column name | Saved example value | What the value means |
| --- | --- | --- |
| Time (EET) -> timestamp_source | 2025.10.01 03:00:09.860 | Original quote time. Here 03:00:09.860 local time becomes 00:00:09.860 UTC. |
| Ask -> ask | 1.17341 | A buyer could see an asking price of 1.17341 US dollars per euro on this feed. |
| Bid -> bid | 1.17336 | A seller could see a bid of 1.17336 US dollars per euro on this feed. |
| AskVolume -> ask_size | 0.9 | Quantity displayed at the ask. Its denomination is unconfirmed; this is not completed trade volume. |
| BidVolume -> bid_size | 1.8 | Quantity displayed at the bid. Here the displayed bid quantity is twice the ask quantity. |
| mid_price | 1.173385 | Average of bid and ask, used as the reference price. It is not a guaranteed execution price. |
| spread_pips | 0.50 pips | The buy-sell price gap is half a pip. One pip equals 0.0001 dollars per euro. |
| feature_timestamp_utc / source_timestamp_utc | 00:00:10 / 00:00:09.860 | Research clock time and the latest source quote already known at that time. Both are UTC. |
| snapshot_staleness_seconds | 0.14 seconds | The quote is 0.14 seconds old at the research time, below the 30-second maximum. |
| price_valid | True | The current quote passes the price and age rules. This does not establish whether it can be traded. |
| size_imbalance | +0.3333 | More size is displayed at the bid: (1.8 minus 0.9) divided by 2.7. Positive imbalance does not prove the next move will be up. |
| size_weighted_quote_price / size_weighted_minus_mid_pips | 1.17339333 / +0.083333 pips | The size-weighted candidate price is 0.083333 pips above the midpoint. It is not a proven fair value. |
| mid_change_5s_pips | -0.15 pips | The midpoint fell 0.15 pips over the preceding five seconds. This describes the past. |
| realized_vol_60s_pips / metric_volatility_pips | 1.078193 pips | Summarizes movement over the previous 60 seconds using squared one-second changes. It is not a forecast for the next minute. |
| snapshot_count_60s / metric_quote_count | 30 | Thirty quote records arrived in the preceding 60 seconds. This is quote activity, not a trade count. |
| metric_spread_mean_pips | 0.418333 pips | Average spread during the preceding 60 seconds, compared with the current spread of 0.50 pips. |
| metric_min_side_size_mean | 0.93 | Average of the smaller displayed side quantity over the past 60 seconds, in the unconfirmed source units. |
| liquidity_regime | normal | Neither the stressed nor thin rule triggered here. This category is not a guarantee that trading is safe. |
| session | other | Outside both defined London and New York sessions: local weekday hours 08:00 to 17:00. |

## Important forecast and result columns

The same observation, with a five-second forecast horizon. Other saved horizons are 1, 30, and 60 seconds; their column names follow the same pattern.

| Column name | Saved example value | What the value means |
| --- | --- | --- |
| horizon_seconds | 5 seconds | How far ahead the forecast looks; the suffix 5s means the same thing. |
| target_timestamp_utc_5s / target_source_timestamp_utc_5s | 00:00:15 / 00:00:14.702 | Exact future assessment time and the last quote observed before it. Both are UTC; no later quote is substituted. |
| target_mid_price_5s | 1.173345 | The midpoint observed at the five-second target, using the permitted source quote. |
| label_5s_pips / actual_5s | -0.40 pips | The actual future midpoint fell 0.40 pips from 1.173385 to 1.173345. This is an outcome, not a predictor. |
| label_valid_5s | True | The current and target prices, plus the intervening path, pass the label rules. |
| eligible_5s | True | This row also passes the shared model-comparison rules, including required sizes and the evaluation boundary. |
| zero_5s | 0.00 pips | The simplest benchmark predicts no change. |
| trailing_5s | -0.15 pips | The recent-move benchmark predicts another 0.15-pip fall, repeating the preceding five-second move. |
| ridge_price_5s | -0.012716 pips | The price-only model predicts a small fall, about 0.012716 pips. |
| ridge_price_size_5s | -0.010545 pips | Adding displayed-size features changes the prediction to a fall of about 0.010545 pips. |

### Whole final test results for the five second size model

These values summarize the full final-test sample, not the single observation above. Error differences below compare the size model with the price-only model.

| Column name | Saved example value | What the value means |
| --- | --- | --- |
| phase / rows | final_test / 21,586,600 | The reserved final test, with 21,586,600 shared observations for the five-second model comparison. |
| mae_pips | 0.179710 pips | Average mistake size was about 0.18 pips, regardless of direction. Lower is better. |
| rmse_pips | 0.328135 pips | Error measure giving extra weight to larger mistakes. Lower is better. |
| delta_mae_size_minus_price_pips | +0.000116 pips | Positive means adding size increased average absolute error slightly. |
| delta_rmse_size_minus_price_pips | -0.000055 pips | Negative means adding size reduced this error measure slightly. It does not establish an executable trading benefit. |

### Project record

Values come from the completed Notebook 09 run 66d2e5a9ff903fe48113c6ea and its saved research, forecast, and evaluation outputs. The earlier January pilot is superseded for full-history conclusions.

Development approach: This is an AI-assisted research project. Raymond Fendi defined the business objectives and provided FX domain direction. OpenAI Codex assisted with implementation and testing. 

## Reproduce the research

The source tick history is not distributed in this repository. Rebuilding the complete results requires the source data and substantial runtime and disk space. Notebook outputs are cleared in Git; completed aggregate results remain in `reports/final_aggregate_metrics.csv` and `reports/research_results.md`.

The final test has been evaluated. Any later model selection must not describe these dates as an untouched test set. Owner review remains pending.
