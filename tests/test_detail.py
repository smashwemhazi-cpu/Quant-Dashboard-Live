"""!task 4 / !email 14: what a task or email is about, the email itself, and the next step."""
from __future__ import annotations

from datetime import datetime, timezone


def _seen(c, msg_id, domain, subject, category, urgency, summary, needs_reply):
    c.execute("INSERT INTO email_seen VALUES (?,?,?,?,?,?,?,?,?)",
              (msg_id, "2026-11-06T09:00:00+00:00", domain, subject, category, urgency, summary, needs_reply,
               datetime.now(timezone.utc).isoformat()))


def test_task_and_email_detail(tmp_path, monkeypatch):
    from donna import discord_bot as b
    from jarvis.adapters import google_bridge
    from jarvis.agents import assistant as a

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("JARVIS_GOOGLE_BRIDGE_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("JARVIS_GOOGLE_BRIDGE_KEY", "k")
    monkeypatch.setattr(google_bridge, "call", lambda action="ping", body=None, **p: {
        "from": "Ops <ops@deusxpay.com>", "subject": "Payment Status Update",
        "body": "Dear Simba,\n\nCan you confirm the settlement of 12500 USD to account 1234567890?"} if action == "message"
        else {"error": "x"})
    with a._db() as c:
        _seen(c, "<pay@deusxpay.com>", "deusxpay.com", "Payment Status Update", "payments", "medium",
              "[rules] Can you confirm the settlement", 1)
        _seen(c, "<promo@x>", "mail.ctrader.com", "See Flex Renko in action", "other", "medium", "[rules] New", 1)
    tid = a.add_task("Reply: Payment Status Update", source="email", source_ref="<pay@deusxpay.com>")
    pid = a.add_task("Reply: See Flex Renko in action", source="email", source_ref="<promo@x>")
    out = b.handle(f"!task {tid}")
    assert out.startswith(f"Task {tid}: Reply: Payment Status Update")
    assert "Where it came from: an email (E1)" in out and "From: deusxpay.com" in out
    assert "settlement of 12500 USD to account [number]?" in out and "1234567890" not in out   # masked
    assert "rfc822msgid%3Apay%40deusxpay.com" in out
    assert "Check the payment in the back office" in out and f"!draft {tid}" in out
    promo = b.handle(f"!task {pid}")
    assert "no answer needed" in promo and f"!done {pid}" in promo and "!waiting mute mail.ctrader.com" in promo
    e = b.handle("!email E1")
    assert e.startswith("EMAIL E1") and f"Task from it: Task {tid}" in e and "NEXT STEP" in e
    a.complete_task(tid)
    assert f"!undo {tid}" in b.handle(f"!task {tid}")
    assert "There's no task 99" in b.handle("!task 99") and "There's no email E99" in b.handle("!email 99")
    assert "Usage" in b.handle("!task x") and f"!task {pid}" in b.handle("!tasks")


def test_manual_task_and_console_clicks():
    from pathlib import Path

    from jarvis.agents import assistant as a

    assert "Do it, then tick it: !done 3" == a.next_step(None, {"id": 3, "status": "open"})
    html = (Path(__file__).resolve().parent.parent / "donna" / "console.html").read_text()
    assert 'data-open="!task ${t.id}"' in html and 'data-open="!email ${Number(x.ref) || 0}"' in html
    assert "Open in Gmail" in html
