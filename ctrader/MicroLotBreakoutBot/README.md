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

## Asian-session test (`asian-test.cbotset`)
Same frozen strategy, but it trades only **01:00–06:00 UTC**. That covers the Tokyo and Shanghai Gold Exchange hours and skips the 21:00–00:00 rollover, when spreads are widest. Session 2 is switched off by setting its start equal to its end. The label `MLBASIA` keeps its daily limits separate from the main instance. This is a measurement, not a recommendation. Compare its profit factor, trade count and average stop slippage against the 08:00–16:30 results before trading Asia live.

## First live session, 15 Oct 2026: two copies of the bot on one account
- The same strategy was running live as two instances, label `MLB` and label `H4TP` ("Sov XAUUSD Hunter"). Every setup was therefore taken twice.
- **Setup 1:** sell fade at 4152.38/39, stopped out −$4.79 and −$4.78. Double the planned risk: $9.57, about 4.5% of equity.
- **Setup 2:** sell at 4157.95, moved to breakeven at 1R, closed −$0.17 and −$0.10.
- **Fix:** new setting `Exclusive Symbol` (default on). The bot stands aside while another label holds a position or pending order on the same symbol. Two instances started in the same second can still race each other, so **run only one instance per symbol**.
- Two setups is not enough data to judge the strategy. Live trading began before the 2024 check and the demo period were done.

## Asian bot (MicroLotAsianBot) backtests, $200 start, m1-bar data
| Period | Net | PF | Trades | Max DD | MC profitable |
|---|---|---|---|---|---|
| Jan 2024 – Dec 2025 (log) | +$840 | 1.89 | 367 | — | — |
| 10 Jan – 14 Dec 2025 | +$840 | 1.98 | 332 | 18.7% | 100% |
| 10 Jan – 14 Oct 2026 | +$2,358 | 2.24 | 588 | 24.2% | 100% |

- **2024 had only 35 trades and lost about $8.** Asian hours were too quiet for the $4 minimum stop, so the bot mostly stayed out. That's the correct behaviour.
- **Profit is concentrated:** Sep–Nov 2025 made $708 of the $840. The results depend on how volatile the Asian session is, and the higher-volatility conditions continued into 2026.
- **The percentage returns are inflated by compounding.** RiskPercent sizing moves up from 0.01 lot as equity grows. Judge the bot on profit factor and on results per trade.
- **Execution risk is higher than in the main sessions:** average stop slippage was $1.92 (vs about $1.24), the worst was $17.57, and 35 trades lost more than 3× their planned risk. The backtest uses a fixed spread, and real Asian spreads are wider, so these costs need checking on demo.
- **Fix:** the false "sessions include 07:00 / 12:00" warning now fires only when a session really overlaps those hours.

## Asian bot, 2-week test (1–14 Oct 2026): 21 trades, +$9.11, PF 1.16
- **12 of the 21 fills gapped past the limit price**, which left the stop closer than the $4 floor (as close as $0.51). Of those 12, 10 were stopped out (−$35.63 in total) and 2 hit their take-profit.
- **Fix (both MicroLot bots, build 16b):** when a fill leaves the stop inside the floor, the stop is widened back to the floor. The resulting risk is never more than the planned risk for that level. This is the same fix that SovereignSMC already has.
- **2-week re-test on 16b:** +$4.04 with 11 stops widened. Gapped fills still went 2 wins / 10 losses, and widening made some of those losses bigger. Widening did not help.

## Asian bot, 2025-26 run with "Open prices only" data: invalid test
- 24 trades, PF 0.31, −$75.56. Starting from $200, the bot fell below the $136 untradable level and stopped on 11 Apr 2025.
- 16 of the 24 trades were gap fills (net −$80.95); the other 8 made +$5.39. In open-prices mode, limits and stops fill at the next bar's open, so most of those gaps are an artifact of the data. Rerun on "m1 bars from server" or tick data.

## Build 16c
- **Gap Fill Handling** (Risk group): `KeepStop` (default, the 16a behaviour), `WidenToFloor` (16b), `CloseImmediately`. Every fill whose stop is closer than the floor is logged, and the RUN SUMMARY reports how many gap fills there were, how many won and their net. Run all three options on full 2025 and 2026, on m1 data, and compare that line.
- **Data check:** after 50 bars of a backtest, the bot prints `DATA CHECK ... OK` or `DATA WARNING`. The warning means fewer than 1.5 prices per bar, which is what "Open prices only" data looks like.

