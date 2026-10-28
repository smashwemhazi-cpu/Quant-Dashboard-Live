"""Complaints register: every client complaint logged, acknowledged, resolved and evidenced.

A regulator or ombud asks four things: when was it received, when was it acknowledged, what was decided
and why, and what did you learn (root cause). Each complaint keeps that trail.

  new -> acknowledged -> investigating -> resolved (upheld / partly upheld / not upheld) -> closed
  escalated  the client took it to the ombud or regulator
  not_complaint  logged automatically from email but you judged it isn't one (reason kept)

Deadlines are settings - set them to your complaints policy:
  JARVIS_COMPLAINT_ACK_DAYS (default 2)       acknowledge within
  JARVIS_COMPLAINT_RESOLVE_DAYS (default 42)  final answer within (six weeks, after which a client can
                                              usually take it to the ombud)
Clients are recorded by account / customer number only - never by name.
Emails that read like complaints are logged automatically as "new - from email" for you to confirm.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jarvis.core import audit
from jarvis.core.config import settings

ACK_DAYS = int(os.getenv("JARVIS_COMPLAINT_ACK_DAYS", "2"))
RESOLVE_DAYS = int(os.getenv("JARVIS_COMPLAINT_RESOLVE_DAYS", "42"))
CATEGORIES = ["withdrawal", "deposit", "execution", "platform", "account", "bonus", "marketing", "conduct", "fees",
              "other"]
OUTCOMES = {"upheld": "upheld", "partly": "partly upheld", "partly upheld": "partly upheld", "rejected": "not upheld",
            "not upheld": "not upheld"}
ACTIVE = ("new", "acknowledged", "investigating", "escalated")
CLIENT_REF = re.compile(r"[A-Za-z]{0,4}-?\d{3,12}")
LIKE_COMPLAINT = re.compile(r"\b(complain\w*|ombud\w*|dispute\w*|unacceptable|escalat\w+|refund|scam\w*|fraud\w*|"
                            r"legal action|lawyer|attorney|regulator|still (?:not|waiting)|never received)\b", re.I)
GUESS = [("withdrawal", r"withdraw|payout|cash ?out"), ("deposit", r"deposit|top.?up|funding"),
         ("execution", r"slippage|requote|price|spread|stop.?out|executed|fill"),
         ("platform", r"platform|app|login|log in|ctrader|server|frozen"),
         ("account", r"kyc|verif|document|account (?:closed|blocked|suspended)"), ("bonus", r"bonus|promo"),
         ("fees", r"fee|commission|swap|charge")]


def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS complaints (id INTEGER PRIMARY KEY AUTOINCREMENT, received TEXT, channel TEXT,
            client TEXT, category TEXT, summary TEXT, status TEXT DEFAULT 'new', ack_due TEXT, resolve_due TEXT,
            acknowledged TEXT, outcome TEXT, root_cause TEXT, redress REAL, resolution TEXT, closed_at TEXT,
            source_ref TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS complaint_log (complaint_id INTEGER, ts TEXT, who TEXT, action TEXT, note TEXT);
    """)
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(c: sqlite3.Connection, cid: int, who: str, action: str, note: str = "") -> None:
    c.execute("INSERT INTO complaint_log VALUES (?,?,?,?,?)", (cid, _now(), who, action, note[:1000]))


