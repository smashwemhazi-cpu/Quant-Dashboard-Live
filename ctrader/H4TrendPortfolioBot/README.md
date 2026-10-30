# H4TrendPortfolioBot (cTrader cBot)

A multi-symbol trend-follower on H4 bars. It runs one instance, trades several symbols, holds at most one position per symbol, and applies portfolio-level risk limits. It compiles cleanly against the official `cTrader.Automate` 1.0.21 package but has **not** been backtested yet.

## Why it exists
Four M5 gold backtests (about 390 trades, profit factor 0.87–0.92) all showed the same problem: **slippage, spread and commission ate about 25–30% of each trade's risk**. This bot only trades a symbol when total cost is under 10% of the stop distance (`Max Cost / Risk`). It uses textbook rules with round-number defaults and **no time-of-day filters**, because the hour filters fitted on past runs failed when tested.

## Rules (per symbol, on each closed H4 bar)
| Step | Rule |
|---|---|
| Trend filter | Long: close > 200 EMA, 50 EMA > 200 EMA, and the 200 EMA rising over 10 bars. Short: the mirror image. ADX(14) must be at least 20. |
| Entry | Close beyond the previous 20 bars' high (long) or low (short). Market order with a slippage cap. If the spread is too wide, the bot retries for up to 2 hours. |
| Initial stop | 2 × ATR(14), set on the server with the order. |
| Trailing stop | Highest high since entry minus 3 × ATR (mirrored for shorts). The stop only moves in the trade's favour. |
| Trend-flip exit | Close the trade when price closes back through the 200 EMA. |

## Risk limits
- **Per-trade risk:** 1.5% of equity. If even the broker's minimum lot would risk more than that, the bot still trades the minimum lot as long as it risks no more than 3.5% (`Min-Lot Risk Ceiling %`). Otherwise the symbol is skipped and the reason is logged.
- **Portfolio limits:**
  - at most 3 positions open at once
  - at most 2 open positions involving the same currency
  - total open risk capped at 8% of equity
- **Drawdown pause:** if closed balance falls 20% below its peak, no new trades for 14 days.
- **Equity floor:** at $100 the bot closes everything and stops.
- **Weekends:** no new entries after 18:00 UTC on Friday. Position size assumes the stop could slip 20% further than set, to cover weekend gaps.
- **Exits during wide spreads:** market exits wait for a normal spread. The server-side stop protects the position in the meantime.

## Setup
1. Attach the bot to **any** chart of one of your symbols, for example `EURUSD.mp`. The suffix after the dot (`.mp`) is read from the chart automatically, so the defaults `EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, XAUUSD` become `EURUSD.mp` and so on.
2. Set `Commission $ per Lot (round turn)` to your broker's rate. Your reports work out to about $8 per lot.
3. Read the log before looking at profit or loss:
   - `FEASIBILITY` lines, one per symbol: H4 ATR, stop size in pips, what the minimum lot risks in $ and % of equity, the cost-to-risk ratio, and **OK or SKIPPED with the reason**.
   - `VERDICT` warns you **before** the run if no symbol can be traded.
   - `RUN SUMMARY` at the end lists signals, fills and every rejection reason, per symbol.

## Testing protocol (no exceptions)
- **Do not** use Jan–Oct 2026 for decisions. Every M5 gold run used that period, so it is no longer an honest test.
- Backtest **2023–2025** on H4 with tick data or m1 bars and commission entered. Pass requires profit factor ≥ 1.2 with at least 60 trades, and profit in at least 2 of the 3 years.
- Forward-test on demo from October 2026 for at least 8 weeks, then compare fills and slippage with the backtest.
- Expect trend-following behaviour: about 35–45% winners, a few trades per month per symbol, and profit concentrated in a few strong trends.
