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
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any

from jarvis.core import markets
from jarvis.core.config import settings

STALE_DAYS = int(os.getenv("JARVIS_DATA_STALE_DAYS", "3"))   # working days (Mon-Fri): weekends don't age a report
LIVE_OPEN_HOURS = float(os.getenv("DONNA_FEED_LIVE_OPEN_HOURS", "6"))  # market hours without a cTrader snapshot

# kind, plain name, where it comes from, what goes stale without it
EXPORTS = [
    ("eod", "End-of-day accounts (equity, balance, margin)", "cTrader's daily 'Accounts EOD report' email (trade@alerts.ctrader.com) - read by itself",
     "client risk, margin alerts, KRIs"),
    ("docs", "Customer documents (KYC)", "Syntellicore customer documents export", "KYC KRIs, expiring documents"),
    ("activity", "Trading activity (volume, deals)", "cTrader back office activity report", "turnover alerts, KPIs"),
    ("kpi", "CRM KPIs", "Syntellicore KPI dashboard export", "growth KPIs"),
    ("leads", "CRM leads", "Syntellicore leads export", "lead and conversion KPIs"),
    ("streams", "Price streams and spreads", "cTrader back office symbols / streams", "spread-widening alerts"),
    ("ib", "IB commissions", "Syntellicore IB / partner commission report", "IB payout checks"),
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


def _when(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace(" ", "T")[:19])
    except ValueError:
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def status(today: date | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    """One row per source: name, last (YYYY-MM-DD), age_days, state (fresh / old / never / live / off), source, how.
    Report ages are counted in working days; the cTrader account in hours the market was open, so a weekend or a
    holiday never turns anything red. Sources DONNA doesn't use (Syntellicore while switched off) are 'off'."""
    now = now or (datetime.combine(today, dtime(12), timezone.utc) if today else datetime.now(timezone.utc))
    today = today or now.date()
    out: list[dict[str, Any]] = []
    with _db() as c:
        for kind, name, src, feeds_what in EXPORTS:
            if markets.source_off(kind):
                out.append({"kind": kind, "name": name, "source": src, "feeds": feeds_what, "last": "",
                            "age_days": None, "state": "off", "by_email": False})
                continue
            last = (_one(c, "SELECT MAX(asof) FROM bo_leads") if kind == "leads"
                    else _one(c, "SELECT value FROM bo_meta WHERE key = ?", f"last_{kind}"))
            by_mail = _one(c, "SELECT MAX(received) FROM bo_mail WHERE kind = ?", kind)
            age = _days(last, today)
            work = None if age is None else markets.working_days_between(today - timedelta(days=age), today)
            state = "never" if work is None else "fresh" if work <= STALE_DAYS else "old"
            out.append({"kind": kind, "name": name, "source": src, "feeds": feeds_what, "last": str(last or "")[:10],
                        "age_days": age, "state": state, "by_email": bool(by_mail)})
        inbox = _one(c, "SELECT MAX(processed_at) FROM email_seen")
        snap = _one(c, "SELECT MAX(ts) FROM position_snapshots")
        trade = _one(c, "SELECT MAX(closed_utc) FROM pt_trades")
    rows = []
    age = _days(inbox, today)
    rows.append(("Inbox (email sorting)", inbox, "Google bridge, every 15 min",
                 "never" if age is None else "live" if age <= 1 else "old"))
    # the cTrader account: judged on the hours the market was open since the last snapshot, so a weekend or a
    # holiday (server closed or in maintenance) is never "old"
    t = _when(snap)
    gap = None if t is None else markets.open_hours_between(t, now)
    feed_live = gap is not None and gap <= LIVE_OPEN_HOURS
    quiet = markets.closed_note(now)
    rows.append(("Your cTrader account (read-only)", snap,
                 "cTrader Open API, every 5 min" + (f"; {quiet}" if quiet and feed_live else ""),
                 "never" if t is None else "live" if feed_live else "old"))
    # trade history comes from the same cTrader API (the source of truth); the emailed / uploaded statement is
    # only a backup, so it is never asked for and never makes this red
    if feed_live:
        src = ("from your cTrader account by itself; last closed trade" if trade
               else "from your cTrader account by itself; no closed trades yet")
        st = "live"
    elif trade:
        tt = _when(trade)
        st = "live" if tt and markets.open_hours_between(tt, now) <= 24 * STALE_DAYS else "old"
        src = "cTrader Open API (your account connection is not answering - !status)"
    else:
        src, st = "cTrader Open API, once your account is connected (!status)", "never"
    rows.append(("Your trade history", trade, src, st))
    for name, last, src, state in rows:
        out.append({"kind": "live", "name": name, "source": src, "feeds": "", "last": str(last or "")[:10],
                    "age_days": _days(last, today), "state": state, "by_email": False})
    return out


def _senders() -> list[str]:
    return [s.strip() for s in os.getenv("JARVIS_REPORT_SENDERS", "").split(",") if s.strip()]


def feeds_text(today: date | None = None, now: datetime | None = None) -> str:
    rows = status(today, now)
    exports = [r for r in rows if r["kind"] != "live" and r["state"] != "off"]
    off = [r for r in rows if r["state"] == "off"]
    old = [r for r in exports if r["state"] in ("old", "never")]
    icon = {"fresh": "🟢", "live": "🟢", "old": "🔴", "never": "⚪", "off": "⚫"}
    L = ["YOUR DATA - how fresh is it?", ""]
    quiet = markets.closed_note(now or (datetime.combine(today, dtime(12), timezone.utc) if today else None))
    if quiet:
        L += [f"Note: {quiet}. Quiet feeds and no new trades are normal now, not a fault.", ""]
    if old:
        L.append(f"{len(old)} of {len(exports)} reports are missing or older than {STALE_DAYS} working days, so the "
                 "numbers built on them can't be trusted yet.")
    else:
        L.append("Every report is fresh.")
    L += ["", "REPORTS FROM THE BACK OFFICE"]
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
    L.append("   Statements by email or upload are optional backup only: DONNA reads your trades from cTrader.")
    if off:
        L += ["", "NOT USED (switched off, never counted as missing)"]
        L += [f"{icon['off']} {r['name']}: {markets.NOT_USED}" for r in off]
    L += ["", "PUT REPORTS ON AUTOPILOT (once, then never upload again)",
          "1. Ask the back office to schedule each report above daily by email to the mailbox DONNA "
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