def log_complaint(summary: str, category: str = "other", client: str = "", channel: str = "manual",
                  received: date | None = None, source_ref: str | None = None, who: str = "officer") -> int | None:
    """Record a complaint. Returns its number, or None if this email was already logged."""
    client = client.strip().upper()
    if client and not CLIENT_REF.fullmatch(client):
        raise ValueError("client must be an account or customer number (e.g. 6000013 or CU142), never a name")
    category = category.strip().lower() if category.strip().lower() in CATEGORIES else "other"
    received = received or date.today()
    with _db() as c:
        cur = c.execute("INSERT OR IGNORE INTO complaints (received, channel, client, category, summary, ack_due, "
                        "resolve_due, source_ref) VALUES (?,?,?,?,?,?,?,?)",
                        (received.isoformat(), channel, client, category, summary.strip()[:1000],
                         (received + timedelta(days=ACK_DAYS)).isoformat(),
                         (received + timedelta(days=RESOLVE_DAYS)).isoformat(), source_ref))
        if not cur.rowcount:
            return None
        _log(c, cur.lastrowid, who, "logged", f"{channel}: {summary[:300]}")
    audit.log(who, "complaint_logged", {"complaint": cur.lastrowid, "category": category, "channel": channel})
    return cur.lastrowid


def guess_category(text: str) -> str:
    return next((cat for cat, rx in GUESS if re.search(rx, text, re.I)), "other")


def from_email(subject: str, summary: str, category: str, sender_domain: str, msg_id: str,
               received: str = "") -> int | None:
    """Log an email that reads like a complaint as 'new - from email', for the officer to confirm."""
    text = f"{subject} {summary}"
    if category not in ("client", "payments", "kyc_compliance", "regulator", "other") or not LIKE_COMPLAINT.search(text):
        return None
    try:
        day = date.fromisoformat(received[:10]) if received else None
    except ValueError:
        day = None
    return log_complaint(f"{subject[:150]} - {summary[:300]} (from {sender_domain})", guess_category(text),
                         channel="email - confirm", received=day, source_ref=msg_id, who="donna")


def update(cid: int, action: str, note: str = "", outcome: str = "", root_cause: str = "",
           redress: float | None = None, who: str = "officer") -> str:
    """ack | investigate | resolve | escalate | close | not_complaint | reopen. Returns a plain-English result."""
    action = action.lower().replace("-", "_")
    with _db() as c:
        row = c.execute("SELECT * FROM complaints WHERE id = ?", (cid,)).fetchone()
        if not row:
            return f"No complaint #{cid}."
        now = _now()
        if action == "ack":
            c.execute("UPDATE complaints SET status = 'acknowledged', acknowledged = COALESCE(acknowledged, ?) "
                      "WHERE id = ?", (now, cid))
        elif action == "investigate":
            c.execute("UPDATE complaints SET status = 'investigating', acknowledged = COALESCE(acknowledged, ?) "
                      "WHERE id = ?", (now, cid))
        elif action == "resolve":
            if outcome.lower() not in OUTCOMES or not root_cause.strip():
                return "To resolve, give the outcome (upheld / partly / rejected) and the root cause."
            c.execute("UPDATE complaints SET status = 'resolved', outcome = ?, root_cause = ?, redress = ?, "
                      "resolution = ?, acknowledged = COALESCE(acknowledged, ?) WHERE id = ?",
                      (OUTCOMES[outcome.lower()], root_cause.strip()[:300], redress, note[:1000], now, cid))
        elif action == "escalate":
            c.execute("UPDATE complaints SET status = 'escalated' WHERE id = ?", (cid,))
        elif action == "close":
            if row["status"] not in ("resolved", "escalated"):
                return f"Complaint #{cid} has no outcome yet - resolve it first (outcome and root cause)."
            c.execute("UPDATE complaints SET status = 'closed', closed_at = ? WHERE id = ?", (now, cid))
        elif action == "not_complaint":
            if not note.strip():
                return "Say why it isn't a complaint - the reason is kept on file."
            c.execute("UPDATE complaints SET status = 'not_complaint', closed_at = ?, resolution = ? WHERE id = ?",
                      (now, note[:1000], cid))
        elif action == "reopen":
            c.execute("UPDATE complaints SET status = 'investigating', closed_at = NULL WHERE id = ?", (cid,))
        else:
            return "Actions: ack, investigate, resolve, escalate, close, notcomplaint, reopen."
        _log(c, cid, who, action, note or (f"{outcome} - {root_cause}" if action == "resolve" else ""))
    audit.log(who, f"complaint_{action}", {"complaint": cid})
    return f"Complaint #{cid}: {action.replace('_', ' ')} recorded."


