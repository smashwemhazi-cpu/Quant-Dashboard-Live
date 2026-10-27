"""Next-day direction predictor with honest, out-of-sample scoring.

Model: L2-regularised logistic regression (pure numpy) on daily features:
returns (1/5/20d), RSI(14), ATR%, distance to EMA50/EMA200 in ATRs, short/long
volatility ratio. Target: tomorrow's close > today's close.

Honesty rules baked in:
  * Skill is measured walk-forward only (train on the past, predict the next day,
    retrain every `retrain_every` days) - never in-sample.
  * Compared with the naive base rate (Brier skill score) and tested for
    significance; "edge" is claimed only if BSS > 0 AND the hit rate beats the
    base rate at p < 0.05. Otherwise output is labelled informational.
  * Every live prediction is logged and scored next day in the wrap-up, so the
    track record is visible and cannot be cherry-picked.
Daily-bar direction is close to a coin flip for most liquid markets; expect
"no demonstrated edge" often. That is the correct answer, not a bug.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from jarvis.core.config import settings

FEATURES = ["ret1", "ret5", "ret20", "rsi", "atr_pct", "d_ema50", "d_ema200", "vol_ratio"]


def _rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def features(df: pd.DataFrame) -> pd.DataFrame:
    """df: date-indexed OHLC (columns open, high, low, close)."""
    c, h, l = df["close"], df["high"], df["low"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    f = pd.DataFrame(index=df.index)
    f["ret1"] = np.log(c).diff()
    f["ret5"] = np.log(c).diff(5)
    f["ret20"] = np.log(c).diff(20)
    f["rsi"] = (_rsi(c) - 50) / 50
    f["atr_pct"] = atr / c
    f["d_ema50"] = (c - c.ewm(span=50, adjust=False).mean()) / atr
    f["d_ema200"] = (c - c.ewm(span=200, adjust=False).mean()) / atr
    f["vol_ratio"] = tr.rolling(5).mean() / tr.rolling(20).mean()
    f["y"] = (c.shift(-1) > c).astype(float)
    f.loc[f.index[-1], "y"] = np.nan  # tomorrow unknown
    return f.iloc[200:]  # EMA200 warm-up


def _fit(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 300) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = np.c_[np.ones(len(X)), (X - mu) / sd]
    w = np.zeros(Z.shape[1])
    for _ in range(iters):  # Newton-Raphson (IRLS) - converges fast, no learning rate to tune
        p = 1 / (1 + np.exp(-Z @ w))
        g = Z.T @ (p - y) + l2 * np.r_[0, w[1:]]
        H = (Z * (p * (1 - p))[:, None]).T @ Z + l2 * np.diag(np.r_[0, np.ones(len(w) - 1)])
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w, mu, sd


def _predict(model: tuple[np.ndarray, np.ndarray, np.ndarray], X: np.ndarray) -> np.ndarray:
    w, mu, sd = model
    return 1 / (1 + np.exp(-(np.c_[np.ones(len(X)), (X - mu) / sd] @ w)))


def walk_forward(df: pd.DataFrame, min_train: int = 500, retrain_every: int = 20) -> dict[str, Any]:
    f = features(df).dropna(subset=FEATURES)
    hist = f.dropna(subset=["y"])
    if len(hist) < min_train + 60:
        return {"error": f"need {min_train + 60}+ usable bars, have {len(hist)}"}
    X, y = hist[FEATURES].to_numpy(), hist["y"].to_numpy()
    probs = np.full(len(y), np.nan)
    for start in range(min_train, len(y), retrain_every):
        model = _fit(X[:start], y[:start])
        probs[start:start + retrain_every] = _predict(model, X[start:start + retrain_every])
    m = ~np.isnan(probs)
    p, yt = probs[m], y[m]
    base = float(y[:min_train].mean())                      # base rate known at the time
    hit = float(((p > 0.5) == (yt == 1)).mean())
    naive_hit = float(max(yt.mean(), 1 - yt.mean()))        # best constant guess, in hindsight (strict)
    brier, brier_ref = float(((p - yt) ** 2).mean()), float(((base - yt) ** 2).mean())
    bss = 1 - brier / brier_ref if brier_ref > 0 else 0.0
    n = len(yt)
    z = (hit - naive_hit) / math.sqrt(naive_hit * (1 - naive_hit) / n)
    p_value = 0.5 * math.erfc(z / math.sqrt(2))             # one-sided
    edge = bss > 0 and p_value < 0.05
    return {"oos_days": n, "hit_rate": round(hit, 4), "naive_hit_rate": round(naive_hit, 4),
            "brier": round(brier, 4), "brier_base_rate": round(brier_ref, 4), "brier_skill": round(bss, 4),
            "p_value": round(p_value, 4), "edge": edge,
            "verdict": "out-of-sample skill vs base rate (p<0.05)" if edge
            else "no demonstrated edge - informational only"}


def completed(df: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Drop today's (UTC) bar: it is still forming, so it is neither a valid input nor a valid outcome.
    The model is trained on finished days; predicting from or scoring against a part-day bar skews both."""
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    if now.tzinfo is not None:
        now = now.tz_convert("UTC").tz_localize(None)
    today = now.normalize()
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return df[idx.normalize() < today]


