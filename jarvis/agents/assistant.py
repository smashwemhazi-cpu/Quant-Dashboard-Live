"""D.O.N.N.A's secretary desk: inbox triage -> tasks, Google Calendar, and the personal "my day" brief.

Inbox  Google Workspace mailbox over IMAP, read-only (BODY.PEEK - nothing is marked read, nothing sent).
       JARVIS_IMAP_HOST=imap.gmail.com, JARVIS_IMAP_USER=<address>, JARVIS_IMAP_PASSWORD=<Google app password>
       Each new email is masked (long numbers, email addresses, phone numbers) before Claude classifies it
       and extracts tasks. Already-seen Message-IDs are skipped, so each email costs one call, once.
       Or, with no password at all, the Google bridge (deploy/google-bridge.gs, jarvis/adapters/google_bridge.py),
       which is used first when JARVIS_GOOGLE_BRIDGE_URL/_KEY are set.
Calendar  Google Calendar "Secret address in iCal format" in JARVIS_GCAL_ICS_URL (read-only), else the Google bridge.
Tasks  Stored in the state DB; created from emails or by hand; listed in the my-day brief.
Delivery  JARVIS_PERSONAL_TARGETS only (e.g. discord:personal) - never the company report channel.
"""
from __future__ import annotations

import email
import imaplib
import json
import os
import re
import sqlite3
import sys
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from jarvis.adapters import google_bridge
from jarvis.core import audit
from jarvis.core.config import settings
from jarvis.core.registry import Agent, register, tool

LOCAL_TZ = ZoneInfo(os.getenv("JARVIS_LOCAL_TZ", "UTC"))
# sorting and drafting email is routine work: the smallest, cheapest model is plenty and stretches the credit
TRIAGE_MODEL = os.getenv("JARVIS_TRIAGE_MODEL", "claude-haiku-5-5")
MAX_BODY_CHARS = 6000
CATEGORIES = ["client", "kyc_compliance", "regulator", "liquidity_provider", "payments", "internal",
              "vendor_marketing", "newsletter", "spam", "other"]
TRIAGE_PROMPT = """You triage email for an operations/dealing staff member at a retail FX/CFD brokerage (Vertex 1 Brokers).
Classify the email and extract only concrete actions the recipient must take. Rules:
- urgency "high" = client money, complaints, regulator/compliance deadlines, margin/stop-out, outages, or a same-day deadline.
- tasks: imperative titles under 90 characters that a person could tick off ("Send CU142 the utility-bill requirements").
  due: YYYY-MM-DD only when the email states or clearly implies a date (today is {today}); otherwise "".
- newsletters, marketing and notifications usually have no tasks.
- Numbers, addresses and phone numbers were masked as [number]/[email]/[phone]; never guess them.
Return only the JSON object."""
SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "urgency": {"type": "string", "enum": ["high", "medium", "low"]},
        "summary": {"type": "string"},
        "needs_reply": {"type": "boolean"},
        "tasks": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "due": {"type": "string"},
                           "priority": {"type": "string", "enum": ["high", "medium", "low"]}},
            "required": ["title", "due", "priority"], "additionalProperties": False}},
    },
    "required": ["category", "urgency", "summary", "needs_reply", "tasks"],
    "additionalProperties": False,
}


