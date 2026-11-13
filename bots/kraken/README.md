# krakenbot

A daily trend-following bot for Kraken spot with volatility-targeted sizing and exchange-side stops. It trades
BTC, ETH and SOL against EUR, long-only, with no leverage by default.

Read [REVIEW.md](REVIEW.md) first. It covers why APEX v30 (`legacy/apex_v30.py`, kept for reference, **do not
run**) was replaced, the backtest, and its limits.

## Install
```bash
pip install ccxt pandas numpy
```

## Safety defaults
- **No currency conversions.** EUR→USDC, USD→USDC and the like are refused unless you set `allow_conversions = true`
  and list the exact pair in `authorised_conversions`. Only EUR pairs are traded.
- **Every position gets a Kraken stop-loss immediately after each fill.** It sits under the last swing low (1–3 ATR),
  only moves up, and the position size is set so a hit costs at most 2% of equity.
- Long-only, no margin, 25% drawdown kill switch, one run per day.

## Entry models
`entry_models` chooses which models may open trades: `trend` (default), `range` and `smc`. Range and SMC are
implemented and tested but **off by default**, because they failed out-of-sample (see REVIEW.md §0). Try them in
paper mode:
```bash
KRAKENBOT_ENTRY_MODELS=trend,smc python -m bots.kraken.krakenbot.run paper --capital 1000
```

## Market report and protecting coins you already hold
```bash
python -m bots.kraken.krakenbot.run report --assets BTC,ETH,SOL,DOGE
```
```bash
python -m bots.kraken.krakenbot.run protect --hold BTC=0.01414 --hold SOL=2.00989
```
```bash
KRAKEN_API_KEY=... KRAKEN_API_SECRET=... python -m bots.kraken.krakenbot.run protect --hold BTC=0.01414 --validate
```
```bash
KRAKEN_API_KEY=... KRAKEN_API_SECRET=... python -m bots.kraken.krakenbot.run protect --hold BTC=0.01414 --i-understand-the-risk
```
The first `protect` call only prints the plan. `--validate` has Kraken check the order without placing it.
`--i-understand-the-risk` places it. Re-run `protect` daily and the stops ratchet up under new swing lows; they
never move down. It refuses to place a stop above the current price, because that would be an instant sale, and
asks you to decide instead.

## Stages (run them in this order)
```bash
python -m bots.kraken.krakenbot.run backtest --capital 1600
```
```bash
python -m bots.kraken.krakenbot.run paper --capital 1000
```
```bash
KRAKEN_API_KEY=... KRAKEN_API_SECRET=... python -m bots.kraken.krakenbot.run validate --capital 1000
```
```bash
KRAKEN_API_KEY=... KRAKEN_API_SECRET=... python -m bots.kraken.krakenbot.run live --capital 400 --i-understand-the-risk --log krakenbot.log
```

- `paper` uses public Kraken data and simulated fills. Its state lives in `krakenbot_paper_state.json`.
- `validate` signs real requests with Kraken's `validate=true` flag: Kraken checks every order and places nothing.
  Use it to confirm permissions, minimums and the spot stop-loss order type.
- `--capital` is read only on the first run. After that, the state file is the ledger.

API key permissions: Query Funds, Query Open Orders & Trades, Query Closed Orders & Trades, Create & Modify
Orders, Cancel/Close Orders. **Never enable Withdraw Funds.**

## Schedule
Run once a day, a few minutes after the 00:00 UTC daily close. Cron example:
```bash
5 0 * * * cd /path/to/Quant-Dashboard-Live && KRAKEN_API_KEY=... KRAKEN_API_SECRET=... python3 -m bots.kraken.krakenbot.run live --i-understand-the-risk --log krakenbot.log
```
If a run fails partway, run it again. Same-day reruns resume instead of trading twice.

## Configuration
All parameters are in `krakenbot/config.py`. Override them with a TOML file (`--config my.toml`) or with env vars:
```bash
KRAKENBOT_RISK_PER_TRADE=0.01 KRAKENBOT_ASSETS=BTC,ETH python -m bots.kraken.krakenbot.run paper
```
The most important settings: `risk_per_trade` (2%), `hard_stop_atr` (3), `trail_atr` (3.5),
`max_drawdown_halt` (25%), `max_slippage` (0.3%), `max_gross` (1.0: no margin).

## Kill switch
If equity falls 25% from its peak, the bot flattens every position and refuses to trade. To resume after you have
reviewed what happened, delete the `"guard"` block from the state file. The peak then restarts at current equity.
Clearing only `halted` is not enough: the old peak would trigger the halt again at once.

## Tests
```bash
python -m pytest -q tests/test_krakenbot.py
```