def predict_next(df: pd.DataFrame) -> dict[str, Any]:
    f = features(df).dropna(subset=FEATURES)
    hist = f.dropna(subset=["y"])
    model = _fit(hist[FEATURES].to_numpy(), hist["y"].to_numpy())
    p_up = float(_predict(model, f[FEATURES].to_numpy()[-1:])[0])
    last = f.iloc[-1]
    return {"as_of": str(f.index[-1])[:10], "p_up": round(p_up, 3),
            "drivers": {k: round(float(last[k]), 3) for k in FEATURES}}


# ------------------------------------------------------------------ track record
def _db() -> sqlite3.Connection:
    Path(settings.state_db).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(settings.state_db)
    c.execute("""CREATE TABLE IF NOT EXISTS predictions (
        symbol TEXT, as_of TEXT, p_up REAL, edge INTEGER, actual_up INTEGER, created_at TEXT,
        PRIMARY KEY (symbol, as_of))""")
    return c


def log_prediction(symbol: str, pred: dict[str, Any], edge: bool) -> None:
    with _db() as c:
        c.execute("INSERT OR IGNORE INTO predictions VALUES (?,?,?,?,NULL,?)",
                  (symbol, pred["as_of"], pred["p_up"], int(edge), datetime.now(timezone.utc).isoformat()))


def score_predictions(symbol: str, df: pd.DataFrame) -> None:
    """Fill actual_up for logged predictions whose next bar now exists."""
    closes = df["close"]
    idx = [str(i)[:10] for i in closes.index]
    with _db() as c:
        for (as_of,) in c.execute("SELECT as_of FROM predictions WHERE symbol=? AND actual_up IS NULL",
                                  (symbol,)).fetchall():
            if as_of in idx and idx.index(as_of) + 1 < len(idx):
                i = idx.index(as_of)
                c.execute("UPDATE predictions SET actual_up=? WHERE symbol=? AND as_of=?",
                          (int(closes.iloc[i + 1] > closes.iloc[i]), symbol, as_of))


def track_record(days: int = 60) -> list[dict[str, Any]]:
    with _db() as c:
        rows = c.execute("""SELECT symbol, COUNT(*), AVG((p_up > 0.5) = (actual_up = 1)),
                            AVG((p_up - actual_up) * (p_up - actual_up)) FROM predictions
                            WHERE actual_up IS NOT NULL AND as_of >= date('now', ?) GROUP BY symbol""",
                         (f"-{days} days",)).fetchall()
    return [{"symbol": s, "scored": n, "hit_rate": round(h, 3), "brier": round(b, 4)} for s, n, h, b in rows]