def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, detail TEXT,
            source TEXT, source_ref TEXT, priority TEXT, due TEXT, status TEXT DEFAULT 'open', created TEXT,
            done_at TEXT, UNIQUE (source_ref, title));
        CREATE TABLE IF NOT EXISTS email_seen (msg_id TEXT PRIMARY KEY, received TEXT, sender_domain TEXT,
            subject TEXT, category TEXT, urgency TEXT, summary TEXT, needs_reply INTEGER, processed_at TEXT);
    """)
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ tasks
def add_task(title: str, due: str | None = None, priority: str = "medium", detail: str = "",
             source: str = "manual", source_ref: str | None = None, conn: sqlite3.Connection | None = None) -> int | None:
    due = due if due and re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) else None
    with (conn or _db()) as c:
        cur = c.execute("INSERT OR IGNORE INTO tasks (title, detail, source, source_ref, priority, due, created) "
                        "VALUES (?,?,?,?,?,?,?)", (title[:200], detail[:1000], source, source_ref or f"manual:{_now()}",
                                                   priority, due, _now()))
        return cur.lastrowid if cur.rowcount else None


def list_tasks(include_done: bool = False) -> list[dict[str, Any]]:
    q = "SELECT * FROM tasks" + ("" if include_done else " WHERE status = 'open'")
    with _db() as c:
        rows = [dict(r) for r in c.execute(q + " ORDER BY due IS NULL, due, CASE priority WHEN 'high' THEN 0 "
                                               "WHEN 'medium' THEN 1 ELSE 2 END, id")]
    return rows


def complete_task(task_id: int) -> bool:
    with _db() as c:
        return c.execute("UPDATE tasks SET status = 'done', done_at = ? WHERE id = ? AND status = 'open'",
                         (_now(), task_id)).rowcount == 1


# ------------------------------------------------------------------ inbox
_MASKS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"\+?\d[\d ()-]{8,}\d"), "[number]"),            # phones, IDs, cards, bank accounts
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), "[number]"),  # IBAN
]


def mask(text: str) -> str:
    for rx, repl in _MASKS:
        text = rx.sub(repl, text)
    return text


def _text_body(msg: email.message.Message) -> str:
    html = ""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
        if part.get_content_type() == "text/plain":
            return text
        if part.get_content_type() == "text/html" and not html:
            html = re.sub(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", " ", text, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html)


def classify(subject: str, sender_domain: str, body: str, client: Any = None) -> dict[str, Any]:
    import anthropic

    client = client or anthropic.Anthropic()
    content = f"From: someone at {sender_domain}\nSubject: {subject}\n\n{body}"
    resp = client.beta.messages.create(
        model=TRIAGE_MODEL, max_tokens=4000,
        system=TRIAGE_PROMPT.format(today=date.today().isoformat()),
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        return {"category": "other", "urgency": "medium", "summary": "Declined by the model's safety system - read manually.",
                "needs_reply": False, "tasks": []}
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


# ------------------------------------------------------------------ triage without Claude
# When the Claude credit runs out (or the key is missing) the inbox is still sorted, by plain rules, so urgent
# mail, replies owed and the workflow numbers keep coming. Rule-sorted emails are marked "[rules]" in the summary.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("regulator", re.compile(r"\b(fsca|fsc|fca|cysec|asic|dfsa|vfsc|fic|regulator|regulatory return|"
                             r"supervisory|inspection|licen[cs]e condition)\b", re.I)),
    ("kyc_compliance", re.compile(r"\b(kyc|aml|cft|due diligence|proof of (address|residence|identity)|"
                                  r"sanction|pep\b|onboarding|verification|compliance|str\b|suspicious)", re.I)),
    ("payments", re.compile(r"\b(withdraw\w*|deposit\w*|payment|refund|chargeback|invoice|psp|bank transfer|"
                            r"remittance|payout)\b", re.I)),
    ("liquidity_provider", re.compile(r"\b(liquidity|lp\b|bridge|prime broker|margin call|swap rates?|"
                                      r"price feed|quotes?)\b", re.I)),
    ("client", re.compile(r"\b(client|customer|account (no|number)|my account|complain\w*|trading account|"
                          r"login|password reset)\b", re.I)),
    ("newsletter", re.compile(r"\b(newsletter|unsubscribe|webinar|digest|weekly update|view in browser)\b", re.I)),
    ("vendor_marketing", re.compile(r"\b(offer|discount|promo\w*|free trial|demo|partnership opportunity|"
                                    r"book a call)\b", re.I)),
]
_URGENT = re.compile(r"\b(urgent|asap|immediately|today|deadline|overdue|final notice|complain\w*|ombud\w*|"
                     r"stop[- ]?out|margin call|outage|down\b|fraud|breach|regulator|escalat\w*)", re.I)
_ASKS = re.compile(r"\?|\b(please|kindly|could you|can you|would you|let me know|confirm|advise|"
                   r"awaiting your|request)\b", re.I)
_NOREPLY = re.compile(r"no-?reply|do-?not-?reply|notifications?@|mailer", re.I)


def classify_rules(subject: str, sender_domain: str, body: str, sender: str = "") -> dict[str, Any]:
    """Claude-free triage: category, urgency and 'needs a reply' from keywords. Never raises."""
    text = f"{subject}\n{body[:3000]}"
    category = next((cat for cat, rx in _RULES if rx.search(text)), "internal" if not sender_domain else "other")
    bulk = category in ("newsletter", "vendor_marketing") or bool(_NOREPLY.search(sender))
    urgency = "high" if _URGENT.search(subject) or (category == "regulator" and not bulk) else (
        "low" if bulk else "medium")
    needs_reply = not bulk and bool(_ASKS.search(text))
    tasks = ([{"title": f"Reply: {subject[:80] or '(no subject)'}", "due": date.today().isoformat()
               if urgency == "high" else "", "priority": urgency}] if needs_reply and urgency != "low" else [])
    summary = re.sub(r"\s+", " ", body).strip()[:200] or subject
    return {"category": category, "urgency": urgency, "summary": f"[rules] {summary}", "needs_reply": needs_reply,
            "tasks": tasks}


def _claude_unusable(e: Exception) -> str | None:
    """Plain reason if Claude can't be used at all right now (credit, key), so the rest of the inbox
    should be sorted by rules instead of failing one email at a time."""
    msg = str(e).lower()
    if "credit balance" in msg:
        return "Claude credit used up"
    if "authentication" in msg or "x-api-key" in msg or "permission" in msg:
        return "Claude key refused"
    return None


def inbox_connected() -> bool:
    return google_bridge.configured() or bool(settings.imap_host and settings.imap_user and settings.imap_password)


def _imap_mail(c: sqlite3.Connection, max_messages: int) -> Iterator[dict[str, str]]:
    with imaplib.IMAP4_SSL(settings.imap_host) as m:
        m.login(settings.imap_user, settings.imap_password)
        m.select("INBOX", readonly=True)
        since = (date.today() - timedelta(days=2)).strftime("%d-%b-%Y")
        _, data = m.search(None, f'(UNSEEN SINCE "{since}")')
        for num in data[0].split()[-max_messages:]:
            _, hdr = m.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
            raw_id = email.message_from_bytes(hdr[0][1]).get("Message-ID", "").strip() or f"num:{num.decode()}"
            if c.execute("SELECT 1 FROM email_seen WHERE msg_id = ?", (raw_id,)).fetchone():
                continue
            _, msg_data = m.fetch(num, "(BODY.PEEK[])")
            msg = email.message_from_bytes(msg_data[0][1])
            try:
                received = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                received = _now()
            yield {"id": raw_id, "received": received, "from": msg.get("From", ""), "body": _text_body(msg),
                   "subject": str(make_header(decode_header(msg.get("Subject", ""))))}


def _bridge_mail(c: sqlite3.Connection, max_messages: int) -> Iterator[dict[str, str]]:
    r = google_bridge.call("mail", days=2, max=max_messages)
    if "error" in r:
        raise ConnectionError(r["error"])
    for m in r.get("messages", []):
        raw_id = str(m.get("id") or "").strip()
        if raw_id and m.get("answered"):  # you replied after DONNA sorted it: it no longer counts as a reply owed
            c.execute("UPDATE email_seen SET needs_reply = 0 WHERE msg_id = ? AND needs_reply = 1", (raw_id,))
        if raw_id and not c.execute("SELECT 1 FROM email_seen WHERE msg_id = ?", (raw_id,)).fetchone():
            # bridge v4 also sends read mail; one you've opened or answered is counted, not chased
            yield {"id": raw_id, "received": str(m.get("date") or _now()), "from": str(m.get("from", "")),
                   "subject": str(m.get("subject", "")), "body": str(m.get("body", "")),
                   "handled": m.get("unread") is False or bool(m.get("answered")),
                   "answered": bool(m.get("answered"))}


def triage_inbox(max_messages: int = 25, client: Any = None) -> dict[str, Any]:
    if not inbox_connected():
        return {"error": "NO DATA - inbox not connected (Google bridge, or JARVIS_IMAP_HOST / _USER / _PASSWORD)"}
    import anthropic

    claude_off = None if client is not None or os.getenv("ANTHROPIC_API_KEY") else "no Claude key"
    done: list[dict[str, Any]] = []
    source = _bridge_mail if google_bridge.configured() else _imap_mail
    with _db() as c:
        try:
            for m in source(c, max_messages):
                subject = mask(m["subject"])
                domain = parseaddr(m["from"])[1].rpartition("@")[2].lower() or "unknown"
                body = mask(m["body"])[:MAX_BODY_CHARS]
                r = None
                if not claude_off:
                    try:
                        r = classify(subject, domain, body, client)
                    except (anthropic.APIError, json.JSONDecodeError, StopIteration) as e:
                        claude_off = _claude_unusable(e)
                        if not claude_off:
                            done.append({"subject": subject, "error": f"{type(e).__name__}: {e}"})
                            continue  # not marked seen - retried next run
                if r is None:
                    r = classify_rules(subject, domain, body, m["from"])
                if m.get("answered"):
                    r["needs_reply"] = False
                c.execute("INSERT INTO email_seen VALUES (?,?,?,?,?,?,?,?,?)",
                          (m["id"], m["received"], domain, subject[:300], r["category"], r["urgency"],
                           r["summary"][:500], int(r["needs_reply"]), _now()))
                ids = [] if m.get("handled") else [
                    add_task(t["title"], t["due"], t["priority"], f"From email: {subject}", "email", m["id"], conn=c)
                    for t in r["tasks"]]
                entry = {"subject": subject, "from_domain": domain, **r, "task_ids": [i for i in ids if i],
                         **({"handled": True} if m.get("handled") else {})}
                try:
                    from jarvis.agents import complaints

                    cid = complaints.from_email(subject, r["summary"], r["category"], domain, m["id"], m["received"])
                    if cid:
                        entry["complaint_id"] = cid
                except Exception as e:  # noqa: BLE001 - the register must never stop triage
                    entry["complaint_error"] = f"{type(e).__name__}: {e}"
                done.append(entry)
        except ConnectionError as e:
            return {"error": str(e), "processed": done}
    audit.log("assistant", "inbox_triage", {"processed": len(done), "rules_only": claude_off})
    return {"processed": done, **({"rules_only": claude_off} if claude_off else {})}


DRAFT_PROMPT = """You draft email replies for SH, Compliance & Risk Officer at Vertex 1 Brokers (a retail FX/CFD broker).
Write only the reply body, ready to paste: plain text, no subject line, no markdown, signed "Kind regards,\nSH".
- Short, polite and professional. Answer what was asked; if something must be checked first, say it will be confirmed.
- Never promise payments, withdrawals, bonuses, refunds or deadlines that the email does not already confirm.
- Never give investment advice or trading recommendations.
- Numbers, addresses and phone numbers were masked as [number]/[email]/[phone]; keep the placeholders, never guess them.
- Follow SH's note if there is one."""


