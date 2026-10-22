"""Bot Lab: turns cBot RLOG/RLOG2 lines into decisions.

Paste or export the cTrader log (backtest or live) to a .txt file. The parser
picks out RLOG2 lines when present (richer), else RLOG v1. Everything is in R,
so results compare across account sizes.

MFE replay caveat: logs give the max favourable/adverse excursion, not the order
in which they happened. The TP sweep assumes a trade that reached k*R would have
banked k*R - an upper bound when the stop could have been hit first. Treat it as
a shortlist for a proper re-backtest, never as a result.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from jarvis.adapters.sources import default_source
from jarvis.core.registry import Agent, register, tool

V1 = ["id", "kind", "side", "stop", "entry", "exit_ts", "hours", "R", "mfeR", "maeR", "exit"]
V2 = ["id", "side", "entry", "exit_ts", "hours", "R", "mfeR", "maeR", "exit", "hour",
      "stopPips", "spreadPips", "slipPips", "costPips", "net"]


def parse_rlog(text: str) -> pd.DataFrame:
    v1, v2 = [], []
    for line in text.splitlines():
        i = line.find("RLOG")
        if i < 0:
            continue
        parts = line[i:].strip().split(",")
        if parts[0] == "RLOG2" and len(parts) == len(V2) + 1 and parts[1] != "id":
            v2.append(parts[1:])
        elif parts[0] == "RLOG" and len(parts) == len(V1) + 1 and parts[1] != "id":
            v1.append(parts[1:])
    df = pd.DataFrame(v2, columns=V2) if v2 else pd.DataFrame(v1, columns=V1)
    if df.empty:
        return df
    for c in ("hours", "R", "mfeR", "maeR", "hour", "stopPips", "spreadPips", "slipPips", "costPips", "net"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["entry"] = pd.to_datetime(df["entry"], errors="coerce")
    if "hour" not in df:
        df["hour"] = df["entry"].dt.hour
    return df.drop_duplicates("id").sort_values("entry").reset_index(drop=True)


def _bootstrap_ci(r: np.ndarray, n: int = 2000, seed: int = 1) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    means = rng.choice(r, (n, len(r)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)), float((means > 0).mean())


def analyse(df: pd.DataFrame, loss_threshold_r: float = 0.25) -> dict[str, Any]:
    if df.empty:
        return {"error": "no RLOG lines found"}
    r = df["R"].to_numpy(dtype=float)
    eq = np.cumsum(r)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    lo, hi, p_pos = _bootstrap_ci(r) if len(r) >= 10 else (np.nan, np.nan, np.nan)
    wins, losses = r[r > loss_threshold_r], r[r < -loss_threshold_r]
    scratches = r[(r >= -loss_threshold_r) & (r <= loss_threshold_r)]
    # Breakeven cost: trades that reached >= 1R but finished as scratches or losses.
    be_victims = df[(df["mfeR"] >= 1.0) & (df["R"] <= loss_threshold_r)]
    grp = lambda col: (df.groupby(col)["R"].agg(trades="size", expectancy="mean", total="sum")  # noqa: E731
                       .round(3).reset_index().to_dict("records"))
    tp_grid = []
    for k in np.arange(1.0, 3.01, 0.25):
        replay = np.where(df["mfeR"] >= k, k, np.minimum(df["R"], k))
        tp_grid.append({"tp_R": round(float(k), 2), "expectancy_upper_bound": round(float(replay.mean()), 3)})
    out = {
        "trades": int(len(r)), "from": str(df["entry"].min()), "to": str(df["entry"].max()),
        "expectancy_R": round(float(r.mean()), 3),
        "expectancy_95ci": [round(lo, 3), round(hi, 3)],
        "prob_edge_positive": round(p_pos, 3),
        "total_R": round(float(r.sum()), 2), "max_drawdown_R": round(dd, 2),
        "win_rate_pct": round(len(wins) / len(r) * 100, 1),
        "scratch_rate_pct": round(len(scratches) / len(r) * 100, 1),
        "avg_win_R": round(float(wins.mean()), 3) if len(wins) else None,
        "avg_loss_R": round(float(losses.mean()), 3) if len(losses) else None,
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if len(losses) and losses.sum() else None,
        "by_hour": grp("hour"), "by_exit": grp("exit"), "by_side": grp("side"),
        "breakeven_scratched_after_1R": int(len(be_victims)),
        "mfe_quantiles_R": df["mfeR"].quantile([.25, .5, .75, .9]).round(2).to_dict(),
        "tp_sweep_mfe_replay": tp_grid,
    }
    if "slipPips" in df and df["slipPips"].notna().any():
        out["avg_entry_slip_pips"] = round(float(df["slipPips"].mean()), 2)
        out["avg_spread_pips"] = round(float(df["spreadPips"].mean()), 2)
        out["cost_share_of_stop_pct"] = round(float(((df["spreadPips"] + df["costPips"]) / df["stopPips"]).mean() * 100), 1)
    verdict = []
    if len(r) < 100:
        verdict.append(f"only {len(r)} trades - not enough to trust any parameter change (aim for 200+)")
    if not np.isnan(hi) and hi < 0:
        verdict.append("expectancy is negative with 95% confidence - do not trade this configuration")
    elif not np.isnan(lo) and lo <= 0:
        verdict.append("95% CI of expectancy includes zero - edge not yet proven")
    if len(be_victims) > 0.15 * len(r):
        verdict.append("breakeven at 1R scratches many trades that ran - test BE at 1.2-1.5R or a cost lock")
    out["verdict"] = verdict or ["edge statistically positive on this sample - confirm out-of-sample"]
    return out


@register
class BotLabAgent(Agent):
    name = "botlab"
    description = "Analyse cBot RLOG output: expectancy with confidence, hour/exit breakdown, TP/BE insights."

    @tool()
    def analyse_log(self, path: str = "") -> dict[str, Any]:
        """Analyse a cTrader cBot log file containing RLOG/RLOG2 lines (backtest or live).
        Default path: the demo log. Returns expectancy in R with bootstrap 95% CI,
        win/scratch rates, drawdown, by-hour/by-exit/by-side breakdown, breakeven cost,
        MFE quantiles, an MFE-replay TP sweep (upper bound) and a plain verdict."""
        p = Path(path) if path else Path(getattr(default_source(), "root", ".")) / "silver_scalper_log.txt"
        if not p.exists():
            return {"error": f"log not found: {p}"}
        return analyse(parse_rlog(p.read_text(errors="ignore")))

    @tool()
    def events_param(self, days_ahead: int = 60, impact: str = "high") -> dict[str, Any]:
        """Build the cBot 'Events UTC' parameter string from the economic calendar
        (high-impact US events by default) for the next N days."""
        ev = default_source().load("events")
        ev["ts"] = pd.to_datetime(ev["ts"])
        now = pd.Timestamp.now()
        ev = ev[(ev["impact"].str.lower() == impact.lower()) & (ev["ts"] >= now.normalize())
                & (ev["ts"] <= now + pd.Timedelta(days=days_ahead))]
        return {"events": len(ev), "param": ";".join(ev.sort_values("ts")["ts"].dt.strftime("%Y-%m-%d %H:%M"))}
