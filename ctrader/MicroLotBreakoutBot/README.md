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

## Backtest reviews #2 and #3 → preset `RangeFadeV1`
- **Run #2** reused stale saved settings, so it repeated run #1 (116 trades, PF 0.91).
- **Run #3** used `Stop Mode = Structural`: 62 trades, all range fades, PF 0.87. The breakout engine placed **zero** trades, because its structural stops never fit the $5.40 cap.
- **Across runs 1 and 3** (`tools/compare_runs.py`):

| Slice | Run 1 | Run 3 |
|---|---|---|
| Range fade, excluding 07:00 and 12:00–13:59 UTC | 56 trades, **PF 1.39** | 38 trades, **PF 1.25** |
| 12:00–13:59 UTC (US data and New York open) | PF 0.47 | PF 0.30 |
| Range-fade buys vs sells | Inconsistent | Inconsistent |

The buy/sell split reflects which way gold moved that year rather than an edge, so it isn't used as a filter. **The new `Preset` parameter (default `RangeFadeV1`) applies the configuration that held up**, and because it's a new parameter it overrides stale saved instances. It is still in-sample: it has to be confirmed on a period never used before (see below).

## Backtest review #4: `RangeFadeV1` failed. Verdict: retire M5 gold for this account
The preset applied correctly (log: `PRESET RangeFadeV1 applied`). Result: 95 trades, **PF 0.91**, t-stat −0.40. The filtered slice from runs 1 and 3 suggested PF 1.25–1.39; it didn't hold up when traded.

**Why:** with a 2-trades-per-day cap and a daily loss streak stop, every trade changes which later trades happen. Removing 07:00 and 12:00–13:59 freed those slots for different 08:00 trades (38 trades, PF 0.65). The "edge" came from slicing one sequence of trades, not from the market.

| Run | Trades | PF | Slippage past stops | Net |
|---|---|---|---|---|
| 1 | 117 | 0.92 | ≈ $62 | −$40 |
| 2 | 116 | 0.91 | ≈ $62 | −$42 |
| 3 | 62 | 0.87 | ≈ $36 | −$29 |
| 4 | 95 | 0.91 | ≈ $56 | −$33 |

Four configurations, ≈ 390 trades, the same answer: **no edge after costs.** Stop slippage alone (≈ 25% of R on $4–5 stops) is larger than the net loss in every run. At a fixed 1 oz on a $180 account, stops can't be wide enough to make execution costs small relative to R. That constraint, not the parameters, is the problem. Further tuning on these nine months would be data mining.

## Gold fix: the signal works, stop execution doesn't
`tools/stop_slippage.py` on runs 1, 3 and 4 shows the entry signals were **profitable before stop slippage** in all three:

| Run | Net | Stop slippage | Net if stops filled at their price | Median slip per stop |
|---|---|---|---|---|
| 1 | −$30.82 | $67.72 | **+$36.90** | $0.60 |
| 3 | −$24.33 | $38.84 | **+$14.51** | $0.71 |
| 4 | −$25.18 | $66.61 | **+$41.43** | $0.71 |

The worst five fills account for 28–40% of all slippage. So the fix targets stop execution, not new signals:
- **`Stop Trigger = DoubleTick` (new default):** a stop triggers only after **two consecutive** prices beyond it, so a single-tick wick no longer stops the trade out at a bad fill. This uses cTrader's `StopTriggerMethod.DoubleTrade`.
- **Slippage accounting:** every stop-loss fill is measured. `STOP SLIPPAGE` lines flag bad fills, and `RUN SUMMARY` prints total, average and maximum slippage plus *net if stops had filled exactly*. Each run now shows directly whether execution is still the problem.

**How to test it:** run the same preset twice, once with `Stop Trigger = SingleTick` and once with `DoubleTick`, on **2025 data** (not 2026). Compare `RUN SUMMARY`. The double trigger has to win on net profit, not only on slippage: it also lets some losing trades run past the stop before they close.

## Limit ladder entries (`Entry Mode = LimitLadder`, the new default)
This copies the structure of the manual trades you showed: **several limit orders spread across a price zone, all sharing one stop beyond the zone.**

