# Trend-following pip scalper (T1) and the 2026 gold regime

## Current market (D1 data to 23 Oct 2026)
- 2026 high 5,330.95 on 2 Mar. Last close 3,996.00, which is **−25%** from that high.
- Price is below the SMA50 (4,302) and the SMA200 (4,510), and the SMA50 is falling.
- Monthly moves in 2026: Jan +12.9%, Feb +8.5%, Mar −11.3%, Jun −11.7%, Aug +10.0%, Sep −6.6%, Oct −3.8% (to the 23rd).
- That is a falling market with violent counter-rallies.
- Volatility is far above 2020–25:

| | 2020–23 | 2024 | 2025 | 2026 |
|---|---|---|---|---|
| H1 ATR (pips) | 40–52 | 57 | 86 | 154–328 |
| D1 ATR (pips) | 200–240 | 286 | 380 | 673 |

- The MicroLotBreakoutBot H1 regime (EMA50 slope vs 0.3 ATR), replicated on 2026 data: flat 38–55% of hours each month, up 14–44%, down 11–46%.

## How the code handles trend and volatility
- **Direction:** an H1 EMA50 regime, symmetric for longs and shorts (breakout and Asian bots). There is no D1 filter.
- **Volatility:** stops are set in ATR units, never closer than the $4 slippage floor, and the 3% equity cap skips any trade whose 0.01-lot risk is too big.
  - In 2026 that cap is the binding rule: at $388 the maximum stop is 116 pips, less than one H1 ATR.
- **News:** a `NewsTimes` blackout exists but has never been filled in.
- **Macro trend:** only GoldDailyTrendBot (D1 Donchian) is built for it, and it was never backtested. A 2-ATR D1 stop of about 1,350 pips is about $135 at 0.01 lot, 35% of the account.

## T1 rules (fixed before the run)
- **D1 filter:** previous close vs SMA50 sets the direction.
- **H1 filter:** the bot's regime must agree with the D1 direction.
- **M5 trigger:** an EMA20 pullback that closes back in the trend direction.
- **Volatility gate:** the fixed SL must be 1.5–3 × M5 ATR (in pips).
- **Hours:** 07:00–16:30 UTC.
- **Exits:** pip SL and TP; anything still open closes at 20:45 UTC.

## Results (costs included, 0.01 lot)

| SL / TP pips | Trades | PF | Expectancy | 2026 |
|---|---|---|---|---|
| 60 / 90 | 524 | 0.87 | −0.08R | PF 0.37 (14 trades) |
| 60 / 120 | 495 | 0.86 | −0.09R | PF 0.50 |
| 100 / 150 | 200 | 1.10 | +0.05R | **PF 0.74 (64 trades)** |
| 100 / 200 | 188 | 1.11 | +0.06R | **PF 0.73 (60 trades)** |

- The 100-pip versions made money only in 2021, 2024 and 2025, the bull-trend years, and the longs carried it (PF 1.34–1.36).
- Shorts lost overall (PF 0.79–0.80), including in the 2026 downtrend.

**Verdict:** this scalper loses in the current regime. Fixed pip stops that the account can afford (≤116 pips) are smaller than one hour of normal 2026 gold movement.
