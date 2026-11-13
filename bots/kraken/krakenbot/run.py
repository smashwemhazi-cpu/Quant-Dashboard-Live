"""Command line.

  python -m bots.kraken.krakenbot.run report --assets BTC,ETH,SOL,DOGE         # read-only market structure
  python -m bots.kraken.krakenbot.run backtest                                   # 2y daily backtest on Kraken EUR data
  python -m bots.kraken.krakenbot.run paper --capital 1000                       # live public data, simulated fills
  python -m bots.kraken.krakenbot.run validate --capital 1000                    # real keys; Kraken checks, places nothing
  python -m bots.kraken.krakenbot.run live --capital 1000 --i-understand-the-risk
  python -m bots.kraken.krakenbot.run protect --hold BTC=0.01414 --hold SOL=2.0  # plan stops for coins you hold
  python -m bots.kraken.krakenbot.run protect --hold BTC=0.01414 --i-understand-the-risk   # ...and place them

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


def _keys() -> tuple[str, str]:
    key, sec = os.environ.get("KRAKEN_API_KEY", ""), os.environ.get("KRAKEN_API_SECRET", "")
    if not key or not sec:
        raise SystemExit("KRAKEN_API_KEY / KRAKEN_API_SECRET not set.")
    return key, sec


def cmd_backtest(cfg, args) -> int:
    from .backtest import buy_and_hold, perf, round_trips, run
    from .broker import KrakenBroker
    from .signals import to_frame

    md = KrakenBroker(cfg)  # public endpoints only
    data = {a: to_frame(md.daily_bars(s, 720)) for a, s in zip(cfg.assets, cfg.symbols)}
    res = run(data, cfg, start_equity=args.capital or 1600.0)
    rt = round_trips(res.trades)
    out = {"strategy": res.stats(), "buy_and_hold": perf(buy_and_hold(data, args.capital or 1600.0, cfg.fee_rate)),
           "by_model": (rt.groupby("sleeve")["pnl"].agg(["count", "sum"]).round(2).to_dict("index") if len(rt) else {})}
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_report(cfg, args) -> int:
    from .broker import KrakenBroker
    from .protect import market_report

    assets = [a.strip().upper() for a in (args.assets or "BTC,ETH,SOL,DOGE").split(",") if a.strip()]
    print(json.dumps(market_report(KrakenBroker(cfg), cfg, assets, args.max_atr), indent=2, default=str))
    return 0


def cmd_protect(cfg, args) -> int:
    from .broker import KrakenBroker
    from .protect import protect_holdings

    holds = {}
    for h in args.hold or []:
        a, _, q = h.partition("=")
        holds[a.strip().upper()] = float(q)
    if not holds:
        raise SystemExit("Name what to protect, e.g. --hold BTC=0.01414 --hold SOL=2.00989")
    place = bool(args.i_understand_the_risk)
    if place or args.validate:
        key, sec = _keys()
        broker = KrakenBroker(dc.replace(cfg, mode="validate" if args.validate else "live"), key, sec)
        cfg = broker.cfg
    else:
        broker = KrakenBroker(cfg)
    res = protect_holdings(broker, cfg, holds, args.state or "krakenbot_protect_state.json", place, args.max_atr)
    print(json.dumps(res, indent=2, default=str))
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
        broker = KrakenBroker(cfg, *_keys())
    state = State.load(cfg.state_file, cfg, args.capital)
    result = Engine(cfg, broker, state, cfg.state_file).run_once()
    print(json.dumps(result, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="krakenbot")
    ap.add_argument("command", choices=["report", "backtest", "paper", "validate", "live", "protect"])
    ap.add_argument("--config", help="TOML file with overrides")
    ap.add_argument("--capital", type=float, help="EUR the bot may manage (first run only)")
    ap.add_argument("--assets", help="report: comma-separated coins")
    ap.add_argument("--hold", action="append", help="protect: COIN=QTY you already hold (repeatable)")
    ap.add_argument("--max-atr", type=float, default=4.0, help="report/protect: widest stop in ATRs (default 4)")
    ap.add_argument("--state", help="protect: state file")
    ap.add_argument("--validate", action="store_true", help="protect: have Kraken validate, place nothing")
    ap.add_argument("--log", help="also log to this file")
    ap.add_argument("--i-understand-the-risk", action="store_true")
    args = ap.parse_args(argv)
    _setup_logging(args.log)
    cfg = load_config(args.config)
    if args.command == "report":
        return cmd_report(cfg, args)
    if args.command == "backtest":
        return cmd_backtest(cfg, args)
    if args.command == "protect":
        return cmd_protect(cfg, args)
    default_state = cfg.state_file if cfg.state_file != "krakenbot_state.json" else f"krakenbot_{args.command}_state.json"
    cfg = dc.replace(cfg, mode=args.command, state_file=default_state).validate()
    return cmd_trade(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
