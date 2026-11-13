# Kraken: what went wrong, what was tested, and what the bot does now

Sources: the October 2026 Kraken statement (242 fills parsed leg by leg), the 13 Nov 2026 portfolio screenshots,
APEX v30 run in a sandbox, Kraken spot EUR data (Nov 2024 → Nov 2026), and Kraken Futures daily data for 16 coins
plus tokenised gold (Mar 2022 → Nov 2026), used as a longer price history. Not financial advice: measurements and
engineering judgement, all reproducible from the code in this folder.

---

## 1. Straight answers

**Perps or futures?** Neither. The bot trades **Kraken spot, EUR pairs only**, buying and holding real coins and
tokenised gold (PAXG). Kraken Futures data was used only to get 4.6 years of price history; spot only goes back 2.
Perp **long/short** trend following was tested and rejected (§3): shorting crypto lost money after squeezes and
funding.

**Margin?** No. No leverage, no shorts. The sleeves together can never exceed 100% of the account.

**Philosophy:** relative-strength momentum across crypto *and* gold, with cash as the third option.
- Each week, own whatever is strongest **and** in its own uptrend.
- When crypto bleeds, gold usually trends; when both fall, sit in EUR.
- Risk control comes from volatility-based sizing and from being out of falling markets, not from tight stops.

**The strategy, two sleeves, rebalanced every Monday close:**

| Sleeve | Share | Rule |
|---|---|---|
| Dual momentum | 60% | Hold whichever of **BTC or gold** has the higher risk-adjusted momentum, if it is in an uptrend; otherwise EUR. |
| Rotation | 40% | Hold the **top 2 of BTC, ETH, SOL, XRP, DOGE, LINK and gold** by the same score, each only if it is in an uptrend; the rest in EUR. |

- **Score:** the average of the 30/60/90-day returns, divided by volatility.
- **Uptrend:** the 10/20/40/80/160-day returns are mostly positive.
- **Size:** 40% target volatility per holding, max 50% per asset per sleeve, 3% cash floor.

**Where the market stands today (bar of 12 Nov 2026):** nothing qualifies, **gold included**. BTC's score is
barely positive, but its trend vote is −0.6. Every other asset is negative on both. **The strategy is 100% EUR.**

---

## 2. Your account: what happened and how to rebuild it

**October in numbers.**
- 242 fills: about 9× the account traded in 10 days.
- €40.56 in fees.
- Median round trip +0.30% before fees against ~0.44% of fees, so **83% of round trips lost money**.
- 49 duplicate fills (≈4,042 USDC of volume nobody intended): most likely two or three copies of the bot running.
- About €20 of withdrawal fees from moving money in and out ($13 to withdraw $100 on 9 Nov).
- The bot's own leftovers (BTC/DOGE/SOL bought on the way down) cost ~€200 more on paper.

**Your holdings (13 Nov): €1,647 spread over 13+ assets.**

| What | Holdings | What `rebuild` does |
|---|---|---|
| In the strategy's universe | BTC 0.01414, DOGE 2,595, SOL 2.01, XRP 39 | **Adopted**: no sell-and-rebuy fees, each gets a disaster stop. Then the first rebalance trades them to target, which **today is 0%: they are sold to EUR**. |
| Not in the universe | ATOM, TRX, plus the xStocks, AVAX, DOT and BABY fractions | Sold to EUR when above Kraken's minimum; otherwise listed as dust for Kraken's *Convert small balances* button. |
| Currencies | USDC 151, plus USD 73, USDG 29 and USDT 12 in Earn | **Not touched.** Converting them is your call (`allow_conversions` plus the exact pair). |
| In Earn/staking | EUR 23, USD 73, USDG 29, USDT 12, BTC 0.000125 | Not touched: unstake in the app first if you want them included. |