## Asian bot on tick data (3 Jun – 15 Oct 2026, $200): 165 trades, PF 0.91, −$37.68
The first tick-data test. On the same dates, the open-prices run made about +$1,100.

| Stop distance | Tick data, $200 | Open prices, $3,000+ equity |
|---|---|---|
| < $4 (gap fills) | 13 trades, 15% won, −$23 | 99 trades, 26% won, +$95 |
| $4–6 | **141 trades, 29% won, −$23** | 69 trades, 42% won, +$253 |
| $6–10 | 11 trades, 36% won, +$8 | 103 trades, 46% won, +$430 |
| $10+ | none | 27 trades, 48% won, +$216 |

(Net figures are per 0.01 lot.)

- **Bar data overstates the win rate in every stop bucket.** It fills limits and targets more favourably than real ticks do, so earlier results from open-prices and m1-bar runs are inflated.
- **At $200, the 3% cap is about $5–6.** That forces the bot into $4–6 stops, the bucket that loses on ticks: 1,949 setups were rejected for exceeding the risk cap. Wider stops were the only positive bucket here, but with 11 trades that isn't proof.
- **Build 16d:** the data check now also flags m1-bar data (fewer than 50 prices per bar). Only tick data counts as a valid test.
- **Next test:** `asian-tick-widestop.cbotset` raises the floor to $6.15 (max slippage share 0.13). Run it on tick data, 2025 to Oct 2026, at $500, to find out whether wide-stop trades have a real edge.

## Asian tick run, breakdown by trade group (3 Jun – 16 Oct 2026, 167 trades)
| Group | Trades | Net | PF |
|---|---|---|---|
| 01:00–05:00 UTC | 117 | +$52.53 | 1.19 |
| **05:00–06:00 UTC** | **50** | **−$90.18** | **0.45** |
| Sells | 79 | +$88 | 1.53 |
| Buys | 88 | −$126 | 0.55 |

- **05:00–06:00 is the worst hour in all three independent periods.** It lost even on optimistic bar data: 2025 PF 0.90, Jan–May 2026 PF 0.62. **Build 16e ends the Asian session at 05:00**, and both Asian cbotsets are updated to match.
- **The buy/sell split is not a fix.** Gold fell from about 4,480 to 4,170 over this window, so sells were favoured by the market direction. A sells-only filter would be fitted to that fall.
- **01:00–05:00 at PF 1.19 is still not enough** after real costs, and it comes from 117 trades in one window. Next run: 16e on tick data, and the wide-stop preset at $500.

## 16e run (17 Oct): the session change never took effect
- The log reads `BUILD ...16e` but `CONFIG | sessions=0100-0600`. The saved instance kept its old Session 1 End of 0600, so the run was trade-for-trade identical to 16c (−$37.65).
- **Build 16f** adds a new parameter, `Skip 05:00-06:00 UTC` (default on). A new parameter always starts at its default, even on old instances, so the block applies whatever the session times say. The CONFIG line prints the entry window that is actually in effect.

## 16f on tick data (3 Jun – 17 Oct 2026, $200): 126 trades, PF 1.11, +$36.22
- The 05:00 cut worked: +$73.87 better than the 06:00 run.
- **One month carried the result.** September made +$68; the other four months together lost about $32.
- **Rejected ideas** (in-sample on this window, or contradicted by earlier periods):
  - Targets under 1.5R did best here (PF 3.17), but on 2025 bar data 1.5–2.5R was best.
  - Hour 1 was weak here (PF 0.85) but strong in both bar-data years.
  - Pullback buys did poorly (PF 0.42) because gold fell through this window.
- **Wide stops ($6–10) were positive again:** 12 trades, +$1.73 each. This is the only pattern that has held in both tick runs, and it is still too small a sample. Test it at $500 with `asian-tick-widestop.cbotset`.
- **Verdict:** demo only. Do not trade live until a forward demo or the wide-stop test shows PF ≥ 1.3.

## 16f on tick data at $500 (3 Jun – 17 Oct 2026): 272 trades, PF 1.04, +$36.33
| Group | Trades | Net (per 0.01 lot) |
|---|---|---|
| Stop $4–6 | 81 | −$104 |
| Stop $6–10 | 175 | +$42 (+$0.24 each) |
| Stop $10+ | 12 | +$109 |
| Reward:risk > 2.5 (deep ladder rungs) | 42 | −$116 (PF 0.47) |

