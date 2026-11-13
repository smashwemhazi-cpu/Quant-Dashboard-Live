"""HARVEST engine: volatility harvesting ("rebalancing premium"), with its OWN ledger and state file.

Hold a constant mix, by default 1/3 BTC, 1/3 tokenised gold (PAXG), 1/3 EUR. When any weight drifts more than
`harvest_band` (10 percentage points) from its target, trade everything back to target: that sells what ran up
and buys what fell. With two volatile, weakly correlated assets plus cash, this mechanically buys low and sells
high. Bands are wide on purpose: only a handful of rebalances happen per year, so fees cannot eat the edge
(your old 0.2% grid was the same idea with bands far too tight for 0.22% fees).

Risk: it is always ~2/3 invested. The disaster stop is deliberately wide (8 sigma of a weekly move) because a
tight stop would sell exactly the dips this engine exists to buy; the -35% kill switch sits on top.
"""
from __future__ import annotations

import dataclasses as dc
from datetime import datetime, timezone

import pandas as pd

from .config import Config
from .engine import Engine, State
from .risk import DrawdownGuard, Holding, on_buy, on_sell, plan_rebalance, ratchet
from .signals import features, to_frame


def harvest_cfg(cfg: Config) -> Config:
    """The engine runs with the wide stop and its own state file; everything else (fees, guard...) is shared."""
    return dc.replace(cfg, stop_sigma=cfg.harvest_stop_sigma, state_file=cfg.harvest_state_file)


def targets(cfg: Config, sidelined: set[str] = frozenset()) -> dict[str, float]:
    """Target weights; an asset whose disaster stop fired stays in EUR (weight 0) until its trend turns up."""
    return {a: (0.0 if a in sidelined else w) for a, w in zip(cfg.harvest_assets, cfg.harvest_targets)}


def update_sidelined(hold: dict, trend: dict[str, float]) -> set[str]:
    """Clear an asset's sideline once its trend vote is positive again; return the assets still sidelined."""
    for a, h in hold.items():
        if h.sidelined and trend.get(a, -1.0) > 0:
            h.sidelined = False
    return {a for a, h in hold.items() if h.sidelined}


def drift(weights: dict[str, float], cfg: Config, sidelined: set[str] = frozenset()) -> float:
    return max(abs(weights.get(a, 0.0) - w) for a, w in targets(cfg, sidelined).items())


class HarvestEngine(Engine):
    @classmethod
    def load(cls, cfg: Config, broker, capital: float | None, sleep=None):
        hc = harvest_cfg(cfg)
        st = State.load(hc.state_file, dc.replace(hc, dual_weight=0.0, rotation_weight=0.0), capital)
        st.holdings = {a: st.holdings.get(a, Holding()) for a in cfg.harvest_assets}
        kw = {"sleep": sleep} if sleep else {}
        return cls(hc, broker, st, hc.state_file, **kw)

    def run_once(self, force_rebalance: bool = False) -> dict:
        cfg, st = self.cfg, self.st
        feats = {a: features(to_frame(self.b.daily_bars(self._sym(a), 400)), cfg) for a in cfg.harvest_assets}
        bar = min(f.index[-1] for f in feats.values())
        self.run_tag = "harvest:" + bar.date().isoformat()
        self.vol = {a: float(f.loc[bar, "vol"]) for a, f in feats.items() if bar in f.index}
        bal = self.b.balances()
        self.check_stops()          # first: book stop fills, THEN reconcile
        self.reconcile(bal)
        try:
            mids = {a: self._mid(self._sym(a))[2] for a in cfg.harvest_assets}
        except Exception as e:  # noqa: BLE001 - bad/stale quote: do nothing today, stops stay on Kraken
            self._commit()
            return {"status": "skipped", "reason": f"no usable quote: {e}"}
        trend = {a: float(f.loc[bar, "trend"]) for a, f in feats.items() if bar in f.index}
        side = update_sidelined(st.holdings, trend)
        equity = st.cash + sum(h.qty * mids[a] for a, h in st.holdings.items())
        weights = {a: st.holdings[a].qty * mids[a] / equity for a in cfg.harvest_assets} if equity > 0 else {}
        if st.guard.update(equity, cfg):
            tgt, reason = {a: 0.0 for a in cfg.harvest_assets}, "halt"
        elif st.last_bar == bar.date().isoformat() and not force_rebalance:
            for a in st.holdings:
                self.protect(a)
            self._commit()
            return {"status": "already-done", "equity": equity, "weights": weights}
        else:
            d = drift(weights, cfg, side)
            if not (force_rebalance or not st.last_rebalance or d > cfg.harvest_band):
                for a in st.holdings:
                    self.protect(a)
                st.last_bar = bar.date().isoformat()
                self._log(equity, weights, None)
                self._commit()
                return {"status": "hold", "equity": equity, "weights": weights, "max_drift": d,
                        "rebalances_when_drift_exceeds": cfg.harvest_band}
            tgt = targets(cfg, side)
            reason = "initial" if not st.last_rebalance else ("forced" if force_rebalance else f"drift {d:.1%}")
            for a, h in st.holdings.items():
                ratchet(h, mids[a], self.vol.get(a, float("nan")), cfg)
        free_eur = bal.get(cfg.quote, {}).get("free", st.cash) if bal else st.cash
        plans = plan_rebalance(equity, mids, {a: h.qty for a, h in st.holdings.items()}, tgt,
                               dc.replace(cfg, rebalance_band=0.0), free_quote=min(st.cash, free_eur))
        for p in plans:
            self.execute(p)
        for a in st.holdings:
            self.protect(a)
        st.last_bar = bar.date().isoformat()
        if reason != "halt":
            st.last_rebalance = st.last_bar
        equity = st.cash + sum(h.qty * mids[a] for a, h in st.holdings.items())
        self._log(equity, {a: st.holdings[a].qty * mids[a] / equity for a in cfg.harvest_assets} if equity else {}, tgt)
        self._commit()
        return {"status": "rebalanced", "reason": reason, "equity": equity, "orders": len(plans), "targets": tgt,
                "sidelined_after_stop": sorted(side) if reason != "halt" else []}

    def _log(self, equity, weights, tgt) -> None:
        self.st.history.append({"bar": self.st.last_bar, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "equity": round(equity, 2), "weights": {a: round(w, 4) for a, w in weights.items()},
                                "rebalanced_to": tgt, "halted": self.st.guard.halted})


