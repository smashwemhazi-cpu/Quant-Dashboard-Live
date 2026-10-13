# MicroLotBreakoutBot (cTrader cBot)

An intraday breakout cBot for **small accounts trading a fixed 0.01 lot**, designed around a ~$200 account on XAUUSD.

It compiles cleanly against the official `cTrader.Automate` 1.0.21 package. It has **not** been backtested yet.

## Why a different design
At a fixed minimum lot you can't size positions. With 1 oz of gold, every $1 the price moves is $1 of profit or loss, so **the only risk control left is how far away the stop is.** The daily bot needed stops of about $600, which is three times this account.

This bot therefore only takes setups whose *natural* stop is small:

| Step | Rule |
|---|---|
| Setup | **Compression box:** the high-low range of the last 12 bars (1 hour on M5) is between 0.8× and 2.5× ATR(14). Tight ranges like this tend to precede expansion, which is the idea behind Toby Crabel's narrow-range (NR) breakouts. |
| Direction | Only in the direction of the H1 trend: close and 50 EMA on the same side, with the EMA sloping that way. |
| Entry | A **stop-limit** order just beyond the box (buffer = 0.1 ATR, plus spread on the Ask side). Slippage is capped by `Stop-Limit Range`. The order expires after 6 bars or at the end of the session. |
| Stop | The **opposite side of the box**: structural, and small by construction. |
| Target | 2R. The stop moves to breakeven at +1R. |
| Hours | London 07:00–10:30 UTC and New York 12:30–15:30 UTC only. Everything is closed by 20:30 UTC (19:30 on Fridays), so there's **no swap, no rollover spread and no weekend gap**. |

## Risk controls (all adjustable)
| Control | Default | Why |
|---|---|---|
| Max risk per trade | 3% of equity (about $6 on $200) | At a fixed lot, any setup whose stop costs more is **skipped**. It is never resized and never forced. |
| Max trades per day | 2 | Stops the bot from over-trading a choppy day. |
| Max daily loss | 6% | About two full losses, then it stops for the day. |
| Losses in a row (per day) | 2 | Same idea, counted by trade rather than by dollars. |
| Equity floor | $120 | Closes everything and **stops the bot** if equity falls below this. |
| Spread filter | ≤ 6 pips and ≤ 15% of the stop | With tight stops, spread is the main cost. |
| News blackout | Manual list | Enter FOMC, CPI and NFP times as `yyyy-MM-dd HH:mm` in UTC. No orders within ±30 minutes, and pending orders are cancelled. |
| Stale or invalid quotes | 60 s | No order is placed on a frozen or crossed quote. |
| Missing stop | — | A position found without a stop is closed immediately. |

## The feasibility report: read it first
At startup the bot measures **your broker's own history** and prints something like this (numbers illustrative):
```
FEASIBILITY | sample 2400 in-session Minute5 bars | 0.01 lots = $1.00 per 1.00 price move | risk cap $6.00 (3% of $200.00)
FEASIBILITY | median ATR(14) = 3.10 -> a 1-ATR stop costs $3.10 (1.6% of equity)
FEASIBILITY | compression setups: 310 | affordable: 95 (31%) | median setup risk $8.40
VERDICT: only some setups fit - expect few trades. Backtest before going further.
```
If the verdict says **NOT suitable**, gold is too volatile for this account at the current settings. The honest options are then:
- trade a symbol with a smaller minimum-lot risk (run the same bot on `USOIL.mp` or `US500.mp`; the report works on any symbol),
- move to M1 (more noise, and costs weigh more), or
- add capital.

## How to test
1. Attach it to an **XAUUSD.mp M5** chart (M15 also works). Use a backtest starting balance equal to your real one (~$200), tick data, and commission entered.
2. Check the `FEASIBILITY` and `VERDICT` lines in the log before looking at profit and loss.
3. Use at least 6 months of data, and judge each month separately. Look at: number of trades, win rate (2R targets need about 34% to break even before costs), average loss in dollars (should be at or below the cap), and maximum drawdown.
4. Run it on `USOIL.mp` and `US500.mp` too, and compare the feasibility verdicts.

## Honest limits
- Even within the rules, 3% per trade on a $200 account is **aggressive**: ten losses in a row cost about 26%. That's the price of a fixed 0.01 lot on a small account. Lower `Max Risk Per Trade %` if you accept fewer trades.
- Session times are fixed in UTC, so London and New York opens shift by an hour with daylight-saving changes. Adjust them seasonally if you want exact alignment.
- cTrader has no economic-calendar API, so news times must be entered by hand.
- Backtest fills on stop-limit orders depend on the tick data quality. Compare demo fills against the backtest before going live.
