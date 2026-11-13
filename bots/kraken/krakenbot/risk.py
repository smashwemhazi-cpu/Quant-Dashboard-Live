"""Risk state machine shared by the backtest and the live engine (one code path = no backtest/live drift)."""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass

from .config import Config


@dataclass
class PositionState:
    qty: float = 0.0
    entry: float = 0.0          # average entry price
    high_close: float = 0.0     # highest close since entry (for the chandelier trail)
    stop: float = 0.0           # current protective stop; only ever ratchets UP while a position is open
    blocked: bool = False       # set after a stop-out: no re-entry until the signal resets or price makes a new high
    block_high: float = 0.0
    stop_order_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PositionState":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def on_buy_fill(st: PositionState, px: float, qty: float, atr_now: float, cfg: Config) -> None:
    new_qty = st.qty + qty
    st.entry = (st.entry * st.qty + px * qty) / new_qty if new_qty > 0 else 0.0
    if st.qty <= 0:
        st.high_close = px
        st.stop = px - cfg.hard_stop_atr * atr_now
    else:
        # adding to a winner never loosens the existing stop
        st.stop = max(st.stop, px - cfg.hard_stop_atr * atr_now) if st.stop else px - cfg.hard_stop_atr * atr_now
    st.qty = new_qty


def on_sell_fill(st: PositionState, qty: float) -> None:
    st.qty = max(0.0, st.qty - qty)
    if st.qty <= 1e-12:
        flat(st)


def flat(st: PositionState) -> None:
    st.qty = st.entry = st.high_close = st.stop = 0.0
    st.stop_order_id = None


def on_stopped_out(st: PositionState) -> None:
    st.blocked, st.block_high = True, st.high_close
    flat(st)


def update_trail(st: PositionState, close: float, atr_now: float, cfg: Config) -> None:
    """Call once per CLOSED bar. Chandelier exit: highest close since entry minus k*ATR. Never moves down."""
    if st.qty <= 0 or not math.isfinite(atr_now):
        return
    st.high_close = max(st.high_close, close)
    st.stop = max(st.stop, st.high_close - cfg.trail_atr * atr_now)


def update_block(st: PositionState, close: float, weight: float) -> None:
    """Lift the post-stop re-entry block once the signal has reset to zero or price makes a new high."""
    if st.blocked and (weight <= 0 or close > st.block_high):
        st.blocked = False


@dataclass
class DrawdownGuard:
    peak: float = 0.0
    halted: bool = False
    reason: str = ""

    def update(self, equity: float, cfg: Config) -> bool:
        """Returns True if trading must stop. Halt is sticky: it needs a human to reset the state file."""
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
    side: str          # buy | sell
    qty: float
    notional: float
    reason: str


def plan_rebalance(equity: float, prices: dict[str, float], holdings: dict[str, float],
                   targets: dict[str, float], cfg: Config, free_quote: float | None = None) -> list[OrderPlan]:
    """Turn target weights into orders. Sells come first so their proceeds fund the buys."""
    sells, buys = [], []
    for a, w in targets.items():
        px = prices[a]
        cur_val = holdings.get(a, 0.0) * px
        diff = w * equity - cur_val
        exiting = w == 0 and cur_val > 0
        if not exiting and abs(diff) < cfg.rebalance_band * equity:
            continue
        if abs(diff) < cfg.min_order_quote:
            continue
        qty = abs(diff) / px
        if diff < 0:
            qty = min(qty, holdings.get(a, 0.0))
            sells.append(OrderPlan(a, "sell", qty, qty * px, "exit" if exiting else "trim"))
        else:
            buys.append(OrderPlan(a, "buy", qty, qty * px, "enter/add"))
    if free_quote is not None and buys:
        budget = free_quote + sum(s.notional * (1 - cfg.fee_rate) for s in sells)
        budget *= 1 - cfg.fee_rate * 2  # leave room for fees and rounding
        need = sum(b.notional for b in buys)
        if need > budget > 0:
            k = budget / need
            buys = [OrderPlan(b.asset, b.side, b.qty * k, b.notional * k, b.reason + " (scaled to cash)")
                    for b in buys if b.notional * k >= cfg.min_order_quote]
        elif budget <= 0:
            buys = []
    return sells + buys


class QuoteError(Exception):
    pass


def check_quote(bid: float | None, ask: float | None, ts_ms: float | None, cfg: Config, now: float | None = None) -> float:
    """Validate a top-of-book quote; returns mid. Raises QuoteError for anything we should not trade on."""
    now = time.time() if now is None else now
    if bid is None or ask is None or not (math.isfinite(bid) and math.isfinite(ask)) or bid <= 0 or ask <= 0:
        raise QuoteError(f"missing/invalid quote bid={bid} ask={ask}")
    if bid >= ask:
        raise QuoteError(f"crossed/locked book bid={bid} ask={ask}")
    mid = (bid + ask) / 2
    if (ask - bid) / mid > cfg.max_spread:
        raise QuoteError(f"spread {(ask - bid) / mid:.4%} > max {cfg.max_spread:.2%}")
    if ts_ms is not None and now - ts_ms / 1000 > cfg.max_quote_age_s:
        raise QuoteError(f"stale quote: {now - ts_ms / 1000:.0f}s old")
    return mid


def limit_price(side: str, bid: float, ask: float, mid: float, attempt: int, cfg: Config) -> tuple[float, bool]:
    """Price for attempt n. Early attempts join our side of the book post-only (maker fee, zero slippage).
    The last attempt crosses, but never beyond mid*(1+/-max_slippage): that IS the slippage control.
    Returns (price, post_only)."""
    if attempt < cfg.post_only_attempts:
        return (bid, True) if side == "buy" else (ask, True)
    cap = mid * (1 + cfg.max_slippage) if side == "buy" else mid * (1 - cfg.max_slippage)
    return (min(ask, cap), False) if side == "buy" else (max(bid, cap), False)
