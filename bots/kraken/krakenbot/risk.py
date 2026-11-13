"""Risk rules shared by backtest and live (one code path, no drift).

Stop-loss, the whole rule:
  * every holding has a stop-loss order resting ON KRAKEN from the moment it is bought, so it protects you when the
    bot, your PC or the internet is down;
  * level = price x (1 - stop_sigma x one-week volatility): at the default 4 that is ~-22% for BTC (40% vol) and
    ~-35% to -40% for a volatile altcoin (harvest uses 8). Set at entry, only ever raised at each rebalance; never
    lowered, never trailed daily;
  * if price is already through the stop when we look, we sell instead of placing a stop that would fire at once.
Why a disaster brake and not a tight trailing stop: the weekly rotation is the exit. In the backtest every daily
trailing stop (2-10 ATR) cut returns AND deepened drawdowns, because it sells pullbacks the rotation then buys back
higher. The brake exists for what a backtest of surviving coins cannot show: a coin collapsing (LUNA, FTT), an
exchange or bot outage, a flash crash.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass

from .config import Config


@dataclass
class Holding:
    qty: float = 0.0
    entry: float = 0.0
    high_close: float = 0.0
    stop: float = 0.0
    stop_order_id: str | None = None
    sidelined: bool = False      # a disaster stop fired: harvest keeps this asset in EUR until its trend turns up

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Holding":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def stop_level(px: float, vol: float, cfg: Config) -> float:
    """Disaster-stop price: k standard deviations of a one-week move below px (never below 10% of px)."""
    if not (math.isfinite(vol) and vol > 0):
        return px * 0.10
    return px * max(0.10, 1 - cfg.stop_sigma * vol * math.sqrt(7 / 365))


def on_buy(h: Holding, px: float, qty: float, vol: float, cfg: Config) -> None:
    if qty <= 0:
        return
    lvl = stop_level(px, vol, cfg)
    if h.qty <= 0:
        h.entry, h.high_close, h.stop = px, px, lvl
    else:
        h.entry = (h.entry * h.qty + px * qty) / (h.qty + qty)
        h.stop = max(h.stop, lvl)
    h.qty += qty


def on_sell(h: Holding, qty: float) -> None:
    h.qty = max(0.0, h.qty - qty)
    if h.qty <= 1e-12:
        h.qty = h.entry = h.high_close = h.stop = 0.0
        h.stop_order_id = None


def ratchet(h: Holding, close: float, vol: float, cfg: Config) -> None:
    """At each weekly rebalance close: raise the disaster stop under the new price. Up only."""
    if h.qty <= 0:
        return
    h.high_close = max(h.high_close, close)
    h.stop = max(h.stop, stop_level(close, vol, cfg))


@dataclass
class DrawdownGuard:
    peak: float = 0.0
    halted: bool = False
    reason: str = ""

    def update(self, equity: float, cfg: Config) -> bool:
        """True = stop trading. Sticky: a human resets it by deleting the 'guard' block in the state file."""
        if equity <= 0 or not math.isfinite(equity):
            self.halted, self.reason = True, f"invalid equity {equity!r}"
            return True
        self.peak = max(self.peak, equity)
        dd = 1 - equity / self.peak
        if dd >= cfg.max_drawdown_halt and not self.halted:
            self.halted, self.reason = True, f"drawdown {dd:.1%} >= limit {cfg.max_drawdown_halt:.0%}"
        return self.halted


@dataclass(frozen=True)
class OrderPlan:
    asset: str
    side: str
    qty: float
    notional: float
    reason: str


def plan_rebalance(equity: float, prices: dict[str, float], holdings: dict[str, float], targets: dict[str, float],
                   cfg: Config, free_quote: float | None = None) -> list[OrderPlan]:
    """Target weights -> orders. Full exits always go through; small adjustments inside the band are skipped.
    Sells first, so their proceeds fund the buys; buys are scaled down to the cash actually available."""
    sells, buys = [], []
    for a, w in targets.items():
        px = prices.get(a)
        if not px:
            continue
        cur = holdings.get(a, 0.0) * px
        diff = w * equity - cur
        exiting = w == 0 and cur > 0
        if not exiting and (abs(diff) < cfg.rebalance_band * equity or abs(diff) < cfg.min_order_quote):
            continue
        if diff < 0:
            qty = holdings.get(a, 0.0) if exiting else min(-diff / px, holdings.get(a, 0.0))
            sells.append(OrderPlan(a, "sell", qty, qty * px, "rotate out" if exiting else "trim"))
        else:
            buys.append(OrderPlan(a, "buy", diff / px, diff, "rotate in" if cur == 0 else "top up"))
    if free_quote is not None and buys:
        budget = (free_quote + sum(s.notional for s in sells) * (1 - cfg.fee_rate)) * (1 - 2 * cfg.fee_rate)
        need = sum(b.notional for b in buys)
        if need > budget:
            k = max(budget, 0.0) / need
            buys = [OrderPlan(b.asset, "buy", b.qty * k, b.notional * k, b.reason) for b in buys
                    if b.notional * k >= cfg.min_order_quote]
    return sells + buys


class QuoteError(Exception):
    pass


def check_quote(bid, ask, ts_ms, cfg: Config, now: float | None = None) -> float:
    """Validate top of book; returns mid. Raises QuoteError for anything unsafe to trade on."""
    now = time.time() if now is None else now
    if bid is None or ask is None or not (math.isfinite(bid) and math.isfinite(ask)) or bid <= 0 or ask <= 0:
        raise QuoteError(f"missing/invalid quote bid={bid} ask={ask}")
    if bid >= ask:
        raise QuoteError(f"crossed/locked book bid={bid} ask={ask}")
    mid = (bid + ask) / 2
    if (ask - bid) / mid > cfg.max_spread:
        raise QuoteError(f"spread {(ask - bid) / mid:.3%} > max {cfg.max_spread:.2%}")
    if ts_ms is not None and now - ts_ms / 1000 > cfg.max_quote_age_s:
        raise QuoteError(f"stale quote: {now - ts_ms / 1000:.0f}s old")
    return mid


def limit_price(side: str, bid: float, ask: float, mid: float, attempt: int, cfg: Config) -> tuple[float, bool]:
    """Post-only at the touch first (maker fee, no slippage); the final attempt crosses but never beyond
    mid x (1 +/- max_slippage). Returns (price, post_only)."""
    if attempt < cfg.post_only_attempts:
        return (bid, True) if side == "buy" else (ask, True)
    cap = mid * (1 + cfg.max_slippage) if side == "buy" else mid * (1 - cfg.max_slippage)
    return (min(ask, cap), False) if side == "buy" else (max(bid, cap), False)