- **Reward:risk above 2.5 is the weakest group in all five datasets**, both tick runs and bar runs. These are deep ladder rungs that fill only when price runs through the zone toward the stop.
- **Build 16g** adds `Max Order Reward:Risk` (default 2.5), which drops those rungs before the orders are placed.
- **Next tests, both at $500:**
  - A = 16g with a $4 stop floor (`asian-16g-500-A.cbotset`)
  - B = 16g with the $6.15 floor (`asian-16g-500-B-widestop.cbotset`)

## 16g on tick data at $500, $4 floor: 245 trades, PF 1.06, +$57.29, max DD $170
- The reward:risk filter dropped 514 deep rungs. That gave +$21 more profit and $29 less drawdown than 16f at $500, well short of the +$150 estimate.
- **The sharp drops are bad weeks, not bad trades.** The worst stretch ran from 29 Jun to 3 Aug (−$170). July lost in every tick run.
- **Using a single ladder order would not help.** Keeping only the first order of each setup comes to +$38.
- **Build 16h** adds `Max Weekly Loss %` (default 4). In a replay of the three tick runs, net profit rose and max drawdown fell by 30–40% in each one.
- **Feasibility:** the best tick-data PF so far is about 1.1. The Asian session is not proven tradable at this broker's costs.

## Build 16i: equity-curve filter
- **What it does.** Every ladder signal goes onto a shadow equity curve that holds both real trades and paper trades. Real orders are placed only while that curve is at or above the average of its last 10 points. Below that, the bot paper-trades the signals until the curve recovers.
- **Settings:** `Use Equity Curve Filter` (default on) and `Equity Curve Average (trades)` (default 10).
- **Log lines:**
  - `EQUITY FILTER: PAUSED` / `EQUITY FILTER: RESUMED`
  - `SHADOW LADDER` / `SHADOW CLOSED`
  - a RUN SUMMARY line with the paper trades' net result (losses avoided or profit missed)
- **How paper trades work:**
  - Fills follow the real rules: limit orders, stop at market, target, and breakeven.
  - Paper trades count toward the daily trade cap and the losing streak, but not toward the cash loss limits.
  - The shadow curve resets when the bot restarts.
- **Replay** on the five Jun–Oct 2026 tick runs: drawdown was roughly halved in all five, net was better in four and worse in one. All five runs cover the same period, so treat this as unproven until it has run on demo.
- **Presets:**
  - `asian-16i-200.cbotset`: $200, 4% risk, 4% weekly limit, filter on
  - `asian-16i-500.cbotset`: $500, filter on

## 16i on tick data, $200 (3 Jun – 18 Oct 2026): 81 trades, PF 1.49, +$111.85, max DD $41 (15%)
- This is the best $200 result so far: the previous best was +$50.54 with a $92 drawdown.
- **The filter worked as designed.**
  - 98 paper trades lost −$83.60. Those are losses the real account avoided.
  - July: −$73.60 was lost on paper, against only −$17.95 in real trades.
  - The cost: September paper trades made +$24 that the real account missed.
  - The filter paused 10 times and resumed 10 times.
- **Still unproven.**
  - t = 1.5 over 81 trades.
  - Monte Carlo worst 5%: net −$3, PF 0.99.
  - The data is the same Jun–Oct window that every fix was tuned on.
- **Build 16j:** on start, the shadow curve is rebuilt from the bot's own closed trades. A restart on a VPS or demo therefore no longer resets the filter. Backtests are unchanged, because history is empty at the start of a backtest.
- **Next step:** demo for 6–8 weeks with `asian-16i-200.cbotset` on build 16j.

## 16j on m1-bar data (10 Jan – 18 Oct 2026, $200): 200 trades, PF 1.88, +$544, max DD $83
| Window | Trades | Win % | PF | t |
|---|---|---|---|---|
| Jan–May (no fix was tuned on this) | 80 | 44% | 1.86 | 2.26 |
| Jun–Oct (all fixes tuned here) | 120 | 45% | 1.90 | 2.84 |
| Jun–Oct on **tick** data | 81 | 40% | 1.49 | 1.52 |

- **The untuned window behaves like the tuned one.** That is the first evidence that the 16f–16j fixes are not only fitted to Jun–Oct.
- **The bar-to-tick haircut on the same window is about PF −0.4** (1.90 to 1.49). Applying it to Jan–May gives an estimated PF of about 1.45 on tick data.
- **The equity filter kept 225 trades to paper.** They lost $460 in total, so the filter avoided those losses. The two losing months left were March (−$30) and July (−$31).
