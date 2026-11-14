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

**Is this "hold a bag and pray"?** No.
- **The momentum engine is out of the market about half the time** (average 47% invested). It sells anything that
  stops trending at the next weekly close, and it is 100% EUR today.
- **The harvest engine (below) trades against volatility.** It sells whatever ran up and buys whatever fell, on
  rules, not hope.

What the bot deliberately does *not* do is the "trading hub" activity: fast in-and-out trades, grids, pairs or
scalps. §3b shows why. At retail fees those strategies transfer your money to market makers, who are paid rebates
for taking the other side.

**Two engines, two ledgers on one account** (split 50/50 by default):

| Engine | Job | Rule |
|---|---|---|
| **Momentum** (the two sleeves above) | Ride trends, step aside in bear markets | Weekly ranking; cash when nothing qualifies |
| **Harvest** | Turn volatility into profit | Hold ⅓ BTC, ⅓ gold, ⅓ EUR. When any weight drifts ±10 points (BTC roughly +50% or −40% against the others), trade back to ⅓ each: sell the winner, buy the loser |

---

## 1b. Code review (Nov 13): bugs found and fixed, each now with a regression test

| # | Bug | What it would have done live | Fix |
|---|---|---|---|
| 1 | Kraken's AddOrder reply has **no fill information**; only post-only orders were read back | Coins bought on the final IOC attempt: never recorded and **never given a stop**. Coins sold that way: EUR never credited, so equity shrank on paper and could trip the kill switch | Every order is read back from Kraken before it is booked |
| 2 | Reconciliation ran **before** booking stops that filled offline | It wiped the stop's order id, so the sale proceeds were **lost from the ledger** | Stop fills are booked first, then reconciliation runs |
| 3 | An order stuck open after a cancel was followed by a new order | Possible double orders | It stops and logs CRITICAL; never sends a second order |
| 4 | The harvest engine rebought an asset the day after its disaster stop fired (happened on 5→6 June 2026 in the backtest) | Sold low, bought straight back: the stop was pointless | The asset stays in EUR until its trend turns up. Cost in this sample: +83% instead of +88%, the price of protection in a real collapse |
| 5 | A bad quote crashed the harvest engine; a crash in one engine stopped the other | A missed day for both engines | Harvest skips cleanly; each engine runs isolated; the exit code is non-zero on failure |
| 6 | Client order ids did not include the quantity | A re-run after a partial crash could reuse an id for a different order | The quantity is part of the id |
| 7 | `rebuild` could run over an existing harvest ledger | EUR counted twice | Refuses if either ledger already exists |

The paper broker now behaves like Kraken (no fill info until the order is read back), so these paths are exercised
by every test.

## 1c. Trading 2026 (to 12 Nov): what worked and what did not

| | 2026 YTD | Max DD |
|---|---|---|
| Momentum engine (36% of days fully in EUR, 35% invested on average) | **+2.7%** | −12.6% |
| Harvest engine | −10.1% | −21.8% |
| 50/50 blend | −3.7% | −14.5% |
| Hold BTC / hold gold | −18.5% / −10.4% | −39.6% / −29.8% |
| Short-BTC "bear sleeve" on perps | +4.6% | −12.0% |

The bear sleeve made money in 2025 and 2026, but lost 20% from 2022 on, and **all 24 neighbouring settings lost
money**. Adding it because it worked this year would be curve-fitting to 2026, so it is not built. In a year when
BTC and gold both fell, the momentum engine did its job: it stayed mostly in EUR, made small gains, and fired no
stops (its 14 exits were weekly rotations). The harvest engine is the part that bleeds in a year like this, by
design. It is the engine that earns in sideways-to-up years such as 2025 (+20%).

---

## 1d. Futures hedge engine (optional): verified facts and evidence

**Checked against Kraken itself (14 Nov 2026), not assumed:**
- **Fees.** Kraken's public futures fee schedules include *MTF Linear Rebate Fees* (0.02% maker / 0.05% taker),
  which is attached to `PF_XBTUSD`, `PF_SOLUSD`, `PF_DOGEUSD` and `PF_PAXGUSD`. They also include *Consumer MTF Linear
  Fees* and *Consumer Linear Fees* at **0.25% / 0.25%**. Which one applies to your account only shows through a
  private endpoint (`feeschedules/volumes`). The engine reads it and refuses to trade on a consumer schedule.
- **Spot fees.** Kraken's public AssetPairs data no longer lists spot fee tiers. The spot figures here are the ones
  measured from your October statement (0.22% / 0.30% per fill).
- **Funding history.** The Kraken API serves only the last 12 months, whatever parameters are sent. Earlier funding
  was **reconstructed** from Kraken's own hourly mark and index prices (the premium; Kraken's contract specs give
  funding coefficient 8 and a cap of 0.5%). Fitted on the 12 real months: daily R² 0.69 (BTC), 0.74 (SOL),
  0.73 (DOGE), 0.87 (gold); the means match exactly. Real funding is used wherever Kraken has it.
