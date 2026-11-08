"""D.O.N.N.A's admin desk, part 2: where SH's time and attention go, and keeping the diary.

Workflow  !workflow [days]   Plain numbers from what DONNA already sees, no Claude needed:
                             email load by type and sender, replies owed, tasks in vs done, how long tasks take,
                             overdue work, meeting hours - then "streamline" suggestions drawn from those numbers
                             (filters for bulk mail, report emails to auto-load, repeating tasks to make recurring,
                             focus time when meetings crowd the week).
Diary     !diary [days]      the week ahead day by day: meetings, tasks due, regulatory deadlines, free slots.
          !diary add <title> | YYYY-MM-DD HH:MM | 45m    put an event in SH's own Google Calendar
          !diary block 12 [90m]                           book focus time for task 12 in the next free slot
          Writing needs the Google bridge script version 4 (deploy/google-bridge.gs). Events are created
          without guests, so nobody gets an invitation; DONNA never edits or deletes an existing event.
Work hours for free slots: JARVIS_WORK_HOURS (local time, default 08:00-17:00, Monday-Friday).
"""
from __future__ import annotations

import os
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any

from jarvis.agents import assistant
from jarvis.core import audit
from jarvis.core.registry import Agent, register, tool

LOCAL_TZ = assistant.LOCAL_TZ
WORK_HOURS = os.getenv("JARVIS_WORK_HOURS", "08:00-17:00")
BULK = {"newsletter", "vendor_marketing", "spam"}
MIN_SLOT = 30  # minutes: shorter gaps aren't worth listing


def _work_window(day: date) -> tuple[datetime, datetime]:
    a, _, b = WORK_HOURS.partition("-")
    ha, ma = (int(x) for x in a.split(":"))
    hb, mb = (int(x) for x in b.split(":"))
    return (datetime(day.year, day.month, day.day, ha, ma, tzinfo=LOCAL_TZ),
            datetime(day.year, day.month, day.day, hb, mb, tzinfo=LOCAL_TZ))


