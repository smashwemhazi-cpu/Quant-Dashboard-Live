# MicroLotBreakoutBot (cTrader cBot)

An intraday cBot for **small accounts at a fixed 0.01 lot**, designed around a ~$200 account on XAUUSD. It runs two engines, one for each market regime, and every trade is held within a cash risk cap.

It compiles cleanly against the official `cTrader.Automate` 1.0.21 package. It has **not** been backtested yet.

## Why it's built this way
At a fixed minimum lot (1 oz of gold, so $1 per $1 move), position size can't change. **The only risk control is how far away the stop is**, so every rule below exists to keep stops small without putting them inside normal market noise.

## The two engines (they never overlap)
The H1 50 EMA's slope over 5 bars, measured in H1 ATRs, sorts each moment into one of two regimes:

| Regime | Engine | Entry | Stop | Target |
|---|---|---|---|---|
| **Trending** (slope > 0.3 ATR, price on the same side) | **Breakout** | Stop-limit order beyond a compression box (12 bars, 0.8–3.0 ATR tall), in the trend's direction | Opposite side of the box | 2R |
| **Flat** (slope ≤ 0.3 ATR) | **Range fade** | Market order when a bar closes back inside the Bollinger Bands (20, 2.0) after the previous bar closed outside | Beyond the outside bar's extreme + 0.25 ATR | The middle band, which must be at least 1R away |

Each regime has its own engine, so adding the fade **increases how often the bot trades without changing the breakout logic**. Either engine can be switched off.

## Stops and the risk cap
- **Risk cap:** 3% of equity per trade (about $6 on $200), or a fixed dollar cap.
- **`Stop Mode = CappedAtRisk`** (default): if the structural stop costs more than the cap, the stop is **tightened to the cap**. It's never tightened below `Min Stop ATR` (1.0 ATR); if the cap is smaller than that, the trade is skipped.
- **`Stop Mode = Structural`**: never tightens. Any trade whose structural stop exceeds the cap is skipped. This version made **zero trades** in your first backtest.
- **Breakeven** at +1R. Everything is closed at 20:30 UTC (19:30 on Fridays): no swap, no weekend gap.

## Cost hurdle
A trade is taken only if its target is at least **3× the round-trip cost** (spread + `Commission $ per Lot`). **Enter your broker's commission here.** This rule stops the extra trades from simply paying the spread.

## Risk limits
| Limit | Default |
|---|---|
| Trades per day | 4 (shared by both engines) |
| Daily loss cap | 6% of balance |
| Losses in a row in one day | 2, then no more trades that day |
| Equity floor | $120: closes everything and stops the bot |
| Spread | ≤ 6 pips and ≤ 15% of the stop |
| Quotes | Ignored if frozen (older than 60 s) or invalid (zero, NaN or crossed) |
| News blackout | Manual list of UTC times, ±30 minutes |

## Diagnostics: why it did or didn't trade
- **At start, `FEASIBILITY`:** how much a 1-ATR stop costs, how many ATRs your risk cap covers, and what percentage of bars could take a trade at all.
- **When the run ends, `RUN SUMMARY`:** orders placed, trades closed, and **a count for each rejection reason**, sorted by frequency. A zero-trade run now explains itself. Set `Print Each Rejection = Yes` to log every rejection individually.

## How to test
1. Attach it to **XAUUSD.mp M5** and backtest with a **$200** starting balance, tick data, and commission entered (both in the broker settings and in `Commission $ per Lot`).
2. Read `FEASIBILITY` and `RUN SUMMARY` in the Log tab before looking at profit and loss.
3. Compare three runs: breakout only, fade only, and both. Each engine should hold up on its own; if one loses money, switch it off rather than tuning it until it fits the past.
4. Repeat on USOIL.mp and US500.mp.

## Backtest review #1 (XAUUSD.mp M5, Jan–Oct 2026, $180)
117 trades, net −$40.18 (−$30.82 gross, −$9.36 commission). Win rate 35.9%, average win $8.19, average loss $5.00, profit factor 0.92. The t-stat is −0.42: **no evidence of an edge either way**. Changes made because of this run:

| Finding | Evidence | Change |
|---|---|---|
| **Stop slippage was the biggest cost** | Losing trades averaged −1.21R instead of −1R; slippage past the stop totalled ≈ $62, about twice the gross loss | `Min Stop ATR` raised from 1.0 to 1.5, so the same slippage is a smaller share of R |
| Stops were inside the noise | Median time to stop-out was about 6–10 minutes | Same change as above |
| Session opens lost money in **both** halves of the year | Jan–May PF 0.61, Jun–Oct PF 0.74, at 07–08 and 12–13 UTC | Sessions changed to 08:00–11:00 and 14:00–16:30 UTC |
| 09–10 UTC *looked* great, but didn't hold up | PF 3.24 in Jan–May, then 1.04 in Jun–Oct | **Not** used as a filter: it was a curve-fit |
| Breakeven move rejected 8 times | `InvalidStopLossTakeProfit` when price had already passed the target | The move is now skipped when the target is about to fill |
| No compounding | Volume was fixed at 0.01 lot | `Sizing Mode = RiskPercent` adds whole volume steps once the risk budget covers them |

Run `python3 ctrader/tools/analyze_events.py events.json 2026-06-01` on every new export.

## Honest limits
- 3% risk per trade on $200 is aggressive: 10 losses in a row cost about 26%.
- Stops tightened by `CappedAtRisk` get hit more often. Whether the higher trade count still makes money after costs is exactly what the backtest has to show. The design doesn't guarantee it.
- Session times are fixed in UTC and don't adjust for daylight saving. News times must be entered by hand.
