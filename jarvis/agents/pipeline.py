"""Deal pipeline & trader/IB sourcing analytics."""
from __future__ import annotations

from typing import Any

import pandas as pd

from jarvis.adapters.sources import default_source
from jarvis.core.config import settings
from jarvis.core.registry import Agent, register, tool

STAGES = ["lead", "qualified", "kyc", "funded", "active", "lost"]
# Probability a deal at this stage ends up funded - recalibrate from your own history.
STAGE_PROB = {"lead": 0.05, "qualified": 0.2, "kyc": 0.5, "funded": 0.9, "active": 1.0, "lost": 0.0}


def analyse_pipeline(df: pd.DataFrame, today: pd.Timestamp | None = None) -> dict[str, Any]:
    today = today or pd.Timestamp.now().normalize()
    d = df.copy()
    d["stage"] = d["stage"].str.lower()
    d["last_activity"] = pd.to_datetime(d["last_activity"], errors="coerce")
    d["idle_days"] = (today - d["last_activity"]).dt.days
    d["weighted"] = d["value"] * d["stage"].map(STAGE_PROB).fillna(0)
    open_ = d[~d["stage"].isin(["active", "lost"])]
    counts = d["stage"].value_counts().reindex(STAGES, fill_value=0)
    progressed = counts.drop("lost")
    reached = progressed[::-1].cumsum()[::-1]  # deals currently at or beyond each stage
    conv = {f"{a}->{b}": round(float(reached[b] / reached[a] * 100), 1) if reached[a] else None
            for a, b in zip(STAGES[:4], STAGES[1:5])}
    src = d.groupby("source").agg(deals=("deal_id", "count"),
                                  won=("stage", lambda s: s.isin(["funded", "active"]).sum()),
                                  value=("value", "sum"))
    src["win_rate_pct"] = (src["won"] / src["deals"] * 100).round(1)
    return {
        "open_deals": int(len(open_)),
        "open_value": round(float(open_["value"].sum()), 2),
        "weighted_forecast": round(float(open_["weighted"].sum()), 2),
        "by_stage": counts.to_dict(),
        "stage_conversion_pct": conv,
        "stale_deals": open_[open_["idle_days"] >= settings.stale_deal_days]
            .sort_values("value", ascending=False)[["deal_id", "name", "stage", "value", "owner", "idle_days"]]
            .head(15).to_dict("records"),
        "best_sources": src.sort_values("win_rate_pct", ascending=False).reset_index().to_dict("records"),
    }


@register
class PipelineAgent(Agent):
    name = "pipeline"
    description = "Deal pipeline health, forecast, stale deals and best lead sources."

    @tool()
    def analyse(self, owner: str = "") -> dict[str, Any]:
        """Analyse the deal pipeline (optionally for one owner): open value,
        probability-weighted forecast, stage conversion, stale deals needing a
        follow-up, and which lead sources convert best (use this to decide where
        to source new traders/IBs)."""
        df = default_source().load("deals")
        if owner:
            df = df[df["owner"].str.lower() == owner.lower()]
        return analyse_pipeline(df)
