"""Follow-ups (!waiting) and meeting prep: plain rules, no Claude."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def _bridge(monkeypatch, answers):
    from jarvis.adapters import google_bridge

    monkeypatch.setenv("JARVIS_GOOGLE_BRIDGE_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("JARVIS_GOOGLE_BRIDGE_KEY", "k")
    monkeypatch.setattr(google_bridge, "call", lambda action="ping", body=None, **p: answers.get(action, {"ok": True, "version": 4}))


def _seen(c, msg_id, received, domain, subject, needs_reply):
    c.execute("INSERT INTO email_seen VALUES (?,?,?,?,?,?,?,?,?)",
              (msg_id, received, domain, subject, "client", "medium", "x", needs_reply,
               datetime.now(timezone.utc).isoformat()))


def test_working_days_skip_weekends():
    from jarvis.agents import followup as f

    assert f.working_days_between(date(2026, 11, 6), date(2026, 11, 9)) == 1     # Fri -> Mon
    assert f.working_days_between(date(2026, 11, 2), date(2026, 11, 5)) == 3


def test_replies_owed_oldest_first_and_cleared(tmp_path, monkeypatch):
    from jarvis.agents import assistant as a, followup as f

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.delenv("JARVIS_GOOGLE_BRIDGE_URL", raising=False)
    with a._db() as c:
        _seen(c, "<new@x>", "2026-11-06T09:00:00+00:00", "client.com", "Deposit question", 1)
        _seen(c, "<old@x>", "2026-10-30T09:00:00+00:00", "ib.co.ke", "IB agreement", 1)
        _seen(c, "<done@x>", "2026-11-01T09:00:00+00:00", "lp.com", "Margin call", 1)
        _seen(c, "<fyi@x>", "2026-11-05T09:00:00+00:00", "news.com", "Digest", 0)
    tid = a.add_task("Reply: Deposit question", source="email", source_ref="<new@x>")
    done = a.add_task("Reply: Margin call", source="email", source_ref="<done@x>")
    a.complete_task(done)                                    # its only task is done: dealt with
    owed = f.replies_owed(date(2026, 11, 9))
    assert [r["subject"] for r in owed] == ["IB agreement", "Deposit question"]
    assert owed[1]["open_task"] == tid and owed[0]["days"] == 10
    text = f.waiting_text(date(2026, 11, 9))
    assert "YOU OWE A REPLY (2)" in text and "OVER 5 DAYS" in text and f"!draft {tid}" in text
    assert "Can't check: needs the Google bridge" in text
    ref = owed[0]["ref"]
    assert f.command("!waiting", f"done E{ref}") == f"E{ref} won't be listed again."
    assert [r["subject"] for r in f.replies_owed(date(2026, 11, 9))] == ["Deposit question"]
    assert "Usage" in f.command("!waiting", "done x") and f.command("!other", "") is None


def test_reply_sent_from_phone_takes_email_off_the_list(tmp_path, monkeypatch):
    from jarvis.agents import assistant as a, followup as f

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    mail = {"id": "<q@x>", "date": "", "from": "c@client.com", "subject": "Withdrawal",
            "body": "Can you confirm my withdrawal?", "unread": True, "answered": False}
    _bridge(monkeypatch, {"mail": {"messages": [mail]}})
    a.triage_inbox()
    assert [r["subject"] for r in f.replies_owed()] == ["Withdrawal"]
    mail.update(unread=False, answered=True)                 # next run: you replied on your phone
    a.triage_inbox()
    assert f.replies_owed() == []


def test_others_owe_you_needs_bridge_v5(tmp_path, monkeypatch):
    from jarvis.agents import followup as f

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    _bridge(monkeypatch, {})                                  # v4 answers an unknown GET with its ping
    assert "Needs the Google bridge script version 5" in f.waiting_text(date(2026, 11, 9))
    _bridge(monkeypatch, {"sent": {"version": 5, "sent": [
        {"to": "Ops <ops@lp.com>", "subject": "Rebate statement", "date": "2026-11-02T10:00:00Z"},
        {"to": "kyc@client.com", "subject": "Missing ID", "date": "2026-11-06T10:00:00Z"}]}})
    r = f.awaiting_others(date(2026, 11, 9))
    assert [i["subject"] for i in r["items"]] == ["Rebate statement"]   # 1 working day isn't late yet
    assert r["items"][0]["to"] == "ops@lp.com"
    assert "Rebate statement -> ops@lp.com" in f.waiting_text(date(2026, 11, 9))


def test_meeting_reminder_carries_a_brief(tmp_path, monkeypatch):
    from jarvis.agents import assistant as a, mind

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    now = datetime.now(timezone.utc)
    with a._db() as c:
        _seen(c, "<1@x>", (now - timedelta(days=1)).isoformat(), "equiti.com", "Rebate statement October", 0)
        _seen(c, "<2@x>", (now - timedelta(days=2)).isoformat(), "fxweekly.com", "Kenya IB news", 0)
        _seen(c, "<3@x>", (now - timedelta(days=3)).isoformat(), "other.com", "Unrelated", 0)
    a.add_task("Send Kenya IB agreement")
    a.add_task("Unrelated task")
    when = datetime(2026, 11, 9, 9, 50, tzinfo=mind.LOCAL_TZ)
    ev = [{"title": "Call IB Kenya", "all_day": False, "start": "2026-11-09 10:00", "end": "10:30", "location": "",
           "guests": ["john@equiti.com", "me.too@gmail.com"]}]
    [msg] = mind.meeting_nudges(when, ev)
    assert msg.startswith("In 10 min: Call IB Kenya, 10:00-10:30")
    assert "With: john@equiti.com, me.too@gmail.com" in msg
    assert "Rebate statement October (equiti.com)" in msg and "Kenya IB news" in msg and "Unrelated" not in msg
    assert "Send Kenya IB agreement" in msg and "Unrelated task" not in msg


def test_bridge_v5_lists_unanswered_sent_mail_and_guests():
    from pathlib import Path

    gs = (Path(__file__).resolve().parent.parent / "deploy" / "google-bridge.gs").read_text()
    sent = gs.split("function sent_")[1].split("\n}\n")[0]
    assert "q.action === 'sent'" in gs and "in:sent" in sent and "getPlainBody" not in sent   # no message text
    assert "getGuestList()" in gs.split("function calendar_")[1].split("\n}\n")[0]


def test_console_and_help_know_waiting():
    from donna import console, discord_bot

    assert console.intent("what am I waiting on") == "!waiting"
    assert "!waiting" in discord_bot.HELP


def test_bridge_v5_gap_fixes():
    """Drafts DONNA saved never count as a reply; the organiser counts as a guest; declined meetings drop out."""
    from pathlib import Path

    gs = (Path(__file__).resolve().parent.parent / "deploy" / "google-bridge.gs").read_text()
    mail = gs.split("function mail_")[1].split("\n}\n")[0]
    sent = gs.split("function sent_")[1].split("\n}\n")[0]
    cal = gs.split("function calendar_")[1].split("\n}\n")[0]
    assert "isDraft()" in mail and "!n.isDraft()" in mail and "isDraft()" in sent
    assert "getCreators()" in cal and "getMyStatus()" in cal and "var VERSION = 5;" in gs


def test_declined_meetings_are_not_part_of_the_day(monkeypatch):
    from datetime import date

    from jarvis.agents import assistant as a

    _bridge(monkeypatch, {"calendar": {"version": 5, "events": [
        {"title": "Kept", "all_day": False, "start": "2026-11-09T09:00:00Z", "end": "2026-11-09T09:30:00Z",
         "location": "", "guests": ["x@lp.com"], "my_status": "YES"},
        {"title": "Declined", "all_day": False, "start": "2026-11-09T10:00:00Z", "end": "2026-11-09T10:30:00Z",
         "location": "", "guests": [], "my_status": "NO"}]}})
    monkeypatch.delenv("JARVIS_GCAL_ICS_URL", raising=False)
    ev = a.calendar_events(date(2026, 11, 9))["events"]
    assert [e["title"] for e in ev] == ["Kept"] and ev[0]["guests"] == ["x@lp.com"]
