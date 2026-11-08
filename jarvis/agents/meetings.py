"""D.O.N.N.A books meetings: she prepares the invitation, holds the slot in your diary, and sends it only when
you tap Approve on that exact invitation (Discord, phone or console).

!meet <email or email ref> | <when> | <title> [| 45m] [| note for the invitation]
    who:   an address (jo@ib-partner.com), or E14 = the sender of email 14 (after triage)
    when:  YYYY-MM-DD HH:MM (your time), or "next" / "tomorrow" / a weekday = the first free slot then
!meet               invitations waiting for your OK
After !meet: a "[HOLD - awaiting your OK]" placeholder sits in your calendar so nothing else takes the slot.
Approve = Google Calendar sends the invitation and the hold is replaced by the real meeting.
Reject  = nothing is sent and the hold is removed.
Needs the Google bridge version 7. Nothing is ever sent without your Approve.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from email.utils import parseaddr
from typing import Any

from jarvis.agents import assistant
from jarvis.core import audit

LOCAL_TZ = assistant.LOCAL_TZ
EMAIL = re.compile(r"^[^@\s,<>]+@[^@\s,<>]+\.[A-Za-z]{2,}$")
HOLD_PREFIX = "[HOLD - awaiting your OK] "
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def guest_of(who: str) -> tuple[str, str]:
    """(address, where it came from) or ("", reason)."""
    who = who.strip()
    m = re.fullmatch(r"[Ee]#?(\d+)", who)
    if m:
        from jarvis.adapters import google_bridge

        with assistant._db() as c:
            row = assistant._email_row(c, ref=int(m.group(1)))
        if not row:
            return "", f"there's no email {m.group(1)} (see !today or !waiting)"
        r = google_bridge.call("message", id=row["msg_id"]) if google_bridge.configured() else {"error": "no bridge"}
        addr = parseaddr(str(r.get("from", "")))[1]
        if "error" in r or not EMAIL.match(addr):
            return "", f"I couldn't read the sender of email {m.group(1)} ({r.get('error', 'no address')})"
        return addr.lower(), f"sender of email {m.group(1)}: {row['subject'][:60]}"
    addr = parseaddr(who)[1] or who
    return (addr.lower(), "") if EMAIL.match(addr) else ("", f"'{who}' isn't an email address or an email ref like E14")


def pick_time(when: str, minutes: int, now: datetime | None = None) -> datetime | None:
    """An exact time, or the first free work-hours slot on the day named (next = from now on)."""
    from jarvis.agents import workflow

    now = now or datetime.now(LOCAL_TZ)
    w = when.strip().lower()
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ t](\d{1,2}):(\d{2})", w)
    if m:
        return datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}", "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)
    if w in ("", "next", "asap", "soon"):
        first, span = now.date(), 7
    elif w == "today":
        first, span = now.date(), 1
    elif w == "tomorrow":
        first, span = now.date() + timedelta(days=1), 1
    elif w[:3] in [d[:3] for d in DAYS]:
        i = [d[:3] for d in DAYS].index(w[:3])
        first, span = now.date() + timedelta(days=(i - now.weekday() - 1) % 7 + 1), 1
    else:
        return None
    cal = assistant.calendar_events(day=first, days=span)
    events = cal.get("events", []) if "error" not in cal else []
    for k in range(span):
        d = first + timedelta(days=k)
        for s, e in workflow.free_slots(d, events, now if d == now.date() else None):
            if (e - s).total_seconds() >= minutes * 60:
                return s
    return None


def propose(guest: str, start: datetime, minutes: int, title: str, note: str = "", origin: str = "") -> dict[str, Any]:
    """Hold the slot and stage the invitation for SH's Approve. Sends nothing."""
    from donna import app
    from jarvis.agents import workflow

    if start <= datetime.now(LOCAL_TZ):
        return {"error": "that time has already passed"}
    hold = workflow.add_event(HOLD_PREFIX + title, start, minutes, f"Invitation to {guest} waiting for your OK")
    if "error" in hold:
        return hold
    payload = {"title": title, "start": start.isoformat(), "minutes": minutes, "guests": [guest],
               "note": note, "hold": hold.get("id", "")}
    preview = (f"Meeting invitation\nTo: {guest}{' (' + origin + ')' if origin else ''}\nTitle: {title}\n"
               f"When: {start:%a %d %b %Y %H:%M} your time, {minutes} min" + (f"\nNote: {note}" if note else ""))
    app.init_db()
    action_id = app.stage_action("donna", "SEND_INVITE", guest, payload, preview)
    audit.log("meetings", "proposed", {"action_id": action_id, "guest_domain": guest.rpartition("@")[2],
                                       "start": start.isoformat()})
    _ping_phone(action_id, preview)
    return {"ok": True, "action_id": action_id, "preview": preview}


