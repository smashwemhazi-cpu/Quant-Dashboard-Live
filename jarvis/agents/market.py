"""Market intelligence: "how has the market been acting?"

Regime = trend (price vs 50/200 SMA) x volatility (20d realised vol vs its
1y percentile), plus a macro overlay (VIX, US10Y, DXY). This replaces the
dashboard's two-threshold VIX/TNX light with something that also reports *why*.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from jarvis.core.registry import Agent, register, tool

DEFAULT_WATCHLIST = "EURUSD=X,GBPUSD=X,USDJPY=X,XAUUSD=X,^GSPC,^NDX,BTC-USD,CL=F"


def _history(ticker: str, period: str = "2y") -> pd.Series:
    import yfinance as yf

    hist = yf.Ticker(ticker).history(period=period)
    if hist.empty:
        raise ValueError(f"no data for {ticker}")
    return hist["Close"].dropna()


def regime_from_closes(close: pd.Series) -> dict[str, Any]:
    """Pure function so it can be tested without network access."""
    if len(close) < 220:
        raise ValueError("need >= 220 bars for regime classification")
    rets = np.log(close).diff().dropna()
    rv20 = rets.rolling(20).std() * math.sqrt(252)
    rv_pct = float((rv20.dropna() <= rv20.iloc[-1]).mean() * 100)
    sma50, sma200 = close.rolling(50).mean().iloc[-1], close.rolling(200).mean().iloc[-1]
    last = float(close.iloc[-1])
    trend = "up" if last > sma50 > sma200 else "down" if last < sma50 < sma200 else "range"
    vol = "high" if rv_pct >= 80 else "low" if rv_pct <= 20 else "normal"
    playbook = {
        ("up", "low"): "trend-following, pullback entries",
        ("up", "normal"): "trend-following with ATR trail",
        ("up", "high"): "trend intact but whippy - cut size",
        ("down", "low"): "trend-following short bias",
        ("down", "normal"): "short rallies, tighter stops",
        ("down", "high"): "risk-off: reduce exposure, watch liquidity",
        ("range", "low"): "mean reversion at range edges",
        ("range", "normal"): "mean reversion, fade extremes",
        ("range", "high"): "stand aside / small size",
    }[(trend, vol)]
    return {
        "last": round(last, 5),
        "ret_5d_pct": round((close.iloc[-1] / close.iloc[-6] - 1) * 100, 2),
        "ret_20d_pct": round((close.iloc[-1] / close.iloc[-21] - 1) * 100, 2),
        "realised_vol_20d_pct": round(float(rv20.iloc[-1]) * 100, 2),
        "vol_percentile_1y": round(rv_pct, 1),
        "trend": trend,
        "volatility": vol,
        "suggested_playbook": playbook,
    }


@register
class MarketAgent(Agent):
    name = "market"
    description = "Market regime, volatility and macro context across a watchlist."

    @tool()
    def regime(self, tickers: str = DEFAULT_WATCHLIST) -> dict[str, Any]:
        """Classify trend and volatility regime for comma-separated Yahoo tickers
        (e.g. 'EURUSD=X,XAUUSD=X,^GSPC'). Returns per-symbol returns, realised vol,
        vol percentile, regime and a suggested strategy playbook."""
        out: dict[str, Any] = {}
        for t in [x.strip() for x in tickers.split(",") if x.strip()][:20]:
            try:
                out[t] = regime_from_closes(_history(t))
            except Exception as e:  # one bad symbol must not sink the board
                out[t] = {"error": str(e)}
        return out

    @tool()
    def macro(self) -> dict[str, Any]:
        """Macro dashboard: VIX, US 10Y yield, Dollar index with 20-day change and a risk light."""
        res: dict[str, Any] = {}
        for label, t in {"VIX": "^VIX", "US10Y": "^TNX", "DXY": "DX-Y.NYB"}.items():
            try:
                c = _history(t, "3mo")
                res[label] = {"last": round(float(c.iloc[-1]), 2),
                              "chg_20d": round(float(c.iloc[-1] - c.iloc[-21]), 2)}
            except Exception as e:
                res[label] = {"error": str(e)}
        vix = res.get("VIX", {}).get("last")
        res["risk_light"] = ("unknown" if vix is None else
                             "red" if vix >= 25 else "green" if vix <= 17 else "amber")
        return res
