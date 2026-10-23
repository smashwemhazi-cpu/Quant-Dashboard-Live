"""Marketing & lead processing.

Leads   Syntellicore leads / customers export dropped in the inbox (recognised by its columns: an id, a status
        and a source or registration date). From each snapshot: new leads, conversion by source, unassigned
        and untouched leads (speed-to-lead), and one follow-up task per batch so nothing sits unworked.
Funnel  Trend of the Syntellicore KPI export (leads, FTDs, conversion, net deposits) across days.
Content Daily social-post drafts written from the day's real market moves, each one checked by the compliance
        promo reviewer before you see it. Drafts only - nothing is ever posted automatically.
Reports carry customer numbers only.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from jarvis.core import audit
from jarvis.core.config import settings
from jarvis.core.registry import Agent, register, tool

STALE_DAYS = int(os.getenv("JARVIS_LEAD_STALE_DAYS", "3"))
UNTOUCHED = re.compile(r"^\s*$|new|fresh|not contacted|no answer|pending", re.I)
FIRM = os.getenv("DONNA_FIRM_DISCLOSURE", "Vertex 1 Brokers")


def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS bo_leads (asof TEXT, lead TEXT, created TEXT, source TEXT, status TEXT,
            owner TEXT, country TEXT, ftd INTEGER, PRIMARY KEY (asof, lead));
        CREATE TABLE IF NOT EXISTS content_drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT,
            channel TEXT, text TEXT, verdict TEXT, review TEXT);
    """)
    return c


# ------------------------------------------------------------------ leads
def _pick(cols: dict[str, str], *names: str, exclude: tuple[str, ...] = ()) -> str | None:
    for n in names:
        for low, orig in cols.items():
            if n in low and not any(x in low for x in exclude):
                return orig
    return None


def lead_columns(d: pd.DataFrame) -> dict[str, str | None] | None:
    cols = {c.lower(): c for c in d.columns}
    if any(x in cols for x in ("document_status", "trader account", "equity (usd)")):
        return None  # other exports handled by the back office
    m = {
        "lead": _pick(cols, "customer_no", "lead_id", "lead id", "lead no", "client id", "customer id"),
        "status": _pick(cols, "lead_status", "sales_status", "status", exclude=("document", "kyc")),
        "source": _pick(cols, "source", "campaign", "utm", "affiliate", "referr", "ib"),
        "created": _pick(cols, "registration", "created", "date"),
        "owner": _pick(cols, "assigned_to", "owner", "agent", "sales", "retention"),
        "country": _pick(cols, "country"),
        "ftd": _pick(cols, "ftd", "first deposit", "first_deposit"),
    }
    return m if m["lead"] and m["status"] and (m["source"] or m["created"]) else None


def store_leads(d: pd.DataFrame, m: dict[str, str | None], asof: str) -> int:
    get = lambda k: d[m[k]].astype(str).str.strip() if m[k] else pd.Series("", index=d.index)  # noqa: E731
    ftd = get("ftd").str.replace(r"[^0-9.\-]", "", regex=True)
    rows = pd.DataFrame({
        "asof": asof, "lead": get("lead"),
        "created": pd.to_datetime(get("created"), errors="coerce").dt.strftime("%Y-%m-%d %H:%M"),
        "source": get("source").replace({"nan": "", "0": ""}), "status": get("status").replace({"nan": ""}),
        "owner": get("owner").replace({"nan": "", "0": ""}), "country": get("country").replace({"nan": ""}),
        "ftd": ((pd.to_numeric(ftd, errors="coerce").fillna(0) > 0) |
                get("ftd").str.lower().isin(["yes", "true", "y"])).astype(int),
    }).drop_duplicates("lead")
    with _db() as c:
        c.execute("DELETE FROM bo_leads WHERE asof = ?", (asof,))
        c.executemany("INSERT INTO bo_leads VALUES (?,?,?,?,?,?,?,?)", rows.fillna("").itertuples(index=False, name=None))
    return len(rows)


