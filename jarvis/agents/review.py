"""Self-review: your trading days scored against your own rules.

Reads only DONNA's own trade journal (pt_trades, via jarvis.agents.edge.load).
Nothing here places, changes or closes a trade, and no Claude credit is needed.

1. discipline()     every trading day checked against four rules (the same numbers as the KPIs and the
                    PAD policy): at most 10 trades, no loss over 1% of the balance, no new trade within
                    5 minutes of a loss, and the daily-loss limit not hit. A day that keeps all four is a
                    "clean day". Shows the streak, the best streak, and what clean days made against the others.
(What the market did versus what you did is in jarvis/agents/vsmarket.py and the Market vs me tab.)
  !discipline [days]   the discipline record in Discord (also !streak)
"""
from __future__ import annotations

import os
from typing import Any

import pandas as pd

from jarvis.core.config import settings

TILT_MIN = 5
RULE_WORDS = {"trades": "at most {n} trades", "loss_cap": "no loss over {cap:g}% of the balance",
              "no_tilt": "no new trade within 5 min of a loss", "daily_loss": "daily-loss limit ({dl:g}%) not hit"}


def _limits() -> dict[str, float]:
    from jarvis.agents import kpi, pad

    return {"n": pad.MAX_TRADES_DAY, "cap": float(kpi.targets()["tr_max_loss"]["target"]),
            "dl": float(settings.max_daily_loss_pct)}


def _day_rows(d: pd.DataFrame, lim: dict[str, float]) -> list[dict[str, Any]]:
    gap = (d.start - d.t.shift()).dt.total_seconds() / 60
    d = d.assign(tilt=(d.net.shift() < 0) & (gap < TILT_MIN),
                 big=(d.net < 0) & d.bal_before.notna() & (-d.net > d.bal_before * lim["cap"] / 100))
    out = []
    for day, g in d.groupby("day", sort=True):
        start_bal = g.bal_before.dropna().iloc[0] if g.bal_before.notna().any() else None
        net = float(g.net.sum())
        kept = {"trades": len(g) <= lim["n"], "loss_cap": not bool(g.big.any()), "no_tilt": not bool(g.tilt.any()),
                "daily_loss": not (start_bal and net < 0 and -net >= start_bal * lim["dl"] / 100)}
        out.append({"day": str(day), "trades": int(len(g)), "net": round(net, 2), "kept": kept,
                    "clean": all(kept.values()), "tilt": int(g.tilt.sum()), "big": int(g.big.sum())})
    return out


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clean = [r for r in rows if r["clean"]]
    best = run = 0
    for r in rows:
        run = run + 1 if r["clean"] else 0
        best = max(best, run)
    now = 0
    for r in reversed(rows):
        if not r["clean"]:
            break
        now += 1
    broken = {k: sum(not r["kept"][k] for r in rows) for k in RULE_WORDS}
    return {"days": len(rows), "clean": len(clean), "clean_pct": round(len(clean) / len(rows) * 100) if rows else None,
            "streak": now, "best": best, "clean_net": round(sum(r["net"] for r in clean), 2),
            "other_net": round(sum(r["net"] for r in rows if not r["clean"]), 2),
            "clean_avg": round(sum(r["net"] for r in clean) / len(clean), 2) if clean else None,
            "other_avg": round(sum(r["net"] for r in rows if not r["clean"]) / (len(rows) - len(clean)), 2)
            if len(rows) > len(clean) else None, "broken": broken}


def discipline(days: int = 30) -> dict[str, Any]:
    from jarvis.agents import edge

    d = edge.load(days * 2 + 1)
    if d.empty:
        return {"error": "NO DATA - no closed trades yet (cTrader feed or !journal upload)"}
    lim = _limits()
    cut = d.t.max() - pd.Timedelta(days=days)
    rows = _day_rows(d, lim)
    cur = [r for r in rows if pd.Timestamp(r["day"]) > cut.normalize()]
    before = [r for r in rows if pd.Timestamp(r["day"]) <= cut.normalize()]
    words = {k: v.format(**lim) for k, v in RULE_WORDS.items()}
    return {"days": cur, "summary": _summary(cur), "before": _summary(before) if before else None,
            "rules": words, "window": days}


def discipline_lines(days: int = 30) -> list[str]:
    x = discipline(days)
    if "error" in x:
        return ["DISCIPLINE", " " + x["error"]]
    s, w = x["summary"], x["rules"]
    L = [f"DISCIPLINE - your last {s['days']} trading days against your own rules",
         " A clean day keeps all four: " + "; ".join(w.values()) + ".",
         f" Clean days: {s['clean']} of {s['days']} ({s['clean_pct']}%). Streak now: {s['streak']}, best: {s['best']}."]
    if s["clean_avg"] is not None and s["other_avg"] is not None:
        L.append(f" Clean days made ${s['clean_avg']:,.2f} on average; the other days ${s['other_avg']:,.2f}.")
    L.append(" Rules broken most: " + "; ".join(f"'{w[k]}' on {n} days" for k, n in
                                                sorted(s["broken"].items(), key=lambda kv: -kv[1]) if n) or " none")
    if x["before"]:
        b = x["before"]
        L.append(f" The {b['days']} trading days before that: {b['clean']} clean ({b['clean_pct']}%).")
    L.append(" Last 10 days: " + " ".join(("OK" if r["clean"] else "x") for r in x["days"][-10:]))
    return L


def command(cmd: str, rest: str) -> str | None:
    if cmd not in ("!discipline", "!streak"):
        return None
    days = int(rest) if rest.strip().isdigit() else int(os.getenv("DONNA_REVIEW_DAYS", "30"))
    return "\n".join(discipline_lines(days) + ["", "Market vs you, day by day: the Daily review tab on the charts page."])