- **Funding the futures wallet.** Kraken's docs: `WalletTransfer` moves funds *"from a Kraken spot wallet to a Kraken
  Futures wallet. Note that a transfer in the other direction must be requested via the Kraken Futures API
  endpoint for withdrawals to Spot wallets."* The endpoint is marked deprecated (it still works but receives no
  updates).
- **Demo exchange.** `demo-futures.kraken.com` now redirects every API path to kraken.com, so there is no paper venue.
- **Contracts.** Contract size 1; minimum step 0.001 PAXG, 0.0001 BTC, 0.01 SOL, 1 DOGE; initial margin 1–2%.

**Backtest (hourly, 2022–2026; real + reconstructed funding; exchange-resting stop/TP; +0.05% slippage):**

| | Pro fees (0.02/0.05%) | Consumer fees (0.25%) |
|---|---|---|
| Standalone shorts (BTC, SOL, DOGE, gold) | lose money (BTC −69%, SOL −49%); DOGE +59% is one coin's crash | everything loses: −19% to −95% |
| Hedge BTC you hold (v33-style rule) | worse than holding (+41% vs +71%); loses in 84% of 81 settings | wipes the bag out |
| **Hedge gold you hold** | **+114% vs +102% holding, drawdown −23% vs −31%**; better return in 85% of 81 settings, shallower drawdown in 98% | worse than holding (+61% vs +102%) |
| Gold hedge with zero funding | lags holding (11.7% vs 24.7%/yr): the edge **is** the funding shorts receive | – |
| "Only hedge while funding pays shorts" gate | hurts gold (85% → 44% of settings better), so not used | – |

Default settings follow the evidence: the engine is **off**, hedges **gold only**, and **refuses consumer fees**.

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

## 3b. Volatility and arbitrage: every idea I could test on Kraken

All on 4.6 years of hourly and daily Kraken Futures data (funding: the last 12 months, all Kraken serves).

| Idea | What it is | Result | Verdict |
|---|---|---|---|
| Funding carry | Long spot + short perp, collect funding | BTC/ETH funding ≈ 4%/yr over the last year. Switching in and out on a funding threshold: **−2% to −22%/yr** after fees. Held the whole year: about +2%/yr | **Out** for now: below a savings rate. It can pay 10–30% in a hot bull market, which this data can't test |
| Stat-arb on ETH/BTC, SOL/BTC, SOL/ETH | Short the rich coin, buy the cheap one, wait for the ratio to revert | **17 of 18 variants lose**; hourly variants −70% to −99% | **Out**: crypto ratios trend more than they revert |
| Short-term reversal on perps | Fade big hourly or daily moves | **11 of 12 lose**, up to −94% | **Out**: the APEX failure again |
| **Volatility harvesting** | Constant BTC/gold/EUR mix, rebalance on drift | +88% to +104% since Sep 2023 (band ±10% to ±20%), drawdown −21% to −22%. In the Sep 2022 – Aug 2023 window where momentum lost 12%, a BTC/EUR harvest **made +14% to +16%** | **In: the harvest engine** |
| Options (selling volatility) | Collect premium | I don't know of options on Kraken for EU retail clients, so this wasn't tested | n/a |
| Cross-exchange or triangular arbitrage | Price gaps between venues or pairs | Three legs × 0.25% = 0.75% per loop, against latency-optimised bots | **Out**: you're the slowest participant |

**Why the harvest works.** BTC and gold are both volatile and weakly correlated. Rebalancing between them and cash
sells strength and buys weakness *and* stays diversified. Holding the same ⅓/⅓/⅓ mix without rebalancing made
+92% with a −32% drawdown; the rebalanced version made more with a smaller drawdown. Bands are wide so it trades
only a few times a year: 16 trades in 3 years at ±10%. Your 0.2% grid had the same idea with bands smaller than its
own fees.

**Harvest stop width:** 4σ hurt (+89%, −25% drawdown, because it sold the dips the engine is built to buy). 6–20σ
were all ≈ +100%. The default is 8σ: about −40% for BTC, −24% for gold.

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

**Momentum + harvest, 50/50 on separate ledgers** (Sep 2023 → Nov 2026): **≈ 26–28%/yr, max drawdown −15% to
−16%, Sharpe 1.34–1.39**. Momentum alone: 30%/yr, drawdown −20%, Sharpe 1.35.

The blend gives up a little return for a third less drawdown, and its two halves cover each other's worst windows.
On Kraken EUR spot since May 2025, the blend made +1% (momentum +5%, harvest −2% to −3%) against −24% for BTC.
2026 has been bad for both BTC and gold.

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

These figures come from a Monte Carlo of the **momentum engine alone**. The 50/50 blend's figures are similar but
tighter: €0/month for 5 years gives a median of €6,000 and a bad case of €3,400; €200/month gives a median of
€30,600 and a bad case of €20,800. The Monte Carlo resamples the strategy's own daily returns from Sep 2023 to Nov 2026 in
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