| H1 regime | Ladder | Zone | Shared stop | Target |
|---|---|---|---|---|
| Flat | **Fade the spike.** Sell limits above the upper Bollinger band (buy limits below the lower band) | From the band out to `Ladder Depth` × ATR | `Ladder Stop Beyond Zone` × ATR past the deepest order | Middle band |
| Trending | **Buy the pullback** in an uptrend (mirror in a downtrend) | Middle band down to `Ladder Depth` × ATR | Same rule | `Reward R` × the shallowest order's risk |

- **Limit orders fill at their price, or better.** There's no entry slippage. The stop can still slip, and the `RUN SUMMARY` slippage line tracks that.
- **Risk fit:** total risk if every order fills and the stop is hit must stay within `Max Risk Per Trade`. Shallow orders, which carry the most risk, are dropped first. Orders whose own stop distance would be under `Ladder Min Order Stop` × ATR are also dropped, so stops aren't placed inside normal price noise.
- **Order lifecycle:**
  - Unfilled ladders are cancelled and re-priced each bar.
  - A partly filled ladder keeps its remaining orders. That is the scale-in.
  - When a ladder position closes and none are left open, the rest of that ladder is cancelled.
- **Daily limits:** all fills from one ladder count as **one trade** toward the daily trade cap and the loss streak.

**Account-size note:** with the defaults (3 orders, 1.0 ATR depth, stop 0.75 ATR beyond), a full ladder risks about 3.75 ATR, roughly $12 at a typical in-session ATR of about $3.20. At 3% risk per trade, that needs about **$400 of equity**. On $180 (cap $5.40), the ladder usually shrinks to its 1–2 deepest orders. You still get the better entry price, but not the scale-in. Each `LADDER` log line shows exactly what was placed.

**Test:** run `Entry Mode = Market` and `Entry Mode = LimitLadder` with otherwise identical settings on **2025 data**. Then compare `RUN SUMMARY` and `tools/analyze_events.py` output. Keep the ladder only if it improves **net profit and profit factor**, not just win rate.

## Parameter review
- **The `RangeFadeV1` preset is removed.** It silently overwrote what you set in the panel (minimum stop 1.0 ATR, ATR 20, 2 trades a day, fixed hours), and backtest #4 showed it lost money (PF 0.91). Every value in the panel now applies as shown, and the `CONFIG` lines print what was actually used.
- **Commission default changed from $0 to $8 per lot round turn,** which matches your reports. With $0, the cost hurdle was ignoring commission.
- **`Ladder Min Order Stop` raised from 0.5 to 1.0 ATR.** At 0.5 ATR (≈ $1.60), the typical $0.65 stop slippage would be about 40% of each ladder order's risk.
- **New config warnings** flag a `Max Spread` over 20 pips (saved instances still carry 80, which is $8 on gold) and ladder stops far tighter than `Min Stop`.
- **`recommended.cbotset`** holds the reviewed settings. Load it, or create a fresh instance, because saved instances keep their old values.

## Ladder review: 2026 vs 2025
| | 2026 (Jan–Oct) | 2025 (Jan–Jun, stopped by the equity floor) |
|---|---|---|
| Result | **+$600.71**, PF 1.41, 466 trades, max drawdown 20.5% | **−$60.19**, PF 0.95, 338 trades |
| Median in-session M5 ATR | ≈ $3.20 | ≈ **$1.95** |
| Average ladder stop | ≈ $3–5 | ≈ **$2.48** |
| Stop slippage | — | **$220**, averaging $0.94 per stop = **42% of risk** |
| Net if stops had filled exactly | — | **+$187** |
| Breakeven scratches | — | −$58.59 across 67 trades |

**What happened:** the ladder entries are good in both years. Slippage costs about $0.65–0.95 per stop whatever the stop size. In 2026, gold's volatility made stops big enough to absorb that. In 2025 the stops were smaller, slippage took about 42% of each trade's risk, and the account drifted down to the $120 equity floor.

