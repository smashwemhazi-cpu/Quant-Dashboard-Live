"""Personal-trading co-pilot. Produces risk-checked trade *plans*; never executes.

Compliance note: if you trade personally while employed by a brokerage, your
personal-account-dealing policy almost certainly applies (pre-clearance,
restricted lists, holding periods). Keep this agent pointed at your own
account data only and have compliance sign off before connecting execution.
"""
from __future__ import annotations

import os
import re
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


ACCOUNT_CCY = os.getenv("JARVIS_ACCOUNT_CCY", "USD").upper()


def quote_to_account(ticker: str, mid: float, account_ccy: str = ACCOUNT_CCY) -> float | None:
    """Account-currency value of a 1.0 price move on 1 unit, i.e. the quote->account FX rate.
    EURUSD (quote USD, USD account) -> 1; USDJPY -> 1/price; EURGBP -> None (needs GBPUSD, pass it in).
    Anything that isn't a recognisable currency pair -> None, so the caller must supply the rate."""
    m = re.fullmatch(r"([A-Z]{3})[-/]?([A-Z]{3})(=X)?", ticker.upper().strip())
    if not m or mid <= 0:
        return None
    base, quote = m.group(1), m.group(2)
    if quote == account_ccy:
        return 1.0
    if base == account_ccy:
        return 1.0 / mid
    return None


@register
class TradingAgent(Agent):
    name = "trading"
    description = "Risk-checked trade plans, ATR stops/trails and daily-loss kill switch."

    @tool()
    def plan_trade(self, ticker: str, side: str, equity: float, start_of_day_equity: float,
                   bid: float, ask: float, pip_size: float, open_risk_amount: float = 0.0,
                   reward_risk: float = 2.0, quote_to_account_rate: float = 0.0) -> dict[str, Any]:
        """Build a risk-checked order plan from a LIVE broker quote (bid/ask/pip_size
        must come from the trading platform, not from delayed data). ATR is taken
        from daily bars of the Yahoo ticker (e.g. 'EURUSD=X'). Returns size, entry,
        hard stop, take-profit, max slippage and approved/rejected with reasons.
        quote_to_account_rate converts the pair's quote currency into the account
        currency (JARVIS_ACCOUNT_CCY, default USD); it is worked out automatically
        when the pair is quoted in, or based on, the account currency (EURUSD, USDJPY)
        and must be supplied for crosses (EURGBP: the GBPUSD rate) and non-FX tickers.
        Does not place orders."""
        rate = quote_to_account_rate or quote_to_account(ticker, (bid + ask) / 2)
        if not rate or rate <= 0:
            return {"approved": False, "units": 0,
                    "reasons": [f"can't convert {ticker}'s quote currency into {ACCOUNT_CCY} - pass "
                                "quote_to_account_rate (e.g. the GBPUSD rate for EURGBP) so the stop "
                                "risks the right amount"]}
        a, _ = atr(ticker)
        plan = risk.plan_order(side, risk.Quote(bid, ask, pip_size), a, equity,
                               start_of_day_equity, open_risk_amount, reward_risk,
                               value_per_unit_per_price=rate)
        out = asdict(plan) | {"atr": round(a, 6), "quote_to_account_rate": rate, "account_ccy": ACCOUNT_CCY}
        audit.log("jarvis", "trade_plan", {"ticker": ticker, **out})
        return out

    @tool()
    def trail(self, ticker: str, side: str, current_stop: float) -> dict[str, Any]:
        """Suggest the next ATR trailing-stop level for an open position (only tightens)."""
        a, close = atr(ticker)
        new = risk.trailing_stop(side.lower(), current_stop, close, a)
        return {"close": close, "atr": round(a, 6), "new_stop": round(new, 6), "moved": new != current_stop}