def _parse(ev: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """Local start/end of a timed event from calendar_events(); None for all-day events."""
    if ev.get("all_day"):
        return None
    try:
        s = datetime.strptime(ev["start"], "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)
        e = datetime.strptime(f"{ev['start'][:10]} {ev['end']}", "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ) \
            if ev.get("end") else s + timedelta(minutes=30)
    except (KeyError, ValueError):
        return None
    return s, max(e, s)


def free_slots(day: date, events: list[dict[str, Any]], now: datetime | None = None) -> list[tuple[datetime, datetime]]:
    """Gaps of MIN_SLOT minutes or more inside work hours on a weekday, after `now`."""
    if day.weekday() >= 5:
        return []
    start, end = _work_window(day)
    if now:
        start = max(start, now)
    busy = sorted(p for p in (_parse(e) for e in events if e.get("start", "")[:10] == day.isoformat()) if p)
    out, cur = [], start
    for s, e in busy:
        if s > cur and (s - cur).total_seconds() >= MIN_SLOT * 60:
            out.append((cur, min(s, end)))
        cur = max(cur, e)
        if cur >= end:
            break
    if end > cur and (end - cur).total_seconds() >= MIN_SLOT * 60:
        out.append((cur, end))
    return [(s, e) for s, e in out if e > s]


# ------------------------------------------------------------------ workflow analysis
def analyse(days: int = 7, today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(LOCAL_TZ).date()
    since = datetime.combine(today - timedelta(days=days), datetime.min.time(), LOCAL_TZ).astimezone(timezone.utc)
    since_s = since.isoformat()
    until_s = datetime.combine(today + timedelta(days=1), datetime.min.time(), LOCAL_TZ).astimezone(
        timezone.utc).isoformat()           # the window ends with 'today', so an earlier window can be compared
    with assistant._db() as c:
        mails = [dict(r) for r in c.execute("SELECT * FROM email_seen WHERE processed_at >= ? AND processed_at < ?",
                                            (since_s, until_s))]
        created = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE created >= ? AND created < ?",
                                              (since_s, until_s))]
        closed = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE status = 'done' AND done_at >= ? "
                                             "AND done_at < ?", (since_s, until_s))]
        open_ = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE status = 'open'")]
    cats = Counter(m["category"] for m in mails)
    senders = Counter(m["sender_domain"] for m in mails if m["category"] not in BULK)
    bulk_senders = Counter(m["sender_domain"] for m in mails if m["category"] in BULK)
    owed = [m for m in mails if m["needs_reply"]]
    close_days = []
    for t in closed:
        try:
            close_days.append((datetime.fromisoformat(t["done_at"]) - datetime.fromisoformat(t["created"])).total_seconds() / 86400)
        except (TypeError, ValueError):
            pass
    overdue = [t for t in open_ if t["due"] and t["due"] < today.isoformat()]
    stale = [t for t in open_ if t["created"] and t["created"] < (since - timedelta(days=7)).isoformat()]
    # tasks that keep coming back with the same opening words are a process, not a one-off
    stems = Counter(" ".join(re.findall(r"[a-z]+", t["title"].lower())[:3]) for t in created + closed)
    repeats = [(s, n) for s, n in stems.most_common(5) if n >= 3 and s]
    cal = assistant.calendar_events(day=today - timedelta(days=days - 1), days=days)
    meetings = None
    if "error" not in cal:
        spans = [p for p in (_parse(e) for e in cal["events"]) if p]
        per_day = Counter(s.strftime("%a") for s, _ in spans)
        hours = sum((e - s).total_seconds() for s, e in spans) / 3600
        b2b = sum(1 for (s1, e1), (s2, _) in zip(sorted(spans), sorted(spans)[1:]) if 0 <= (s2 - e1).total_seconds() <= 300)
        meetings = {"count": len(spans), "hours": round(hours, 1), "busiest": per_day.most_common(1),
                    "back_to_back": b2b}
    return {"days": days, "emails": len(mails), "categories": dict(cats.most_common()),
            "top_senders": senders.most_common(5), "bulk_senders": bulk_senders.most_common(5),
            "high": sum(1 for m in mails if m["urgency"] == "high"), "replies_owed": len(owed),
            "rules_sorted": sum(1 for m in mails if str(m["summary"]).startswith("[rules]")),
            "tasks_created": len(created), "tasks_done": len(closed),
            "tasks_by_source": dict(Counter(t["source"] for t in created)),
            "median_days_to_close": round(median(close_days), 1) if close_days else None,
            "open": len(open_), "overdue": len(overdue), "stale": len(stale), "repeats": repeats,
            "meetings": meetings, "calendar_error": cal.get("error")}


def suggestions(a: dict[str, Any]) -> list[str]:
    """Streamlining ideas, each tied to a number above. Rules, not guesses."""
    out = []
    bulk = sum(a["categories"].get(k, 0) for k in BULK)
    if a["emails"] and bulk / a["emails"] >= 0.25:
        names = ", ".join(d for d, _ in a["bulk_senders"][:3])
        out.append(f"{bulk} of {a['emails']} emails were newsletters or sales mail. A Gmail filter that skips the "
                   f"inbox for {names or 'those senders'} would take them off your plate.")
    report_from = os.getenv("JARVIS_REPORT_SENDERS", "").lower()
    for dom, n in a["top_senders"]:
        if n >= 5 and dom and dom not in report_from:
            out.append(f"{dom} sent you {n} emails. If those are reports, tell me and I'll load them automatically "
                       f"(trusted report sender), so you never open them by hand.")
            break
    if a["replies_owed"] >= 5:
        out.append(f"{a['replies_owed']} emails asked you something. Use !draft <task id> and I'll write the reply "
                   f"into your Gmail Drafts for you to check and send.")
    for stem, n in a["repeats"][:2]:
        out.append(f"\"{stem} ...\" came up {n} times. If it's a regular duty, make it a recurring item "
                   f"(!oblig add <name> | YYYY-MM-DD | monthly) so it's planned, not chased.")
    if a["overdue"] >= 5 or a["stale"] >= 10:
        out.append(f"{a['overdue']} tasks are overdue and {a['stale']} have sat open for over a week. Ten minutes "
                   f"with !tasks to close or drop the dead ones makes the list trustworthy again.")
    m = a["meetings"]
    if m and m["hours"] >= 3 * a["days"]:
        out.append(f"{m['hours']}h in meetings over {a['days']} days. Book focus time for your top task with "
                   f"!diary block <task id>.")
    if m and m["back_to_back"] >= 3:
        out.append(f"{m['back_to_back']} meetings ran straight into the next one. Leave 10 minutes between them.")
    if a["tasks_created"] > a["tasks_done"] * 2 and a["tasks_created"] >= 6:
        out.append(f"{a['tasks_created']} tasks came in and {a['tasks_done']} were closed. The list is growing; "
                   f"pick three for tomorrow in !shutdown.")
    return out


