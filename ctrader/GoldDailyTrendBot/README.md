# GoldDailyTrendBot (cTrader cBot)

A Daily-timeframe XAUUSD trend-following cBot. It enters on Donchian breakouts and on pullbacks within the trend, and it always holds a server-side ATR stop.

It compiles cleanly against the official `cTrader.Automate` 1.0.21 package (`cAlgo.API`, net6.0). It has **not** been backtested yet: run the protocol below before trading it live.

## Install
1. In cTrader, go to **Algo → cBots → New**, name it `GoldDailyTrendBot`, and paste in `GoldDailyTrendBot.cs`.
2. Build it, then attach it to an **XAUUSD, Daily** chart. The bot stops itself on any other timeframe.
3. Use a broker or feed whose daily bar closes at **17:00 New York** (GMT+2/+3 server time). The bot detects Sunday stub candles and stops by default.

## Strategy logic
| Step | Rule |
|---|---|
| Regime | Long: close above the 200 EMA, 50 EMA above the 200 EMA, 200 EMA rising over 20 bars, ADX(14) at or above 20. Short is the mirror image. |
| Breakout entry | Close beyond the high (or low) of the previous 20 bars, in the regime's direction. |
| Pullback entry | RSI(2) at or below 10 (at or above 90 for shorts), or the bar touches the 20 EMA, while the regime holds. |
| Timing | The signal is evaluated on the closed daily bar. It executes no earlier than 60 minutes after the new bar opens, and only once spread and quote checks pass. |
| Initial stop | 2.5 × ATR(20), placed server-side with the order. |
| Trail | Chandelier stop at the extreme since entry ∓ 3 × ATR(20). It only moves in the trade's favor. |
| Time stop | Pullback trades that haven't reached +1R after 10 bars are closed. |
| Swap-bleed exit | Closes a trade whose negative swap exceeds 0.25R while it is below +1R. |

Position size = `equity × risk% ÷ (stop pips × gap buffer × PipValue)`, rounded **down** to the broker's volume step. If that falls below the broker's minimum, the trade is skipped rather than rounded up.

## How each known weakness is handled

| Weakness | What the bot does |
|---|---|
| **Bull-market bias** (2024–26 gold makes long-only look great) | Shorts are built in and enabled by default (`Direction = Both`). Every rule is symmetric. The regime filter requires the 200 EMA's *slope* to agree, so it can't be long through a downtrend like 2013. The backtest protocol below **must** include 2011–2015. |
| **Whipsaw in ranges** | The ADX filter skips trendless markets. The 50/200 EMA alignment and the slope requirement mean flat ranges produce no signals. After a losing trade there's a cooldown of N bars before trading the same side again. Stale pullback trades are cut by the time stop, which frees the risk budget. |
| **Overfitting** | Few rules, all using textbook default values. Every threshold is a parameter with a sensible optimization range. Use the walk-forward protocol below; parameters that only work in a narrow band should be rejected. |
| **Losing streaks** (6–10 losses in a row is normal) | Risk per trade is 0.75%. After 4 consecutive losses, risk is halved until a win. There's a 2% cap on total open risk. At a 15% drawdown from peak equity, new entries stop. The peak is stored in `LocalStorage` (live only), so restarting the bot doesn't silently clear the halt. |
| **Weekend and news gaps** | Size is calculated as if the stop were 1.25× wider, so a gap through the stop lands near the intended risk. Entries before the weekend are skipped. FOMC, CPI and NFP dates go in `Blackout Dates`; cTrader has no economic-calendar API, so they must be entered manually. |
| **Rollover spread spike** | Entries **and** discretionary exits wait until 60 minutes after the daily open. They also need the spread to be no more than 2.5× its rolling 24-hour median and no more than an absolute cap. Stop modifications, which never fill at market, happen immediately. |
| **Swap carry** | Negative swap is checked each bar. A trade that isn't working is closed once its carry cost passes 0.25R. |
| **Sunday candles distorting ATR and Donchian** | Detected at startup (two daily opens less than 20 hours apart). The bot stops by default. |
| **Macro headwinds** | Optional US-dollar proxy (e.g. `USDX`, or `EURUSD` with *Inverted* set). If the dollar's 20-bar move works against the trade, size is cut in half. Real yields (`DFII10`) aren't available inside cTrader, so check them manually. |

## Execution resilience
- **Invalid or stale quotes:** prices that are NaN, zero or crossed are ignored. Orders aren't sent if the last tick is older than `Stale Quote (sec)`.
- **Slippage:** orders are `ExecuteMarketRangeOrder` with a `Max Slippage (pips)` limit.
- **Retries:** `TechnicalError`, `Disconnected`, `Timeout` and `MarketClosed` are retried up to `Max Order Retries` times, spaced by a timer so the bot thread never blocks. Other errors drop the signal.
- **Missing stops:** a position that ends up without a stop gets one re-attached. If that fails, the position is closed.
- **Restarts:** state is rebuilt from `Positions`, `History` and the bars (the entry bar, R and the trail extreme), so a restart or reconnect doesn't lose track of open trades. A signal that was queued but not yet sent is lost on restart, which is the safe failure.
- **Known gap:** cAlgo has no reliable disconnect event that I'm aware of. Disconnects are handled indirectly through stale-quote detection and retrying on the `Disconnected` error code.

## Backtest protocol (do this before going live)
1. Data: tick data from **2008 to today**, using the broker's real spreads, with commissions and **swap** switched on.
2. Look at results by regime: 2008–2011 (bull), **2011–2015 (bear)**, 2016–2019 (range), 2019–2026 (bull). If it only works in the bulls, it doesn't work.
3. Walk forward: optimize on 4 years, test on the next 1 year, roll forward, and stitch the test years together. Optimize at most 3–4 parameters (e.g. Donchian period, initial stop ATR, trail ATR, ADX min).
4. Robustness: vary each chosen parameter by ±20%. The equity curve should degrade gradually, not collapse.
5. Realistic expectations: a win rate of 35–45%, average win about 2–3R, and maximum drawdown in the 15–25% range at 0.75% risk per trade.
6. Forward-test on demo for at least 2–3 months, comparing fills and slippage against the backtest.

## Notes
- `Symbol.PipValue` is fixed when the bot starts (per the API docs). For a USD account trading XAUUSD that's exact. For other account currencies, restart the bot periodically to refresh it.
- Pip size for XAUUSD differs between brokers (often 0.01). Slippage and spread caps are in pips, so check the `PipSize` printed at startup and adjust.
