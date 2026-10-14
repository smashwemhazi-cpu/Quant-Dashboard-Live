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

## Quick-trade (M1) variant: `quick-m1.cbotset`
Run it on an **M1** chart. Structure on M1 is smaller, so most stops from the gap to the sweep wick should be under $4. Those get widened to the $4 floor, giving a risk of about $4.08, which fits even a $150 account at 3%. Other changes:
- 3-hour liquidity lookback
- 15-bar CHoCH window
- displacement of 0.7 × ATR
- 20-minute order expiry
- killzones 07:00–11:00 and 13:30–16:30 UTC
- verbose setup log

Targets stay at 1.5–3R, so each winner still needs a move of $6 or more. More trades also means the costs show up faster, so judge it on profit factor after commission, not on trade count.

## Run 1: M1, 6 Jan – 14 Oct 2026, $150, edited parameters. 10 trades, −$14.92, PF 0.46
- **Funnel:** 18,446 sweeps, 3,701 CHoCHs, 2,075 FVGs, 1,065 skipped for risk (median $5.41, so about $180 of equity needed), 35 orders placed, 10 filled, 20 expired, 5 cancelled.
- **Adverse selection showed up:** some orders missed winners that ran without filling (the old counter mixed these with stop-broken cancels). In the filled trades, 4 of 10 sells filled 1.2–5.9 above the limit price, after price pushed through the gap. One filled beyond its own stop and was closed straight away.
- **M1 stop slippage:** median $1.06 across 8 stops, about 26% of the $4 risk. That's above the $0.80 assumed for M5. The quick M1 settings now use $1.05, which makes the floor about $5.25.
- **Breakeven at 1R** (a panel setting; the default is off) turned two trades into scratches.
- **Code changes:**
  - A pending order is now cancelled if a bar closes through the far edge of its gap.
  - Missed winners and stop-broken cancels are now counted separately.
  - Fills that gapped past the stop are now counted.
- **Next test:** load `recommended.cbotset` on **M5** with **$210** starting capital and label `SovSMC`. A sample of 10 trades can't show whether there's an edge.

## Run 2: M5 at $200, zero trades
A $200 account can risk about $5.92 per trade. On M5, the distance from the middle of the gap to the sweep wick is usually more than that, so the setups were skipped as too risky. **New `Stop Mode` setting (default `Auto`):**
- Try the sweep-wick stop first.
- If that's too expensive, put the stop just beyond the far edge of the gap instead, never closer than the $4 minimum stop.

A bar that closes through that edge already cancels the pending order, so the stop sits at the level where the setup is proven wrong. Each `ORDER` line in the log and the run summary show which kind of stop was used. Compare the results of the two stop types before trusting the gap stop.

## Run 3: M5 at $200, Stop Mode Auto. 5 trades, 0 wins, −$19.23
- **Bug found:** 9 of the 15 orders were cancelled about 1 minute after being placed as "target hit before fill". Price was already at the target when each order went in. They weren't missed winners; they were stale setups. The cause was the bot choosing the largest gap in the move, which was often far behind price.
  - **Fix:** use the latest gap, and skip a setup once price has already covered more than `Max Chase` (50%) of the distance to the target.
- **Stop slippage:** median $1.31 on 5 stops, about 32% of the $4 risk.
- **Breakeven at 1R** (a panel setting) again turned two +1R trades into −0.39R losses.
- Settings in the run differed from the recommended file: ATR 9, H1 EMA 50, stop buffer 0.5, label SovV83.
- **New:** the previous day's high and low are now used as liquidity levels. `Liquidity Pools = MajorOnly` restricts setups to the Asian range and the previous day's high/low, so it gives fewer setups. Use `major-pools.cbotset` to A/B test it.
- **Sample size:** the SMC runs so far total 15 filled trades with 2 wins. That isn't enough to judge the strategy. Test 1 Jan 2025 – 14 Oct 2026 to get more trades.

## Run 4: M5, Jan 2025 – Oct 2026, $200. 6 trades, 0 wins, −$13.14
- **Funnel:** 8,563 sweeps, 1,607 CHoCHs, 862 gaps, and 11 orders placed in 21 months. Of the gaps, 305 offered too little reward-to-risk, 263 were blocked by the H1 bias filter, 149 fell outside the sessions, 114 were too risky, and 18 had already run.
- **Breakeven at 1R** (a panel setting) fired on 4 of the 6 trades, and all 4 closed between −0.07R and −0.37R.
- **Median stop slippage was $1.33**, about 33% of the $4 risk. SMC stops sit just past swept liquidity, which is where price moves fastest, so they fill worse than the earlier bots' stops ($0.65–0.95).
- **Bug fixed:** when price jumped past the limit price, the order filled better than requested. That left the stop well inside the $4 minimum distance (in one trade only 1.05 away). The stop is now moved back out to the $4 minimum on fill.
