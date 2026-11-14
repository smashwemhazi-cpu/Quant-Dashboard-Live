"""`report` and `rebuild`: what the strategy wants today, and how to get your CURRENT account there.

rebuild = sweep the account clean and hand it to the bot:
  * coins the strategy can hold (BTC, ETH, SOL, XRP, DOGE, LINK, PAXG) are ADOPTED into the bot's ledger as they
    are (no sell-and-rebuy fees), each with a disaster stop; the first rebalance then trims/adds to target;
  * every other coin (ATOM, TRX, AVAX, DOT, BABY, xStocks...) is SOLD to EUR, if it is above Kraken's minimum;
  * fiat/stablecoins (USD, USDC, USDG, USDT) are NOT touched: converting them is your decision
    (allow_conversions + the exact pair); they are listed so you can decide;
  * anything in Earn/staking (balance codes with a suffix such as `.F`/`.S`/`.M`, or not "free") is listed, not
    touched: unstake it in the app first if you want it included;
  * dust below Kraken's minimum order is listed for Kraken's own "Convert small balances" button.
Default is a dry-run plan. --validate has Kraken check every order without placing it. --i-understand-the-risk
executes, writes the bot's state file and runs the first rebalance immediately.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time

from .broker import BelowMinimum, BrokerError, assert_tradeable
from .config import FIAT_AND_STABLES, Config
from .engine import Engine, State
from .harvest import HarvestEngine
from .harvest import targets as harvest_targets
from .risk import Holding, OrderPlan, check_quote, stop_level
from .signals import features, target_weights, to_frame

log = logging.getLogger("krakenbot.portfolio")


def harvest_report(broker, cfg: Config) -> dict:
    out = {"targets": harvest_targets(cfg), "eur_target": round(1 - sum(cfg.harvest_targets), 4),
           "rebalance_band": cfg.harvest_band}
    if os.path.exists(cfg.harvest_state_file):
        with open(cfg.harvest_state_file) as fh:
            st = json.load(fh)
        out["last"] = (st.get("history") or [None])[-1]
    return out


def market_report(broker, cfg: Config) -> dict:
    rows = {}
    for a in cfg.assets:
        f = features(to_frame(broker.daily_bars(f"{a}/{cfg.quote}", 400)), cfg)
        rows[a] = f.iloc[-1]
    w, tables = target_weights(rows, cfg)
    bar = min(r.name for r in rows.values())
    return {"bar": str(bar.date()), "targets": {a: round(v, 4) for a, v in w.items() if v},
            "cash_weight": round(1 - sum(w.values()), 4),
            "sleeves": {k: v.round(4).reset_index().rename(columns={"index": "asset"}).to_dict("records")
                        for k, v in tables.items()},
            "disaster_stops_if_bought_now": {a: round(stop_level(float(r["close"]), float(r["vol"]), cfg), 6)
                                             for a, r in rows.items() if w.get(a)}}


def _price(broker, sym: str, cfg: Config) -> float | None:
    try:
        bid, ask, ts = broker.top_of_book(sym)
        return check_quote(bid, ask, ts, cfg)
    except Exception:  # noqa: BLE001 - unlisted pair, wide spread, etc.: reported as "no EUR price"
        return None


def plan_rebuild(broker, cfg: Config) -> dict:
    bal = broker.balances()
    keep, sell, currencies, locked, dust, unknown = [], [], [], [], [], []
    eur = bal.get(cfg.quote, {}).get("free", 0.0)
    for code, b in sorted(bal.items()):
        total, free = float(b.get("total") or 0), float(b.get("free") or 0)
        if total <= 0 or code == cfg.quote:
            continue
        if "." in code:   # .F is merged into its base by the broker; .S/.M/.B/.T are separate products
            locked.append({"asset": code, "qty": total,
                           "note": "staked/opt-in/yield/tokenized product (not tradable as is): unstake in the app"})
            continue
        if code in FIAT_AND_STABLES:
            currencies.append({"asset": code, "qty": total,
                               "note": "currency: NOT converted unless you authorise it (allow_conversions + pair)"})
            continue
        px = _price(broker, f"{code}/{cfg.quote}", cfg)
        if px is None:
            unknown.append({"asset": code, "qty": total, "note": f"no usable {code}/{cfg.quote} market: handle manually"})
            continue
        item = {"asset": code, "qty": free, "price": px, "value": free * px}
        if free < total:
            locked.append({"asset": code, "qty": total - free, "note": "reserved by open orders or locked"})
        if free <= 0:
            continue
        try:
            broker.normalise(f"{code}/{cfg.quote}", free, px)
        except BelowMinimum:
            dust.append({**item, "note": "below Kraken minimum: use 'Convert small balances' in the app"})
            continue
        except BrokerError as e:
            unknown.append({**item, "note": str(e)})
            continue
        (keep if code in cfg.assets else sell).append(item)
    total = eur + sum(i["value"] for i in keep + sell)
    return {"eur_cash": eur, "adopt": keep, "sell_to_eur": sell, "currencies_untouched": currencies,
            "locked_untouched": locked, "dust": dust, "needs_manual": unknown, "investable_eur": total,
            "after_rebuild_bot_manages_eur": total}


def execute_rebuild(broker, cfg: Config, plan: dict, state_path: str, harvest_share: float = 0.0,
                    sleep=time.sleep) -> dict:
    """Sell non-strategy coins, adopt strategy coins, split the EUR between the momentum ledger and the harvest
    ledger (`harvest_share` of the whole investable value, funded from EUR), then rebalance both at once."""
    state = State(cash=plan["eur_cash"])
    for a in cfg.assets:
        state.holdings[a] = Holding()
    eng = Engine(cfg, broker, state, state_path, sleep=sleep)
    eng.run_tag = "rebuild"
    for it in plan["sell_to_eur"]:
        a = it["asset"]
        assert_tradeable(f"{a}/{cfg.quote}", cfg)
        state.holdings[a] = Holding(qty=it["qty"], entry=it["price"])
        eng.execute(OrderPlan(a, "sell", it["qty"], it["value"], "rebuild: not in strategy"))
        left = state.holdings.pop(a)
        if left.qty > 0:
            log.error("%s: %.8f could not be sold; left untouched in your account", a, left.qty)
    for it in plan["adopt"]:
        f = features(to_frame(broker.daily_bars(f"{it['asset']}/{cfg.quote}", 400)), cfg).iloc[-1]
        vol = float(f["vol"]) if math.isfinite(float(f["vol"])) else float("nan")
        h = Holding(qty=it["qty"], entry=it["price"], high_close=it["price"], stop=stop_level(it["price"], vol, cfg))
        state.holdings[it["asset"]] = h
    harvest_eur = 0.0
    if harvest_share > 0:
        bal = broker.balances() if cfg.mode == "live" else {}
        eur_now = bal.get(cfg.quote, {}).get("free", state.cash) if bal else state.cash
        state.cash = min(state.cash, eur_now)
        total = state.cash + sum(i["value"] for i in plan["adopt"])
        harvest_eur = min(state.cash, harvest_share * total)
        state.cash -= harvest_eur
    if cfg.mode != "validate":
        state.save(state_path)
    mom = Engine(cfg, broker, state, state_path, sleep=sleep)
    result = {"momentum": mom.run_once(force_rebalance=True)}
    if harvest_eur > 0:
        hv = HarvestEngine.load(cfg, broker, harvest_eur, sleep=sleep)
        hv.reserved = {a: h.qty for a, h in mom.st.holdings.items()}
        result["harvest"] = hv.run_once(force_rebalance=True)
        result["harvest"]["capital"] = harvest_eur
        if cfg.mode == "validate":
            result["harvest"]["note"] = "validated, nothing placed"
    return {"rebuild": "done" if cfg.mode != "validate" else "validated (nothing placed)", "first_rebalance": result}
