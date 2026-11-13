"""Daily-bar event backtest with fees, slippage, exchange-side stops and the SAME risk code the live bot runs.

Timing (no look-ahead):
  * signals and trailing stops are computed on bar t's close;
  * rebalances fill at bar t+1's open (+/- slippage, + fee);
  * a resting stop triggers inside bar t+1 if its low trades through it, filling at min(open, stop) - slippage
    (a gap through the stop fills at the open, which is what really happens).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config
from .risk import (DrawdownGuard, PositionState, on_buy_fill, on_sell_fill, on_stopped_out, plan_rebalance,
                   update_block, update_trail)
from .signals import ANNUAL, scale_portfolio, target_weight


@dataclass
class Result:
    equity: pd.Series
    trades: pd.DataFrame
    fees: float
    stops_hit: int
    halted_on: object = None

    def stats(self) -> dict:
        e = self.equity
        r = e.pct_change().dropna()
        years = max((e.index[-1] - e.index[0]).days / 365.25, 1e-9)
        cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / years) - 1
        dd = (e / e.cummax() - 1).min()
        sharpe = r.mean() / r.std() * math.sqrt(ANNUAL) if r.std() > 0 else 0.0
        down = r[r < 0].std()
        sortino = r.mean() / down * math.sqrt(ANNUAL) if down and down > 0 else 0.0
        turnover = self.trades["notional"].sum() / e.mean() / years if len(self.trades) else 0.0
        exposure = float(self.trades.attrs.get("avg_exposure", np.nan))
        return dict(start=e.index[0].date(), end=e.index[-1].date(), total_return=e.iloc[-1] / e.iloc[0] - 1,
                    cagr=cagr, max_dd=dd, sharpe=sharpe, sortino=sortino, calmar=cagr / abs(dd) if dd < 0 else np.nan,
                    trades=len(self.trades), stops=self.stops_hit, fees=self.fees, turnover_x_per_yr=turnover,
                    avg_exposure=exposure, halted_on=self.halted_on)


def run(data: dict[str, pd.DataFrame], cfg: Config, start_equity: float = 1600.0, slippage: float = 0.001) -> Result:
    assets = list(data)
    sig = {a: target_weight(data[a], cfg) for a in assets}
    idx = sorted(set.intersection(*[set(d.index) for d in data.values()]))
    idx = pd.DatetimeIndex(idx)
    cash = start_equity
    pos = {a: PositionState() for a in assets}
    guard = DrawdownGuard()
    eq_curve, trades, fees, stops, exposure = [], [], 0.0, 0, []
    pending: dict[str, float] | None = None
    halted_on = None

    for i, t in enumerate(idx):
        bar = {a: data[a].loc[t] for a in assets}
        # 1) execute yesterday's plan at today's open
        if pending is not None:
            equity_open = cash + sum(pos[a].qty * bar[a]["open"] for a in assets)
            prices = {a: bar[a]["open"] for a in assets}
            plans = plan_rebalance(equity_open, prices, {a: pos[a].qty for a in assets}, pending, cfg, free_quote=cash)
            for p in plans:
                px = prices[p.asset] * (1 + slippage if p.side == "buy" else 1 - slippage)
                notional = p.qty * px
                fee = notional * cfg.fee_rate
                if p.side == "buy":
                    if notional + fee > cash:
                        continue
                    cash -= notional + fee
                    prev = max(sig[p.asset].index.get_loc(t) - 1, 0)  # ATR known at the decision close
                    on_buy_fill(pos[p.asset], px, p.qty, sig[p.asset]["atr"].iloc[prev], cfg)
                else:
                    cash += notional - fee
                    on_sell_fill(pos[p.asset], p.qty)
                fees += fee
                trades.append(dict(ts=t, asset=p.asset, side=p.side, qty=p.qty, px=px, notional=notional, fee=fee, why=p.reason))
            pending = None
        # 2) resting stops trigger intrabar
        for a in assets:
            st = pos[a]
            if st.qty > 0 and st.stop > 0 and bar[a]["low"] <= st.stop:
                px = min(bar[a]["open"], st.stop) * (1 - slippage)
                notional = st.qty * px
                fee = notional * cfg.fee_rate
                cash += notional - fee
                fees += fee
                stops += 1
                trades.append(dict(ts=t, asset=a, side="sell", qty=st.qty, px=px, notional=notional, fee=fee, why="stop"))
                on_stopped_out(st)
        # 3) mark to market at the close, update trails and signals
        equity = cash + sum(pos[a].qty * bar[a]["close"] for a in assets)
        eq_curve.append((t, equity))
        exposure.append(sum(pos[a].qty * bar[a]["close"] for a in assets) / equity if equity > 0 else 0)
        if guard.update(equity, cfg):
            if halted_on is None:
                halted_on = t.date()
            pending = {a: 0.0 for a in assets}  # flatten next open, then stay flat
            continue
        raw = {}
        for a in assets:
            s = sig[a].loc[t]
            update_trail(pos[a], bar[a]["close"], s["atr"], cfg)
            update_block(pos[a], bar[a]["close"], s["weight"])
            raw[a] = 0.0 if pos[a].blocked else float(s["weight"])
        pending = scale_portfolio(raw, cfg)

    eq = pd.Series(dict(eq_curve)).sort_index()
    tr = pd.DataFrame(trades, columns=["ts", "asset", "side", "qty", "px", "notional", "fee", "why"])
    tr.attrs["avg_exposure"] = float(np.mean(exposure)) if exposure else np.nan
    return Result(eq, tr, fees, stops, halted_on)


def buy_and_hold(data: dict[str, pd.DataFrame], start_equity: float = 1600.0, fee: float = 0.0025,
                 start=None) -> pd.Series:
    idx = sorted(set.intersection(*[set(d.index) for d in data.values()]))
    idx = pd.DatetimeIndex(idx)
    if start is not None:
        idx = idx[idx >= start]
    per = start_equity * (1 - fee) / len(data)
    qty = {a: per / data[a].loc[idx[0], "close"] for a in data}
    return pd.Series({t: sum(qty[a] * data[a].loc[t, "close"] for a in data) for t in idx})