def draft_reply(task_id: int, note: str = "", client: Any = None) -> dict[str, Any]:
    """Write a reply to the email behind a task and save it in Gmail Drafts. Never sends."""
    with _db() as c:
        t = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not t:
        return {"error": f"no task #{task_id}"}
    if t["source"] != "email" or not t["source_ref"]:
        return {"error": f"task #{task_id} didn't come from an email, so there is nothing to reply to"}
    if not google_bridge.configured():
        return {"error": "drafts need the Google bridge (vertex1-connect email, questions 6a and 6b)"}
    if client is None and not os.getenv("ANTHROPIC_API_KEY"):
        return {"error": "ANTHROPIC_API_KEY not set - drafting needs the Claude API"}
    m = google_bridge.call("message", id=t["source_ref"])
    if "error" in m:
        return {"error": m["error"] if m["error"] != "unknown action" else "the Google bridge script is an older copy"}
    if "body" not in m:
        return {"error": "the Google bridge script is an older copy - paste the new google-bridge.gs and deploy a new version"}
    import anthropic

    client = client or anthropic.Anthropic()
    content = (f"Task: {t['title']}\nSH's note: {note or '(none)'}\n\nEmail from someone at "
               f"{parseaddr(str(m.get('from', '')))[1].rpartition('@')[2] or 'unknown'}\nSubject: {mask(str(m.get('subject', '')))}"
               f"\n\n{mask(str(m.get('body', '')))[:MAX_BODY_CHARS]}")
    resp = client.beta.messages.create(
        model=TRIAGE_MODEL, max_tokens=2000, system=DRAFT_PROMPT, messages=[{"role": "user", "content": content}],
        output_config={"effort": "low"}, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    if resp.stop_reason == "refusal":
        return {"error": "the model declined to draft this one - reply manually"}
    text = next((b.text for b in resp.content if b.type == "text"), "").strip()
    if not text:
        return {"error": "the draft came back empty - try again"}
    d = google_bridge.call("draft", body={"id": t["source_ref"], "text": text})
    if "error" in d:
        return {"error": d["error"] if d["error"] != "unknown action" else "the Google bridge script is an older copy"}
    audit.log("assistant", "draft_reply", {"task": task_id, "chars": len(text)})
    return {"draft": text, "saved": True}


def inbox_status(hours: int = 24) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM email_seen WHERE processed_at >= ? ORDER BY "
                                           "CASE urgency WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END", (since,))]
    return {"emails": len(rows), "needs_reply": [r for r in rows if r["needs_reply"]],
            "high": [r for r in rows if r["urgency"] == "high"]}