def workflow_text(days: int = 7) -> str:
    a = analyse(days)
    L = [f"YOUR WORKFLOW - last {days} days"]
    if a["emails"]:
        cats = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in list(a["categories"].items())[:6])
        L += ["", "Email", f" {a['emails']} new ({a['emails'] / days:.0f} a day): {cats}",
              f" {a['high']} urgent, {a['replies_owed']} asked you something"]
        if a["top_senders"]:
            L.append(" Most mail from: " + ", ".join(f"{d} ({n})" for d, n in a["top_senders"][:4]))
        if a["rules_sorted"]:
            L.append(f" {a['rules_sorted']} sorted by simple rules because Claude was unavailable")
    else:
        L += ["", "Email", " Nothing sorted in this period. If your inbox had mail, check !status (email line)."]
    L += ["", "Tasks", f" {a['tasks_created']} new, {a['tasks_done']} done, {a['open']} open "
                      f"({a['overdue']} overdue, {a['stale']} open over a week)"]
    if a["tasks_by_source"]:
        L.append(" Where they came from: " + ", ".join(f"{k} {v}" for k, v in a["tasks_by_source"].items()))
    if a["median_days_to_close"] is not None:
        L.append(f" A task usually takes {a['median_days_to_close']:g} days to close")
    m = a["meetings"]
    L += ["", "Diary"]
    if m:
        busiest = f", busiest day {m['busiest'][0][0]}" if m["busiest"] else ""
        L.append(f" {m['count']} meetings, {m['hours']:g} hours{busiest}")
    else:
        L.append(f" Calendar not readable: {a['calendar_error']}")
    tips = suggestions(a)
    L += ["", "Streamline"] + ([f" - {t}" for t in tips] or [" Nothing stands out - your week ran clean."])
    return "\n".join(L)


# ------------------------------------------------------------------ diary
def diary_text(days: int = 7, now: datetime | None = None) -> str:
    from jarvis.agents import compliance

    now = now or datetime.now(LOCAL_TZ)
    today = now.date()
    cal = assistant.calendar_events(day=today, days=days)
    if "error" in cal:
        return f"Diary not readable: {cal['error']}"
    tasks = assistant.list_tasks()
    try:
        obligations = compliance.list_obligations(days)
    except Exception:  # noqa: BLE001 - the diary must show even if the compliance DB is unreadable
        obligations = []
    L = [f"DIARY - next {days} days"]
    overdue = [t for t in tasks if t["due"] and t["due"] < today.isoformat()]
    if overdue:
        L.append(f"Overdue: {len(overdue)} task(s) - " + "; ".join(f"#{t['id']} {t['title'][:40]}" for t in overdue[:3]))
    for i in range(days):
        d = today + timedelta(days=i)
        evs = [e for e in cal["events"] if e["start"][:10] == d.isoformat()]
        due = [t for t in tasks if t["due"] == d.isoformat()]
        obs = [o for o in obligations if o["due"] == d.isoformat()]
        slots = free_slots(d, cal["events"], now if i == 0 else None)
        if not (evs or due or obs or slots):
            continue
        L += ["", d.strftime("%a %d %b") + (" (today)" if i == 0 else "")]
        L += [f" {'all day' if e['all_day'] else e['start'][11:] + '-' + e['end']}  {e['title'][:60]}" for e in evs]
        L += [f" DUE  #{t['id']} {t['title'][:60]}" for t in due]
        L += [f" DEADLINE  {o['name'][:60]}" for o in obs]
        if slots:
            L.append(" Free: " + ", ".join(f"{s:%H:%M}-{e:%H:%M}" for s, e in slots[:4]))
    L += ["", "Add: !diary add <title> | YYYY-MM-DD HH:MM | 45m    Focus time: !diary block <task id>"]
    return "\n".join(L)


