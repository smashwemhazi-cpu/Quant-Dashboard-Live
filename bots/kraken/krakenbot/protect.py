"""Market-structure report and stop-loss protection for coins you ALREADY hold (outside the bot's ledger).

`report`  read-only. For each coin: regime, momentum, SMC structure (last BOS/CHoCH, swing high/low, sweeps,
          FVG, order block) and where a structural stop would sit. This is the data behind the BTC/DOGE/SOL advice.
`protect` places (or ratchets up) a Kraken stop-loss under the last swing low for the exact quantities you name.
          It never buys, never converts currencies, never moves a stop down, and refuses to place a stop that is
          already above the market (that would be an instant sale: it tells you instead).
"""
from __future__ import annotations

import dataclasses as dc
import json
import logging
import math
import os

from .broker import BrokerError, client_id
from .config import Config
from .risk import PositionState, check_quote, structural_stop
from .signals import features, to_frame
from .strategy import decide

log = logging.getLogger("krakenbot.protect")


def _num(x) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def market_report(broker, cfg: Config, assets: list[str], max_atr: float = 4.0) -> list[dict]:
    out = []
    pcfg = dc.replace(cfg, hard_stop_atr=max(max_atr, cfg.min_stop_atr + 0.1))
    for a in assets:
        sym = f"{a}/{cfg.quote}"
        f = features(to_frame(broker.daily_bars(sym, 400)), cfg)
        r = f.iloc[-1]
        close = float(r["close"])
        c = f["close"]
        rets = {f"ret_{n}d": close / float(c.iloc[-1 - n]) - 1 for n in cfg.lookbacks if len(c) > n}
        stop = structural_stop(close, _num(r["swing_low"]), float(r["atr"]), pcfg)
        flat_view = decide(r, PositionState(), cfg)
        last_bos = f.index[f["bos_up"] | f["bos_dn"]]
        last_choch = f.index[f["choch"]]
        out.append({
            "asset": a, "bar": f.index[-1].date().isoformat(), "close": close, "regime": r["regime"],
            "ema100": _num(r["ema"]), "ema_slope_10d": _num(r["slope"]), "efficiency_ratio": _num(r["er"]),
            **rets, "momentum_score": _num(r["score"]), "trend_weight": _num(r["trend_w"]),
            "structure": {1: "bullish", -1: "bearish"}.get(int(r["structure"]), "none"),
            "last_bos": last_bos[-1].date().isoformat() if len(last_bos) else None,
            "last_bos_dir": ("up" if f.loc[last_bos[-1], "bos_up"] else "down") if len(last_bos) else None,
            "last_choch": last_choch[-1].date().isoformat() if len(last_choch) else None,
            "swing_high": _num(r["swing_high"]), "swing_low": _num(r["swing_low"]),
            "bars_since_sweep": _num(r["bars_since_sweep"]), "fvg": [_num(r["fvg_lo"]), _num(r["fvg_hi"])],
            "order_block": [_num(r["ob_lo"]), _num(r["ob_hi"])], "atr": float(r["atr"]),
            "structural_stop_for_holders": stop, "stop_distance": 1 - stop / close,
            "bot_would": flat_view.reason if not flat_view.weight else f"enter {flat_view.sleeve} ({flat_view.reason})",
        })
    return out


def protect_holdings(broker, cfg: Config, holds: dict[str, float], state_path: str, place: bool,
                     max_atr: float = 4.0) -> list[dict]:
    """Place/ratchet stops for named quantities. `place=False` only prints the plan."""
    pcfg = dc.replace(cfg, hard_stop_atr=max(max_atr, cfg.min_stop_atr + 0.1))
    state = {}
    if os.path.exists(state_path):
        with open(state_path) as fh:
            state = json.load(fh)
    bal = broker.balances() if place or cfg.mode == "validate" else {}
    results = []
    for a, want in holds.items():
        sym = f"{a}/{cfg.quote}"
        res = {"asset": a, "requested_qty": want}
        try:
            f = features(to_frame(broker.daily_bars(sym, 400)), cfg)
            r = f.iloc[-1]
            bid, ask, ts = broker.top_of_book(sym)
            mid = check_quote(bid, ask, ts, cfg)
            level = structural_stop(mid, _num(r["swing_low"]), float(r["atr"]), pcfg)
            prev = state.get(a, {})
            level = max(level, float(prev.get("level") or 0))  # ratchet: never lower a stop
            qty = want
            if bal:
                reserved = (prev.get("qty") or 0.0) if prev.get("order_id") else 0.0  # our own resting stop locks these
                free = bal.get(a, {}).get("free", 0.0) + reserved
                if free < want:
                    log.warning("%s: asked to protect %.8f but only %.8f is free (rest may be in Earn/staking)", a, want, free)
                qty = min(want, free)
            res.update(mid=mid, stop=level, distance=1 - level / mid, qty=qty, loss_if_hit=(mid - level) * qty)
            if level >= mid:
                res["action"] = "REFUSED: stop is above the market (price already broke structure). Decide manually."
            elif not place and cfg.mode != "validate":
                res["action"] = "plan only (add --i-understand-the-risk to place it on Kraken)"
            else:
                if prev.get("order_id") and abs(float(prev.get("level", 0)) - level) / level < 0.002 \
                        and abs(float(prev.get("qty", 0)) - qty) <= qty * 1e-4:
                    o = broker.fetch(prev["order_id"], sym)
                    if o.get("status") == "open":
                        res["action"] = "unchanged (already resting)"
                        results.append(res)
                        continue
                if prev.get("order_id"):
                    broker.cancel(prev["order_id"], sym)
                o = broker.place_stop(sym, qty, level, client_id("protect", sym, f"{level:.8f}", f"{qty:.8f}"))
                if cfg.mode == "validate":
                    res["action"] = "validated by Kraken (not placed)"
                else:
                    state[a] = {"level": level, "qty": qty, "order_id": str(o["id"])}
                    res["action"] = "stop resting on Kraken"
        except BrokerError as e:
            res["action"] = f"ERROR: {e}"
        results.append(res)
    if place and cfg.mode != "validate":
        tmp = state_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh, indent=2)
        os.replace(tmp, state_path)
    return results
