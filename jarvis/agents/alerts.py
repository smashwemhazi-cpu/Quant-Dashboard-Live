"""D.O.N.N.A's alert protocol: one place decides what deserves a ping, so real alerts aren't lost in noise.

Every heads-up DONNA wants to send goes through `raise_alert(kind, text)`. The kind's level decides what happens:

  CRITICAL   now, to Discord and your phone, at any hour. Repeats every 30 min (twice at most) until you !ack.
             Trading risk breaches, a regulator writing to you, DONNA herself down for hours.
  IMPORTANT  now during your alert hours (default 07:00-21:00 your time, every day); outside them it waits for
             the morning message. At most 4 an hour; extra ones are held, not lost.
  NOTICE     no ping. Collected and shown once, in the next morning or evening message ("Since last time").
  LOG        no ping, no message. In the audit trail and !alerts only.

The same alert (same kind and text) is sent once per 24 hours at most.
  !alerts            what is held, what was sent in the last 24 hours
  !alerts policy     the table below in plain words
  !ack               "I've seen it": stops a critical alert repeating
  !quiet 2h          only critical alerts for the next 2 hours (!quiet off ends it)
Plain rules, no Claude credit.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.core import audit
from jarvis.core.config import settings

CRITICAL, IMPORTANT, NOTICE, LOG = "CRITICAL", "IMPORTANT", "NOTICE", "LOG"
ORDER = [CRITICAL, IMPORTANT, NOTICE, LOG]
ICON = {CRITICAL: "🔴", IMPORTANT: "🟠", NOTICE: "🔵", LOG: "⚪"}

# kind -> (level, what it covers, in plain words)
POLICY: dict[str, tuple[str, str]] = {
    "trading_risk": (CRITICAL, "your account: daily-loss limit hit, no stop on a position, margin near stop-out"),
    "regulator_email": (CRITICAL, "an email from a regulator or authority"),
    "donna_down": (CRITICAL, "a core part of DONNA (email, heartbeat) broken for over 6 hours"),
    "urgent_email": (IMPORTANT, "an urgent email you haven't opened yet"),
    "meeting_soon": (IMPORTANT, "a meeting starting soon, with its brief"),
    "deadline": (IMPORTANT, "a compliance deadline or obligation due within 2 days"),
    "discipline": (IMPORTANT, "your own trading as it happens: cool-off after a loss, trades used today, losses in a row, "
                              "PAD flags; sent at any hour you are trading"),
    "approval": (IMPORTANT, "something waiting for your OK (a meeting invitation, a reply)"),
    "desk_risk": (IMPORTANT, "execution or LP slippage outside limits"),
    "bots": (IMPORTANT, "a cBot on your account stopped"),
    "midday": (IMPORTANT, "the midday check of open high-priority work"),
    "job_failed": (NOTICE, "a DONNA job failing for over an hour (it keeps retrying)"),
    "waiting_setup": (NOTICE, "a connection that needs you to reconnect it"),
    "leads": (NOTICE, "new or unassigned leads"),
    "market": (NOTICE, "a big market move"),
    "reports_loaded": (NOTICE, "reports picked up from your email"),
    "info": (LOG, "everything else"),
}
LIVE_KINDS = {"discipline"}  # you are at the screen when these happen: no alert hours, no hourly cap (the guard self-limits)
CORE_JOBS = {"inbox_triage", "heartbeat"}          # these failing for hours is DONNA being down
CORE_DOWN_HOURS = float(os.getenv("JARVIS_ALERT_CORE_DOWN_HOURS", "6"))
REPEAT_MINUTES = int(os.getenv("JARVIS_ALERT_REPEAT_MINUTES", "30"))
MAX_REPEATS = int(os.getenv("JARVIS_ALERT_MAX_REPEATS", "2"))
PER_HOUR = int(os.getenv("JARVIS_ALERT_PER_HOUR", "4"))
DEDUPE_HOURS = float(os.getenv("JARVIS_ALERT_DEDUPE_HOURS", "24"))
HOURS = os.getenv("JARVIS_ALERT_HOURS", "07:00-21:00")
LOCAL_TZ = ZoneInfo(os.getenv("JARVIS_LOCAL_TZ", "UTC"))
REGULATOR = re.compile(os.getenv("JARVIS_REGULATOR_DOMAINS", r"fscmauritius\.org|fsc\.mu|\.gov\b|fiu\.mu|"
                                 r"fca\.org\.uk|esma\.europa\.eu|regulator"), re.I)


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(os.getenv("JARVIS_STATE_DB", settings.state_db))
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT,
                 level TEXT, text TEXT, key TEXT, status TEXT, sent_at TEXT, repeats INTEGER DEFAULT 0)""")
    c.execute("CREATE TABLE IF NOT EXISTS alert_state (k TEXT PRIMARY KEY, v TEXT)")
    return c


