# Order-block research (XAUUSD, all-day, 2020–2026)

`obsim.py` backtests an order-block strategy on M1 bid bars with UTC timestamps. The rules were fixed before the first run.

## Rules
- **Structure:** M15. A swing point needs 3 bars on each side.
- **Bias:** trade only in the direction of the last closed H1 bar relative to the H1 EMA 50.
- **Break of structure:** an M15 close beyond the last unbroken swing point. The move must leave a fair value gap.
- **Order block:** the last opposite-colour candle at or before the extreme of that move.
- **Entry:** a limit order at the near edge of the block. No order if price is already back inside the block.
- **Stop:** beyond the far edge of the block, plus 0.2 ATR and the spread. Minimum stop 4.0. Skip if the stop is over $11.64 (3% of $388 at 0.01 lot).
- **Target:** 2R.
- **Order lifetime:** 32 bars. Cancel if price closes through the block or reaches the target before the order fills.
- **Hours:** new trades 00:00–20:00 UTC (Friday until 18:00). Everything closes at 20:45.
- **Limits:** at most 3 trades a day; stop for the day after 2 losses.
- **Costs:** spread 0.30, stop slippage 0.50, commission $0.07 per 0.01 lot.
- **Conservative fills:** on the bar an order fills, only the stop can be hit. If the stop and target are both inside one M1 bar, the stop is assumed.

## Results (fixed 0.01 lot)

| Run | Trades | Win % | Exp. R | PF | Net |
|---|---|---|---|---|---|
| Pre-registered M15, 2020–2025 | 860 | 34.0 | −0.167 | 0.76 | −$654 |
| Same rules, 2026 (Mar–Sep) | 11 | 27.3 | −0.235 | 0.68 | −$24 |
| Compounding at $388 / 3% | 123 | 25.2 | −0.370 | 0.56 | account fell to $131 by 2021 |

Sensitivity grid (timeframe 5m/15m/1h, entry depth 0 or 0.5 into the block, target 1.5R/2R/3R):
- **All 18 variants had negative expectancy.** The best was 1h, near edge, 1.5R: 110 trades, PF 0.94.
- **No variant was positive in more than 3 of the 6 years.**

## Why it fails
- **No edge before costs.** Costs average about 0.16R per trade, and the expectancy is −0.167R, so before costs the entries are close to a coin flip.
- **The edge would have to cover about 0.16R of costs per trade.** That is the threshold for any intraday gold setup with stops of $4–6.
- **In 2026 the account size blocks most setups.** Order-block stops on M15 are usually above the $11.64 cap at 0.01 lot, so only 11 of 308 setups could be traded.

This matches the earlier SovereignSMC result (sweep + CHoCH + FVG: about 20 fills, almost no winners). **Verdict: do not trade order blocks live with this account.**

Reproduce: `python3 obsim.py m1.pkl '{"fixed_lots":0.01,"max_stop":11.64}'`
