# SovereignCommander V83 (cTrader cBot)

A rebuild of the V82 "DynamicConclave" M5 gold pullback bot. The **signal is unchanged in spirit**: a pullback to the 9 EMA, with the 9 EMA above the 21 (below for shorts) and price on the right side of the H1 200 EMA. Everything around the signal has been replaced with the risk and execution layers that were validated in `MicroLotBreakoutBot`.

It compiles cleanly against `cTrader.Automate` 1.0.21 with no warnings. It has **not** been backtested.

| V82 | V83 | Why |
|---|---|---|
| Minimum stop of $2.50 | Stop is at least the larger of 1.5 × ATR and the slippage floor ($0.80 ÷ 0.20 = **$4**) | About $0.65–0.95 is lost per stop. On a $2.50 stop that's 25–40% of R. |
| Stop clamped to a $7 maximum | A stop wider than 4 × ATR **skips** the trade | Clamping puts the stop inside the structure it's meant to protect. |
| Pullback band of 0.08% of price | `0.25 × ATR` | A percentage of price ignores volatility, and the band drifts as gold's price changes. |
| Fixed hours, Asian session on | Sessions 08:00–11:00 and 14:00–16:30 UTC; Asian session off; news blackout list | The opens and the thin Asian hours were the losing hours in our data. |
| `ExecuteMarketOrder` with no slippage cap, `tp = 0` | `ExecuteMarketRangeOrder` with a slippage cap, spread and quote checks, retries only on transient errors, `null` TP, DoubleTick stops | DoubleTick stops won both A/B tests. |
| Loss counter kept in memory, gross P/L, counts every bot's trades | Loss count **and** a daily cash limit, both rebuilt from `History` using this bot's label, symbol and **net** P/L | Survives restarts, includes commission, and other bots' trades no longer interfere. |
| No equity-based risk limit | Max risk % per trade (skip if exceeded), equity floor, margin check via `Symbol.GetEstimatedMargin` | Fixed 0.01 lot is still the default, but each trade is checked against equity. |
| Trail = price − 1.8 × ATR, checked every tick | Breakeven plus commission at 1R; chandelier trail from the high or low since entry, 2.5 × ATR from 1.5R, updated on closed bars | Fewer modify calls, no reaction to a single spike tick, and gives the trade more room. |
| Results of close and modify calls not checked | Every `TradeResult` is checked; a missing stop is re-attached, or the position is closed | |
| No diagnostics | `CONFIG` printed at start, `FILLED` / `CLOSED` (with R) per trade, `RUN SUMMARY` with skip reasons at stop | |

Test it the same way as before: 2024 untouched as the unseen year, commission entered, tick data, then a Monte Carlo run with ruin set to the equity floor.

## Run 1 (6 Jan – 14 Oct 2026, $150, edited parameters): 4 trades, −$10.28
- **The risk cap blocked almost everything.** 1,922 skips said "risk above max %". At $150, 3% is $4.50. The minimum stop is $4 plus about $0.08 commission. So any swing stop wider than about $4.40 was skipped. Typical M5 swing stops are wider than that.
- **The exits had no take-profit.** Breakeven at 1R fired on 2 of the 4 trades, and both closed as scratches (−$0.19 and −$0.04). The trail never moved. The two trades that never reached breakeven lost 1.07R and 1.45R; the second had $1.70 of stop slippage.
- **Fixes in the code:**
  - TP now defaults to 1.5R and breakeven is off.
  - The same breakeven modify was sent twice; that's fixed.
  - The signal count now includes only fresh setups inside a session.
  - The summary now prints the risk-$ distribution of skipped setups and the equity each would need.
- Re-test from `baseline.cbotset`: ATR 14, H1 EMA 200 and the new exits, with starting capital set to the real balance (about $210).
