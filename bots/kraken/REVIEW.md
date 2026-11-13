# Kraken: portfolio post-mortem, APEX v30 teardown, and the rebuild

Sources: the October 2026 Kraken spot account statement (34 pages, 242 trade fills parsed leg by leg), portfolio
screenshots from 13 Nov 2026, APEX v30 run in a sandbox against the live Kraken API and in an offline simulator fed
with real Kraken candles, and Kraken's live market metadata (pairs, minimums, leverage, futures instruments).

> Not financial advice. These are measurements and engineering judgement. Every number below can be re-derived
> from the statement and the scripts in this folder.

---

## 1. Bottom line

1. **The account is not losing money because of bad luck. It is losing money because of the machine and the habits around it.**
   The bot paid more in fees than it earned on every typical round trip, bought into a falling market without
   limit, and left you holding the bags. On top of that, the deposit/withdraw churn leaks fees.
2. **APEX v30 as written cannot trade at all.** `datetime` is never imported. The first loop iteration raises
   `NameError`, the bare `except Exception` swallows it, sleeps 10 s and repeats forever. It runs the "cleanse"
   (market-converts your EUR/USD into USDC), then silently does nothing. Verified in the sandbox: 20 loop
   iterations, **0 grid cycles, 0 orders**.
3. **With that one bug fixed, the strategy is worse than doing nothing.** On the last 5 and 19 days of real Kraken
   data it ended 82 buys / 7 sells and 117 buys / 28 sells: ~85% of the cash turned into coins in a falling market,
   with resting sell orders 7–28% above price that never fill. It is buy-and-hold with extra fees and no stop.
4. **The replacement** (`krakenbot/`) is a daily trend-following, volatility-targeted, long-only bot on EUR pairs,
   with exchange-side stops. Backtest Nov 2024 → Nov 2026: **+4.6% vs −48% for holding the same coins, max drawdown
   −19% vs −62%.** That is capital preservation, not a money machine (see §5 for what the robustness test says).
5. **Margin would not help.** In the backtest the bot never wants more than ~20% of capital because the risk limit,
   not buying power, is the binding constraint. Raising leverage changed nothing except fees and the kill switch.
6. **The single biggest lever is not an algorithm.** €1,647 compounding at an excellent 20%/yr is ~€27/month.
   A steady monthly deposit beats any parameter you can tune.

---

## 2. What the October statement shows

| Measure | Value |
|---|---|
| Portfolio 1 Oct → 31 Oct | €1,980.72 → €1,899.04 (−€81.69), with large deposits/withdrawals in between |
| Bot trading window | 12 Oct 03:54 → 22 Oct 01:56 UTC (5 active days) |
| Trade fills | **242** (all against USDC: BTC, SOL, DOGE, XRP, AVAX) |
| Volume traded | **€17,376**, about **9× the whole account in 10 days** |
| Trading fees | **€40.56**. Fee rate per fill: 0.22% (198 fills), 0.30% (42 fills) |
| Round trips (buys → next sell) | 36; **median gross move +0.30%** |
| Fees per round trip | ~0.44% (2 × 0.22%) at best |
| **Round trips that lost money after fees** | **83%** |
| Duplicate fills (same second, size and price, different order IDs) | **40 clusters, 49 extra fills, ~4,042 USDC of unintended volume** |
| Inventory left behind (approx. cost → value on 13 Nov) | BTC 0.0103 @ ~€74.6k (−€123), DOGE 2,595 @ ~€0.075 (−€39), SOL 1.82 @ ~€95 (−€41) |
| Gross scalping profit on closed round trips | ≈ +31 USDC, **before** €40.56 of fees |
| Withdrawal fees in October | **€19.59** (incl. $13 on a USD withdrawal). On 9 Nov: **$13 to withdraw $100 (13%)** |

How to read this: the bot picked up ~€31 in pennies, paid ~€41 to do it, and is sitting on ~€200 of unrealised
losses in coins it bought on the way down. That is the classic grid-bot failure: small frequent wins, one large
trend loss.

**Duplicates.** Triplets of identical orders in the same second, e.g. 3 × 0.67 SOL at 03:54:23, then 3 × 0.00089
BTC, then 3 × 53 XRP, look like duplicate submissions. Likely causes are two or three copies of the bot running at
once, or retries with no client order ID. Check that only one instance runs anywhere: PC, VPS, a phone app or a
second terminal.

