"""Personal-trading co-pilot. Produces risk-checked trade *plans*; never executes.

Compliance note: if you trade personally while employed by a brokerage, your
personal-account-dealing policy almost certainly applies (pre-clearance,
restricted lists, holding periods). Keep this agent pointed at your own
account data only and have compliance sign off before connecting execution.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from jarvis.core import audit, risk
from jarvis.core.registry import Agent, register, tool


def atr(ticker: str, period: int = 14) -> tuple[float, float]:
    """Wilder-style ATR from daily bars via yfinance. Returns (atr, last_close)."""
    import yfinance as yf

    h = yf.Ticker(ticker).history(period="6mo").dropna()
    if len(h) < period + 1:
        raise ValueError(f"not enough bars for {ticker}")
    prev = h["Close"].shift(1)
    tr = (h["High"] - h["Low"]).combine((h["High"] - prev).abs(), max).combine((h["Low"] - prev).abs(), max)
    return float(tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]), float(h["Close"].iloc[-1])


@register
class TradingAgent(Agent):
    name = "trading"
    description = "Risk-checked trade plans, ATR stops/trails and daily-loss kill switch."

    @tool()
    def plan_trade(self, ticker: str, side: str, equity: float, start_of_day_equity: float,
                   bid: float, ask: float, pip_size: float, open_risk_amount: float = 0.0,
                   reward_risk: float = 2.0) -> dict[str, Any]:
        """Build a risk-checked order plan from a LIVE broker quote (bid/ask/pip_size
        must come from the trading platform, not from delayed data). ATR is taken
        from daily bars of the Yahoo ticker (e.g. 'EURUSD=X'). Returns size, entry,
        hard stop, take-profit, max slippage and approved/rejected with reasons.
        Does not place orders."""
        a, _ = atr(ticker)
        plan = risk.plan_order(side, risk.Quote(bid, ask, pip_size), a, equity,
                               start_of_day_equity, open_risk_amount, reward_risk)
        out = asdict(plan) | {"atr": round(a, 6)}
        audit.log("jarvis", "trade_plan", {"ticker": ticker, **out})
        return out

    @tool()
    def trail(self, ticker: str, side: str, current_stop: float) -> dict[str, Any]:
        """Suggest the next ATR trailing-stop level for an open position (only tightens)."""
        a, close = atr(ticker)
        new = risk.trailing_stop(side.lower(), current_stop, close, a)
        return {"close": close, "atr": round(a, 6), "new_stop": round(new, 6), "moved": new != current_stop}
