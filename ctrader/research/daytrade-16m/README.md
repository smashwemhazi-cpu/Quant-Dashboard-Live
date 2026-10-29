# Day-trading bot 16m on Dukascopy XAUUSD bid/ask data (Jan 2025 – 29 Oct 2026)

`sim16m.py` is a Python replica of MicroLotBreakoutBot build 16m (day-trade profile), run on the user's Dukascopy 1-minute bid and ask bars.
- Start equity $388, fixed 0.01 lot, equity floor off so the whole period runs.
- The docstring lists exactly what is modelled.

## Validation against the real bot
The replica was compared with the cTrader **tick** backtest (16l-NF, $500, 3 Jun – 26 Oct 2026, log `7125a766`), pullback trades only:

| | Trades | Matched cTrader entries (±5 min) | Median entry difference | PF |
|---|---|---|---|---|
| cTrader ticks | 155 | | | 1.05 |
| Replica, 3-pip spread | 147 | **121 (78%)** | $0.31 | 0.86 |
| Replica, Dukascopy spread | 115 | 74 | $0.38 | 0.62 |

- Entries match well.
- The replica's PF is about 0.2 lower than the bot's, because inside each minute it assumes the stop is hit before the target.
- Add about 0.2 to the replica's PF to estimate what cTrader ticks would show.

## Results ($388 start, 0.01 lot)

| Spread | Trades | Win % | PF | Net | Max DD | 2025 PF | 2026 PF | 2026 net |
|---|---|---|---|---|---|---|---|---|
| Dukascopy real (median 5.8 pips) | 265 | 22.6 | 0.72 | −$246 | $258 | 0.82 | **0.52** | −$146 |
| Fixed 3 pips | 441 | 25.6 | 0.84 | −$220 | $234 | 0.92 | **0.74** | −$153 |

- **2026 by month at 3 pips:** January (PF 1.18) and June (PF 1.25) were positive. The other 8 months lost: Feb, Mar, Apr, May, Jul, Aug, Sep and Oct.
- **At the real Dukascopy spread,** many bars are skipped by the 6-pip spread cap, and May–October lost every month.
- **By side (3 pips):** longs PF 0.83, shorts PF 0.87.
- **Even after adding the 0.2 correction,** 2026 is about PF 0.94 at 3 pips and about 0.72 at Dukascopy's spread. 2025 comes out at about 1.0–1.1.

## Verdict
- On clean 2025–26 data, the 16m day-trading bot shows **no edge in 2026**, and at best a break-even in 2025.
- The earlier PF 1.21 for the "pullback + RR cap" subset came from picking the best of nine slices of one run; deflated, it was 0.24 (see the MicroLotBreakoutBot README). This data confirms it was luck.
- **Keep 16m off live.**
