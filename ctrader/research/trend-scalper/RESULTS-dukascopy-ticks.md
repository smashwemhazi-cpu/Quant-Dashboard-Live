# T1 trend scalper on Dukascopy XAUUSD data (Jan 2025 – 29 Oct 2026)

The cloud environment could not reach `datafeed.dukascopy.com`, so the data was downloaded on the user's PC with
`npx dukascopy-node -i xauusd -from 2025-01-01 -to 2026-10-29 -t m1 -p bid|ask -f csv` and uploaded.

- Data: 1-minute bars, bid and ask, joined by minute. 155,218 stale flat minutes (closed market) were dropped.
- Spread is measured as ask close minus bid close:
  - median 5.8 pips over 07:00–16:30 UTC (6.0 in 2026);
  - 6.6–6.9 pips in Asian hours;
  - p90 7.8–8.3 pips;
  - 11–13 pips around 22:00–23:00.
- T1 rules and the simulator are unchanged (`trendscalp.py`, `scalp.py`). Within each minute the simulator still assumes the stop is hit before the target.

## Results at 0.01 lot ($1 per 10 pips)

| Spread | SL / TP pips | Trades | Win % | Exp. R | PF | Net | 2025 PF | 2026 PF (trades, net) |
|---|---|---|---|---|---|---|---|---|
| 3.0 | 60 / 90 | 213 | 35.2 | −0.19 | 0.73 | −$246 | 0.85 | 0.43 (54, −$153) |
| 3.0 | 60 / 120 | 192 | 30.2 | −0.18 | 0.76 | −$207 | 0.91 | 0.44 (51, −$150) |
| 3.0 | 100 / 150 | 250 | 37.6 | −0.13 | 0.80 | −$326 | 1.09 | **0.63 (141, −$380)** |
| 3.0 | 100 / 200 | 227 | 33.5 | −0.10 | 0.85 | −$233 | 1.19 | **0.67 (131, −$338)** |
| 5.8 | 60 / 90 | 216 | 33.8 | −0.23 | 0.68 | −$299 | 0.78 | 0.43 |
| 5.8 | 60 / 120 | 197 | 28.9 | −0.22 | 0.71 | −$260 | 0.83 | 0.44 |
| 5.8 | 100 / 150 | 250 | 36.0 | −0.16 | 0.76 | −$397 | 0.97 | 0.63 |
| 5.8 | 100 / 200 | 226 | 31.9 | −0.14 | 0.80 | −$312 | 1.11 | 0.62 |

**2026 by month** (SL100/TP150): Jan −$77, Feb −$55, Mar −$27, Apr +$22, May −$67, Jun −$46, Jul −$74, Aug −$52, Sep −$40, Oct +$37. That is 8 losing months out of 10.

**By side** (SL100/TP150): longs PF 0.78–0.84, shorts PF 0.72. Shorts lose too, even though gold fell 25% from March.

## Verdict
- This confirms the minute-bar result (2026 PF 0.74), and the real data is worse (PF 0.63).
- On a $388 account at 0.01 lot, the 2026 loss of about $380 would have wiped out the account.
- Only in 2025, when gold trended steadily up, did the 100-pip versions break even or make a little money.
- **T1 must not trade live.** Fixed pip stops that the account can afford sit inside gold's normal 2026 hourly range, which is 150–330 pips.
