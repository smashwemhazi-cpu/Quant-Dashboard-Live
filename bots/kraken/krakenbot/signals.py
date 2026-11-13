"""Pure feature maths on daily OHLCV and the rotation itself. No I/O; backtest and live share every line.

Row t only uses bars <= t. Decisions taken on bar t's close are executed at the next bar's open.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import Config

ANNUAL = 365  # crypto trades every day


def to_frame(ohlcv: list[list[float]]) -> pd.DataFrame:
    """ccxt-style OHLCV rows -> DataFrame indexed by UTC day. Rejects corrupt candles instead of trading on them."""
    df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.normalize()
    df = df.drop_duplicates("ts", keep="last").set_index("ts").sort_index().astype(float)
    px = df[["open", "high", "low", "close"]]
    if (px <= 0).any().any() or px.isna().any().any():
        raise ValueError("non-positive or missing price in OHLCV")
    if (df["high"] < px.max(axis=1) - 1e-9).any() or (df["low"] > px.min(axis=1) + 1e-9).any():
        raise ValueError("high/low inconsistent with open/close in OHLCV")
    return df


def features(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    c = df["close"]
    f = df[["open", "high", "low", "close"]].copy()
    f["vol"] = np.log(c).diff().rolling(cfg.vol_window, min_periods=cfg.vol_window).std() * math.sqrt(ANNUAL)
    # risk-adjusted momentum: average of (return over L) / annualised vol, over several L (no single magic L)
    f["score"] = sum((c / c.shift(n) - 1) for n in cfg.rank_lookbacks) / len(cfg.rank_lookbacks) / f["vol"]
    # trend vote in [-1, 1]: share of lookbacks with a positive return, minus the share negative
    f["trend"] = sum(np.sign(c / c.shift(n) - 1) for n in cfg.trend_lookbacks) / len(cfg.trend_lookbacks)
    f["ready"] = f[["vol", "score", "trend"]].notna().all(axis=1) & (f["vol"] > 0)
    return f


def sleeve_weights(rows: dict[str, pd.Series], cfg: Config, top_k: int) -> tuple[dict[str, float], pd.DataFrame]:
    """One sleeve: rank by score, keep the top_k that are positive AND in an uptrend, size by volatility.
    Weights are fractions of the SLEEVE. rows = each asset's feature row at the same closed bar."""
    if not rows:
        return {}, pd.DataFrame(columns=["rank", "score", "trend", "vol", "eligible", "weight"])
    tab = pd.DataFrame({a: r for a, r in rows.items()}).T
    tab = tab[tab["ready"].astype(bool)].copy()
    tab["score"] = tab["score"].astype(float)
    tab["trend"] = tab["trend"].astype(float)
    tab = tab.sort_values("score", ascending=False)
    tab["rank"] = np.arange(1, len(tab) + 1)
    tab["eligible"] = (tab["score"] > 0) & (tab["trend"] > 0)
    picks = tab[tab["eligible"]].head(top_k)
    w = {a: 0.0 for a in rows}
    for a, r in picks.iterrows():
        w[a] = min(cfg.max_weight, cfg.target_vol / float(r["vol"]) / top_k)
    if sum(w.values()) > 1.0:
        tot = sum(w.values())
        w = {a: v / tot for a, v in w.items()}
    tab["weight"] = [w.get(a, 0.0) for a in tab.index]
    return w, tab[["rank", "score", "trend", "vol", "eligible", "weight"]]


def target_weights(rows: dict[str, pd.Series], cfg: Config) -> tuple[dict[str, float], dict[str, pd.DataFrame]]:
    """Whole portfolio = sum over sleeves of (sleeve share x sleeve weights), capped at 1 - cash_reserve.
    Returns portfolio weights per asset and each sleeve's ranking table (for reports)."""
    total = {a: 0.0 for a in cfg.assets}
    tables = {}
    for name, share, assets, k in cfg.sleeves:
        if share <= 0:
            continue
        w, tab = sleeve_weights({a: rows[a] for a in assets if a in rows}, cfg, k)
        tables[name] = tab
        for a, v in w.items():
            total[a] += share * v
    gross, cap = sum(total.values()), 1.0 - cfg.cash_reserve
    if gross > cap:
        total = {a: v * cap / gross for a, v in total.items()}
    return total, tables
