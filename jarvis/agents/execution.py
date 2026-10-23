"""Execution & bot-failure monitor.

Covers "bot learning and failure to execute": reads the execution log
(from the bridge / MT5 / cTrader journals), measures fill quality and groups
failures by cause so recurring problems become tickets, not surprises.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from jarvis.adapters.sources import default_source
from jarvis.core.config import settings
from jarvis.core.registry import Agent, register, tool

# Map raw platform error codes/strings to an operational cause + fix.
ERROR_PLAYBOOK = {
    "requote": ("price moved during submission", "widen slippage tolerance or use market-range orders"),
    "off_quotes": ("no liquidity / LP rejected", "check LP bridge, retry with backoff"),
    "no_money": ("insufficient margin", "position sizing used stale equity - refresh before sizing"),
    "market_closed": ("session closed", "add trading-hours filter to the bot"),
    "invalid_stops": ("SL/TP inside stop level", "respect broker min stop distance"),
    "timeout": ("bridge/server latency", "check VPS/network; add heartbeat + reconnect"),
    "trade_disabled": ("symbol/account trading disabled", "check account permissions"),
}


def analyse_executions(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"orders": 0}
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    filled = df[df["status"].str.lower() == "filled"].copy()
    sign = filled["side"].str.lower().map({"buy": 1, "sell": -1}).fillna(0)
    # positive = adverse slippage (paid more on buys, received less on sells)
    filled["slip_pips"] = (filled["fill_price"] - filled["requested_price"]) * sign / filled["pip_size"]
    failed = df[df["status"].str.lower() != "filled"]
    causes = (failed["error_code"].fillna("unknown").str.lower().value_counts().to_dict())
    return {
        "orders": int(len(df)),
        "fill_rate_pct": round(len(filled) / len(df) * 100, 2),
        "avg_slippage_pips": round(float(filled["slip_pips"].mean()), 3) if len(filled) else None,
        "p95_slippage_pips": round(float(filled["slip_pips"].quantile(0.95)), 3) if len(filled) else None,
        "slippage_breaches": int((filled["slip_pips"] > settings.max_slippage_pips).sum()),
        "p95_latency_ms": float(df["latency_ms"].quantile(0.95)) if "latency_ms" in df else None,
        "failures_by_cause": {
            k: {"count": v,
                "meaning": ERROR_PLAYBOOK.get(k, ("unclassified", "inspect raw journal"))[0],
                "fix": ERROR_PLAYBOOK.get(k, ("", "inspect raw journal"))[1]}
            for k, v in causes.items()
        },
        "worst_symbols": (filled.groupby("symbol")["slip_pips"].mean()
                          .sort_values(ascending=False).head(5).round(3).to_dict()),
    }


@register
class ExecutionAgent(Agent):
    name = "execution"
    description = "Fill quality, slippage and failed-order root causes for bots and accounts."

    @tool()
    def report(self, account: str = "", platform: str = "") -> dict[str, Any]:
        """Execution quality report. Optionally filter by account id and/or platform
        (e.g. 'mt5', 'ctrader'). Returns fill rate, slippage stats, latency and
        failures grouped by cause with suggested fixes."""
        df = default_source().load("executions")
        if account:
            df = df[df["account"].astype(str) == account]
        if platform:
            df = df[df["platform"].str.lower() == platform.lower()]
        return analyse_executions(df)
