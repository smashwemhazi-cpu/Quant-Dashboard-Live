"""Marketing attribution: where clients come from, what each source costs, and what it brings in.

  channel   the lead's CRM source, grouped: a campaign tag from !campaign (ke-ib-2026w44), an IB / partner,
            or the raw source (facebook, google, website ...)
  funnel    leads -> funded (FTD) -> conversion %
  value     deposits, net deposits and estimated revenue (commission + swaps + client losses), when the leads
            export carries the trading account so a lead can be followed to its account activity
  cost      what you spent on that channel (!spend facebook 1200), giving cost per funded client and ROI
  IBs       for accounts in the IB report: funded clients, deposits, revenue, rebates paid and the net

!attribution [days]      by channel (leads created in the last N days, default 90)
!attribution country     by country
!spend <source or tag> <amount> [note]    record marketing spend;  !spend  lists it
Revenue is an estimate (the B-book view) - for comparing channels, not for the accounts.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from jarvis.core import audit

TAG = re.compile(r"\b([a-z]{2}-(?:ib|clients|retention)-\d{4}w\d{2}(?:-\d+)?)\b")
PARTNER = re.compile(r"\b(ib|partner|affiliate|introduc\w*|referr\w*)\b", re.I)


def _db() -> sqlite3.Connection:
    from jarvis.agents import growth

    c = growth._db()  # noqa: SLF001 - leads and spend live together
    c.execute("CREATE TABLE IF NOT EXISTS marketing_spend (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, "
              "amount REAL, note TEXT, added TEXT)")
    return c


def channel(source: str) -> str:
    s = (source or "").strip()
    tag = TAG.search(s.lower())
    if tag:
        return f"campaign {tag.group(1)}"
    if PARTNER.search(s):
        return f"partner {s[:40]}"
    return s.lower()[:40] or "(no source)"


def add_spend(source: str, amount: float, note: str = "") -> int:
    with _db() as c:
        sid = c.execute("INSERT INTO marketing_spend (source, amount, note, added) VALUES (?,?,?,?)",
                        (source.strip().lower(), amount, note[:200],
                         datetime.now(timezone.utc).isoformat(timespec="seconds"))).lastrowid
    audit.log("officer", "marketing_spend", {"source": source, "amount": amount})
    return sid


def _frame(days: int, now: datetime | None = None) -> tuple[pd.DataFrame, str | None, bool]:
    with _db() as c:
        last = c.execute("SELECT MAX(asof) FROM bo_leads").fetchone()[0]
        if not last:
            return pd.DataFrame(), None, False
        leads = pd.read_sql_query("SELECT lead, created, source, country, ftd FROM bo_leads WHERE asof = ?", c, params=(last,))
        links = pd.read_sql_query("SELECT * FROM lead_accounts", c)
    now = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    t = pd.to_datetime(leads.created, errors="coerce")
    leads = leads[t.isna() | (t >= now - timedelta(days=days))].copy()
    leads["channel"] = leads.source.map(channel)
    leads["country"] = leads.country.replace("", "(unknown)").fillna("(unknown)")
    from jarvis.agents import backoffice as bo

    with bo._db() as c:  # noqa: SLF001 - read-only
        act_asof = c.execute("SELECT MAX(asof) FROM bo_activity").fetchone()[0]
        act = pd.read_sql_query("SELECT * FROM bo_activity WHERE asof = ?", c, params=(act_asof,)) if act_asof \
            else pd.DataFrame()
    valued = not links.empty and not act.empty
    if valued:
        a = act[["account", "deposit", "withdrawal", "commission", "swaps", "net_pnl"]].copy()
        a["revenue"] = a.commission.abs() + a.swaps.clip(upper=0).abs() + (-a.net_pnl).clip(lower=0)
        per_lead = links.merge(a, on="account").groupby("lead")[["deposit", "withdrawal", "revenue"]].sum().reset_index()
        leads = leads.merge(per_lead, on="lead", how="left")
    for col in ("deposit", "withdrawal", "revenue"):
        if col not in leads:
            leads[col] = 0.0
    leads[["deposit", "withdrawal", "revenue"]] = leads[["deposit", "withdrawal", "revenue"]].fillna(0.0)
    return leads, last, valued


def _spend() -> dict[str, float]:
    with _db() as c:
        return {r[0]: r[1] for r in c.execute("SELECT source, SUM(amount) FROM marketing_spend GROUP BY source")}


def report(days: int = 90, by: str = "channel", now: datetime | None = None) -> dict[str, Any]:
    leads, asof, valued = _frame(days, now)
    if asof is None:
        return {"error": "NO DATA - no CRM leads export received yet"}
    spend = _spend()
    rows = []
    for key, g in leads.groupby(by):
        cost = 0.0
        if by == "channel":
            name = key.split(" ", 1)[1] if key.startswith(("campaign ", "partner ")) else key
            cost = spend.get(name.lower(), 0.0)
        ftd = int(g.ftd.sum())
        rev = float(g.revenue.sum())
        rows.append({by: key, "leads": len(g), "ftd": ftd, "conv_pct": round(100 * ftd / len(g), 1) if len(g) else 0.0,
                     "deposits": float(g.deposit.sum()), "net_deposits": float(g.deposit.sum() - g.withdrawal.sum()),
                     "revenue": rev, "spend": cost, "cpa": round(cost / ftd, 2) if cost and ftd else None,
                     "roi_pct": round(100 * (rev - cost) / cost, 1) if cost and valued else None})
    rows.sort(key=lambda r: (-r["ftd"], -r["leads"]))
    return {"asof": asof, "days": days, "by": by, "valued": valued, "rows": rows,
            "leads": len(leads), "ftd": int(leads.ftd.sum()), "spend": sum(r["spend"] for r in rows)}


def ib_report() -> list[dict[str, Any]]:
    from jarvis.agents import ib

    s = ib.summary()
    if "error" in s:
        return []
    return [{"ib": x["ib"], "funded": x["funded"], "deposits": x["deposits"], "revenue": x["revenue"],
             "paid": x["paid"], "net": x["revenue"] - x["paid"]} for x in s["ibs"]]


def text(days: int = 90, by: str = "channel") -> str:
    r = report(days, by)
    if "error" in r:
        return r["error"] + ". Schedule the Syntellicore leads export to your email."
    L = [f"MARKETING ATTRIBUTION - leads created in the last {r['days']} days (CRM export {r['asof']}): "
         f"{r['leads']} leads, {r['ftd']} funded" + (f", spend {r['spend']:,.2f}" if r["spend"] else "")]
    if not r["valued"]:
        L.append("Deposits and revenue need the trading account in the leads export (and the account-activity report).")
    L.append(f" {'source' if by == 'channel' else by:<34} leads  funded  conv%   net dep.    revenue   cost/funded")
    for x in r["rows"][:15]:
        cpa = f"{x['cpa']:,.0f}" if x["cpa"] is not None else "-"
        L.append(f" {str(x[by])[:34]:<34} {x['leads']:>5} {x['ftd']:>7} {x['conv_pct']:>6.1f} {x['net_deposits']:>10,.0f} "
                 f"{x['revenue']:>10,.0f} {cpa:>13}" + (f"  ROI {x['roi_pct']:+.0f}%" if x["roi_pct"] is not None else ""))
    ibs = ib_report()
    if ibs and by == "channel":
        L += ["", " IBs (from the IB report)        funded   deposits    revenue    rebates        net"]
        L += [f" {x['ib'][:30]:<30} {x['funded']:>7} {x['deposits']:>10,.0f} {x['revenue']:>10,.0f} {x['paid']:>10,.0f} "
              f"{x['net']:>10,.0f}" for x in ibs[:10]]
    L += ["", "!attribution country   !attribution 30   !spend <source or campaign tag> <amount>"]
    return "\n".join(L)


def command(cmd: str, rest: str) -> str | None:
    if cmd == "!spend":
        parts = rest.split()
        if not parts:
            with _db() as c:
                rows = c.execute("SELECT source, SUM(amount), COUNT(*) FROM marketing_spend GROUP BY source "
                                 "ORDER BY 2 DESC").fetchall()
            return "\n".join(f" {r[0]:<30} {r[1]:>10,.2f} ({r[2]} entr{'y' if r[2] == 1 else 'ies'})" for r in rows) or \
                "No spend recorded. !spend facebook 1200 [note]  or  !spend ke-clients-2026w44 250"
        try:
            amount = float(parts[1].replace(",", ""))
        except (IndexError, ValueError):
            return "Usage: !spend <source or campaign tag> <amount> [note]"
        add_spend(parts[0], amount, " ".join(parts[2:]))
        return f"Recorded {amount:,.2f} spend on {parts[0].lower()}. It shows as cost per funded client in !attribution."
    if cmd != "!attribution":
        return None
    parts = rest.split()
    days = next((int(p) for p in parts if p.isdigit()), 90)
    by = "country" if "country" in parts else "channel"
    return text(days, by)
