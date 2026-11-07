"""Admin desk: Claude-free inbox sorting, !workflow analysis and the !diary."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def _bridge(monkeypatch, a, mails, calls=None):
    from jarvis.adapters import google_bridge

    monkeypatch.setenv("JARVIS_GOOGLE_BRIDGE_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("JARVIS_GOOGLE_BRIDGE_KEY", "k")

    def call(action="ping", body=None, **params):
        if calls is not None:
            calls.append((action, body, params))
        if action == "mail":
            return {"messages": mails}
        if action == "calendar":
            return {"events": []}
        if action == "event":
            return {"ok": True, "id": "e1"}
        return {}
    monkeypatch.setattr(google_bridge, "call", call)


def test_inbox_is_sorted_by_rules_when_claude_has_no_credit(tmp_path, monkeypatch):
    import anthropic

    from jarvis.agents import assistant as a

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    mails = [{"id": "<1@x>", "date": "2026-11-06T08:00:00Z", "from": "FSCA <info@fsca.co.za>",
              "subject": "Regulatory return due Friday", "body": "Please submit the quarterly return by Friday."},
             {"id": "<2@x>", "date": "2026-11-06T09:00:00Z", "from": "News <noreply@fxweekly.com>",
              "subject": "Your weekly digest", "body": "Top stories. Unsubscribe here."}]
    _bridge(monkeypatch, a, mails)

    class NoCredit:
        class beta:
            class messages:
                @staticmethod
                def create(**kw):
                    raise anthropic.APIError("Your credit balance is too low to access the Anthropic API",
                                             request=None, body=None)

    r = a.triage_inbox(client=NoCredit())
    assert r["rules_only"] == "Claude credit used up"
    got = {x["subject"]: x for x in r["processed"]}
    assert got["Regulatory return due Friday"]["category"] == "regulator"
    assert got["Regulatory return due Friday"]["urgency"] == "high" and got["Regulatory return due Friday"]["task_ids"]
    assert got["Your weekly digest"]["category"] == "newsletter" and not got["Your weekly digest"]["needs_reply"]
    assert a.triage_inbox(client=NoCredit())["processed"] == []            # marked seen: not re-processed
    # no key at all: rules straight away, no error
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    mails[:] = [{"id": "<3@x>", "date": "", "from": "c@client.com", "subject": "Withdrawal",
                 "body": "Can you confirm my withdrawal?"}]
    r = a.triage_inbox()
    assert r["rules_only"] == "no Claude key" and r["processed"][0]["category"] == "payments"


def test_triage_job_speaks_up_when_every_email_fails(tmp_path, monkeypatch):
    import pytest

    from jarvis import scheduler
    from jarvis.core import registry

    class Fake:
        def triage(self):
            return {"processed": [{"subject": "x", "error": "APIError: overloaded"}]}
    monkeypatch.setattr(scheduler, "agents", lambda: {"assistant": Fake()})
    with pytest.raises(RuntimeError, match="failed for all 1"):
        scheduler.job_inbox_triage()
    Fake.triage = lambda self: {"error": "Google bridge refused the key", "processed": []}
    with pytest.raises(RuntimeError, match="refused the key"):
        scheduler.job_inbox_triage()
    assert registry  # imported for the agents() patch target


def test_workflow_numbers_and_streamline_ideas(tmp_path, monkeypatch):
    from jarvis.agents import assistant as a, workflow as w

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setattr(a, "calendar_events", lambda day=None, days=1: {"events": [
        {"title": "LP call", "all_day": False, "start": "2026-11-04 09:00", "end": "10:00", "location": ""},
        {"title": "Board", "all_day": False, "start": "2026-11-04 10:00", "end": "12:00", "location": ""}]})
    now = datetime.now(timezone.utc).isoformat()
    with a._db() as c:
        for i in range(8):
            c.execute("INSERT INTO email_seen VALUES (?,?,?,?,?,?,?,?,?)",
                      (f"n{i}", now, "fxweekly.com", "digest", "newsletter", "low", "[rules] x", 0, now))
        for i in range(6):
            c.execute("INSERT INTO email_seen VALUES (?,?,?,?,?,?,?,?,?)",
                      (f"c{i}", now, "client.com", "q", "client", "medium", "x", 1, now))
    for i in range(3):
        a.add_task(f"Send monthly KYC report {i}", None, "medium", source="email", source_ref=f"r{i}")
    text = w.workflow_text(7)
    assert "14 new" in text and "6 asked you something" in text and "fxweekly.com" in text
    assert "8 sorted by simple rules" in text
    assert "2 meetings, 3 hours" in text
    assert "recurring" in text and "!draft" in text


def test_diary_free_slots_and_adding_events(tmp_path, monkeypatch):
    from jarvis.agents import assistant as a, workflow as w

    tz = ZoneInfo("Etc/GMT-4")
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setattr(w, "LOCAL_TZ", tz)
    mon = date(2026, 11, 9)
    evs = [{"title": "LP call", "all_day": False, "start": "2026-11-09 09:00", "end": "10:00", "location": ""},
           {"title": "Board", "all_day": False, "start": "2026-11-09 13:00", "end": "17:30", "location": ""}]
    slots = w.free_slots(mon, evs)
    assert [(s.strftime("%H:%M"), e.strftime("%H:%M")) for s, e in slots] == [("08:00", "09:00"), ("10:00", "13:00")]
    assert w.free_slots(date(2026, 11, 7), evs) == []                      # Saturday
    monkeypatch.setattr(a, "calendar_events", lambda day=None, days=1: {"events": evs})
    a.add_task("Draft AML policy update", "2026-11-09", "high")
    text = w.diary_text(3, now=datetime(2026, 11, 8, 20, 0, tzinfo=tz))
    assert "Mon 09 Nov" in text and "09:00-10:00  LP call" in text and "DUE  #1 Draft AML" in text
    assert "Free: 08:00-09:00, 10:00-13:00" in text

    calls = []
    _bridge(monkeypatch, a, [], calls)
    out = w.command("!diary", "add Call IB Kenya | 2026-11-10 14:30 | 45m")
    assert out.startswith("Added to your calendar") and "45 min" in out
    action, body, _ = calls[-1]
    assert action == "event" and body["minutes"] == 45 and body["start"].startswith("2026-11-10T14:30")
    assert "Usage" in w.command("!diary", "add no date here")
    monkeypatch.setattr(w, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: datetime(2026, 11, 9, 7, 0, tzinfo=tz))}))
    out = w.command("!diary", "block 1 90m")
    assert "Booked 90 min" in out and "10:00" in out and calls[-1][1]["title"].startswith("Focus: Draft AML")


def test_old_bridge_script_gets_a_plain_answer(monkeypatch):
    from datetime import datetime as dt

    from jarvis.adapters import google_bridge
    from jarvis.agents import workflow as w

    monkeypatch.setenv("JARVIS_GOOGLE_BRIDGE_URL", "https://x")
    monkeypatch.setenv("JARVIS_GOOGLE_BRIDGE_KEY", "k")
    monkeypatch.setattr(google_bridge, "call", lambda *a, **k: {"error": "unknown action"})
    r = w.add_event("x", dt(2026, 11, 10, 9, tzinfo=timezone.utc) + timedelta(0))
    assert "version 4" in r["error"]


def test_help_lists_the_admin_commands():
    from donna import discord_bot

    assert "!diary" in discord_bot.HELP and "!workflow" in discord_bot.HELP
