"""Client retention & book health.

Transparent rule-based churn score (0-100). Deliberately not a black box:
account managers need to see *why* a client is flagged. Once you have ~6
months of labelled churn data, swap `churn_score` for a gradient-boosted
model trained on the same features - the tool interface stays the same.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from jarvis.adapters.sources import default_source
from jarvis.core.config import settings
from jarvis.core.registry import Agent, register, tool


def churn_score(df: pd.DataFrame, today: pd.Timestamp | None = None) -> pd.DataFrame:
    today = today or pd.Timestamp.now(tz=None).normalize()
    d = df.copy()
    for c in ("last_login", "last_trade"):
        d[c] = pd.to_datetime(d[c], errors="coerce")
    d["days_since_trade"] = (today - d["last_trade"]).dt.days.fillna(999)
    d["days_since_login"] = (today - d["last_login"]).dt.days.fillna(999)
    dep_drop = (d["deposits_prev_30d"] - d["deposits_30d"]).clip(lower=0) / d["deposits_prev_30d"].replace(0, pd.NA)
    act_drop = (d["trades_prev_30d"] - d["trades_30d"]).clip(lower=0) / d["trades_prev_30d"].replace(0, pd.NA)
    net_out = (d["withdrawals_30d"] - d["deposits_30d"]).clip(lower=0) / d["equity"].replace(0, pd.NA)

    d["score"] = (
        30 * (d["days_since_trade"] / settings.churn_inactive_days).clip(upper=1)
        + 15 * (d["days_since_login"] / settings.churn_inactive_days).clip(upper=1)
        + 20 * dep_drop.fillna(0).astype(float).clip(upper=1)
        + 15 * act_drop.fillna(0).astype(float).clip(upper=1)
        + 15 * net_out.fillna(0).astype(float).clip(upper=1)
        + 5 * (d["open_tickets"] > 0)
    ).round(1)

    def why(r: pd.Series) -> str:
        bits = []
        if r.days_since_trade >= settings.churn_inactive_days: bits.append(f"no trade {int(r.days_since_trade)}d")
        if r.deposits_prev_30d > 0 and r.deposits_30d < 0.5 * r.deposits_prev_30d: bits.append("deposits halved")
        if r.trades_prev_30d > 0 and r.trades_30d < 0.5 * r.trades_prev_30d: bits.append("activity halved")
        if r.withdrawals_30d > r.deposits_30d and r.withdrawals_30d > 0.2 * max(r.equity, 1): bits.append("net withdrawing")
        if r.open_tickets > 0: bits.append(f"{int(r.open_tickets)} open ticket(s)")
        return ", ".join(bits) or "-"

    d["reasons"] = d.apply(why, axis=1)
    d["tier"] = pd.cut(d["score"], [-1, 35, 60, 101], labels=["healthy", "watch", "at_risk"])
    return d.sort_values("score", ascending=False)


@register
class ClientAgent(Agent):
    name = "clients"
    description = "Client churn risk, book health and retention actions."

    @tool()
    def at_risk(self, min_score: float = 60, limit: int = 20) -> list[dict[str, Any]]:
        """Clients ranked by churn risk score (0-100) with plain-English reasons
        and their account manager. Use min_score 35 to include the 'watch' tier."""
        d = churn_score(default_source().load("clients"))
        d = d[d["score"] >= min_score].head(limit)
        cols = ["client_id", "name", "account_manager", "segment", "equity", "score", "tier", "reasons"]
        return d[cols].astype({"tier": str}).to_dict("records")

    @tool()
    def book_summary(self) -> dict[str, Any]:
        """Totals for the client book: equity, net flows, active ratio, risk tiers by manager."""
        d = churn_score(default_source().load("clients"))
        return {
            "clients": int(len(d)),
            "total_equity": round(float(d["equity"].sum()), 2),
            "net_flow_30d": round(float(d["deposits_30d"].sum() - d["withdrawals_30d"].sum()), 2),
            "active_30d_pct": round(float((d["trades_30d"] > 0).mean() * 100), 1),
            "tiers": d["tier"].astype(str).value_counts().to_dict(),
            "at_risk_by_manager": d[d["tier"] == "at_risk"].groupby("account_manager").size().to_dict(),
        }
