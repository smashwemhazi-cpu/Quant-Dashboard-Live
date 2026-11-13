"""Command line.

  python -m bots.kraken.krakenbot.run backtest                      # 2y daily backtest on Kraken data
  python -m bots.kraken.krakenbot.run paper --capital 1000          # live public data, simulated fills
  python -m bots.kraken.krakenbot.run validate --capital 1000       # real keys, Kraken validates but places nothing
  python -m bots.kraken.krakenbot.run live --capital 1000 --i-understand-the-risk

API keys come from KRAKEN_API_KEY / KRAKEN_API_SECRET. Create a key with ONLY: Query Funds, Query Open Orders &
Trades, Query Closed Orders & Trades, Create & Modify Orders, Cancel/Close Orders. NEVER enable Withdraw Funds.
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
    handlers = [logging.StreamHandler(sys.stdout)]
    if path:
        handlers.append(logging.FileHandler(path))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def cmd_backtest(cfg, args) -> int:
    from .backtest import buy_and_hold, run, Result
    from .broker import KrakenBroker
    from .signals import to_frame
    import pandas as pd

    md = KrakenBroker(cfg)  # public endpoints only
    data = {a: to_frame(md.daily_bars(s, 720)) for a, s in zip(cfg.assets, cfg.symbols)}
    res = run(data, cfg, start_equity=args.capital or 1600.0)
    bh = buy_and_hold(data, args.capital or 1600.0, cfg.fee_rate)
    out = {"strategy": res.stats(), "buy_and_hold": Result(bh, pd.DataFrame(columns=["notional"]), 0, 0).stats()}
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_trade(cfg, args) -> int:
    from .broker import KrakenBroker, PaperBroker
    from .engine import Engine, State

    if cfg.mode == "live" and not args.i_understand_the_risk:
        print("Refusing to trade live without --i-understand-the-risk. Run `validate` first.", file=sys.stderr)
        return 2
    if cfg.mode == "paper":
        broker = PaperBroker(cfg, KrakenBroker(cfg))
    else:
        key, sec = os.environ.get("KRAKEN_API_KEY", ""), os.environ.get("KRAKEN_API_SECRET", "")
        if not key or not sec:
            print("KRAKEN_API_KEY / KRAKEN_API_SECRET not set.", file=sys.stderr)
            return 2
        broker = KrakenBroker(cfg, key, sec)
    state = State.load(cfg.state_file, cfg, args.capital)
    result = Engine(cfg, broker, state, cfg.state_file).run_once()
    print(json.dumps(result, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="krakenbot")
    ap.add_argument("command", choices=["backtest", "paper", "validate", "live"])
    ap.add_argument("--config", help="TOML file with overrides")
    ap.add_argument("--capital", type=float, help="EUR the bot may manage (first run only)")
    ap.add_argument("--log", help="also log to this file")
    ap.add_argument("--i-understand-the-risk", action="store_true")
    args = ap.parse_args(argv)
    _setup_logging(args.log)
    cfg = load_config(args.config)
    if args.command != "backtest":
        default_state = cfg.state_file if cfg.state_file != "krakenbot_state.json" else f"krakenbot_{args.command}_state.json"
        cfg = dc.replace(cfg, mode=args.command if args.command != "paper" else "paper", state_file=default_state).validate()
        return cmd_trade(cfg, args)
    return cmd_backtest(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
