# Silver Scalper: review and v2 changes

`SilverScalperBot.cs` is v2. It compiles against `cTrader.Automate` 1.0.21 (the official NuGet API package). It has **not been backtested** here, and every behaviour change listed below needs a fresh cTrader backtest with tick data before you run it live.

## What was already good
- The sizing check against the minimum lot happens *before* `NormalizeVolumeInUnits`, so the broker minimum can't push risk past the cap. This is a subtle bug that most bots have and this one avoids.
- The short-side stop adds the spread (bars are bid prices and shorts stop out on the ask).
- The bot uses market-range orders with a slippage cap, goes flat before the daily close, and logs each trade in R with MFE/MAE.
- The strategy choices are evidence-led: trend-pullback over mean reversion, the London session only, and trading with the H1 trend.

## Defects fixed in v2
| # | Issue | Effect in v1 | Fix |
|---|---|---|---|
| 1 | A breakeven scratch (net = −commission) counted as a loss | With "Stop Day After 1 Loss", every scratched winner ended the day | A trade only counts as a loss below `−LossThresholdR` (default 0.25R) |
| 2 | Daily loss limit measured in nominal 1% R | A forced-min-lot trade (up to 2%) counted as 1R | Each trade is measured in its own R (net ÷ risk money at entry) |
| 3 | Breakeven had one attempt | A rejected modify left the trade at full risk | Up to 3 attempts, 10 s apart, plus an optional cost lock |
| 4 | No check that a stop was actually attached | A rejected SL left a naked position | The stop is re-applied from the stored R; if that fails, the position is closed |
| 5 | The blackout list held only FOMC (18–19 UTC) | It never overlapped the 07–12 entry window, so it blocked nothing. Meanwhile a late entry plus the 180-min time stop held through 12:30/13:30 UTC US data (CPI/NFP) | `Flatten Before US Data` (12:25 UTC by default); the blackout now also flattens open positions |
| 6 | Costs were ignored in sizing | On 6-pip stops, commission and spread are a large share of R | `Round-turn Cost (pips)` is included in sizing and in the spread filter |

## Additions (off or neutral by default)
- **ATR trailing stop** (`Trail Start (R)`): off by default, because a 1.5R TP makes trailing mostly moot.
- **Equity drawdown breaker:** the peak is persisted across restarts in `LocalStorage`, keyed by account number. Reset it deliberately with `Reset Drawdown Peak On Start`.
- **Weekly loss limit in R**.
- **Gold H1 trend confirmation** (`Confirm Symbol`, e.g. `XAUUSD`): an intermarket filter.
- **M5 ATR band filter**, skip dates, and a feed watchdog.
- **RLOG2** log lines with entry hour, stop size, spread and slippage at entry, and costs. JARVIS → Bot lab analyses these.

## How to judge it (JARVIS Bot lab does this from the logs)
1. **Sample size:** fewer than 100 trades can't support a parameter change. Aim for 200 or more.
2. **Expectancy confidence:** if the bootstrap 95% CI includes 0, the edge is not proven yet.
3. **Session result:** "07–11 UTC was the only positive session in two 2026 runs" is a small-sample finding. Confirm it walk-forward: optimise on one period and test on the next, with no overlap.
4. **Breakeven cost:** count the trades that reached 1R and were then scratched. If that's more than about 15% of trades, test BE at 1.2–1.5R.
5. **Cost share:** spread plus commission as a percentage of the stop. Above about 25%, the edge has to be large to survive.

## Strategy context
Trend-following pullbacks are a sound base for silver, because silver trends with gold and the dollar. Mean reversion on a 6–16 pip stop gets chopped up by spread. The best modern additions are:
- **Intermarket confirmation:** gold's trend and DXY direction (gold is implemented in v2).
- **Volatility-normalised stops:** the ATR bounds already exist; use them when porting the bot to gold.
- **A session-liquidity filter** keyed to spread *percentiles* rather than a fixed pip cap.

Pure order-flow signals are not available to a retail cBot on CFD data, so don't chase them.
