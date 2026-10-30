# Pip scalping and long-side research (XAUUSD, $388 account, 0.01 lot)

Data: M1 bid bars (UTC), 2020-04 → 2025-10, plus 2026-03 → 2026-09. 1 pip = 0.10 price = $0.10 at 0.01 lot.

Costs per trade:
- spread 3 pips
- commission 0.7 pips
- stop slippage 5 pips

A winner pays about 4 pips in costs and a loser about 9. Each candidate is judged in three periods: 2020–22, 2023–25 and 2026.

## Account limit
At 3% of $388, one 0.01-lot trade can risk $11.64, so the largest allowed stop is **116 pips**.

Median ATR14 in pips:

| TF | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|
| M5 | 13 | 10 | 11 | 10 | 14 | 21 | about 38 |
| M15 | 24 | 18 | 20 | 18 | 26 | 40 | |
| H1 | 52 | 41 | 45 | 40 | 57 | 86 | |
| D1 | 237 | 200 | 213 | 202 | 286 | 380 | |

## Scalps (M5 signal, H1 EMA50 trend filter, fixed pip SL/TP, 2-hour time stop, 01:00–20:00 UTC)
- **S1, pullback:** price rejects the EMA20 with EMA20 above the EMA50.
- **S2, breakout:** a fresh 12-bar breakout.
- **S3, dip:** RSI(2) below 10 in an uptrend, or above 90 in a downtrend.

Grid: SL 40/60/80/100 pips × reward:risk 1/1.5/2/3, so 48 versions in total.

**Result: all 48 versions lose in all three periods.** The best was S1 with a 100-pip stop and 2R: PF 0.90 over 6,311 trades. Expectancy improves only as the stop widens, because costs become a smaller share of R. The entries themselves are a coin flip, and costs of 4–9 pips decide the result.

## Long side
- **L1** (H4 uptrend, H1 RSI(2) below 10, exit when an H1 bar closes above the EMA5, 100-pip stop): PF 1.05–1.11 in each period, expectancy +0.02R, t ≈ 0.9.
- **L2** (D1 close above SMA50 and yesterday a down day, buy 07:00 UTC, 100-pip stop, exit 20:45 UTC): PF 1.13 over 392 trades, expectancy +0.077R, t ≈ 1.3.
  - Loses in 2020 and 2021. The gains come in 2022, 2024, 2025 and 2026.

Baselines:
- Buying every day: PF 0.97.
- Buying every uptrend day: PF 1.02.

The down-day rule adds about 0.06R, but that is not statistically significant. Most of the long-side result is gold's 2022–26 rally (beta), not a timing edge.

## Verdict
- Gold scalping does not fit this account: costs of 4–9 pips are larger than any edge these entries have.
- Long-only gold makes money only while gold rallies, and the edge is not proven.
- The binding constraint is size: 0.01 lot of gold at about $4,000 is roughly 10x leverage on $388.
