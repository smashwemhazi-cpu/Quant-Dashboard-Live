"""Risk state machine shared by the backtest and the live engine (one code path = no backtest/live drift).

Stop-loss rules (the same everywhere):
  * every position has a protective stop from the moment it exists; live, it rests ON KRAKEN as a stop-loss order,
    so it protects you even when this bot, your PC or the internet is down;
  * the initial stop is structural: just below the last confirmed swing low, but never closer than min_stop_atr
    and never further than hard_stop_atr (ATR = average true range);
  * position size is derived FROM the stop: (entry - stop) x quantity = the risk budget, so a wide stop means a
    small position, never a bigger loss;
  * stops only move up (trend trades: chandelier trail and each new higher swing low); never down;
  * if price is already through the stop when we look, we exit instead of placing a stop that would fire at once.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass

from .config import Config


@dataclass
class PositionState:
    qty: float = 0.0
    entry: float = 0.0          # average entry price
    high_close: float = 0.0     # highest close since entry (chandelier trail)
    stop: float = 0.0           # protective stop; only ever ratchets UP while a position is open
    sleeve: str = ""            # trend | range | smc
    take_profit: float = 0.0    # range/SMC target (0 = none); judged on the daily close
    bars_held: int = 0
    cooldown: int = 0           # bars left before a new entry is allowed after a stop-out
    blocked: bool = False       # trend re-entry block after a stop until a new high or a signal reset
    block_high: float = 0.0
    stop_order_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PositionState":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def structural_stop(ref: float, swing_low: float, atr_now: float, cfg: Config) -> float:
    """Below the last swing low (+buffer), clamped to [ref - hard_stop_atr*ATR, ref - min_stop_atr*ATR]."""
    widest = ref - cfg.hard_stop_atr * atr_now
    tightest = ref - cfg.min_stop_atr * atr_now
    if swing_low is not None and math.isfinite(swing_low) and swing_low < ref:
        s = swing_low - cfg.stop_buffer_atr * atr_now
    else:
        s = widest
    return min(max(s, widest), tightest)


def risk_weight(ref: float, stop: float, risk: float) -> float:
    """Fraction of equity such that hitting `stop` from `ref` loses `risk` of equity."""
    if stop <= 0 or stop >= ref:
        return 0.0
    return risk / ((ref - stop) / ref)


def on_buy_fill(st: PositionState, px: float, qty: float, stop: float, sleeve: str, take_profit: float = 0.0) -> None:
    if qty <= 0:
        return
    new_qty = st.qty + qty
    if st.qty <= 0:
        st.entry, st.high_close, st.stop = px, px, stop
        st.sleeve, st.take_profit, st.bars_held = sleeve, take_profit, 0
    else:
        st.entry = (st.entry * st.qty + px * qty) / new_qty
        st.stop = max(st.stop, stop)  # adding never loosens the stop
    st.qty = new_qty


def on_sell_fill(st: PositionState, qty: float) -> None:
    st.qty = max(0.0, st.qty - qty)
    if st.qty <= 1e-12:
        flat(st)


def flat(st: PositionState) -> None:
    st.qty = st.entry = st.high_close = st.stop = st.take_profit = 0.0
    st.sleeve, st.bars_held, st.stop_order_id = "", 0, None


def on_stopped_out(st: PositionState, cfg: Config, high_close: float | None = None) -> None:
    """Mark a stop-out: flat, cooldown, trend re-entry blocked until a new high. Pass `high_close` if the
    position was already flattened by booking the fill (flattening resets it)."""
    st.blocked, st.block_high = True, st.high_close if high_close is None else high_close
    st.cooldown = cfg.cooldown_bars
    flat(st)


def new_bar(st: PositionState, close: float, atr_now: float, swing_low: float, trend_w: float, cfg: Config) -> None:
    """Call exactly once per CLOSED bar, before deciding. Ages the trade, ratchets the stop, lifts blocks."""
    if st.qty > 0:
        st.bars_held += 1
        st.high_close = max(st.high_close, close)
        if st.sleeve == "trend" and math.isfinite(atr_now):
            st.stop = max(st.stop, st.high_close - cfg.trail_atr * atr_now)
            if cfg.trail_swing_lows and swing_low is not None and math.isfinite(swing_low) and swing_low > st.entry:
                st.stop = max(st.stop, swing_low - cfg.stop_buffer_atr * atr_now)
    elif st.cooldown > 0:
        st.cooldown -= 1
    if st.blocked and (trend_w <= 0 or close > st.block_high):
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
                   targets: dict[str, float], cfg: Config, free_quote: float | None = None,
                   reasons: dict[str, str] | None = None) -> list[OrderPlan]:
    """Turn target weights into orders. Sells come first so their proceeds fund the buys."""
    reasons = reasons or {}
    sells, buys = [], []
    for a, w in targets.items():
        px = prices[a]
        cur_val = holdings.get(a, 0.0) * px
        diff = w * equity - cur_val
        exiting = w == 0 and cur_val > 0
        if not exiting and abs(diff) < cfg.rebalance_band * equity:
            continue
        if abs(diff) < cfg.min_order_quote and not exiting:
            continue
        qty = abs(diff) / px
        if diff < 0:
            qty = holdings.get(a, 0.0) if exiting else min(qty, holdings.get(a, 0.0))
            sells.append(OrderPlan(a, "sell", qty, qty * px, reasons.get(a, "exit" if exiting else "trim")))
        else:
            buys.append(OrderPlan(a, "buy", qty, qty * px, reasons.get(a, "enter/add")))
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
    """Early attempts join our side of the book post-only (maker, zero slippage). The last attempt crosses, but
    never beyond mid*(1 +/- max_slippage): that IS the slippage control. Returns (price, post_only)."""
    if attempt < cfg.post_only_attempts:
        return (bid, True) if side == "buy" else (ask, True)
    cap = mid * (1 + cfg.max_slippage) if side == "buy" else mid * (1 - cfg.max_slippage)
    return (min(ask, cap), False) if side == "buy" else (max(bid, cap), False)
