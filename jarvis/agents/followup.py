"""D.O.N.N.A's admin desk, part 3: follow-ups and meeting prep. Plain rules, no Claude needed.

!waiting            what you owe others (emails that asked you something and you haven't answered, oldest
                    first) and what others owe you (emails you sent that nobody has answered after 3 working days).
!waiting done E12   you've dealt with email E12 another way (phone, in person): stop listing it.
Meeting prep        the 15-minute meeting reminder also says who the meeting is with, your last emails with
                    them and open tasks that mention them.

"Others owe you" and the guest list need the Google bridge script version 5 (deploy/google-bridge.gs); with an
older copy those parts say so and everything else still works.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr
from typing import Any

from jarvis.agents import assistant

LOCAL_TZ = assistant.LOCAL_TZ
OWED_DAYS = 30          # replies owed: look back this far
CHASE_WORKING_DAYS = int(os.getenv("JARVIS_CHASE_DAYS", "3"))
FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "live.com",
             "proton.me", "protonmail.com", "aol.com"}
# words in a meeting title that say nothing about who it is with
_STOP = {"call", "meeting", "meet", "sync", "weekly", "daily", "monthly", "catch", "catchup", "with", "and", "the",
         "for", "review", "update", "chat", "online", "teams", "zoom", "google", "discussion", "session", "focus",
         "intro", "follow", "followup", "re", "fw", "fwd", "about", "team", "vertex", "brokers", "our"}


def working_days_between(a: date, b: date) -> int:
    """Monday-Friday days after a, up to and including b."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def _ago(iso: str, today: date) -> int:
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return 0
    return (today - (d.astimezone(LOCAL_TZ).date() if d.tzinfo else d.date())).days


# ------------------------------------------------------------------ you owe others
def replies_owed(today: date | None = None) -> list[dict[str, Any]]:
    """Emails that asked you something, not answered and not dealt with, oldest first.
    An email whose tasks are all marked done counts as dealt with."""
    today = today or datetime.now(LOCAL_TZ).date()
    since = (datetime.now(timezone.utc) - timedelta(days=OWED_DAYS)).isoformat()
    with assistant._db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT rowid AS ref, e.*, (SELECT COUNT(*) FROM tasks t WHERE t.source_ref = e.msg_id) AS n_tasks, "
            "(SELECT MIN(id) FROM tasks t WHERE t.source_ref = e.msg_id AND t.status = 'open') AS open_task "
            "FROM email_seen e WHERE needs_reply = 1 AND processed_at >= ? ORDER BY received", (since,))]
    out = [r for r in rows if not r["n_tasks"] or r["open_task"]]
    for r in out:
        r["days"] = _ago(r["received"], today)
    return out


def mark_dealt_with(ref: int) -> bool:
    with assistant._db() as c:
        return c.execute("UPDATE email_seen SET needs_reply = 0 WHERE rowid = ? AND needs_reply = 1",
                         (ref,)).rowcount == 1


# ------------------------------------------------------------------ others owe you
def awaiting_others(today: date | None = None) -> dict[str, Any]:
    """Emails you sent that nobody has answered after CHASE_WORKING_DAYS working days (bridge v5)."""
    from jarvis.adapters import google_bridge

    if not google_bridge.configured():
        return {"error": "needs the Google bridge"}
    r = google_bridge.call("sent", days=21, max=40)
    if "error" in r:
        return {"error": r["error"]}
    if "sent" not in r:
        return {"error": "older bridge", "version": r.get("version")}
    today = today or datetime.now(LOCAL_TZ).date()
    out = []
    for m in r["sent"]:
        try:
            d = datetime.fromisoformat(str(m["date"]).replace("Z", "+00:00")).astimezone(LOCAL_TZ).date()
        except (KeyError, ValueError):
            continue
        wd = working_days_between(d, today)
        if wd >= CHASE_WORKING_DAYS:
            to = ", ".join(a for _, a in getaddresses([str(m.get("to", ""))]) if a)[:80] or "?"
            out.append({"to": to, "subject": assistant.mask(str(m.get("subject", "")))[:80],
                        "days": (today - d).days, "working_days": wd})
    return {"items": sorted(out, key=lambda x: -x["days"])}