def add_event(title: str, start: datetime, minutes: int = 30, note: str = "") -> dict[str, Any]:
    """Create one event in SH's own calendar (no guests, so no invitations go out)."""
    from jarvis.adapters import google_bridge

    if not google_bridge.configured():
        return {"error": "adding to your calendar needs the Google bridge (vertex1-connect email)"}
    if not title.strip():
        return {"error": "the event needs a title"}
    minutes = max(5, min(int(minutes), 8 * 60))
    r = google_bridge.call("event", body={"title": title.strip()[:200], "start": start.isoformat(),
                                          "minutes": minutes, "note": note[:500]})
    if r.get("error") == "unknown action":
        return {"error": "your Google bridge script is an older copy. Paste the new google-bridge.gs (version 4) "
                         "and deploy a new version - steps in DONNA.md under 'Updating the bridge script'"}
    if "error" in r:
        return r
    audit.log("workflow", "diary_add", {"title": title[:80], "start": start.isoformat(), "minutes": minutes})
    return {"ok": True, "start": start, "minutes": minutes, "id": r.get("id", "")}


def _minutes(s: str, default: int) -> int:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(h|hr|hrs|hours?|m|min|mins|minutes?)?\s*", s or "")
    if not m:
        return default
    v = float(m.group(1))
    return int(v * 60) if (m.group(2) or "m").startswith("h") else int(v)


def command(cmd: str, rest: str) -> str | None:
    if cmd == "!workflow":
        n = re.search(r"\d+", rest)
        return workflow_text(max(1, min(int(n.group()), 90)) if n else 7)
    if cmd != "!diary":
        return None
    sub, _, arg = rest.strip().partition(" ")
    if sub.lower() == "add":
        parts = [p.strip() for p in arg.split("|")]
        when = next((p for p in parts[1:] if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}", p)), None)
        if not parts[0] or not when:
            return "Usage: !diary add <title> | YYYY-MM-DD HH:MM | 45m   (your local time; length optional, 30m default)"
        start = datetime.strptime(when.replace("T", " "), "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)
        length = next((_minutes(p, 0) for p in parts[1:] if p != when and _minutes(p, 0)), 30)
        r = add_event(parts[0], start, length)
        return f"Not added: {r['error']}" if "error" in r else \
            f"Added to your calendar: {parts[0]} on {start:%a %d %b %H:%M} for {length} min (no invitations sent)."
    if sub.lower() == "block":
        m = re.match(r"#?(\d+)\s*(.*)", arg)
        if not m:
            return "Usage: !diary block <task id> [90m]"
        task = next((t for t in assistant.list_tasks() if t["id"] == int(m.group(1))), None)
        if not task:
            return f"No open task #{m.group(1)}."
        length = _minutes(m.group(2), 60) if m.group(2) else 60
        now = datetime.now(LOCAL_TZ)
        cal = assistant.calendar_events(day=now.date(), days=7)
        if "error" in cal:
            return f"Not booked: {cal['error']}"
        for i in range(7):
            d = now.date() + timedelta(days=i)
            for s, e in free_slots(d, cal["events"], now if i == 0 else None):
                if (e - s).total_seconds() >= length * 60:
                    r = add_event(f"Focus: {task['title'][:150]}", s, length, f"DONNA task #{task['id']}")
                    return f"Not booked: {r['error']}" if "error" in r else \
                        f"Booked {length} min of focus time for #{task['id']} on {s:%a %d %b %H:%M}."
        return f"No free {length}-minute slot in work hours ({WORK_HOURS}) in the next 7 days."
    n = re.search(r"\d+", rest)
    return diary_text(max(1, min(int(n.group()), 14)) if n else 7)


@register
class WorkflowAgent(Agent):
    name = "workflow"
    description = "SH's workflow numbers (email load, tasks, meetings) with streamlining ideas, and the diary view."

    @tool()
    def workflow(self, days: int = 7) -> dict[str, Any]:
        """Email load, replies owed, tasks in/done/overdue, meeting hours over N days, plus streamlining ideas."""
        a = analyse(days)
        return {**a, "suggestions": suggestions(a)}

    @tool()
    def diary(self, days: int = 7) -> str:
        """The next N days: meetings, tasks due, regulatory deadlines and free slots."""
        return diary_text(days)
