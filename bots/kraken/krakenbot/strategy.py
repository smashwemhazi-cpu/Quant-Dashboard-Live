"""Regime-adaptive decision for ONE asset at ONE closed bar. Pure function; backtest and live call the same code.

Regimes (from signals.features):
  up     price above a rising 100-EMA        -> TREND model: momentum entry, structural stop, trailing exits
  range  efficiency ratio low or EMA flat    -> RANGE model: buy the swing-low zone, take profit at the
                                                swing-high zone; SMC model: buy a liquidity sweep + reclaim
  down   price below a falling 100-EMA       -> no new longs; open trades are managed by their stops and rules

Which models may open trades is set by cfg.entry_models; the default is the one that survived out-of-sample
testing (see REVIEW.md). An open trade is always managed by the model that opened it, and a range/SMC trade that
reaches its target while the market has turned into an uptrend is handed to the trend model to let it run.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from .config import Config
from .risk import PositionState, risk_weight, structural_stop


@dataclass(frozen=True)
class Decision:
    weight: float | None     # target fraction of equity; None = hold the current position unchanged
    stop: float = 0.0        # protective stop for the position after this decision
    take_profit: float = 0.0
    sleeve: str = ""         # trend | range | smc | "" (flat)
    reason: str = ""

    @property
    def is_exit(self) -> bool:
        return self.weight == 0

    def as_dict(self) -> dict:
        return {"weight": self.weight, "stop": self.stop, "take_profit": self.take_profit,
                "sleeve": self.sleeve, "reason": self.reason}


FLAT = Decision(0.0, reason="flat")


def _f(row: pd.Series, k: str) -> float:
    v = row.get(k)
    return float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else float("nan")


def _trend_weight(row: pd.Series, stop: float, cfg: Config) -> float:
    return min(float(row["trend_w"]), cfg.max_weight, risk_weight(float(row["close"]), stop, cfg.risk_per_trade))


def decide(row: pd.Series, pos: PositionState, cfg: Config) -> Decision:
    if not bool(row.get("warm", False)):
        return Decision(None if pos.qty > 0 else 0.0, pos.stop, pos.take_profit, pos.sleeve, "warming up")
    close, atr_now, regime = float(row["close"]), float(row["atr"]), row["regime"]
    trend_ok = "trend" in cfg.entry_models

    # ---------------------------------------------------------------- manage an open position
    if pos.qty > 0:
        if pos.sleeve in ("range", "smc"):
            hit = pos.take_profit > 0 and close >= pos.take_profit
            if hit and trend_ok and regime == "up" and row["trend_w"] > 0:
                w = _trend_weight(row, pos.stop, cfg)
                if w > 0:
                    return Decision(w, pos.stop, 0.0, "trend", f"{pos.sleeve} target hit in an uptrend: let it run")
            if hit:
                return Decision(0.0, reason=f"{pos.sleeve} take-profit (swing-high zone)")
            max_bars = cfg.range_max_bars if pos.sleeve == "range" else cfg.smc_max_bars
            if pos.bars_held >= max_bars:
                return Decision(0.0, reason=f"{pos.sleeve} time stop ({pos.bars_held} bars)")
            if pos.sleeve == "range" and regime == "down":
                return Decision(0.0, reason="range broke into a downtrend")
            if pos.sleeve == "smc" and row["structure"] < 0:
                return Decision(0.0, reason="bearish change of character")
            return Decision(None, pos.stop, pos.take_profit, pos.sleeve, "hold")
        # trend trade
        if row["trend_w"] <= 0:
            return Decision(0.0, reason="trend over (momentum/EMA)")
        return Decision(_trend_weight(row, pos.stop, cfg), pos.stop, 0.0, "trend", "trend hold/rebalance")

    # ---------------------------------------------------------------- flat: look for an entry
    if pos.cooldown > 0:
        return Decision(0.0, reason=f"cooldown after stop ({pos.cooldown} bars)")

    if trend_ok and regime == "up" and row["trend_w"] > 0 and not pos.blocked:
        stop = structural_stop(close, _f(row, "swing_low"), atr_now, cfg)
        w = _trend_weight(row, stop, cfg)
        if w > 0:
            return Decision(w, stop, 0.0, "trend", "uptrend entry")

    if "smc" in cfg.entry_models and regime != "down":
        structure_ok = row["structure"] > 0 or not cfg.smc_require_bullish_structure
        recent = _f(row, "bars_since_sweep") <= cfg.smc_sweep_lookback
        level, sweep_low = _f(row, "last_swept_level"), _f(row, "last_sweep_low")
        if structure_ok and recent and close > level and close > float(row["open"]):
            stop = min(sweep_low - cfg.stop_buffer_atr * atr_now, close - cfg.min_stop_atr * atr_now)
            target = _f(row, "swing_high")
            if not (math.isfinite(target) and target > close):
                target = float(row["don_hi"])
            dist = close - stop
            if 0 < dist <= cfg.hard_stop_atr * atr_now and target - close >= cfg.smc_min_rr * dist:
                w = min(cfg.smc_max_weight, risk_weight(close, stop, cfg.smc_risk))
                return Decision(w, stop, target, "smc", "liquidity sweep of swing low + reclaim")

    if "range" in cfg.entry_models and regime == "range":
        hi, lo = float(row["don_hi"]), float(row["don_lo"])
        width = hi - lo
        if width > 0 and cfg.range_min_atr <= width / atr_now <= cfg.range_max_atr:
            zone = (close - lo) / width
            if zone <= cfg.range_entry_zone and close > float(row["open"]):
                stop = min(lo - cfg.stop_buffer_atr * atr_now, close - cfg.min_stop_atr * atr_now)
                target = lo + cfg.range_exit_zone * width
                dist = close - stop
                if 0 < dist <= cfg.hard_stop_atr * atr_now and target - close >= cfg.range_min_rr * dist:
                    w = min(cfg.range_max_weight, risk_weight(close, stop, cfg.range_risk))
                    return Decision(w, stop, target, "range", "swing-low zone of a sideways range")

    return Decision(0.0, reason=f"no setup ({regime})")
