"""Risk engine for the personal-trading agent.

Pure functions, no I/O, so they are unit-testable and reusable from a cBot
bridge, an MT5 bridge or the UI. JARVIS never sends orders itself: it produces
an *order plan* that passes every check here, and a human (or a separately
approved execution bot) places it.

Maths
-----
* Position size  = (equity * risk%) / (stop_distance_price * value_per_unit)
* Initial stop   = entry -/+ stop_atr_mult * ATR
* Trailing stop  = max(prev_stop, close - trail_atr_mult * ATR) for longs
                   (min(...) + ATR for shorts) - it only ever tightens.
* Kill switch    = realised + floating P&L today <= -max_daily_loss% of
                   start-of-day equity  -> block all new risk.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from jarvis.core.config import settings


@dataclass
class Quote:
    bid: float
    ask: float
    pip_size: float  # e.g. 0.0001 for EURUSD, 0.01 for USDJPY / XAUUSD

    def validate(self) -> None:
        if not all(math.isfinite(x) and x > 0 for x in (self.bid, self.ask, self.pip_size)):
            raise ValueError("invalid quote: non-positive or non-finite price")
        if self.ask < self.bid:
            raise ValueError("invalid quote: crossed market (ask < bid)")

    @property
    def spread_pips(self) -> float:
        return (self.ask - self.bid) / self.pip_size


@dataclass
class OrderPlan:
    side: str
    units: float
    entry: float
    stop: float
    take_profit: float | None
    max_slippage_pips: float
    risk_amount: float
    approved: bool
    reasons: list[str] = field(default_factory=list)


def position_size(equity: float, risk_pct: float, stop_distance: float,
                  value_per_unit_per_price: float = 1.0, lot_step: float = 1.0) -> float:
    """Units such that hitting the stop loses `risk_pct` of equity, floored to lot_step."""
    if equity <= 0 or risk_pct <= 0 or stop_distance <= 0 or value_per_unit_per_price <= 0:
        return 0.0
    raw = (equity * risk_pct / 100.0) / (stop_distance * value_per_unit_per_price)
    return math.floor(raw / lot_step) * lot_step


def trailing_stop(side: str, prev_stop: float, close: float, atr: float,
                  mult: float | None = None) -> float:
    """ATR trail that only moves in the trade's favour."""
    m = settings.trail_atr_mult if mult is None else mult
    if side == "buy":
        return max(prev_stop, close - m * atr)
    return min(prev_stop, close + m * atr)


def daily_loss_breached(start_equity: float, current_equity: float,
                        max_daily_loss_pct: float | None = None) -> bool:
    lim = settings.max_daily_loss_pct if max_daily_loss_pct is None else max_daily_loss_pct
    if start_equity <= 0:
        return True
    return (start_equity - current_equity) / start_equity * 100.0 >= lim


def plan_order(side: str, quote: Quote, atr: float, equity: float, start_of_day_equity: float,
               open_risk_amount: float = 0.0, rr: float | None = 2.0,
               value_per_unit_per_price: float = 1.0, lot_step: float = 1.0) -> OrderPlan:
    """Build a risk-checked order plan. `approved=False` comes with reasons."""
    reasons: list[str] = []
    side = side.lower()
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    quote.validate()
    if not (math.isfinite(atr) and atr > 0):
        raise ValueError("ATR must be positive")

    entry = quote.ask if side == "buy" else quote.bid
    dist = settings.stop_atr_mult * atr
    stop = entry - dist if side == "buy" else entry + dist
    tp = None if rr is None else (entry + rr * dist if side == "buy" else entry - rr * dist)
    units = position_size(equity, settings.risk_per_trade_pct, dist, value_per_unit_per_price, lot_step)
    risk_amt = units * dist * value_per_unit_per_price

    if quote.spread_pips > settings.max_spread_pips:
        reasons.append(f"spread {quote.spread_pips:.1f} pips > limit {settings.max_spread_pips}")
    if daily_loss_breached(start_of_day_equity, equity):
        reasons.append("daily loss limit hit - kill switch active")
    if equity > 0 and (open_risk_amount + risk_amt) / equity * 100 > settings.max_open_risk_pct:
        reasons.append("aggregate open risk would exceed limit")
    if units <= 0:
        reasons.append("size rounds to zero at this risk / stop distance")

    return OrderPlan(side, units, entry, stop, tp, settings.max_slippage_pips,
                     round(risk_amt, 2), approved=not reasons, reasons=reasons)
