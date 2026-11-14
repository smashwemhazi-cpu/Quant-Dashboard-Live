"""Daily event backtest of the rotation with fees, slippage and resting stops, using the live code's own rules.

Timing (no look-ahead): ranking and stops use bar t's close; trades fill at bar t+1's open (+/- slippage, + fee);
a resting stop triggers inside a bar when the low trades through it, filling at min(open, stop) - slippage.
Assets join the universe only once they have enough history (no trading on warm-up data).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config
from .risk import DrawdownGuard, Holding, on_buy, on_sell, plan_rebalance, ratchet
from .signals import ANNUAL, features, target_weights


@dataclass
class Result:
    equity: pd.Series
    trades: pd.DataFrame
    fees: float
    stops: int
    holdings: pd.DataFrame      # weight per asset per day
    halted_on: object = None

    def stats(self) -> dict:
        return perf(self.equity, len(self.trades), self.fees, self.stops, self.halted_on)


def perf(e: pd.Series, trades: int = 0, fees: float = 0.0, stops: int = 0, halted_on=None) -> dict:
    r = e.pct_change().dropna()
    years = max((e.index[-1] - e.index[0]).days / 365.25, 1e-9)
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / years) - 1
    dd = float((e / e.cummax() - 1).min())
    sd = r.std()
    return dict(start=e.index[0].date(), end=e.index[-1].date(), total_return=e.iloc[-1] / e.iloc[0] - 1, cagr=cagr,
                max_dd=dd, sharpe=r.mean() / sd * math.sqrt(ANNUAL) if sd and sd > 0 else 0.0,
                calmar=cagr / abs(dd) if dd < 0 else float("nan"), trades=trades, stops=stops, fees=fees,
                halted_on=halted_on)


def run(data: dict[str, pd.DataFrame], cfg: Config, start_equity: float = 1600.0, slippage: float = 0.001,
        start=None) -> Result:
    assets = [a for a in cfg.assets if a in data]
    feat = {a: features(data[a], cfg) for a in assets}
    idx = pd.DatetimeIndex(sorted(set().union(*[set(d.index) for d in data.values()])))
    if start is not None:
        st = pd.Timestamp(start)
        idx = idx[idx >= (st if st.tzinfo else st.tz_localize("UTC"))]
    cash, fees, stops = start_equity, 0.0, 0
    hold = {a: Holding() for a in assets}
    guard = DrawdownGuard()
    curve, trades, weights_log = [], [], []
    pending: dict[str, float] | None = None
    halted_on = None
    last_close: dict[str, float] = {}

    def fill(t, a, side, qty, px, why):
        nonlocal cash, fees
        notional = qty * px
        fee = notional * cfg.fee_rate
        cash += -(notional + fee) if side == "buy" else notional - fee
        fees += fee
        trades.append(dict(ts=t, asset=a, side=side, qty=qty, px=px, notional=notional, fee=fee, why=why))

    for t in idx:
        live = [a for a in assets if t in feat[a].index]
        # 1) execute last close's plan at today's open
        if pending is not None:
            opens = {a: float(data[a].loc[t, "open"]) for a in live}
            eq_open = cash + sum(hold[a].qty * opens.get(a, last_close.get(a, 0.0)) for a in assets)
            for p in plan_rebalance(eq_open, opens, {a: hold[a].qty for a in live},
                                    {a: w for a, w in pending.items() if a in opens}, cfg, free_quote=cash):
                if p.side == "buy":
                    px = opens[p.asset] * (1 + slippage)
                    if p.qty * px * (1 + cfg.fee_rate) > cash + 1e-9:
                        continue
                    prev = feat[p.asset].index.get_loc(t) - 1
                    fill(t, p.asset, "buy", p.qty, px, p.reason)
                    on_buy(hold[p.asset], px, p.qty, float(feat[p.asset]["vol"].iloc[max(prev, 0)]), cfg)
                else:
                    fill(t, p.asset, "sell", p.qty, opens[p.asset] * (1 - slippage), p.reason)
                    on_sell(hold[p.asset], p.qty)
            pending = None
        # 2) resting stops
        for a in live:
            h = hold[a]
            if h.qty > 0 and h.stop > 0 and float(data[a].loc[t, "low"]) <= h.stop:
                fill(t, a, "sell", h.qty, min(float(data[a].loc[t, "open"]), h.stop) * (1 - slippage), "stop")
                stops += 1
                on_sell(h, h.qty)
        # 3) close
        for a in live:
            last_close[a] = float(data[a].loc[t, "close"])
        equity = cash + sum(hold[a].qty * last_close.get(a, 0.0) for a in assets)
        curve.append((t, equity))
        weights_log.append({a: hold[a].qty * last_close.get(a, 0.0) / equity if equity > 0 else 0 for a in assets})
        if guard.update(equity, cfg):
            halted_on = halted_on or t.date()
            pending = {a: 0.0 for a in assets}
            continue
        if t.dayofweek == cfg.rebalance_weekday:
            for a in live:
                ratchet(hold[a], last_close[a], float(feat[a].loc[t, "vol"]), cfg)
            w, _ = target_weights({a: feat[a].loc[t] for a in live}, cfg)
            pending = {a: w.get(a, 0.0) for a in assets}

    eq = pd.Series(dict(curve)).sort_index()
    tr = pd.DataFrame(trades, columns=["ts", "asset", "side", "qty", "px", "notional", "fee", "why"])
    return Result(eq, tr, fees, stops, pd.DataFrame(weights_log, index=eq.index), halted_on)


def buy_and_hold(data: dict[str, pd.DataFrame], assets: list[str], start_equity: float = 1600.0,
                 fee: float = 0.0025, start=None) -> pd.Series:
    idx = pd.DatetimeIndex(sorted(set.intersection(*[set(data[a].index) for a in assets])))
    if start is not None:
        st = pd.Timestamp(start)
        idx = idx[idx >= (st if st.tzinfo else st.tz_localize("UTC"))]
    per = start_equity * (1 - fee) / len(assets)
    qty = {a: per / data[a].loc[idx[0], "close"] for a in assets}
    return pd.Series({t: sum(qty[a] * data[a].loc[t, "close"] for a in assets) for t in idx})


def monte_carlo(daily_returns: pd.Series, start: float, monthly_add: float, years: int, n: int = 5000,
                block: int = 30, seed: int = 7) -> dict:
    """Block-bootstrap the strategy's own daily returns (keeps volatility clustering) with monthly deposits."""
    rng = np.random.default_rng(seed)
    r = daily_returns.dropna().to_numpy()
    days = years * 365
    out = np.empty(n)
    for i in range(n):
        path = np.concatenate([r[s:s + block] for s in rng.integers(0, len(r) - block, days // block + 1)])[:days]
        eq = start
        for d, x in enumerate(path):
            eq *= 1 + x
            if d % 30 == 29:
                eq += monthly_add
        out[i] = eq
    deposited = start + monthly_add * (days // 30)
    return {"deposited": deposited, "p10": float(np.percentile(out, 10)), "p50": float(np.percentile(out, 50)),
            "p90": float(np.percentile(out, 90)), "prob_below_deposits": float(np.mean(out < deposited))}
