"""Pure, deterministic feature maths on daily OHLCV. No I/O; shared by backtest, live engine and the report.

Every column at row t uses only bars <= t. A swing point at bar t-k is only *known* at bar t (it needs k bars on
its right), and that is when it appears here. The engine acts on bar t's close at the next bar's open.

Smart Money Concepts, defined objectively (so they can be tested rather than eyeballed):
  swing high/low   fractal pivot: the bar's high (low) is the max (min) of the 2k+1 bars centred on it
  structure        +1 after a bullish break of structure (close above the last unbroken swing high),
                   -1 after a bearish one (close below the last unbroken swing low)
  BOS / CHoCH      a structure break; CHoCH when it flips the previous structure
  liquidity sweep  bar trades below the last known swing low but CLOSES back above it (sell-side liquidity taken)
  FVG (bullish)    low[t] > high[t-2]: a three-candle imbalance; stored as the gap's bounds
  order block      the last down candle before a bullish BOS; stored as its low/high
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config

ANNUAL = 365  # crypto trades every day


def to_frame(ohlcv: list[list[float]]) -> pd.DataFrame:
    """ccxt-style OHLCV rows -> DataFrame indexed by UTC timestamp."""
    df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop_duplicates("ts").set_index("ts").sort_index().astype(float)
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("non-positive price in OHLCV")
    if (df["high"] < df[["open", "close", "low"]].max(axis=1) - 1e-12).any():
        raise ValueError("high below open/close/low in OHLCV")
    return df


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()  # Wilder smoothing


def realised_vol(close: pd.Series, n: int) -> pd.Series:
    return np.log(close).diff().rolling(n, min_periods=n).std() * np.sqrt(ANNUAL)


def efficiency_ratio(close: pd.Series, n: int) -> pd.Series:
    """Kaufman ER: net move / path length. ~1 = clean trend, ~0 = chop."""
    net = (close - close.shift(n)).abs()
    path = close.diff().abs().rolling(n, min_periods=n).sum()
    return (net / path.replace(0, np.nan)).clip(0, 1)


def momentum_score(close: pd.Series, lookbacks: tuple[int, ...]) -> pd.Series:
    """Fraction of lookbacks with a positive trailing return (0, 1/3, 2/3, 1 for three lookbacks)."""
    votes = [(close / close.shift(n) - 1 > 0).astype(float).where(close.shift(n).notna()) for n in lookbacks]
    return pd.concat(votes, axis=1).mean(axis=1, skipna=False)


def swings(df: pd.DataFrame, k: int) -> pd.DataFrame:
    """Confirmed fractal swing points. Row t carries pivots that became known at t (pivot bar = t-k)."""
    win = 2 * k + 1
    lo_k, hi_k = df["low"].shift(k), df["high"].shift(k)
    new_low = lo_k.eq(df["low"].rolling(win, min_periods=win).min()) & lo_k.notna()
    new_high = hi_k.eq(df["high"].rolling(win, min_periods=win).max()) & hi_k.notna()
    out = pd.DataFrame(index=df.index)
    out["new_swing_low"] = new_low
    out["new_swing_high"] = new_high
    out["swing_low"] = lo_k.where(new_low).ffill()
    out["swing_high"] = hi_k.where(new_high).ffill()
    return out


def smc(df: pd.DataFrame, sw: pd.DataFrame) -> pd.DataFrame:
    """Structure, BOS/CHoCH, liquidity sweeps, FVGs, order blocks. One pass, O(n), no look-ahead."""
    n = len(df)
    o, h, l, c = (df[x].to_numpy() for x in ("open", "high", "low", "close"))
    new_sl, new_sh = sw["new_swing_low"].to_numpy(), sw["new_swing_high"].to_numpy()
    sl_val, sh_val = sw["swing_low"].to_numpy(), sw["swing_high"].to_numpy()
    structure = np.zeros(n)
    bos_up = np.zeros(n, bool)
    bos_dn = np.zeros(n, bool)
    choch = np.zeros(n, bool)
    sweep = np.zeros(n, bool)
    sweep_low = np.full(n, np.nan)
    swept_level = np.full(n, np.nan)
    ob_lo = np.full(n, np.nan)
    ob_hi = np.full(n, np.nan)
    fvg_lo = np.full(n, np.nan)
    fvg_hi = np.full(n, np.nan)
    struct, ref_hi, ref_lo, used_hi, used_lo = 0, np.nan, np.nan, True, True
    known_lo = np.nan  # last swing low known BEFORE this bar (for sweeps)
    last_down = -1     # index of the last down candle (order-block candidate)
    cur_ob = (np.nan, np.nan)
    cur_fvg = (np.nan, np.nan)
    for t in range(n):
        # sweep is judged against levels known before bar t
        if not np.isnan(known_lo) and l[t] < known_lo and c[t] > known_lo:
            sweep[t], sweep_low[t], swept_level[t] = True, l[t], known_lo
        if new_sh[t]:
            ref_hi, used_hi = sh_val[t], False
        if new_sl[t]:
            ref_lo, used_lo = sl_val[t], False
        if not used_hi and c[t] > ref_hi:
            choch[t] = struct == -1
            struct, used_hi, bos_up[t] = 1, True, True
            if last_down >= 0:
                cur_ob = (l[last_down], h[last_down])
        if not used_lo and c[t] < ref_lo:
            choch[t] = choch[t] or struct == 1
            struct, used_lo, bos_dn[t] = -1, True, True
        if t >= 2 and l[t] > h[t - 2]:
            cur_fvg = (h[t - 2], l[t])
        if c[t] < o[t]:
            last_down = t
        structure[t] = struct
        ob_lo[t], ob_hi[t] = cur_ob
        fvg_lo[t], fvg_hi[t] = cur_fvg
        if not np.isnan(sl_val[t]):
            known_lo = sl_val[t]
    out = pd.DataFrame(index=df.index)
    out["structure"] = structure
    out["bos_up"], out["bos_dn"], out["choch"] = bos_up, bos_dn, choch
    out["sweep"], out["sweep_low"], out["swept_level"] = sweep, sweep_low, swept_level
    out["ob_lo"], out["ob_hi"], out["fvg_lo"], out["fvg_hi"] = ob_lo, ob_hi, fvg_lo, fvg_hi
    # most recent sweep within the window, carried forward with its age
    idx = pd.Series(np.where(sweep, np.arange(n), np.nan), index=df.index).ffill()
    out["bars_since_sweep"] = np.arange(n) - idx.to_numpy()
    out["last_sweep_low"] = out["sweep_low"].ffill()
    out["last_swept_level"] = out["swept_level"].ffill()
    return out


def features(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    close = df["close"]
    f = df[["open", "high", "low", "close"]].copy()
    f["ema"] = ema(close, cfg.trend_ema)
    f["slope"] = f["ema"] / f["ema"].shift(cfg.slope_window) - 1
    f["atr"] = atr(df, cfg.atr_window)
    f["vol"] = realised_vol(close, cfg.vol_window)
    f["er"] = efficiency_ratio(close, cfg.er_window)
    f["score"] = momentum_score(close, cfg.lookbacks)
    f["don_hi"] = df["high"].rolling(cfg.range_window, min_periods=cfg.range_window).max()
    f["don_lo"] = df["low"].rolling(cfg.range_window, min_periods=cfg.range_window).min()
    sw = swings(df, cfg.swing_k)
    f = f.join(sw).join(smc(df, sw))
    flat = (f["er"] < cfg.er_range) | (f["slope"].abs() < cfg.slope_flat)
    up = (close > f["ema"]) & (f["slope"] > 0)
    down = (close < f["ema"]) & (f["slope"] < 0)
    f["regime"] = np.select([flat, up, down], ["range", "up", "down"], default="range")
    vol_scale = (cfg.target_vol / f["vol"]).clip(upper=1.0)
    f["trend_w"] = (f["score"] * vol_scale).where(close > f["ema"], 0.0).fillna(0.0).clip(lower=0.0)
    f["warm"] = f[["ema", "slope", "atr", "vol", "er", "score", "don_hi", "don_lo"]].notna().all(axis=1)
    f.loc[~f["warm"], "regime"] = "warmup"
    return f


def scale_portfolio(weights: dict[str, float], cfg: Config) -> dict[str, float]:
    """Apply gross exposure cap and cash reserve across assets."""
    cap = min(cfg.max_gross, 1.0 - cfg.cash_reserve) if cfg.max_gross <= 1.0 else cfg.max_gross
    gross = sum(abs(w) for w in weights.values())
    if gross <= cap or gross == 0:
        return dict(weights)
    k = cap / gross
    return {a: w * k for a, w in weights.items()}
