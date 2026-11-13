"""Pure, deterministic signal maths on daily OHLCV. No I/O, so it is unit-tested and shared by backtest and live.

Every function uses only data up to and including bar t. The engine acts on bar t's CLOSE at the NEXT bar's
open, so nothing here can look ahead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config

ANNUAL = 365  # crypto trades every day


def to_frame(ohlcv: list[list[float]]) -> pd.DataFrame:
    """ccxt OHLCV rows -> DataFrame indexed by UTC timestamp. Drops a still-forming last bar is the CALLER's job."""
    df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop_duplicates("ts").set_index("ts").sort_index().astype(float)
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("non-positive price in OHLCV")
    return df


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()  # Wilder smoothing


def realised_vol(close: pd.Series, n: int) -> pd.Series:
    return np.log(close).diff().rolling(n, min_periods=n).std() * np.sqrt(ANNUAL)


def momentum_score(close: pd.Series, lookbacks: tuple[int, ...]) -> pd.Series:
    """Fraction of lookbacks with a positive trailing return: 0, 1/3, 2/3 or 1 for three lookbacks.

    Averaging horizons instead of picking one is the standard defence against fitting a single lucky lookback.
    """
    votes = [(close / close.shift(n) - 1 > 0).astype(float).where(close.shift(n).notna()) for n in lookbacks]
    return pd.concat(votes, axis=1).mean(axis=1, skipna=False)


def target_weight(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Per-bar desired weight for ONE asset before portfolio-level caps, plus the inputs that produced it."""
    close = df["close"]
    out = pd.DataFrame(index=df.index)
    out["score"] = momentum_score(close, cfg.lookbacks)
    out["ema"] = ema(close, cfg.trend_ema)
    out["vol"] = realised_vol(close, cfg.vol_window)
    out["atr"] = atr(df, cfg.atr_window)
    in_trend = close > out["ema"]
    vol_scale = (cfg.target_vol / out["vol"]).clip(upper=1.0)
    # Risk-based cap: a hard stop k*ATR below entry must not cost more than risk_per_trade of equity.
    stop_dist = (cfg.hard_stop_atr * out["atr"] / close).clip(lower=1e-6)
    risk_cap = cfg.risk_per_trade / stop_dist
    w = out["score"] * vol_scale
    w = w.where(in_trend, 0.0)
    w = np.minimum(np.minimum(w, cfg.max_weight), risk_cap)
    out["weight"] = w.fillna(0.0).clip(lower=0.0)
    out["warm"] = out[["score", "ema", "vol", "atr"]].notna().all(axis=1)
    out.loc[~out["warm"], "weight"] = 0.0
    return out


def scale_portfolio(weights: dict[str, float], cfg: Config) -> dict[str, float]:
    """Apply gross exposure cap and cash reserve across assets."""
    cap = min(cfg.max_gross, 1.0 - cfg.cash_reserve) if cfg.max_gross <= 1.0 else cfg.max_gross
    gross = sum(abs(w) for w in weights.values())
    if gross <= cap or gross == 0:
        return dict(weights)
    k = cap / gross
    return {a: w * k for a, w in weights.items()}