def _now() -> datetime:
    return datetime.now(timezone.utc)


def level_of(kind: str) -> str:
    return POLICY.get(kind, POLICY["info"])[0]


def in_hours(now: datetime | None = None) -> bool:
    local = (now or _now()).astimezone(LOCAL_TZ)
    a, _, b = HOURS.partition("-")
    try:
        start = datetime.strptime(a.strip(), "%H:%M").time()
        end = datetime.strptime(b.strip(), "%H:%M").time()
    except ValueError:
        return True
    return start <= local.time() < end


def quiet_until(c: sqlite3.Connection) -> datetime | None:
    row = c.execute("SELECT v FROM alert_state WHERE k = 'quiet_until'").fetchone()
    try:
        t = datetime.fromisoformat(row[0]) if row else None
    except ValueError:
        return None
    return t if t and t > _now() else None


def _targets(kind: str) -> list[str]:
    from jarvis.agents.personal import PERSONAL_TARGETS
    from jarvis.scheduler import ALERT_TARGETS

    system = kind in ("job_failed", "waiting_setup", "donna_down")
    return list(dict.fromkeys((ALERT_TARGETS or PERSONAL_TARGETS) if system else (PERSONAL_TARGETS or ALERT_TARGETS)))


def _send(kind: str, level: str, text: str, repeat: int = 0) -> dict[str, str]:
    targets = _targets(kind)
    if not targets:
        return {}
    from donna.channels import broadcast

    head = f"{ICON[level]} {level}" + (f" (reminder {repeat})" if repeat else "")
    tail = "\nType !ack when you've seen it." if level == CRITICAL else ""
    return broadcast(f"{head}\n{text}{tail}", targets, title=f"D.O.N.N.A {level.lower()}")


def raise_alert(kind: str, text: str, key: str = "", now: datetime | None = None) -> str:
    """Decide and deliver. Returns what happened: sent, held, duplicate, logged."""
    now = now or _now()
    level = level_of(kind)
    text = text.strip()
    key = key or hashlib.sha1(f"{kind}|{re.sub(r'[0-9]+', '#', text)[:300]}".encode()).hexdigest()[:16]
    with _db() as c:
        since = (now - timedelta(hours=DEDUPE_HOURS)).isoformat()
        if c.execute("SELECT 1 FROM alerts WHERE key = ? AND ts >= ?", (key, since)).fetchone():
            return "duplicate"
        quiet = quiet_until(c)
        hour_ago = (now - timedelta(hours=1)).isoformat()
        sent_hour = c.execute("SELECT COUNT(*) FROM alerts WHERE level = ? AND status = 'sent' AND sent_at >= ?",
                              (IMPORTANT, hour_ago)).fetchone()[0]
        if level == CRITICAL:
            status = "sent"
        elif kind in LIVE_KINDS:
            status = "held" if quiet else "sent"
        elif level == IMPORTANT:
            status = "sent" if in_hours(now) and not quiet and sent_hour < PER_HOUR else "held"
        elif level == NOTICE:
            status = "held"
        else:
            status = "logged"
        c.execute("INSERT INTO alerts (ts, kind, level, text, key, status, sent_at) VALUES (?,?,?,?,?,?,?)",
                  (now.isoformat(), kind, level, text[:2000], key, status,
                   now.isoformat() if status == "sent" else None))
    if status == "sent":
        _send(kind, level, text)
    audit.log("alerts", "alert", {"kind": kind, "level": level, "status": status})
    return status


def repeat_criticals(now: datetime | None = None) -> int:
    """Scheduler: remind about critical alerts nobody acknowledged."""
    now = now or _now()
    due = (now - timedelta(minutes=REPEAT_MINUTES)).isoformat()
    with _db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM alerts WHERE level = ? AND status = 'sent' AND repeats < ? "
                                           "AND sent_at <= ?", (CRITICAL, MAX_REPEATS, due))]
        for r in rows:
            c.execute("UPDATE alerts SET repeats = repeats + 1, sent_at = ? WHERE id = ?", (now.isoformat(), r["id"]))
    for r in rows:
        _send(r["kind"], CRITICAL, r["text"], r["repeats"] + 1)
    return len(rows)


def ack() -> int:
    with _db() as c:
        return c.execute("UPDATE alerts SET status = 'acked' WHERE level = ? AND status = 'sent'",
                         (CRITICAL,)).rowcount