**Funding churn.** Deposit €549 → withdraw €624 the same day → deposit €624 → withdraw €900 → deposit €900 →
withdraw €945 → USD/USDC transfers in and out. Each withdrawal costs €1–€5 (USD: ~$13). It also makes Kraken's P&L
percentages meaningless: "−1,760.61%" and "−2,560.76%" are computed on a near-zero net-deposit base, so ignore them.

**Current allocation (13 Nov)**: 13 assets for €1,647. BTC 53.8%, DOGE 9.4%, SOL 8.9%, USDC 8.3%, EUR 7.3%,
XRP 2.2%, USDG 1.6%, ATOM 1.0%, plus TRX/AVAX/DOT/BABY dust and fractions of a cent of CRWDx/GLDx/PLTRx. Every
crypto position is down 17–25%. That's not diversification. Small leftovers like these add decisions without
adding return.

---

## 3. APEX v30 teardown

Run in the sandbox: with no keys it crashes at boot (`AuthenticationError`), as expected. With keys it gets past
boot and hits defect 1. Defects 2–17 were confirmed in the simulator (`legacy/apex_v30.py` driven unchanged,
`datetime` patched in) and against Kraken's live market list.

| # | Defect | Evidence | Consequence |
|---|---|---|---|
| 1 | `datetime` not imported | pyflakes: `undefined name 'datetime'`; sim: 0 cycles in 20 loops | Bot never trades; the error is swallowed forever |
| 2 | Bare `except Exception: pass` in the grid and sweeper | code | Auth, funds, rate-limit and precision errors are all invisible. You cannot know it is broken |
| 3 | Minimum spread 0.2% each side vs 0.22% fee per side | statement: median round trip +0.30% gross, ~0.44% fees | **Negative expectancy by construction**: 83% of round trips lost money |
| 4 | Sell price = *current* bid + spread, not cost basis | code | In a decline it books losses and calls them grid profits |
| 5 | Only one sell order allowed; it is never re-priced | sim: resting sells 7–28% above market; most coins not covered by any sell | Inventory piles up: 82 buys vs 7 sells in 5 days |
| 6 | No stop-loss, no max inventory, no drawdown limit | code | Unlimited bag-holding in a trend; exactly what happened to BTC/DOGE/SOL |
| 7 | "Falling knife" filter = 15-minute RSI < 30 | code | Does nothing in a multi-week downtrend (RSI hovers at 35–50 while price falls 15%) |
| 8 | RSI returns **0** when there were no down moves (`rs = 0` instead of ∞) | code | A strong rally reads as "oversold" and halts buying at the wrong time |
| 9 | 200-EMA "Dynamic Delta Hedging" only writes `os.environ` | code | No hedge exists. The futures client is loaded and never trades |
| 10 | Futures symbols `PI_XBTUSD`, `PI_SOLUSD`, `PI_XDGUSD` | Kraken Futures instrument list today: **not listed**. Only `PF_XBTUSD`, `PF_SOLUSD`, `PF_DOGEUSD` (linear, USD-settled) | Any hedge code would fail. Inverse contracts would also be coin-margined, not USDC |
| 11 | Futures → spot sweep via spot `WalletTransfer` | Kraken documents that endpoint as spot → futures only | Sweeper always fails, silently (defect 2) |
| 12 | Cleanse sweeps `USDT/USDC` | Kraken spot market list: **USDT/USDC not listed** | Fails every boot |
| 13 | Cleanse buys `USDC/EUR` with `amount = EUR balance` | ccxt `amount` is in base (USDC) | Tries to buy more USDC than the EUR can pay for: rejected, or wrong size |
| 14 | Converts an EUR account into USDC | statement: all 242 fills vs USDC | Adds EUR/USD FX risk and conversion fees to every decision; USD withdrawals cost ~$13 |
| 15 | Hard-coded price/size decimals | Kraken: DOGE/EUR has 7 price decimals, DOGE/USDC 5, BTC/EUR 1 | Rejected or rounded orders. Use `price_to_precision` / `amount_to_precision` |
| 16 | No client order IDs or idempotency | statement: 49 duplicate fills | Retries and restarts duplicate orders |
| 17 | Balance read once per cycle, not decremented across assets | code | The 3 assets can over-commit the same cash |
| 18 | 15-second loop, 250 candles × 3 pairs re-downloaded, buys cancel/replace on 0.15% drift | sim: 179 cancels for 271 creates in 5 days | Rate-limit pressure and Kraken's order-churn counter, for no edge |
| 19 | Exchange sessions never closed on error paths | sandbox warnings | Resource leaks on long runs |

