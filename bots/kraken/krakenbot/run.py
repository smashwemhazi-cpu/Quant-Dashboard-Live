"""Command line.

  python -m bots.kraken.krakenbot.run report                       # what both sleeves want today (read-only)
  python -m bots.kraken.krakenbot.run backtest                     # 2y backtest on Kraken EUR data
  python -m bots.kraken.krakenbot.run rebuild                      # plan: how your CURRENT account maps to the strategy
  python -m bots.kraken.krakenbot.run rebuild --validate           # Kraken checks every order, places nothing
  python -m bots.kraken.krakenbot.run rebuild --i-understand-the-risk   # sweep, adopt, first rebalance
  python -m bots.kraken.krakenbot.run live --i-understand-the-risk # the daily run (cron, 00:05 UTC)
  python -m bots.kraken.krakenbot.run paper --capital 1600         # simulated fills on live public data

API keys: KRAKEN_API_KEY / KRAKEN_API_SECRET with ONLY Query Funds, Query Open Orders & Trades, Query Closed
Orders & Trades, Create & Modify Orders, Cancel/Close Orders. NEVER enable Withdraw Funds.
"""
from __future__ import annotations

import argparse
import dataclasses as dc
import json
import logging
import os
import sys

from .config import load_config


def _setup_logging(path: str | None) -> None:
    handlers = [logging.StreamHandler(sys.stdout)] + ([logging.FileHandler(path)] if path else [])
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _keys() -> tuple[str, str]:
    key, sec = os.environ.get("KRAKEN_API_KEY", ""), os.environ.get("KRAKEN_API_SECRET", "")
    if not key or not sec:
        raise SystemExit("KRAKEN_API_KEY / KRAKEN_API_SECRET not set.")
    return key, sec


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="krakenbot")
    ap.add_argument("command", choices=["report", "backtest", "rebuild", "paper", "validate", "live"])
    ap.add_argument("--config", help="TOML file with overrides")
    ap.add_argument("--capital", type=float, help="paper/live first run without rebuild: EUR the bot may manage")
    ap.add_argument("--validate", action="store_true", help="rebuild: Kraken validates, nothing is placed")
    ap.add_argument("--log", help="also log to this file")
    ap.add_argument("--i-understand-the-risk", action="store_true")
    args = ap.parse_args(argv)
    _setup_logging(args.log)
    cfg = load_config(args.config)

    from .broker import KrakenBroker, PaperBroker

    if args.command == "report":
        from .portfolio import market_report
        _print(market_report(KrakenBroker(cfg), cfg))
        return 0

    if args.command == "backtest":
        from .backtest import buy_and_hold, perf, run
        from .signals import to_frame
        md = KrakenBroker(cfg)
        data = {a: to_frame(md.daily_bars(s, 720)) for a, s in zip(cfg.assets, cfg.symbols)}
        res = run(data, cfg, start_equity=args.capital or 1600.0)
        first = res.holdings.sum(axis=1).ne(0).idxmax()
        eq = res.equity[res.equity.index >= first]
        _print({"strategy": perf(eq, len(res.trades), res.fees, res.stops, res.halted_on),
                "hold_btc": perf(buy_and_hold(data, ["BTC"], start=first)),
                "hold_gold": perf(buy_and_hold(data, ["PAXG"], start=first)),
                "note": "Kraken serves 720 daily bars; 160 are warm-up. See REVIEW.md for the 4.6-year test."})
        return 0

    state_file = cfg.state_file if cfg.state_file != "krakenbot_state.json" else (
        "krakenbot_paper_state.json" if args.command == "paper" else "krakenbot_state.json")

    if args.command == "rebuild":
        from .portfolio import execute_rebuild, plan_rebuild
        execute = args.i_understand_the_risk or args.validate
        mode = "validate" if args.validate else ("live" if args.i_understand_the_risk else "paper")
        cfg = dc.replace(cfg, mode=mode, state_file=state_file).validate()
        broker = KrakenBroker(cfg, *_keys())
        plan = plan_rebuild(broker, cfg)
        _print(plan)
        if not execute:
            print("\nPlan only. Add --validate to have Kraken check it, or --i-understand-the-risk to execute.")
            return 0
        if mode == "live" and os.path.exists(state_file):
            raise SystemExit(f"{state_file} already exists: the account is already managed. Move it away to rebuild.")
        _print(execute_rebuild(broker, cfg, plan, state_file))
        return 0

    from .engine import Engine, State
    cfg = dc.replace(cfg, mode=args.command, state_file=state_file).validate()
    if cfg.mode == "live" and not args.i_understand_the_risk:
        print("Refusing to trade live without --i-understand-the-risk. Run `rebuild --validate` first.", file=sys.stderr)
        return 2
    broker = PaperBroker(cfg, KrakenBroker(cfg)) if cfg.mode == "paper" else KrakenBroker(cfg, *_keys())
    state = State.load(cfg.state_file, cfg, args.capital)
    _print(Engine(cfg, broker, state, cfg.state_file).run_once())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