def get(cid: int) -> dict[str, Any] | None:
    with _db() as c:
        row = c.execute("SELECT * FROM complaints WHERE id = ?", (cid,)).fetchone()
        if not row:
            return None
        log = [dict(r) for r in c.execute("SELECT * FROM complaint_log WHERE complaint_id = ? ORDER BY ts", (cid,))]
    return {**dict(row), "log": log}


def active(today: date | None = None) -> list[dict[str, Any]]:
    """Open complaints, most urgent first, each with what is overdue."""
    today = (today or date.today()).isoformat()
    with _db() as c:
        rows = [dict(r) for r in c.execute(f"SELECT * FROM complaints WHERE status IN {ACTIVE} ORDER BY resolve_due, id")]  # noqa: S608
    for r in rows:
        r["ack_overdue"] = not r["acknowledged"] and r["ack_due"] < today
        r["overdue"] = r["status"] != "escalated" and r["resolve_due"] < today
    return rows


def stats(days: int = 90) -> dict[str, Any]:
    """Management information: volumes, outcomes, time to resolve, root causes, overdue."""
    since = (date.today() - timedelta(days=days)).isoformat()
    with _db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM complaints WHERE received >= ? AND status != 'not_complaint'",
                                           (since,))]
    by_cat: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    causes: dict[str, int] = {}
    days_to_answer = []
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
        if r["outcome"]:
            outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
            causes[r["root_cause"]] = causes.get(r["root_cause"], 0) + 1
            done = next((x["ts"][:10] for x in get(r["id"])["log"] if x["action"] == "resolve"), None)
            if done:
                days_to_answer.append((date.fromisoformat(done) - date.fromisoformat(r["received"])).days)
    act = active()
    return {"days": days, "received": len(rows), "by_category": by_cat, "outcomes": outcomes,
            "upheld_pct": round(100 * (outcomes.get("upheld", 0) + outcomes.get("partly upheld", 0))
                                / sum(outcomes.values()), 1) if outcomes else None,
            "avg_days_to_answer": round(sum(days_to_answer) / len(days_to_answer), 1) if days_to_answer else None,
            "top_root_causes": sorted(causes.items(), key=lambda x: -x[1])[:5],
            "open": len(act), "overdue": sum(r["overdue"] for r in act),
            "ack_overdue": sum(r["ack_overdue"] for r in act), "redress_total": sum(r["redress"] or 0 for r in rows)}


def line(r: dict[str, Any]) -> str:
    flag = "  !! NOT ACKNOWLEDGED" if r.get("ack_overdue") else "  !! OVERDUE" if r.get("overdue") else ""
    who = f" {r['client']}" if r["client"] else ""
    return (f"#{r['id']:<4} {r['status'][:12]:<12} {r['category']:<10}{who}  rcvd {r['received']}  "
            f"answer by {r['resolve_due']}: {r['summary'][:90]}{flag}")


def report_lines() -> list[str]:
    act = active()
    s = stats(30)
    L = [f"COMPLAINTS ({len(act)} open, {s['received']} received in 30 days)"]
    L += [" " + line(r) for r in act[:10]] or [" None open"]
    if len(act) > 10:
        L.append(f" ... and {len(act) - 10} more - !complaints")
    return L


HELP = ("!complaints                 open complaints (!complaints all)\n"
        "!complaint add CU142 | withdrawal | what the client says\n"
        "!complaint 3                one complaint with its history\n"
        "!complaint 3 ack|investigate|escalate <note>\n"
        "!complaint 3 resolve upheld|partly|rejected | <root cause> | <what was done> | <redress amount>\n"
        "!complaint 3 close   !complaint 3 notcomplaint <why>   !complaint mi 90")


