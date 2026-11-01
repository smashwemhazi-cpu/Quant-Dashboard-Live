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

## Asian bot 16j on the same data (`simladder.py`, profile `asian16j`)
`simladder.py` generalises the replica:
- the flat-regime fade ladder;
- the 4% weekly cap;
- the equity-curve filter, with paper trades.

Its `day16m` profile reproduces `sim16m.py` exactly (441 trades, PF 0.84).

| Spread | Real trades | PF | Net | 2025 PF | 2026 PF |
|---|---|---|---|---|---|
| Fixed 3 pips, $388 | 258 | 0.76 | −$217 | 0.70 | 0.82 |
| Dukascopy real, $388 | 98 | 0.74 | −$90 | 0.72 | 0.77 |

**Low confidence; this does not settle the Asian bot:**
- **Thin-hours data:** Dukascopy's 01:00–05:00 spread is 6.6–7.5 pips, above the bot's 6-pip cap, and its prices differ from Vertex by a few dollars. For example, on 4 Jun at 01:00 the M5 middle band was 4452.38 on Dukascopy and about 4455.3 on Vertex, against a stop of about $7.
- **Validation is weak:** against the cTrader 16j tick log ($200, 3 Jun – 16 Oct 2026), only 24 of the replica's 57 filled setups match a cTrader setup (same minute and side).
- **Too sensitive:** reading the H1 regime one bar later at the top of the hour raises matches to 32 of 77, and moves the replica's real-trade PF from 0.33 to 0.93.
- The day-trading bot's replica matched 78% of fills, so the 16m verdict above stands; the Asian replica cannot be trusted to the same degree.

## 16n live beta on the same data (`simladder.py` profile `day16n`, `beta16n.py`)
`day16n` mirrors `breakout-16n-live-beta.cbotset`:
- the 16m day sessions (pullbacks only) plus the Asian window 01:00–05:00 UTC, where fade ladders are allowed;
- the 3-trade cap and 2-loss streak counted per block (Asia from 01:00, day from 05:00);
- daily loss 4% per calendar day, 3% risk, commission $7/lot;
- equity floor $340 (the run stops there).

Check: with the Asian window off it reproduces 16m exactly (441 trades, PF 0.84, −$220.49).

### Full period, $388 start (Jan 2025 – Oct 2026)
| Spread | Floor | Real trades | Win % | PF | Net | Ends |
|---|---|---|---|---|---|---|
| Dukascopy real | $340 | 30 | 23.3 | **0.50** | −$50 | **floor hit in early 2025** |
| Fixed 3 pips | $340 | 21 | 14.3 | **0.28** | −$52 | **floor hit in early 2025** |
| Dukascopy real | off | 267 | 27.7 | 0.69 | −$256 | stops trading in 2025 at about $132 |
| Fixed 3 pips | off | 646 | 30.5 | 0.87 | −$255 | stops trading in Aug 2026 at about $133 |

- With the floor off, the account eventually stops trading. Below about $133, 3% of equity can't cover even one rung at the $4 minimum stop.
- **By setup type (3 pips, floor off):**
  - Asian fades lose: 208 trades, PF 0.64, −$223.
  - Pullbacks are about break-even: 438 trades, PF 0.98, −$32.
  - The Asian half is what makes 16n worse than 16m.
- **By side (3 pips, floor off):** longs PF 0.83, shorts PF 0.94.
- **2026 months (3 pips, floor off):** January and February were small gains, then March–August all lost.

### Rolling start of the beta (pre-registered rules applied, start $388 on the 1st of each month)
`python beta16n.py <bidask.pkl> <real|0.30> <YYYY-MM-DD>`, 21 starts from Feb 2025 to Oct 2026:

| Outcome | 3-pip spread | Dukascopy spread |
|---|---|---|
| **KEEP** (PF ≥ 1.2, DD ≤ 15% at 40 trades) | 3 (Jul, Oct, Nov 2025) | 1 (Sep 2025) |
| RETIRE at 40 trades | 6 | 6 |
| STOP at 20 trades (PF < 0.8) | 6 | 5 |
| **FAIL** ($340 floor hit) | 6 | 9 |

2026 starts at the Dukascopy spread:
- floor hit: Feb, Mar, Apr, May, Aug;
- retire: Jan, Jun, Sep, Oct;
- stop: Jul;
- keep: none.

### Caveats
- In the 16m validation, the replica's PF ran about 0.2 below cTrader ticks, because inside each minute it assumes the stop is hit before the target.
- The Asian replica matched cTrader poorly (see above), so the Asian numbers have low confidence.
- Even with +0.2 added, 16n is at most break-even in 2026.

### Verdict
- **On this data the 16n live beta most likely ends at the $340 floor or in retirement.**
- It passed its own KEEP rule in 1 of 21 monthly starts at the Dukascopy spread, and 3 of 21 at 3 pips. None of those starts was in 2026.
- **Keep it on demo, not live.**