def backtest(data: dict[str, pd.DataFrame], cfg: Config, start_equity: float = 1600.0, slippage: float = 0.001,
             start=None):
    """Same rules as HarvestEngine, daily bars: decide on the close, fill at the next open, stops intrabar."""
    from .backtest import Result
    hc = harvest_cfg(cfg)
    assets = list(cfg.harvest_assets)
    feat = {a: features(data[a], hc) for a in assets}
    idx = pd.DatetimeIndex(sorted(set.intersection(*[set(data[a].index) for a in assets])))
    if start is not None:
        s = pd.Timestamp(start)
        idx = idx[idx >= (s if s.tzinfo else s.tz_localize("UTC"))]
    cash, fees, stops = start_equity, 0.0, 0
    hold = {a: Holding() for a in assets}
    guard, curve, trades, wlog = DrawdownGuard(), [], [], []
    pending, last_reb = None, None
    for t in idx:
        px = {a: data[a].loc[t] for a in assets}
        if pending is not None:
            opens = {a: float(px[a]["open"]) for a in assets}
            eq = cash + sum(hold[a].qty * opens[a] for a in assets)
            for p in plan_rebalance(eq, opens, {a: hold[a].qty for a in assets}, pending,
                                    dc.replace(hc, rebalance_band=0.0), free_quote=cash):
                fill = opens[p.asset] * (1 + slippage if p.side == "buy" else 1 - slippage)
                n = p.qty * fill
                if p.side == "buy":
                    if n * (1 + hc.fee_rate) > cash + 1e-9:
                        continue
                    cash -= n * (1 + hc.fee_rate)
                    prev = feat[p.asset].index.get_loc(t) - 1
                    on_buy(hold[p.asset], fill, p.qty, float(feat[p.asset]["vol"].iloc[max(prev, 0)]), hc)
                else:
                    cash += n * (1 - hc.fee_rate)
                    on_sell(hold[p.asset], p.qty)
                fees += n * hc.fee_rate
                trades.append(dict(ts=t, asset=p.asset, side=p.side, qty=p.qty, px=fill, notional=n, fee=n * hc.fee_rate, why="harvest"))
            pending = None
        for a in assets:
            h = hold[a]
            if h.qty > 0 and h.stop > 0 and float(px[a]["low"]) <= h.stop:
                fill = min(float(px[a]["open"]), h.stop) * (1 - slippage)
                cash += h.qty * fill * (1 - hc.fee_rate)
                fees += h.qty * fill * hc.fee_rate
                trades.append(dict(ts=t, asset=a, side="sell", qty=h.qty, px=fill, notional=h.qty * fill,
                                   fee=h.qty * fill * hc.fee_rate, why="stop"))
                stops += 1
                on_sell(h, h.qty)
                h.sidelined = True
        closes = {a: float(px[a]["close"]) for a in assets}
        equity = cash + sum(hold[a].qty * closes[a] for a in assets)
        curve.append((t, equity))
        w = {a: hold[a].qty * closes[a] / equity for a in assets} if equity > 0 else {}
        wlog.append(w)
        if guard.update(equity, hc):
            pending = {a: 0.0 for a in assets}
            continue
        if not bool(feat[assets[0]].loc[t, "ready"]) and last_reb is None:
            continue
        side = update_sidelined(hold, {a: float(feat[a].loc[t, "trend"]) for a in assets})
        if last_reb is None or drift(w, hc, side) > hc.harvest_band:
            for a in assets:
                ratchet(hold[a], closes[a], float(feat[a].loc[t, "vol"]), hc)
            pending, last_reb = targets(hc, side), t
    eq = pd.Series(dict(curve)).sort_index()
    tr = pd.DataFrame(trades, columns=["ts", "asset", "side", "qty", "px", "notional", "fee", "why"])
    return Result(eq, tr, fees, stops, pd.DataFrame(wlog, index=eq.index), guard.halted or None)