# ------------------------------------------------------------------ calendar
def calendar_events(day: date | None = None, days: int = 1) -> dict[str, Any]:
    url = os.getenv("JARVIS_GCAL_ICS_URL", "").strip().strip("\"'")
    if not url and google_bridge.configured():
        return _bridge_calendar(day, days)
    if not url:
        return {"error": "NO DATA - calendar not connected (JARVIS_GCAL_ICS_URL)"}
    if not url.startswith("https://"):
        return {"error": "JARVIS_GCAL_ICS_URL is not an https:// link - run vertex1-connect again"}
    import icalendar
    import recurring_ical_events

    day = day or datetime.now(LOCAL_TZ).date()
    start = datetime.combine(day, datetime.min.time(), LOCAL_TZ)
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        return {"error": f"calendar unreachable ({type(e).__name__})"}
    cal = icalendar.Calendar.from_ical(r.content)
    out = []
    for ev in recurring_ical_events.of(cal).between(start, start + timedelta(days=days)):
        s, e = ev.get("DTSTART").dt, ev.get("DTEND").dt if ev.get("DTEND") else None
        all_day = not isinstance(s, datetime)
        to_local = lambda x: x.astimezone(LOCAL_TZ) if isinstance(x, datetime) and x.tzinfo else x  # noqa: E731
        out.append({"title": str(ev.get("SUMMARY", "(no title)")), "all_day": all_day,
                    "start": to_local(s).strftime("%Y-%m-%d") if all_day else to_local(s).strftime("%Y-%m-%d %H:%M"),
                    "end": "" if all_day or e is None else to_local(e).strftime("%H:%M"),
                    "location": str(ev.get("LOCATION", "")), "guests": _ics_guests(ev.get("ATTENDEE"))})
    return {"events": sorted(out, key=lambda x: (not x["all_day"], x["start"]))}