def _ping_phone(action_id: str, preview: str) -> None:
    """The same Approve / Reject buttons on the phone (if the phone is paired)."""
    import os

    chat = os.getenv("DONNA_TELEGRAM_CHAT_ID", "").strip()
    if not (chat and os.getenv("DONNA_TELEGRAM_BOT_TOKEN")):
        return
    from donna import phone

    phone.call("sendMessage", chat_id=chat, text="Waiting for your OK:\n\n" + preview,
               reply_markup=phone.approval_keyboard(action_id))


def send(row: Any) -> str:
    """Executor (donna.app): runs only after Approve. Google Calendar sends the invitation."""
    from jarvis.adapters import google_bridge

    p = json.loads(row["payload"])
    r = google_bridge.call("invite", body={k: p.get(k) for k in ("title", "start", "minutes", "guests", "note", "hold")})
    if r.get("error") == "unknown action":
        raise RuntimeError("the Google bridge needs version 7 to send invitations (paste the new google-bridge.gs)")
    if "error" in r:
        raise RuntimeError(r["error"])
    return f"invitation sent to {', '.join(p['guests'])}"


def cancel(row: Any) -> None:
    """On Reject: remove the hold so the slot is free again."""
    from jarvis.adapters import google_bridge

    hold = json.loads(row["payload"]).get("hold")
    if hold:
        google_bridge.call("unhold", body={"hold": hold})


def pending() -> list[dict[str, Any]]:
    from donna import app

    app.init_db()
    with app.db() as c:
        return [dict(r) for r in c.execute("SELECT action_id, preview_text, created_at FROM actions WHERE "
                                           "action_type = 'SEND_INVITE' AND status = 'PENDING' AND expires_at > ? "
                                           "ORDER BY created_at", (app._now().isoformat(),))]


def command(cmd: str, rest: str) -> str | None:
    if cmd != "!meet":
        return None
    if not rest.strip():
        p = pending()
        if not p:
            return ("No invitations waiting.\nBook one: !meet jo@partner.com | tomorrow | Intro call | 30m\n"
                    "or after triage: !meet E14 | 2026-11-12 10:00 | Account opening | 45m")
        return "WAITING FOR YOUR OK\n\n" + "\n\n".join(f"{x['preview_text']}\n Approve: !approve {x['action_id']}"
                                                         for x in p)
    parts = [x.strip() for x in rest.split("|")]
    if len(parts) < 3 or not parts[2]:
        return "Usage: !meet <email or E14> | <YYYY-MM-DD HH:MM, tomorrow, friday or next> | <title> [| 45m] [| note]"
    guest, origin = guest_of(parts[0])
    if not guest:
        return f"Not booked: {origin}."
    from jarvis.agents.workflow import _minutes

    minutes = next((_minutes(x, 0) for x in parts[3:] if _minutes(x, 0)), 30)
    note = next((x for x in parts[3:] if not _minutes(x, 0)), "")
    start = pick_time(parts[1], minutes)
    if start is None:
        return f"Not booked: no free {minutes}-minute slot for '{parts[1]}'. Give a time: YYYY-MM-DD HH:MM"
    r = propose(guest, start, minutes, parts[2][:150], note, origin)
    if "error" in r:
        return f"Not booked: {r['error']}"
    return (f"{r['preview']}\n\nThe slot is held in your calendar. Nothing is sent until you approve.\n"
            f"Approve: !approve {r['action_id']}   Reject: !reject {r['action_id']}")
