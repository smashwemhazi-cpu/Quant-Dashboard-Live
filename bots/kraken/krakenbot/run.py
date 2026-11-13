"""Command line.

  python -m bots.kraken.krakenbot.run report                       # what both sleeves want today (read-only)
  python -m bots.kraken.krakenbot.run backtest                     # 2y backtest on Kraken EUR data
  python -m bots.kraken.krakenbot.run rebuild                      # plan: how your CURRENT account maps to the strategy
  python -m bots.kraken.krakenbot.run rebuild --validate           # Kraken checks every order, places nothing
  python -m bots.kraken.krakenbot.run rebuild --i-understand-the-risk   # sweep, adopt, split 50/50 momentum/harvest
  python -m bots.kraken.krakenbot.run live --i-understand-the-risk # the daily run (cron, 00:05 UTC): both engines
  python -m bots.kraken.krakenbot.run paper --capital 800 --harvest-capital 800   # simulated fills, live data
  python -m bots.kraken.krakenbot.run harvest --capital 500 --i-understand-the-risk # start ONLY a harvest ledger

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
    ap.add_argument("command", choices=["report", "backtest", "rebuild", "paper", "validate", "live", "harvest"])
    ap.add_argument("--config", help="TOML file with overrides")
    ap.add_argument("--capital", type=float, help="paper/live first run without rebuild: EUR the bot may manage")
    ap.add_argument("--validate", action="store_true", help="rebuild/harvest: Kraken validates, nothing is placed")
    ap.add_argument("--harvest-share", type=float, default=0.5, help="rebuild: share of the account for harvest")
    ap.add_argument("--harvest-capital", type=float, help="paper/live first run: EUR for the harvest ledger")
    ap.add_argument("--log", help="also log to this file")
    ap.add_argument("--i-understand-the-risk", action="store_true")
    args = ap.parse_args(argv)
    _setup_logging(args.log)
    cfg = load_config(args.config)

    from .broker import KrakenBroker, PaperBroker

    if args.command == "report":
        from .portfolio import harvest_report, market_report
        _print({"momentum": market_report(KrakenBroker(cfg), cfg), "harvest": harvest_report(None, cfg)})
        return 0

    if args.command == "backtest":
        from . import harvest as hv
        from .backtest import buy_and_hold, perf, run
        from .signals import to_frame
        md = KrakenBroker(cfg)
        data = {a: to_frame(md.daily_bars(f"{a}/{cfg.quote}", 720)) for a in set(cfg.assets) | set(cfg.harvest_assets)}
        res = run(data, cfg, start_equity=args.capital or 1600.0)
        first = res.holdings.sum(axis=1).ne(0).idxmax()
        eq = res.equity[res.equity.index >= first]
        h = hv.backtest(data, cfg, start=first)
        blend = 0.5 * eq / eq.iloc[0] + 0.5 * (h.equity / h.equity.iloc[0]).reindex(eq.index).ffill()
        _print({"momentum": perf(eq, len(res.trades), res.fees, res.stops, res.halted_on),
                "harvest": perf(h.equity, len(h.trades), h.fees, h.stops),
                "blend_50_50": perf(blend * 1600),
                "hold_btc": perf(buy_and_hold(data, ["BTC"], start=first)),
                "hold_gold": perf(buy_and_hold(data, ["PAXG"], start=first)),
                "note": "Kraken serves 720 daily bars; 160 are warm-up. See REVIEW.md for the 4.6-year test."})
        return 0

    state_file = cfg.state_file if cfg.state_file != "krakenbot_state.json" else (
        "krakenbot_paper_state.json" if args.command == "paper" else "krakenbot_state.json")

    paper = args.command == "paper"
    if paper:  # simulated fills are instant: no need to wait 90 s per post-only order
        cfg = dc.replace(cfg, harvest_state_file="krakenbot_paper_harvest_state.json", post_only_wait_s=1)

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
        for existing in (state_file, cfg.harvest_state_file):
            if mode == "live" and os.path.exists(existing):
                raise SystemExit(f"{existing} already exists: the account is already managed. Move it away to rebuild.")
        if not 0 <= args.harvest_share <= 0.9:
            raise SystemExit("--harvest-share must be between 0 and 0.9")
        _print(execute_rebuild(broker, cfg, plan, state_file, args.harvest_share))
        return 0

    from .engine import Engine, State
    from .harvest import HarvestEngine
    if args.command == "harvest":
        mode = "validate" if args.validate else ("live" if args.i_understand_the_risk else None)
        if mode is None:
            print("harvest: add --validate (Kraken checks, nothing placed) or --i-understand-the-risk.", file=sys.stderr)
            return 2
        cfg = dc.replace(cfg, mode=mode).validate()
        hv = HarvestEngine.load(cfg, KrakenBroker(cfg, *_keys()), args.capital)
        if os.path.exists(state_file):
            hv.reserved = {a: h.qty for a, h in State.load(state_file, cfg, None).holdings.items()}
        _print(hv.run_once())
        return 0

    cfg = dc.replace(cfg, mode=args.command, state_file=state_file).validate()
    if cfg.mode == "live" and not args.i_understand_the_risk:
        print("Refusing to trade live without --i-understand-the-risk. Run `rebuild --validate` first.", file=sys.stderr)
        return 2
    broker = PaperBroker(cfg, KrakenBroker(cfg)) if cfg.mode == "paper" else KrakenBroker(cfg, *_keys())
    out, failed = {}, False
    harvest_exists = os.path.exists(cfg.harvest_state_file) or bool(args.harvest_capital)
    hv = HarvestEngine.load(cfg, broker, args.harvest_capital) if harvest_exists else None
    mom = None
    if os.path.exists(cfg.state_file) or args.capital:
        mom = Engine(cfg, broker, State.load(cfg.state_file, cfg, args.capital), cfg.state_file)
        if hv:
            mom.reserved = {a: h.qty for a, h in hv.st.holdings.items()}
    # Each engine runs on its own: a crash in one must not stop the other. Stops already on Kraken stay put.
    for name, eng in (("momentum", mom), ("harvest", hv)):
        if eng is None:
            continue
        if name == "harvest" and mom is not None:
            eng.reserved = {a: h.qty for a, h in mom.st.holdings.items()}
        try:
            out[name] = eng.run_once()
        except Exception as e:  # noqa: BLE001 - logged loudly, reported via the exit code
            logging.getLogger("krakenbot").exception("%s engine failed", name)
            out[name] = {"status": "FAILED", "error": repr(e)}
            failed = True
    if not out:
        raise SystemExit("Nothing to run: start with `rebuild`, or pass --capital / --harvest-capital.")
    _print(out)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