def _ics_guests(att: Any) -> list[str]:
    """ATTENDEE is one value or a list of 'mailto:' addresses."""
    items = att if isinstance(att, list) else [att] if att else []
    return [str(a).split(":", 1)[-1] for a in items if "@" in str(a)]


def _bridge_calendar(day: date | None, days: int) -> dict[str, Any]:
    day = day or datetime.now(LOCAL_TZ).date()
    start = datetime.combine(day, datetime.min.time(), LOCAL_TZ)
    r = google_bridge.call("calendar", start=start.isoformat(), days=days)
    if "error" in r:
        return {"error": r["error"]}
    out = []
    for ev in r.get("events", []):
        try:
            s = datetime.fromisoformat(str(ev["start"]).replace("Z", "+00:00")).astimezone(LOCAL_TZ)
            e = datetime.fromisoformat(str(ev["end"]).replace("Z", "+00:00")).astimezone(LOCAL_TZ)
        except (KeyError, ValueError):
            continue
        if str(ev.get("my_status") or "").upper() == "NO":  # you declined it (bridge v5): not part of your day
            continue
        all_day = bool(ev.get("all_day"))
        out.append({"title": str(ev.get("title") or "(no title)"), "all_day": all_day,
                    "start": (ev.get("day") or s.strftime("%Y-%m-%d")) if all_day else s.strftime("%Y-%m-%d %H:%M"),
                    "end": "" if all_day else e.strftime("%H:%M"), "location": str(ev.get("location") or ""),
                    "guests": [str(g) for g in ev.get("guests") or []]})  # bridge v5 and later
    return {"events": sorted(out, key=lambda x: (not x["all_day"], x["start"]))}


