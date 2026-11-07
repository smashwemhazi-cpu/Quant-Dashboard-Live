"""D.O.N.N.A's admin desk, part 3: follow-ups and meeting prep. Plain rules, no Claude needed.

!waiting            what you owe others (emails that asked you something and you haven't answered, oldest
                    first) and what others owe you (emails you sent that nobody has answered after 3 working days).
!waiting done E12   you've dealt with email E12 another way (phone, in person): stop listing it (and the
                    rest of that conversation).
!waiting mute x.com never list replies owed to that sender (undo: !waiting unmute x.com).
!waiting all        the list including promos, newsletters and muted senders, which are normally left out.
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
_BULK_CATEGORIES = {"newsletter", "vendor_marketing", "spam"}
_PREFIX = re.compile(r"^\s*((re|fwd?|aw|sv)\s*(\[\d+\])?\s*:\s*)+", re.I)


def _thread_key(subject: str, domain: str) -> tuple[str, str]:
    """Same conversation = same sender domain and same subject once Re:/Fwd: are taken off."""
    return re.sub(r"\s+", " ", _PREFIX.sub("", subject or "")).strip().lower(), (domain or "").lower()


def _muted(c: Any) -> set[str]:
    c.execute("CREATE TABLE IF NOT EXISTS waiting_muted (domain TEXT PRIMARY KEY, added TEXT)")
    return {r[0] for r in c.execute("SELECT domain FROM waiting_muted")}


def _is_muted(domain: str, muted: set[str]) -> bool:
    d = (domain or "").lower()
    return any(d == m or d.endswith("." + m) for m in muted)


def _owed(today: date, everything: bool = False) -> tuple[list[dict[str, Any]], int]:
    """(conversations you owe a reply on, oldest first; how many emails were left out as promos or muted).
    An email whose tasks are all marked done counts as dealt with. A thread that repeats shows once."""
    since = (datetime.now(timezone.utc) - timedelta(days=OWED_DAYS)).isoformat()
    with assistant._db() as c:
        muted = _muted(c)
        rows = [dict(r) for r in c.execute(
            "SELECT rowid AS ref, e.*, (SELECT COUNT(*) FROM tasks t WHERE t.source_ref = e.msg_id) AS n_tasks, "
            "(SELECT MIN(id) FROM tasks t WHERE t.source_ref = e.msg_id AND t.status = 'open') AS open_task "
            "FROM email_seen e WHERE needs_reply = 1 AND processed_at >= ? ORDER BY received", (since,))]
    rows = [r for r in rows if not r["n_tasks"] or r["open_task"]]
    kept = rows if everything else [
        r for r in rows if r["category"] not in _BULK_CATEGORIES and not _is_muted(r["sender_domain"], muted)
        and not assistant.looks_bulk(r["subject"] or "", (r["summary"] or "").removeprefix("[rules]"),
                                     f"@{r['sender_domain']}")]
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for r in kept:
        g = groups.get(_thread_key(r["subject"], r["sender_domain"]))
        if g is None:
            groups[_thread_key(r["subject"], r["sender_domain"])] = {**r, "count": 1, "refs": [r["ref"]],
                                                                     "days": _ago(r["received"], today)}
        else:
            g["count"] += 1
            g["refs"].append(r["ref"])
            g["open_task"] = g["open_task"] or r["open_task"]
    return list(groups.values()), len(rows) - len(kept)


def replies_owed(today: date | None = None, everything: bool = False) -> list[dict[str, Any]]:
    """Conversations that asked you something, not answered and not dealt with, oldest first.
    Promos, newsletters and muted senders are left out unless everything=True."""
    return _owed(today or datetime.now(LOCAL_TZ).date(), everything)[0]


def mark_dealt_with(ref: int) -> bool:
    """Take email E<ref> off the list, with every other email in the same conversation."""
    with assistant._db() as c:
        row = c.execute("SELECT subject, sender_domain FROM email_seen WHERE rowid = ? AND needs_reply = 1",
                        (ref,)).fetchone()
        if not row:
            return False
        key = _thread_key(row[0], row[1])
        refs = [r[0] for r in c.execute("SELECT rowid, subject, sender_domain FROM email_seen "
                                        "WHERE needs_reply = 1 AND sender_domain = ?", (row[1],))
                if _thread_key(r[1], r[2]) == key]
        c.executemany("UPDATE email_seen SET needs_reply = 0 WHERE rowid = ?", [(r,) for r in refs])
        return True


def mute(domain: str) -> int:
    """Never list replies owed to this sender domain again; returns how many waiting emails it hides now."""
    d = domain.strip().lower().lstrip("@").rpartition("@")[2]
    with assistant._db() as c:
        _muted(c)
        c.execute("INSERT OR REPLACE INTO waiting_muted VALUES (?, ?)", (d, datetime.now(timezone.utc).isoformat()))
        return c.execute("SELECT COUNT(*) FROM email_seen WHERE needs_reply = 1 AND "
                         "(sender_domain = ? OR sender_domain LIKE ?)", (d, f"%.{d}")).fetchone()[0]


def unmute(domain: str) -> bool:
    with assistant._db() as c:
        _muted(c)
        return c.execute("DELETE FROM waiting_muted WHERE domain = ?", (domain.strip().lower(),)).rowcount == 1


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


def waiting_text(today: date | None = None, everything: bool = False) -> str:
    today = today or datetime.now(LOCAL_TZ).date()
    owed, hidden = _owed(today, everything)
    L = [f"D.O.N.N.A | WAITING | {today:%a %d %b}", "-" * 34, f"YOU OWE A REPLY ({len(owed)}) - oldest first"]
    for n, r in enumerate(owed[:12], 1):
        days = "today" if r["days"] == 0 else f"{r['days']} day{'s' if r['days'] != 1 else ''}"
        details = [f"from {r['sender_domain']}", ("arrived today" if r["days"] == 0 else f"waiting {days}") + (" - OVER 5 DAYS" if r["days"] > 5 else "")]
        if r["count"] > 1:
            details.append(f"x{r['count']} emails in this thread")
        do = [f"write the reply: !draft {r['open_task']}"] if r["open_task"] else []
        do.append(f"already handled: !waiting done E{r['ref']}")
        L += ["", f" {n}. {_PREFIX.sub('', r['subject'] or '')[:70]}", "    " + " | ".join(details),
              "    " + " | ".join(do)]
    if not owed:
        L.append(" Nothing - every email that asked you something has an answer.")
    elif len(owed) > 12:
        L.append(f" ...and {len(owed) - 12} more")
    if hidden:
        L.append(f" ({hidden} promo, newsletter or muted email{'s' if hidden != 1 else ''} left out - !waiting all)")
    aw = awaiting_others(today)
    L += ["", "OTHERS OWE YOU (no answer after "
          f"{CHASE_WORKING_DAYS} working days)"]
    if aw.get("error") == "older bridge":
        v = aw.get("version")
        L.append(f" Your Google bridge is still answering as version {v or 'older than 5'}: the new code is saved but "
                 "not published. Deploy > Manage deployments > pencil > Version: New version > Deploy. Optional.")
    elif "error" in aw:
        L.append(f" Can't check: {aw['error']}")
    else:
        L += [f" - {a['subject'][:60]} -> {a['to']} | sent {a['days']} days ago" for a in aw["items"][:12]] \
            or [" Nothing outstanding."]
    if owed:
        L += ["", "Stop seeing a sender for good: !waiting mute example.com"]
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
    sub = sub.lower()
    if sub == "done":
        m = re.fullmatch(r"\s*[Ee]?(\d+)\s*", arg)
        if not m:
            return "Usage: !waiting done E12"
        return f"E{m.group(1)} won't be listed again." if mark_dealt_with(int(m.group(1))) else \
            f"E{m.group(1)} isn't on the list."
    if sub in ("mute", "unmute"):
        d = arg.strip().lower()
        if not re.fullmatch(r"@?[a-z0-9.-]+\.[a-z]{2,}", d):
            return f"Usage: !waiting {sub} example.com"
        if sub == "unmute":
            return f"{d} is listed again." if unmute(d.lstrip("@")) else f"{d} wasn't muted."
        n = mute(d)
        return f"Muted {d.lstrip('@')}: its emails won't show under YOU OWE A REPLY ({n} hidden now). " \
               f"Undo with !waiting unmute {d.lstrip('@')}"
    return waiting_text(everything=sub == "all")
