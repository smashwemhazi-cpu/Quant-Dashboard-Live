"""D.O.N.N.A's data freshness view: every source she reads, how fresh it is, and how to put it on autopilot.

!feeds   (also "how fresh is my data?" in the console)

Reports on autopilot: ask the back office / CRM to schedule each export by email, daily, to the mailbox the
Google bridge reads, from one fixed sender address. DONNA saves the attachment (fetch_mail_reports, every few
hours) and every number built on it stays current. Senders allowed: JARVIS_REPORT_SENDERS (vertex1-connect email).
Plain rules, no Claude needed.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timezone
from typing import Any

from jarvis.core.config import settings

STALE_DAYS = int(os.getenv("JARVIS_DATA_STALE_DAYS", "3"))

# kind, plain name, where it comes from, what goes stale without it
EXPORTS = [
    ("eod", "End-of-day accounts (equity, balance, margin)", "cTrader's daily 'Accounts EOD report' email (trade@alerts.ctrader.com) - read by itself",
     "client risk, margin alerts, KRIs"),
    ("docs", "Customer documents (KYC)", "Syntellicore customer documents export", "KYC KRIs, expiring documents"),
    ("activity", "Trading activity (volume, deals)", "cTrader back office activity report", "turnover alerts, KPIs"),
    ("kpi", "CRM KPIs", "Syntellicore KPI dashboard export", "growth KPIs"),
    ("leads", "CRM leads", "Syntellicore leads export", "lead and conversion KPIs"),
    ("streams", "Price streams and spreads", "cTrader back office symbols / streams", "spread-widening alerts"),
    ("ib", "IB commissions", "back office IB report", "IB payout checks"),
]


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(os.getenv("JARVIS_STATE_DB", settings.state_db))
    c.row_factory = sqlite3.Row
    return c


def _one(c: sqlite3.Connection, sql: str, *args: Any) -> Any:
    try:
        row = c.execute(sql, args).fetchone()
    except sqlite3.OperationalError:       # that part of DONNA has never run yet: no table
        return None
    return row[0] if row else None


def _days(iso: str | None, today: date) -> int | None:
    if not iso:
        return None
    try:
        return (today - date.fromisoformat(str(iso)[:10])).days
    except ValueError:
        return None


def status(today: date | None = None) -> list[dict[str, Any]]:
    """One row per source: name, last (YYYY-MM-DD), age_days, state (fresh / old / never / live), source, how."""
    today = today or datetime.now(timezone.utc).date()
    out: list[dict[str, Any]] = []
    with _db() as c:
        for kind, name, src, feeds_what in EXPORTS:
            last = (_one(c, "SELECT MAX(asof) FROM bo_leads") if kind == "leads"
                    else _one(c, "SELECT value FROM bo_meta WHERE key = ?", f"last_{kind}"))
            by_mail = _one(c, "SELECT MAX(received) FROM bo_mail WHERE kind = ?", kind)
            age = _days(last, today)
            state = "never" if age is None else "fresh" if age <= STALE_DAYS else "old"
            out.append({"kind": kind, "name": name, "source": src, "feeds": feeds_what, "last": str(last or "")[:10],
                        "age_days": age, "state": state, "by_email": bool(by_mail)})
        live = [
            ("Inbox (email sorting)", _one(c, "SELECT MAX(processed_at) FROM email_seen"), "Google bridge, every 15 min"),
            ("Your cTrader account (read-only)", _one(c, "SELECT MAX(ts) FROM position_snapshots"),
             "cTrader Open API, every 5 min"),
            ("Your trade history", _one(c, "SELECT MAX(closed_utc) FROM pt_trades"), "statements you upload"),
        ]
    feed_age = _days(live[1][1], today)
    for name, last, src in live:
        age = _days(last, today)
        state = "never" if age is None else "live" if age <= 1 else "old"
        if name == "Your trade history" and feed_age is not None and feed_age <= 1:
            # closed trades come from the live cTrader feed: an old date just means no trade closed since
            # (a weekend, a day off), not a missing upload
            state, src = "live", ("from your cTrader account by itself; last closed trade" if age is not None
                                  else "from your cTrader account by itself; no closed trades yet")
        out.append({"kind": "live", "name": name, "source": src, "feeds": "", "last": str(last or "")[:10],
                    "age_days": age, "state": state, "by_email": False})
    return out


def _senders() -> list[str]:
    return [s.strip() for s in os.getenv("JARVIS_REPORT_SENDERS", "").split(",") if s.strip()]


def feeds_text(today: date | None = None) -> str:
    rows = status(today)
    exports = [r for r in rows if r["kind"] != "live"]
    old = [r for r in exports if r["state"] in ("old", "never")]
    icon = {"fresh": "🟢", "live": "🟢", "old": "🔴", "never": "⚪"}
    L = ["YOUR DATA - how fresh is it?", ""]
    if old:
        L.append(f"{len(old)} of {len(exports)} reports are missing or older than {STALE_DAYS} days, so the numbers "
                 "built on them can't be trusted yet.")
    else:
        L.append("Every report is fresh.")
    L += ["", "REPORTS FROM THE BACK OFFICE AND CRM"]
    for r in exports:
        when = ("never received" if r["state"] == "never" else
                f"last one {r['last']} ({'today' if r['age_days'] == 0 else str(r['age_days']) + ' days old'})")
        auto = " - arrives by email ✓" if r["by_email"] else ""
        L.append(f"{icon[r['state']]} {r['name']}: {when}{auto}")
        if r["state"] != "fresh":
            L.append(f"   From: {r['source']}. Without it: {r['feeds']}.")
    L += ["", "LIVE CONNECTIONS"]
    for r in rows:
        if r["kind"] == "live":
            if r["source"].startswith("from your cTrader account"):
                L.append(f"{icon[r['state']]} {r['name']}: {r['source']}" + (f" {r['last']}" if r["last"] else ""))
                continue
            when = "nothing yet" if r["state"] == "never" else f"last update {r['last']}"
            L.append(f"{icon[r['state']]} {r['name']}: {when} ({r['source']})")
    L += ["", "PUT REPORTS ON AUTOPILOT (once, then never upload again)",
          "1. Ask the back office / CRM admin to schedule each report above daily by email to the mailbox DONNA "
          "reads, from one fixed sender address (Excel or CSV, not PDF).",
          "2. Tell DONNA that sender: on the server run  vertex1-connect email  and answer the 'report senders' "
          "question.",
          "3. DONNA picks the files up every few hours by herself; this list turns green."]
    s = _senders()
    L.append(f"Report senders DONNA accepts now: {', '.join(s)}" if s else
             "Report senders DONNA accepts now: any sender (set one so strangers' spreadsheets are ignored).")
    return "\n".join(L)


def command(cmd: str, rest: str) -> str | None:
    if cmd in ("!feeds", "!data", "!reports"):
        return feeds_text()
    return None