# ------------------------------------------------------------------ my day
def my_day_text() -> str:
    from jarvis.agents import personal

    now = datetime.now(LOCAL_TZ)
    today = now.date().isoformat()
    L = [f"D.O.N.N.A | MY DAY | {now:%a %d %b %Y}", "-" * 34, "CALENDAR"]
    cal = calendar_events()
    if "error" in cal:
        L.append(cal["error"])
    else:
        L += [f" {'all day' if e['all_day'] else e['start'][11:] + '-' + e['end']}  {e['title']}"
              + (f" ({e['location']})" if e["location"] else "") for e in cal["events"]] or [" Nothing scheduled"]
    tasks = list_tasks()
    overdue = [t for t in tasks if t["due"] and t["due"] < today]
    due_today = [t for t in tasks if t["due"] == today]
    rest = [t for t in tasks if t not in overdue and t not in due_today]
    L += ["", f"TASKS ({len(tasks)} open)"]
    fmt = lambda t: f" #{t['id']} [{t['priority']}] {t['title']}" + (f" (due {t['due']})" if t["due"] else "")  # noqa: E731
    if overdue:
        L += ["Overdue:"] + [fmt(t) for t in overdue]
    if due_today:
        L += ["Today:"] + [fmt(t) for t in due_today]
    if rest:
        L += ["Next:"] + [fmt(t) for t in rest[:8]] + ([f" ... and {len(rest) - 8} more"] if len(rest) > 8 else [])
    if not tasks:
        L.append(" No open tasks")
    ib = inbox_status()
    L += ["", f"INBOX (last 24h: {ib['emails']} analysed)"]
    if not inbox_connected():
        L.append(" NO DATA - inbox not connected")
    L += [f" !! {r['subject'][:80]} - {r['summary'][:120]}" for r in ib["high"][:5]]
    L += [f" Reply needed: {r['subject'][:80]} ({r['sender_domain']})" for r in ib["needs_reply"]
          if r not in ib["high"]][:5]
    L += [""] + personal.report_lines()
    try:
        from jarvis.agents import mind

        L = L[:2] + mind.focus_lines() + [""] + L[2:]  # focus first, right under the header
    except Exception as e:  # the brief must still go out if the mind DB is unreadable
        L += ["", f"FOCUS: unavailable ({type(e).__name__})"]
    return "\n".join(L)


@register
class AssistantAgent(Agent):
    name = "assistant"
    description = "Inbox triage into tasks, Google Calendar, task list and the personal my-day brief."

    @tool()
    def tasks(self, include_done: bool = False) -> list[dict[str, Any]]:
        """Open tasks (id, title, priority, due, source), overdue first."""
        return list_tasks(include_done)

    @tool()
    def create_task(self, title: str, due: str = "", priority: str = "medium", detail: str = "") -> dict[str, Any]:
        """Add a task to D.O.N.N.A's list. due as YYYY-MM-DD or blank; priority high/medium/low."""
        return {"task_id": add_task(title, due or None, priority, detail)}

    @tool()
    def finish_task(self, task_id: int) -> dict[str, Any]:
        """Mark a task done."""
        return {"done": complete_task(task_id)}

    @tool()
    def triage(self, max_messages: int = 25) -> dict[str, Any]:
        """Read new unread emails (read-only), classify them, and turn required actions into tasks."""
        return triage_inbox(max_messages)

    @tool()
    def calendar(self, days: int = 1) -> dict[str, Any]:
        """Google Calendar events from today for N days (local time)."""
        return calendar_events(days=days)

    @tool()
    def my_day(self, send: bool = False) -> dict[str, Any]:
        """Personal brief: calendar, tasks, inbox items needing action, personal trading and bots.
        send=True delivers to JARVIS_PERSONAL_TARGETS (never the company channel)."""
        text = my_day_text()
        res: dict[str, Any] = {"text": text}
        if send:
            from jarvis.agents.personal import PERSONAL_TARGETS

            if not PERSONAL_TARGETS:
                res["delivery"] = {"error": "JARVIS_PERSONAL_TARGETS not set"}
            else:
                from donna.channels import broadcast

                res["delivery"] = broadcast(text, PERSONAL_TARGETS, title="D.O.N.N.A my day")
        audit.log("assistant", "my_day", {"chars": len(text), "delivery": res.get("delivery")})
        return res


if __name__ == "__main__":
    # python -m jarvis.agents.assistant tasks | done <id> | add "<title>" [YYYY-MM-DD] | triage | myday
    cmd = sys.argv[1] if len(sys.argv) > 1 else "myday"
    if cmd == "tasks":
        for t in list_tasks():
            print(f"#{t['id']:<4} [{t['priority']:<6}] {t['due'] or '':<10}  {t['title']}")
    elif cmd == "done" and len(sys.argv) > 2:
        print("done" if complete_task(int(sys.argv[2])) else "no open task with that id")
    elif cmd == "add" and len(sys.argv) > 2:
        print("added task", add_task(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    elif cmd == "triage":
        for r in triage_inbox().get("processed", []):
            print(r.get("urgency", "ERR"), "|", r.get("subject"), "|", r.get("summary", r.get("error")))
    else:
        print(my_day_text())