def waiting_text(today: date | None = None) -> str:
    today = today or datetime.now(LOCAL_TZ).date()
    owed = replies_owed(today)
    L = [f"D.O.N.N.A | WAITING | {today:%a %d %b}", "-" * 34, f"YOU OWE A REPLY ({len(owed)})"]
    for r in owed[:12]:
        task = f"  task #{r['open_task']} (!draft {r['open_task']})" if r["open_task"] else ""
        old = "  OVER 5 DAYS" if r["days"] > 5 else ""
        L.append(f" E{r['ref']}  {r['days']}d  {r['subject'][:60]} ({r['sender_domain']}){task}{old}")
    if not owed:
        L.append(" Nothing - every email that asked you something has an answer.")
    elif len(owed) > 12:
        L.append(f" ...and {len(owed) - 12} more")
    aw = awaiting_others(today)
    L += ["", "OTHERS OWE YOU (no answer after "
          f"{CHASE_WORKING_DAYS} working days)"]
    if aw.get("error") == "older bridge":
        L.append(" Needs the Google bridge script version 5 - see DONNA.md 'Updating the bridge script'. Optional.")
    elif "error" in aw:
        L.append(f" Can't check: {aw['error']}")
    else:
        L += [f" {a['days']}d  {a['subject'][:60]} -> {a['to']}" for a in aw["items"][:12]] or [" Nothing outstanding."]
    if owed:
        L += ["", "Dealt with one another way? !waiting done E12"]
    return "\n".join(L)


# ------------------------------------------------------------------ meeting prep
def _domain(addr: str) -> str:
    return parseaddr(addr)[1].rpartition("@")[2].lower()


def _keywords(title: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]{3,}", title.lower()) if w not in _STOP and not w.isdigit()]


def meeting_brief(event: dict[str, Any], limit: int = 3) -> list[str]:
    """Who the meeting is with, the last emails with them and open tasks that mention them. [] when nothing found."""
    guests = [g for g in event.get("guests") or [] if "@" in g]
    domains = {d for d in (_domain(g) for g in guests) if d and d not in FREE_MAIL}
    words = _keywords(event.get("title", ""))
    words += [d.split(".")[0] for d in domains if len(d.split(".")[0]) >= 3]
    L: list[str] = []
    if guests:
        L.append("  With: " + ", ".join(guests[:5]) + (f" +{len(guests) - 5}" if len(guests) > 5 else ""))
    if not domains and not words:
        return L
    with assistant._db() as c:
        rows = [dict(r) for r in c.execute("SELECT received, sender_domain, subject FROM email_seen "
                                           "ORDER BY received DESC LIMIT 500")]
    hits = [r for r in rows if r["sender_domain"] in domains
            or any(re.search(rf"\b{re.escape(w)}", (r["subject"] or "").lower()) for w in words)][:limit]
    if hits:
        L.append("  Recent email: " + "; ".join(f"{r['received'][5:10]} {r['subject'][:50]} ({r['sender_domain']})"
                                                for r in hits))
    tasks = [t for t in assistant.list_tasks()
             if any(re.search(rf"\b{re.escape(w)}", f"{t['title']} {t['detail'] or ''}".lower()) for w in words)][:limit]
    if tasks:
        L.append("  Open tasks: " + "; ".join(f"#{t['id']} {t['title'][:50]}" for t in tasks))
    return L


def command(cmd: str, rest: str) -> str | None:
    if cmd != "!waiting":
        return None
    sub, _, arg = rest.strip().partition(" ")
    if sub.lower() == "done":
        m = re.fullmatch(r"\s*[Ee]?(\d+)\s*", arg)
        if not m:
            return "Usage: !waiting done E12"
        return f"E{m.group(1)} won't be listed again." if mark_dealt_with(int(m.group(1))) else \
            f"E{m.group(1)} isn't on the list."
    return waiting_text()