def command(cmd: str, rest: str) -> str | None:
    """Discord / console commands; None if not a complaints command."""
    if cmd == "!complaints":
        rows = active() if rest.strip() != "all" else [dict(r) for r in _db().execute(
            "SELECT * FROM complaints ORDER BY id DESC LIMIT 40")]
        return "\n".join(line(r) for r in rows) or "No open complaints."
    if cmd != "!complaint":
        return None
    rest = rest.strip()
    if not rest:
        return HELP
    if rest.lower().startswith("add"):
        parts = [p.strip() for p in rest[3:].split("|")]
        if len(parts) < 3 or not parts[2]:
            return "Usage: !complaint add <account no. or blank> | <category> | <what the client says>\nCategories: " + \
                ", ".join(CATEGORIES)
        try:
            cid = log_complaint(parts[2], parts[1], parts[0], channel="logged by officer")
        except ValueError as e:
            return str(e)
        r = get(cid)
        return f"Logged complaint #{cid} ({r['category']}). Acknowledge by {r['ack_due']}, final answer by {r['resolve_due']}."
    if rest.lower().startswith("mi"):
        n = re.search(r"\d+", rest)
        s = stats(int(n.group()) if n else 90)
        return "\n".join([f"Complaints, last {s['days']} days: {s['received']} received, {s['open']} open, "
                          f"{s['overdue']} overdue, {s['ack_overdue']} not acknowledged in time",
                          " By category: " + (", ".join(f"{k} {v}" for k, v in s["by_category"].items()) or "none"),
                          " Outcomes: " + (", ".join(f"{k} {v}" for k, v in s["outcomes"].items()) or "none yet")
                          + (f" (upheld {s['upheld_pct']}%)" if s["upheld_pct"] is not None else ""),
                          f" Average days to final answer: {s['avg_days_to_answer'] if s['avg_days_to_answer'] is not None else 'n/a'}",
                          " Top root causes: " + (", ".join(f"{k} ({v})" for k, v in s["top_root_causes"]) or "none yet"),
                          f" Redress paid: {s['redress_total']:,.2f}"])
    m = re.match(r"#?(\d+)\s*(\w+)?\s*(.*)", rest, re.S)
    if not m:
        return HELP
    cid, action, arg = int(m.group(1)), (m.group(2) or "").lower(), m.group(3).strip()
    if not action:
        r = get(cid)
        if not r:
            return f"No complaint #{cid}."
        hist = "\n".join(f" {h['ts'][:16]} {h['who']}: {h['action']} {h['note'] or ''}" for h in r["log"])
        return (f"Complaint #{r['id']} [{r['status']}] {r['category']}" + (f" - client {r['client']}" if r["client"] else "")
                + f"\nReceived {r['received']} via {r['channel']}. Acknowledge by {r['ack_due']}"
                + (f" (done {r['acknowledged'][:10]})" if r["acknowledged"] else "")
                + f". Final answer by {r['resolve_due']}.\nWhat the client says: {r['summary']}\n"
                + (f"Outcome: {r['outcome']}. Root cause: {r['root_cause']}. "
                   + (f"Redress: {r['redress']:,.2f}. " if r["redress"] else "") + f"{r['resolution'] or ''}\n"
                   if r["outcome"] else "") + f"History:\n{hist}")
    if action == "resolve":
        oc, _, tail = arg.partition("|")
        bits = [b.strip() for b in tail.split("|")]
        redress = None
        if len(bits) > 2 and bits[2]:
            try:
                redress = float(re.sub(r"[^\d.]", "", bits[2]) or 0)
            except ValueError:
                redress = None
        return update(cid, "resolve", bits[1] if len(bits) > 1 else "", oc.strip(), bits[0] if bits else "", redress)
    return update(cid, {"notcomplaint": "not_complaint"}.get(action, action), arg)
