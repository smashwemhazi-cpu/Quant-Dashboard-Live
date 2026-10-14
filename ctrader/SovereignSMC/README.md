# SovereignSMC (cTrader cBot)

An SMC (Smart Money Concepts) entry engine for XAUUSD on M5, built for a small account trading a fixed 0.01 lot. It compiles cleanly against `cTrader.Automate` 1.0.21 with no warnings. **It has not been backtested.**

## Why it exists
V83's log printed the dollar risk of every setup it had to skip. The median was **$14.56**, which needs about $485 of equity at 3% risk. A market entry at the pullback close sits too far from the swing stop for a $210 account.

SMC fixes the distance between entry and stop, not the risk rule. The bot waits for a liquidity sweep and a displacement candle that breaks structure. It then places a **limit order back inside the fair value gap (FVG)**. The stop still sits behind the sweep wick, but the entry is much closer to it, and limit entries pay no entry slippage.

## Setup (long; shorts are the mirror image)
1. **Bias:** H1 close above the H1 200 EMA, and the EMA rising.
2. **Liquidity:** confirmed pivot lows within the last 48 bars, plus today's Asian-range low. The level must not have been traded through since it formed.
3. **Sweep:** a candle wicks below the level and closes back above it.
4. **CHoCH (change of character):** within 12 bars, a candle closes above the last pivot high before the sweep, with a body of at least 0.8 × ATR. If price instead closes 0.5 × ATR below the swept level, the setup is cancelled.
5. **FVG (fair value gap):** the largest 3-bar gap in the displacement leg.
6. **Order:** a limit at the middle of the gap. The stop goes beyond the sweep wick, plus 0.2 × ATR and the spread, and is never closer than the $4 slippage floor. If the risk is too big at the middle of the gap, the bot tries the far edge; if it's still too big, the trade is **skipped**.
7. **Target:** the external high before the sweep, capped at 3R. Setups that offer less than 1.5R are skipped.
8. **Expiry:** the order lasts 12 bars. It's cancelled if price reaches the target or the stop before it fills.

## Risk and execution
- Each trade is checked against the % of equity it risks, and skipped if it's over the cap.
- The stop can't be tighter than the slippage floor.
- The daily loss count and daily cash loss are rebuilt from History, using net P/L.
- Equity floor.
- Trading windows 08:00–11:00 and 14:00–16:30 UTC.
- News blackout times.
- Spread and quote checks.
- DoubleTick stops.
- Everything is flattened at 20:30 UTC, and at 19:30 on Fridays.
- Every order, close and cancel result is checked, and a position that fills without a stop is closed.

## Reading the log
- `CONFIG` prints the **affordable stop window**, for example $4.00–$6.22 at $210 and 3%. If the floor is above the cap, a warning says no trade can pass.
- `ORDER` / `FILLED` / `CLOSED` lines give the price levels, risk, reward-to-risk ratio and the R result.
- `RUN SUMMARY funnel` counts each stage:
  - sweeps → CHoCHs → gaps found → placed → filled → expired
  - plus every reason a setup was blocked
- `risk-skipped` gives the dollar risk of the setups that were too expensive.
- `stop slippage` totals what was lost when stops filled past their level.

## Known weaknesses to measure
- **Adverse selection.** Limit orders fill most often when price comes back through the level, so the losing trades always fill. Some winners run away without ever retracing and never fill. Compare expired orders against filled ones.
- **Definitions.** SMC terms are loosely defined. Every rule here is fixed in code, so don't tune them one by one on the same data.
- **Test protocol.** Backtest 2025 and 2026 at the real balance with $8/lot commission, then test 2024 untouched. Pass if PF ≥ 1.3 with at least 60 trades and the run is profitable in 2 of the 3 years. Run Monte Carlo with ruin set to the equity floor. Then 4–8 weeks on demo.