Rebuilding today means roughly €1,400 goes to EUR. The cost is about €3–4 in fees at 0.25%. The bot buys back
automatically when BTC, gold or the top coins turn up. That is the rule doing its job, not a forecast. If you
would rather keep a long-term BTC core outside the bot, sell or move only what you want the bot to manage, and
leave the rest out of its ledger (it never touches coins it didn't adopt).

---

## 3. What was tested, and what was thrown out

All results are after 0.25% fees plus 0.10% slippage per side on spot, and 0.05% + 0.05% plus funding on perps.
Every model was tested in-sample (Mar 2022 – Jun 2024) and out-of-sample (Jul 2024 – Nov 2026).

| Idea | Result | Verdict |
|---|---|---|
| APEX v30 15-minute grid (your bot) | Never trades as written (`datetime` not imported). Fixed, it bought 82 times and sold 7 times in 5 days. | **Out** |
| Long-only trend, BTC/ETH/SOL (my v1/v2) | +26% over 4.6 years; out-of-sample ≈ 0 | **Out**: too narrow, three correlated coins |
| Range trading (buy swing lows, sell swing highs) | +7% in-sample, −9.5% out-of-sample | **Out**: curve-fit |
| SMC, coded objectively (BOS/CHoCH, liquidity sweeps, FVG, order blocks) | 14 trades in 4.6 years; best variant ≈ 0 | **Out**: no edge once the rules are fixed in advance |
| Daily trailing stops (2–10 ATR) on the rotation | Every distance **cut returns and deepened drawdowns** (no stop: +135%; 3 ATR: +46%) | **Out**: replaced by a disaster stop |
| Perp long/short trend (3 or 16 coins, ± gold) | −6% to +10%, drawdown −28% to −36% | **Out**: shorts get squeezed and pay funding |
| Funding carry (long spot, short perp) | BTC/ETH funding ≈ 3.7% a year over the last 12 months, before four legs of fees | **Not built**: below a savings rate today |
| Rotation across 16 coins + gold, top 3 | Great 2023–24, lost money 2025–26 | **Out**: a bull-market amplifier only |
| **Dual momentum BTC vs gold** | Positive every year; 36/36 neighbouring settings ≥ +12.7%/yr | **In: 60% anchor** |
| **Rotation, 6 liquid coins + gold, top 2** | 36/36 settings ≥ +23%/yr, but regime-dependent | **In: 40% growth sleeve** |

The robustness checks were neighbouring-parameter grids (36–240 settings), leave-one-coin-out, start-date shifts
and fee stress tests. In-sample was used to choose; out-of-sample only to judge.

---

## 4. The final strategy's results

Through the live code path, with weekly rebalancing, next-day fills, fees, slippage and disaster stops:

| Period / data | Strategy | Hold BTC | Hold gold | Hold BTC/ETH/SOL |
|---|---|---|---|---|
| Sep 2023 → Nov 2026 (perp-proxy prices) | **+131%, 30%/yr, max DD −20%, Sharpe 1.35** | +177%, DD −53% | +103%, DD −30% | +174%, DD −68% |
| By year: 2023 (from Sep) / 2024 / 2025 / 2026 YTD | +48% / +36% / +8% / +2% | +64% / +111% / −7% / −20% | +6% / +29% / +65% / −11% | |
| Kraken **EUR spot**, May 2025 → Nov 2026 | **+5%, DD −14%** | −24%, DD −52% | +17%, DD −25% | −18%, DD −64% |
| **Worst case:** Sep 2022 → Aug 2023 (no gold data yet) | **−12%**, DD −17% | +29% | n/a | |
| Stop width 3σ / 4σ / 6σ / 10σ | +132% / +131% / +151% / +141% | | | |

How to read it:
- **It will lag BTC in a raging bull market** (2024: +36% vs +111%).
- **It wins in bear and choppy years** (2025–26).
- **It roughly halves BTC's worst drawdown.**
- The stop width barely matters, so no fragile setting is carrying the result.
- The worst-case row is real: without gold to rotate into, it lost 12% while BTC rallied.
- Part of the 2023–26 result is gold's own bull run. The dual sleeve would have held BTC more if gold had stalled.

---

## 5. Stop-loss logic

- Every holding gets a **stop-loss order resting on Kraken immediately after each fill**, so it protects you
  when the bot, your PC or the internet is down.
- **Level:** price × (1 − 4 × one-week volatility). That is about −22% for BTC and −30% to −40% for a volatile
  coin, never below 10% of the price.
- It is **raised at each weekly rebalance and never lowered**.
- It is a **disaster brake** for coin collapses (LUNA, FTT), hacks, outages and flash crashes. The weekly rotation
  is the real exit.
- If the price is already through the stop, the bot sells immediately rather than placing an order that would fire
  at once.
- A holding that can't get a stop after 3 tries is sold.
- **Portfolio kill switch:** −35% from the equity peak sells everything and halts until you reset it. That is
  beyond the worst backtest drawdown (−20%), so it is only for when something is truly broken.

---

## 6. Growth: what it can realistically be

These figures come from a Monte Carlo that resamples the strategy's own daily returns from Sep 2023 to Nov 2026 in
30-day blocks (3,000 paths), starting from €1,647. **That period was good for both crypto and gold, so treat the
median as optimistic.** The haircut row halves every return.

| Monthly deposit | Horizon | Deposited | Bad case (p10) | Median | Good case (p90) |
|---|---|---|---|---|---|
| €0 | 3 years | €1,647 | €2,200 | €3,700 | €6,600 |
| €0 | 5 years | €1,647 | €3,200 | €6,500 | €13,600 |
| €100 | 5 years | €7,647 | €11,800 | €19,500 | €35,300 |
| €200 | 5 years | €13,647 | €20,200 | €32,400 | €57,200 |
| €200, **returns halved** | 5 years | €13,647 | €16,700 | €20,800 | |

The bottom line: the strategy can compound, but **with €1,647 the deposits are the engine**. €200 a month
contributes more than the strategy does in the first years. Planning on the halved row and being pleasantly
surprised is the professional stance.

---

## 7. Known limits

- **Survivorship:** the coins were chosen from today's liquid list. A coin that dies is exactly what the disaster
  stop and the uptrend filter are for, but the backtest never saw one.
- **Short history:** gold data only from Aug 2023, EUR spot only 2 years, one full crypto cycle. Small samples
  flatter results.
- **Execution:** post-only orders may not fill and fall back to a capped IOC order. The paper broker fills
  instantly.
- **Spot stop-loss orders** should be confirmed on your account with `rebuild --validate` before going live.
- **Kraken fees** on your tier were 0.22–0.30% per side in October. The backtest assumes 0.25% + 0.10% slippage.

---

## 8. APEX v30 defects (for the record)

1. `datetime` was never imported, and the error was swallowed by a bare `except` and retried forever.
2. The minimum spread (0.2%) was smaller than one side's fee (0.22%).
3. Sell orders were priced from the current bid, not the cost basis.
4. There was a single sell order that never got re-priced.
5. No stop-loss, no inventory limit.
6. A 15-minute RSI "crash filter" that did nothing in multi-week declines.
7. RSI returned 0 when the market only went up.
8. The "delta hedge" only wrote an environment variable.
9. The `PI_` futures contracts it referenced are not listed.
10. Futures→spot transfers were sent through the spot API, which only supports spot→futures.
11. USDT/USDC is not listed on Kraken.
12. The EUR→USDC sweep passed the EUR amount where a USDC amount was expected, and converted your currency without
    asking. **That sweep is now gated by `APEX_AUTHORISE_CONVERSION=YES`.**
13. Price precision was hard-coded.
14. No client order IDs (hence the duplicates).
15. Balances were read once and not decremented across assets.
16. A 15-second loop with constant cancel/replace churn.
