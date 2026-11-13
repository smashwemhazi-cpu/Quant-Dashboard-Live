# krakenbot

A two-sleeve momentum bot for **Kraken spot, EUR pairs**. Long-only; no perps, futures, margin or currency
conversions.

- **Dual momentum (60%):** BTC or gold (PAXG), whichever is stronger and in an uptrend, otherwise EUR.
- **Rotation (40%):** the top 2 of BTC, ETH, SOL, XRP, DOGE, LINK and gold, each only if it is in an uptrend.

It rebalances weekly. Every holding gets a disaster stop-loss resting on Kraken.

A second engine, **harvest**, keeps its own ledger. It holds ⅓ BTC, ⅓ gold and ⅓ EUR, and rebalances back whenever
a weight drifts ±10 points: it sells what ran up and buys what fell. `rebuild` splits the account 50/50 between the
two engines.

Read [REVIEW.md](REVIEW.md) for the evidence, including every volatility and arbitrage idea that was tested and
rejected (§3b), and the limits.

## Install
```bash
pip install ccxt pandas numpy
```

## Use it in this order
1. See what the strategy wants today. This is read-only and needs no keys.
```bash
python -m bots.kraken.krakenbot.run report
```
2. See the backtest on Kraken EUR data.
```bash
python -m bots.kraken.krakenbot.run backtest
```
3. See how your current account maps onto the strategy. This is only a plan; it needs read-only keys.
```bash
KRAKEN_API_KEY=... KRAKEN_API_SECRET=... python -m bots.kraken.krakenbot.run rebuild
```
4. Have Kraken validate every order in that plan. Nothing is placed.
```bash
KRAKEN_API_KEY=... KRAKEN_API_SECRET=... python -m bots.kraken.krakenbot.run rebuild --validate
```
5. Execute the plan:
   - sell coins that are not in the strategy to EUR;
   - adopt BTC, ETH, SOL, XRP, DOGE, LINK and PAXG into the momentum ledger;
   - fund the harvest ledger with `--harvest-share` (default 0.5) of the account, from EUR;
   - rebalance both engines immediately and put stops on Kraken.
```bash
KRAKEN_API_KEY=... KRAKEN_API_SECRET=... python -m bots.kraken.krakenbot.run rebuild --i-understand-the-risk
```
6. From then on, run once a day, a few minutes after 00:00 UTC. Momentum rebalances on Mondays and catches up if a
   week was missed. Harvest rebalances whenever its drift exceeds the band. Both check their stops every day.
```bash
KRAKEN_API_KEY=... KRAKEN_API_SECRET=... python -m bots.kraken.krakenbot.run live --i-understand-the-risk --log krakenbot.log
```

`rebuild` never touches USD, USDC, USDT, USDG or anything in Earn/staking. It lists them so you can decide.
Paper mode (`paper --capital 800 --harvest-capital 800`) runs on live public data with simulated fills.

**API key permissions:** Query Funds, Query Open Orders & Trades, Query Closed Orders & Trades, Create & Modify
Orders, Cancel/Close Orders. **Never enable Withdraw Funds.**

Cron example:
```bash
5 0 * * * cd /path/to/Quant-Dashboard-Live && KRAKEN_API_KEY=... KRAKEN_API_SECRET=... python3 -m bots.kraken.krakenbot.run live --i-understand-the-risk --log krakenbot.log
```

## Safety
- **No currency conversions** (EUR→USDC and similar) unless you set `allow_conversions = true` and list the exact
  pair in `authorised_conversions`. Only EUR pairs are traded.
- **Disaster stop** on Kraken after every fill: price × (1 − 4 × weekly volatility). It is raised weekly and never
  lowered. If the price is already through it, the bot sells. A holding that can't get a stop is sold.
- **Kill switch:** a −35% drawdown from the peak sells everything and halts. To resume after reviewing, delete the
  `"guard"` block in `krakenbot_state.json`.
- **Idempotent:** a crashed run can simply be re-run. Each order has a deterministic `cl_ord_id`, so a timeout
  cannot duplicate it.

## Configuration
All settings live in `krakenbot/config.py`. Override them with `--config my.toml` or with `KRAKENBOT_<NAME>`
environment variables, for example:
```bash
KRAKENBOT_DUAL_WEIGHT=0.7 KRAKENBOT_ROTATION_WEIGHT=0.3 python -m bots.kraken.krakenbot.run report
```

## Tests
```bash
python -m pytest -q tests/test_krakenbot.py
```