What was good, and is kept in the rebuild:
- post-only limit orders (maker fees);
- a cash reserve;
- volatility awareness, though it was used in the wrong direction (wider bands while buying *more*);
- a trend filter as an idea, though it was never wired to anything.

---

## 4. Rebuild: `krakenbot/`

### Strategy (and why this one)
| Approach | Fit for a €1.6k Kraken retail account |
|---|---|
| Grid / mean reversion (APEX) | Works in ranges, dies in trends, and fees eat a 0.2–0.4% edge. **Rejected: measured to lose.** |
| Order flow / market making | Needs L2/L3 data, low latency and a better fee tier than 0.22–0.40%. Not competitive. |
| Statistical arbitrage | Needs many pairs, low fees and shorting. Costs swamp it at this size. |
| Smart Money Concepts | Discretionary pattern language with no testable, codifiable definition. Not automatable honestly. |
| Funding carry (long spot + short `PF_` perp) | Real and market-neutral, but needs futures access and margin monitoring, and yields €-tens/yr at this size. A possible phase 2. |
| **Time-series momentum + vol targeting** | **The best-documented systematic edge in crypto. Trades rarely, so fees stay small. Steps aside in bear markets. Chosen.** |

**Logic** (daily bars, EUR pairs BTC/ETH/SOL):
- **Signal**: the fraction of 20/60/120-day returns that are positive (0, ⅓, ⅔ or 1), and zero below the 100-day EMA.
  Averaging three horizons avoids betting on one lucky lookback.
- **Size**: `signal × min(1, 35% / realised vol)`, capped at 45% per asset and at
  `2% equity ÷ (3 × ATR / price)`, so a hard-stop hit costs at most 2% of equity. 5% cash reserve; gross ≤ 100%.
- **Exits**: the signal goes to zero, **or** an exchange-side `stop-loss` at entry − 3·ATR, ratcheted up as a
  chandelier trail (highest close − 3.5·ATR). After a stop, there's no re-entry until a new high or a fresh signal.
- **Kill switch**: −25% from the equity peak flattens everything and halts until a human resets it.

**Execution and resilience**
- Runs once per day after the 00:00 UTC close. The forming candle is dropped, so there is no look-ahead.
- Post-only limit at the touch, re-priced up to 4 times, then an IOC limit capped at mid ± 0.3%. That cap is the
  slippage control.
- Quote sanity: rejects crossed books, spreads > 0.4% and quotes older than 30 s. On bad data it skips the run and
  leaves the stops in place.
- Deterministic `cl_ord_id` per (day, pair, side, attempt). After a timeout it looks the order up before re-sending,
  so there are no duplicates (tested).
- Retries with backoff + jitter only for transient errors. Auth, funds and invalid-order errors are raised, never
  swallowed.
- State is written atomically after every fill. Re-running the same day is a no-op (tested). If you sell coins by
  hand, the ledger notices and clamps.
- **Sub-account ledger**: it only trades the EUR `--capital` you give it and the coins *it* bought. Your existing
  BTC/DOGE/SOL are never touched.
- No dead-man switch (`CancelAllOrdersAfter`), on purpose: on Kraken it cancels **all** orders, including the
  protective stops.
- A position that cannot get a stop after 3 attempts is flattened. A naked position is not allowed to exist.

### Backtest (Kraken daily data, 24 Nov 2024 → 12 Nov 2026, fees 0.25% + 0.10% slippage per side)

| | Return | CAGR | Max DD | Sharpe | Trades | Fees |
|---|---|---|---|---|---|---|
| **krakenbot (default)** | **+4.6%** | +2.3% | **−19.1%** | 0.24 | 101 | €71 |
| Buy & hold BTC | −30.8% | −17.1% | −51.7% | −0.23 | – | – |
| Buy & hold BTC/ETH/SOL | −47.7% | −28.1% | −62.0% | −0.31 | – | – |
| Buy & hold your mix (BTC/SOL/DOGE/XRP) | −54.2% | −32.8% | −70.0% | −0.33 | – | – |