def take_held(limit: int = 12) -> list[str]:
    """Lines for the morning / evening message: everything held since last time, then marked as shown."""
    with _db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM alerts WHERE status = 'held' ORDER BY "
                                           "CASE level WHEN 'IMPORTANT' THEN 0 ELSE 1 END, ts")]
        c.execute("UPDATE alerts SET status = 'shown' WHERE status = 'held'")
    if not rows:
        return []
    out = [f" {ICON[r['level']]} {r['text'].splitlines()[0][:160]}" for r in rows[:limit]]
    if len(rows) > limit:
        out.append(f" ...and {len(rows) - limit} more: !alerts")
    return out


def held_lines() -> list[str]:
    try:
        lines = take_held()
    except sqlite3.Error:
        return []
    return ["SINCE LAST TIME (held back so they didn't interrupt you)"] + lines if lines else []


def status_rows(limit: int = 50) -> list[dict[str, Any]]:
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))]


def policy_text() -> str:
    L = ["ALERT POLICY - what deserves a ping", ""]
    words = {CRITICAL: "Ping now, any hour, Discord + phone; repeats until you type !ack",
             IMPORTANT: f"Ping now during {HOURS} (your time), max {PER_HOUR} an hour; otherwise held for the morning",
             NOTICE: "No ping; listed once in the next morning or evening message",
             LOG: "No ping; in !alerts and the audit trail only"}
    for lv in ORDER:
        L += [f"{ICON[lv]} {lv}: {words[lv]}"] + [f"   - {what}" for k, (lvl, what) in POLICY.items() if lvl == lv] + [""]
    L.append(f"The same alert is sent once per {DEDUPE_HOURS:g} hours at most. !quiet 2h = only critical for 2 hours.")
    return "\n".join(L)


def status_text(now: datetime | None = None) -> str:
    now = now or _now()
    since = (now - timedelta(hours=24)).isoformat()
    with _db() as c:
        sent = [dict(r) for r in c.execute("SELECT * FROM alerts WHERE ts >= ? AND status IN ('sent','acked') "
                                           "ORDER BY ts DESC LIMIT 15", (since,))]
        held = [dict(r) for r in c.execute("SELECT * FROM alerts WHERE status = 'held' ORDER BY ts")]
        counts = dict(c.execute("SELECT level, COUNT(*) FROM alerts WHERE ts >= ? GROUP BY level", (since,)).fetchall())
        quiet = quiet_until(c)
    L = ["ALERTS - last 24 hours", ""]
    L.append("Raised: " + ", ".join(f"{counts.get(lv, 0)} {lv.lower()}" for lv in ORDER))
    if quiet:
        L.append(f"Quiet mode until {quiet.astimezone(LOCAL_TZ):%H:%M} (only critical). !quiet off ends it.")
    L += ["", "PINGED YOU"] + ([f" {ICON[r['level']]} {r['ts'][11:16]} {r['text'].splitlines()[0][:120]}"
                                + (" (seen)" if r["status"] == "acked" else "") for r in sent] or [" nothing"])
    L += ["", "WAITING FOR YOUR NEXT MESSAGE"] + ([f" {ICON[r['level']]} {r['text'].splitlines()[0][:120]}"
                                                  for r in held] or [" nothing"])
    L += ["", "!alerts policy = what deserves a ping   !ack = stop a critical repeating   !quiet 2h"]
    return "\n".join(L)


def set_quiet(arg: str) -> str:
    with _db() as c:
        if arg.strip().lower() in ("off", "stop", "end", "0"):
            c.execute("DELETE FROM alert_state WHERE k = 'quiet_until'")
            return "Quiet mode off. Important alerts ping you again."
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(h|hr|hrs|hours?|m|min|mins|minutes?)?\s*", arg or "2h")
        if not m:
            return "Usage: !quiet 2h   (or 30m, or !quiet off)"
        mins = float(m.group(1)) * (60 if (m.group(2) or "h").startswith("h") else 1)
        until = _now() + timedelta(minutes=min(mins, 24 * 60))
        c.execute("INSERT OR REPLACE INTO alert_state VALUES ('quiet_until', ?)", (until.isoformat(),))
    return (f"Quiet until {until.astimezone(LOCAL_TZ):%H:%M}: only critical alerts will ping you. "
            "The rest waits for your next message.")


def command(cmd: str, rest: str) -> str | None:
    if cmd == "!alerts":
        return policy_text() if rest.strip().lower().startswith("policy") else status_text()
    if cmd == "!ack":
        n = ack()
        return f"Got it: {n} critical alert{'s' if n != 1 else ''} won't repeat." if n else "Nothing to acknowledge."
    if cmd == "!quiet":
        return set_quiet(rest or "2h")
    return None