**Fix: slippage floor.** `Expected Stop Slippage` (0.8) ÷ `Max Slippage Share of Risk` (20%) means every stop must be at least **4.0 price ($4 at 0.01 lot)**. Market engines widen their stop to that floor if it fits the risk cap; otherwise they skip. Ladder orders closer to the stop than that are dropped. In quiet markets like 2025, the bot now **stands aside** instead of feeding slippage. The `CONFIG` line prints the floor in use.

**Re-test both years with identical settings.** Keep the floor only if **2026 stays profitable and 2025 stops losing**, whether through fewer trades or better ones.

## Validation: three years, stop-trigger A/B → configuration frozen
| Period | Stop trigger | Net | PF | Trades | Max equity DD |
|---|---|---|---|---|---|
| **2024 (never used in design)** | DoubleTick | +$231.71 | **1.45** | 213 | 28.6% |
| 2025 | DoubleTick | +$1,324.50 | **1.94** | 524 | 18.4% |
| 2025 | SingleTick | +$911.68 | 1.71 | 494 | 24.8% |
| 2026 (Jan–Oct) | DoubleTick | +$1,688.63 | **1.86** | 576 | 17.5% |
| Aug–Oct 2026 | DoubleTick | +$88.49 | 1.48 | 69 | 22.3% |
| Aug–Oct 2026 | SingleTick | −$26.83 | 0.77 | 37 | 21.4% |

- **Profitable in all three years**, including 2024, which was never used for any design decision.
- **DoubleTick beats SingleTick** on both periods tested, on net profit, profit factor and drawdown. It stays the default.
- **2025 log review:**
  - All four quarters, both ladder types, both directions and both sessions are profitable.
  - The top 10 winners are 26% of net profit.
  - Typical stop-out slippage is 14% of risk.
  - Tail risk: five stop fills of 5–6× normal size, most in Q4.
- **Frozen configuration** = `recommended.cbotset` (LimitLadder, DoubleTick, slippage floor $4, 3% risk, sessions 08:00–11:00 and 14:00–16:30 UTC). **No further tuning** until forward-test results are in.
- **Small-account constraint:**
  - At 0.01 lot, the risk cap must stay at or above the $4 slippage floor, so about 2.3% risk at $180 is the minimum that still trades.
  - Drawdowns of about 30% are therefore possible at $180; at about $400 the same losing streaks would be roughly 13%.
- **Next steps:**
  1. Run Monte Carlo on the 2024 run.
  2. Demo for 4–8 weeks.
  3. Go live at 0.01 lot, with a pause if drawdown exceeds about 35%.

## Honest limits
- 3% risk per trade on $200 is aggressive: 10 losses in a row cost about 26%.
- Stops tightened by `CappedAtRisk` get hit more often. Whether the higher trade count still makes money after costs is exactly what the backtest has to show. The design doesn't guarantee it.
- Session times are fixed in UTC and don't adjust for daylight saving. News times must be entered by hand.

## Run of 6 Jan 2025 – 14 Oct 2026 (user settings), net −$70.08, PF 0.75
**Differences from the frozen config:**
- ATR **9** instead of 14
- OrderExpiry **12** instead of 6
- MaxTrades **4** instead of 3
- label `SovV83`
- data **m1 bars with a fixed 1.5-pip spread**, not tick data

**What happened:**
- **All 84 trades fell between Jan and Apr 2025.** By mid-April equity was $129.92, and 3% of that is $3.90, below the $4.08 minimum risk (the $4 slippage floor plus commission). Every setup after that was rejected ("one order alone exceeds the risk cap" ×17,844). The bot then sat idle for 18 months without stopping.
- Before slippage the trades netted only +$6.98, so the entries were roughly breakeven. Stop slippage was $77 in total, about 18% of risk per stop.
- In the same months the frozen config, run on tick data, was profitable (2025: 524 trades, PF 1.94). Five settings differ between the two runs, so this run can't say which one caused the difference.

**Code fix:**
- New `Stop When Untradable` setting (default on). When the risk cap falls below the minimum stop cost, the bot logs `UNTRADABLE` with the equity needed to trade again, then stops.
- At 3% risk the minimum equity is **about $136**. On a $200 account, the real drawdown headroom is therefore about $64 (about 15 full losses), not $80.
- New build stamp `MLB-2026-10-14b`.