Sensitivity (same period):

| Change | Return | Max DD | Note |
|---|---|---|---|
| risk 1% / trade | +2.8% | −9.8% | half the risk, half the return |
| risk 3% / trade | −6.9% | −25.0% | **kill switch fired** |
| risk 3% + 1.5× margin | −7.1% | −25.3% | margin never used |
| risk 5% + 2× margin | −0.7% | −25.4% | more fees, same outcome |
| add XRP + DOGE | −11.3% | −25.4% | meme/alt exposure hurts |
| taker fees 0.40% + 0.20% slippage | +0.1% | −21.7% | why post-only matters |

---

## 5. What I don't know, and the weaknesses of the rebuild

- **Two years is one regime.** Kraken's OHLC API serves the last 720 daily bars, and this window was mostly a
  bear market. Trend following is built to lose little in bears and catch big bull trends. This sample only proves
  the first half.
- **Robustness grid (81 neighbouring parameter sets):** median CAGR ≈ 0%, 44% positive, 89% kept max drawdown
  better than −25%. Read that as: **the drawdown control is robust, the return is not proven.** Don't
  re-optimise until the best cell looks great; that's curve-fitting.
- Average exposure is only ~16%, so most of the time the capital sits in EUR. That is the point in a bear market,
  and it is also why returns are small.
- Exchange stops are stop-*market*. A crash gaps through them, and the backtest fills gaps at the open, but
  real slippage can be worse.
- The paper broker fills post-only orders at once. Live, some will miss and fall back to the capped IOC.
- ccxt's docstring marks `stopLossPrice` "margin only", but Kraken Pro supports spot stop-loss orders. **Confirm
  with `validate` mode and your keys before going live.** `validate` sends Kraken's `validate=true`: Kraken checks
  the order and places nothing.

---

## 6. Margin and derivatives: what Kraken allows and what to do with it

- Kraken spot metadata today lists **2×–10× margin** on BTC/EUR, ETH/EUR, SOL/EUR and the USDC pairs. Your account
  shows a margin wallet ("Healthy", €1,552 available).
- Kraken Futures lists **linear `PF_` perpetuals** (PF_XBTUSD, PF_SOLUSD at 1% initial margin; PF_DOGEUSD at 2%).
  EU access runs through Kraken's regulated derivatives entity, so check your eligibility in the app.
- Margin costs: an opening fee plus a **rollover fee every 4 hours**. I haven't verified the current rates. Check
  Kraken's margin fee page. Historically they have been in the 0.01–0.02% per 4 h range, which is roughly 20–40% a
  year if held.

**Recommendation: don't use margin.** The evidence above shows that leverage doesn't add return to this system,
only fees and a faster route to the kill switch. Your account's history is a trend drawdown; leverage multiplies
exactly that. `max_gross` stays at 1.0, and `allow_short` stays off. The one margin use worth revisiting later is a
**hedged** funding-carry trade, after 3+ months of clean live running.

---

## 7. What to do with the portfolio

1. **Stop APEX now.** Cancel its open orders. Make sure no second copy is running anywhere (see the duplicates in §2).
2. **Stop the funding churn.** Deposit on a schedule, withdraw rarely and in EUR via SEPA (€1), never USD (~$13).
3. **Clear out the dust.** XRP, ATOM, TRX, USDG, AVAX, DOT, BABY and the xStocks fractions total about €100 across
   8+ positions. Convert them to **EUR**, not USDC: you live in euros.
4. **Decide on the bags with a forward-looking rule, not your purchase price.** Cost basis is sunk. Today the trend
   model says BTC, ETH and SOL are all in downtrends (target weight 0). Choose one, deliberately:
   - **Core holding:** keep some BTC as a long-term position you will not trade, sized so that a 50–70% drawdown
     wouldn't make you sell, or
   - **Hand it to the system:** sell to EUR and give that EUR to `krakenbot` as `--capital`.
   DOGE has no role in either plan.
5. **Run the new bot in stages:** `backtest` → `paper` daily for 2–4 weeks → `validate` with keys →
   `live` with €300–500 → scale up only after 2–3 months that match the paper record.
6. **Fund growth with contributions.** €100–200 a month of new capital will move this account more than any
   strategy can on a €1.6k base.
