"""Daily-bar event backtest with fees, slippage, exchange-side stops and the SAME decision/risk code as live.

Timing (no look-ahead):
  * features, stops and decisions use bar t's close;
  * orders fill at bar t+1's open +/- slippage, + fee; an entry is skipped if that open is already at/below its stop;
  * a resting stop triggers inside bar t+1 if its low trades through it and fills at min(open, stop) - slippage,
    so a gap through the stop fills at the (worse) open, as it would in reality;
  * take-profits are judged on the close and exit at the next open (live: the stop rests on Kraken, the target is
    checked by the bot each day, because Kraken spot cannot hold a stop and a take-profit on the same coins).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config
from .risk import DrawdownGuard, PositionState, new_bar, on_buy_fill, on_sell_fill, on_stopped_out, plan_rebalance
from .signals import ANNUAL, features, scale_portfolio
from .strategy import Decision, decide


@dataclass
class Result:
    equity: pd.Series
    trades: pd.DataFrame
    fees: float
    stops_hit: int
    halted_on: object = None
    exposure: float = float("nan")

    def stats(self) -> dict:
        return perf(self.equity, self.trades, self.fees, self.stops_hit, self.exposure, self.halted_on)


def perf(e: pd.Series, trades: pd.DataFrame | None = None, fees: float = 0.0, stops: int = 0,
         exposure: float = float("nan"), halted_on=None) -> dict:
    r = e.pct_change().dropna()
    years = max((e.index[-1] - e.index[0]).days / 365.25, 1e-9)
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / years) - 1
    dd = (e / e.cummax() - 1).min()
    sd = r.std()
    sharpe = r.mean() / sd * math.sqrt(ANNUAL) if sd and sd > 0 else 0.0
    n = 0 if trades is None else len(trades)
    return dict(start=e.index[0].date(), end=e.index[-1].date(), total_return=e.iloc[-1] / e.iloc[0] - 1,
                cagr=cagr, max_dd=dd, sharpe=sharpe, calmar=cagr / abs(dd) if dd < 0 else np.nan,
                trades=n, stops=stops, fees=fees, avg_exposure=exposure, halted_on=halted_on)


def round_trips(trades: pd.DataFrame) -> pd.DataFrame:
    """Pair entries and exits per asset into round trips with net P&L (fees included)."""
    rows = []
    for a, g in trades.groupby("asset"):
        qty = cost = fees = 0.0
        sleeve, opened = "", None
        for _, t in g.iterrows():
            if t.side == "buy":
                if qty == 0:
                    sleeve, opened, cost, fees = t.sleeve, t.ts, 0.0, 0.0
                qty += t.qty
                cost += t.notional
                fees += t.fee
            else:
                if qty <= 0:
                    continue
                part = min(t.qty, qty) / qty
                pnl = t.notional - cost * part - fees * part - t.fee
                rows.append(dict(asset=a, sleeve=sleeve, opened=opened, closed=t.ts, pnl=pnl, why=t.why,
                                 ret=pnl / (cost * part) if cost else 0.0))
                qty -= min(t.qty, qty)
                cost *= 1 - part
                fees *= 1 - part
    return pd.DataFrame(rows, columns=["asset", "sleeve", "opened", "closed", "pnl", "why", "ret"])


def run(data: dict[str, pd.DataFrame], cfg: Config, start_equity: float = 1600.0, slippage: float = 0.001,
        start=None) -> Result:
    assets = list(data)
    feat = {a: features(data[a], cfg) for a in assets}
    idx = pd.DatetimeIndex(sorted(set.intersection(*[set(d.index) for d in data.values()])))
    if start is not None:
        idx = idx[idx >= pd.Timestamp(start, tz="UTC")]
    cash = start_equity
    pos = {a: PositionState() for a in assets}
    guard = DrawdownGuard()
    eq_curve, trades, fees, stops, exposure = [], [], 0.0, 0, []
    pending: dict[str, float] | None = None
    decisions: dict[str, Decision] = {}
    halted_on = None

    def fill(t, a, side, qty, px, why, sleeve):
        nonlocal cash, fees
        notional = qty * px
        fee = notional * cfg.fee_rate
        cash += -(notional + fee) if side == "buy" else notional - fee
        fees += fee
        trades.append(dict(ts=t, asset=a, side=side, qty=qty, px=px, notional=notional, fee=fee, why=why, sleeve=sleeve))

    for t in idx:
        bar = {a: data[a].loc[t] for a in assets}
        # 1) yesterday's decisions fill at today's open
        if pending is not None:
            prices = {a: float(bar[a]["open"]) for a in assets}
            equity_open = cash + sum(pos[a].qty * prices[a] for a in assets)
            reasons = {a: d.reason for a, d in decisions.items()}
            for p in plan_rebalance(equity_open, prices, {a: pos[a].qty for a in assets}, pending, cfg,
                                    free_quote=cash, reasons=reasons):
                d = decisions.get(p.asset)
                if p.side == "buy":
                    px = prices[p.asset] * (1 + slippage)
                    if d is None or prices[p.asset] <= d.stop or p.qty * px * (1 + cfg.fee_rate) > cash:
                        continue
                    fill(t, p.asset, "buy", p.qty, px, p.reason, d.sleeve)
                    on_buy_fill(pos[p.asset], px, p.qty, d.stop, d.sleeve, d.take_profit)
                else:
                    fill(t, p.asset, "sell", p.qty, prices[p.asset] * (1 - slippage), p.reason, pos[p.asset].sleeve)
                    on_sell_fill(pos[p.asset], p.qty)
            pending = None
        # 2) resting stops trigger intrabar
        for a in assets:
            st = pos[a]
            if st.qty > 0 and st.stop > 0 and float(bar[a]["low"]) <= st.stop:
                px = min(float(bar[a]["open"]), st.stop) * (1 - slippage)
                fill(t, a, "sell", st.qty, px, "stop", st.sleeve)
                stops += 1
                on_stopped_out(st, cfg)
        # 3) close: mark to market, then decide for tomorrow
        closes = {a: float(bar[a]["close"]) for a in assets}
        equity = cash + sum(pos[a].qty * closes[a] for a in assets)
        eq_curve.append((t, equity))
        exposure.append(sum(pos[a].qty * closes[a] for a in assets) / equity if equity > 0 else 0)
        if guard.update(equity, cfg):
            halted_on = halted_on or t.date()
            pending, decisions = {a: 0.0 for a in assets}, {a: Decision(0.0, reason="drawdown halt") for a in assets}
            continue
        raw = {}
        for a in assets:
            row = feat[a].loc[t]
            new_bar(pos[a], closes[a], float(row["atr"]), float(row["swing_low"]), float(row["trend_w"]), cfg)
            d = decide(row, pos[a], cfg)
            decisions[a] = d
            if pos[a].qty > 0 and not d.is_exit:
                pos[a].sleeve = d.sleeve or pos[a].sleeve
                pos[a].take_profit = d.take_profit
                pos[a].stop = max(pos[a].stop, d.stop)
            raw[a] = (pos[a].qty * closes[a] / equity) if d.weight is None else d.weight
        pending = scale_portfolio(raw, cfg)

    eq = pd.Series(dict(eq_curve)).sort_index()
    tr = pd.DataFrame(trades, columns=["ts", "asset", "side", "qty", "px", "notional", "fee", "why", "sleeve"])
    return Result(eq, tr, fees, stops, halted_on, float(np.mean(exposure)) if exposure else float("nan"))


def buy_and_hold(data: dict[str, pd.DataFrame], start_equity: float = 1600.0, fee: float = 0.0025,
                 start=None) -> pd.Series:
    idx = pd.DatetimeIndex(sorted(set.intersection(*[set(d.index) for d in data.values()])))
    if start is not None:
        idx = idx[idx >= pd.Timestamp(start, tz="UTC")]
    per = start_equity * (1 - fee) / len(data)
    qty = {a: per / data[a].loc[idx[0], "close"] for a in data}
    return pd.Series({t: sum(qty[a] * data[a].loc[t, "close"] for a in data) for t in idx})