def leads_summary(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    with _db() as c:
        row = c.execute("SELECT MAX(asof) FROM bo_leads").fetchone()
        if not row or not row[0]:
            return {"error": "NO DATA - no Syntellicore leads export ingested"}
        d = pd.read_sql_query("SELECT * FROM bo_leads WHERE asof = ?", c, params=(row[0],))
    d["t"] = pd.to_datetime(d.created, errors="coerce")
    untouched = d.status.fillna("").str.match(UNTOUCHED) & (d.ftd == 0)
    new24 = d[d.t >= now - timedelta(hours=24)]
    stale = d[untouched & (d.t < now - timedelta(days=STALE_DAYS))]
    by_src = (d.assign(source=d.source.replace("", "(none)")).groupby("source")
              .agg(leads=("lead", "size"), ftd=("ftd", "sum")).assign(conv=lambda x: x.ftd / x.leads * 100)
              .sort_values("leads", ascending=False).reset_index())
    return {
        "asof": row[0], "leads": len(d), "ftd": int(d.ftd.sum()),
        "new_24h": len(new24), "new_7d": int((d.t >= now - timedelta(days=7)).sum()),
        "unassigned": int((d.owner == "").sum()), "unassigned_new": int(((d.owner == "") & d.index.isin(new24.index)).sum()),
        "untouched": int(untouched.sum()), "stale": len(stale), "stale_ids": stale.sort_values("t").lead.head(15).tolist(),
        "by_source": by_src.head(10).to_dict("records"),
        "by_status": d.status.replace("", "(blank)").value_counts().head(8).to_dict(),
    }


def lead_tasks(s: dict[str, Any]) -> list[int]:
    """One task per batch - assign new leads, chase stale ones - deduplicated per day."""
    from jarvis.agents import assistant

    day, ids = s["asof"], []
    if s.get("unassigned_new"):
        ids.append(assistant.add_task(f"Assign {s['unassigned_new']} new unassigned leads (speed-to-lead)", day, "high",
                                      source="leads", source_ref=f"leads-assign-{day}"))
    if s.get("stale"):
        ids.append(assistant.add_task(f"Chase {s['stale']} leads untouched for {STALE_DAYS}+ days: "
                                      + ", ".join(s["stale_ids"][:8]), day, "medium",
                                      source="leads", source_ref=f"leads-stale-{day}"))
    return [i for i in ids if i]


# ------------------------------------------------------------------ funnel
def funnel_trend(days: int = 14) -> list[dict[str, Any]]:
    from jarvis.agents import backoffice as bo

    with bo._db() as c:
        rows = c.execute("SELECT asof, metric, value FROM bo_kpi WHERE asof >= ? ORDER BY asof",
                         ((datetime.now() - timedelta(days=days)).date().isoformat(),)).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for asof, metric, value in rows:
        out.setdefault(asof, {"asof": asof})[metric] = value
    return list(out.values())


# ------------------------------------------------------------------ content
CONTENT_PROMPT = """You write short social posts for {firm}, a retail FX/CFD broker, from today's real market data.
Write {n} posts: one LinkedIn post (80-130 words, professional), one X post (under 260 characters), one Telegram
channel post (60-100 words). Educational market commentary only: what moved, the likely driver from the headlines,
what traders are watching next. No trade signals, no predictions presented as certain, no profit claims, no urgency.
End every post with: "CFDs are complex instruments and come with a high risk of losing money. {firm}."
Return only the JSON object."""
CONTENT_SCHEMA = {
    "type": "object",
    "properties": {"posts": {"type": "array", "items": {"type": "object", "properties": {
        "channel": {"type": "string", "enum": ["linkedin", "x", "telegram"]}, "text": {"type": "string"}},
        "required": ["channel", "text"], "additionalProperties": False}}},
    "required": ["posts"], "additionalProperties": False,
}


def market_brief() -> str:
    from jarvis.agents.briefing import market_table, news

    rows = [r for r in market_table() if not r.get("error")]
    lines = [f"{r['inst'].label}: {r['close']:.{r['inst'].decimals}f} ({r['chg_pct']:+.2f}% day, {r['chg_5d_pct']:+.2f}% 5d)"
             for r in rows]
    heads = [x["title"] for items in news(hours=24, per_cat=4).values() for x in items]
    return "Markets:\n" + "\n".join(lines) + "\n\nHeadlines:\n" + "\n".join(f"- {h}" for h in heads)


def draft_posts(client: Any = None, brief: str | None = None) -> list[dict[str, Any]]:
    import anthropic

    from jarvis.agents.compliance import review_promotion

    client = client or anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=settings.model, max_tokens=8000, system=CONTENT_PROMPT.format(firm=FIRM, n=3),
        messages=[{"role": "user", "content": brief or market_brief()}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": CONTENT_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        return []
    posts = json.loads(next(b.text for b in resp.content if b.type == "text"))["posts"]
    out = []
    with _db() as c:
        for p in posts:
            r = review_promotion(p["text"], client)   # every draft passes the compliance check first
            text = r["compliant_version"] or p["text"] if r["verdict"] != "approve" else p["text"]
            cid = c.execute("INSERT INTO content_drafts (created, channel, text, verdict, review) VALUES (?,?,?,?,?)",
                            (datetime.now(timezone.utc).isoformat(timespec="seconds"), p["channel"], text,
                             r["verdict"], json.dumps(r["issues"]))).lastrowid
            out.append({"id": cid, "channel": p["channel"], "text": text, "verdict": r["verdict"],
                        "issues": len(r["issues"])})
    audit.log("growth", "content_drafts", {"drafts": len(out)})
    return out


def drafts_text(drafts: list[dict[str, Any]]) -> str:
    if not drafts:
        return "No drafts produced."
    L = ["D.O.N.N.A | MARKETING DRAFTS (not posted - copy, edit and publish yourself)", "-" * 34]
    for d in drafts:
        tag = {"approve": "compliance OK", "approve_with_changes": f"rewritten by compliance ({d['issues']} fixes)",
               "reject": "compliance REJECTED - do not use"}.get(d["verdict"], d["verdict"])
        L += ["", f"[{d['channel'].upper()}] draft #{d['id']} - {tag}", d["text"]]
    return "\n".join(L)


# ------------------------------------------------------------------ report
def report_lines() -> list[str]:
    s = leads_summary()
    if "error" in s:
        return [f"LEADS: {s['error']} (export the leads/customers list from Syntellicore and upload it)"]
    L = [f"LEADS (Syntellicore export {s['asof']}): {s['leads']} leads, {s['ftd']} funded | "
         f"new 24h {s['new_24h']}, 7d {s['new_7d']}"]
    L.append(f" Unassigned {s['unassigned']} (of them new today {s['unassigned_new']}) | untouched {s['untouched']} | "
             f"untouched > {STALE_DAYS}d: {s['stale']}")
    L.append(" Sources: " + ", ".join(f"{x['source']} {x['leads']} ({x['conv']:.0f}% FTD)" for x in s["by_source"][:6]))
    if s["stale"]:
        L.append(f" !! Chase: {', '.join(s['stale_ids'][:10])}")
    return L


@register
class GrowthAgent(Agent):
    name = "growth"
    description = "Lead processing (speed-to-lead, sources, conversion), funnel trend and compliant marketing drafts."

    @tool()
    def leads(self) -> dict[str, Any]:
        """Latest leads snapshot: new, unassigned, untouched/stale, conversion by source, statuses."""
        return leads_summary()

    @tool()
    def funnel(self, days: int = 14) -> list[dict[str, Any]]:
        """Daily Syntellicore KPI history (leads, FTDs, conversion, deposits) for trend questions."""
        return funnel_trend(days)

    @tool()
    def marketing_drafts(self) -> dict[str, Any]:
        """Write today's social-post drafts from real market moves, each checked by compliance. Never posts."""
        return {"text": drafts_text(draft_posts())}
