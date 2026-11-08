"""PA desk: the alert policy, meetings booked for SH's Approve, mail read on the phone, the PA standard and the
weekly analysis."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "donna.db"))


def test_alert_levels_hours_dedupe_and_digest(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    from jarvis.agents import alerts

    sent = []
    monkeypatch.setattr(alerts, "_send", lambda kind, level, text, repeat=0: sent.append((level, text, repeat)) or {})
    monkeypatch.setattr(alerts, "HOURS", "07:00-21:00")
    day = datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)
    night = datetime(2026, 11, 9, 23, 30, tzinfo=timezone.utc)
    assert alerts.raise_alert("trading_risk", "Daily-loss limit hit", now=night) == "sent"      # critical: any hour
    assert alerts.raise_alert("urgent_email", "Email: FSC request", now=night) == "held"        # important, at night
    assert alerts.raise_alert("urgent_email", "Email: client complaint", now=day) == "sent"
    assert alerts.raise_alert("urgent_email", "Email: client complaint", now=day) == "duplicate"
    assert alerts.raise_alert("job_failed", "The watch job has been failing", now=day) == "held"  # notice
    assert alerts.raise_alert("info", "loaded", now=day) == "logged"
    assert [s[0] for s in sent] == ["CRITICAL", "IMPORTANT"]
    held = alerts.held_lines()
    assert held[0].startswith("SINCE LAST TIME") and any("FSC request" in x for x in held)
    assert any("watch job" in x for x in held) and alerts.held_lines() == []          # shown once
    # an unacknowledged critical repeats, twice at most; !ack stops it
    later = night + timedelta(minutes=31)
    assert alerts.repeat_criticals(later) == 1 and sent[-1][2] == 1
    assert alerts.repeat_criticals(later + timedelta(minutes=31)) == 1
    assert alerts.repeat_criticals(later + timedelta(minutes=62)) == 0
    assert "1 critical alert won't repeat" in alerts.command("!ack", "")
    assert "only critical" in alerts.command("!quiet", "2h")
    assert alerts.raise_alert("meeting_soon", "Call with IB in 15 min") == "held"     # quiet mode holds important
    assert "Quiet mode off" in alerts.command("!quiet", "off")
    assert "ALERT POLICY" in alerts.command("!alerts", "policy") and "PINGED YOU" in alerts.command("!alerts", "")


def test_regulator_and_unopened_urgent_mail_ping_opened_mail_does_not(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    from jarvis import scheduler
    from jarvis.agents import alerts

    raised = []
    monkeypatch.setattr(alerts, "raise_alert", lambda kind, text, key="", now=None: raised.append(kind) or "sent")
    monkeypatch.setattr(scheduler, "notify", lambda *a, **k: None)

    class A:
        def triage(self):
            return {"processed": [
                {"subject": "Licence query", "summary": "x", "urgency": "medium", "category": "regulator",
                 "from_domain": "fscmauritius.org", "task_ids": [1]},
                {"subject": "Urgent payment", "summary": "y", "urgency": "high", "category": "payments",
                 "from_domain": "bank.com", "task_ids": []},
                {"subject": "Urgent but read", "summary": "z", "urgency": "high", "category": "payments",
                 "from_domain": "bank.com", "task_ids": [], "opened": True},
                {"subject": "Hello", "summary": "w", "urgency": "low", "category": "other", "from_domain": "a.com"}]}
    monkeypatch.setattr(scheduler, "agents", lambda: {"assistant": A()})
    scheduler.job_inbox_triage()
    assert raised == ["regulator_email", "urgent_email"]


def test_mail_opened_on_the_phone_still_gets_its_tasks(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    monkeypatch.setenv("JARVIS_GOOGLE_BRIDGE_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("JARVIS_GOOGLE_BRIDGE_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from jarvis.adapters import google_bridge
    from jarvis.agents import assistant as a

    calls = []

    def bridge(action="ping", body=None, **p):
        calls.append((action, p))
        if action == "mail":
            return {"messages": [{"id": "<m1@x>", "date": "2026-11-08T08:00:00Z", "from": "Ops <ops@psp.com>",
                                  "subject": "Please confirm the settlement by Friday",
                                  "body": "Can you please confirm the settlement and send the report by Friday?",
                                  "unread": False, "answered": False},
                                 {"id": "<m2@x>", "date": "2026-11-08T08:00:00Z", "from": "jo@ib.com",
                                  "subject": "Can we talk?", "body": "Can you call me?", "unread": False,
                                  "answered": True}]}
        return {"error": "x"}
    monkeypatch.setattr(google_bridge, "call", bridge)
    r = a.triage_inbox()
    first = next(x for x in r["processed"] if x["subject"].startswith("Please confirm"))
    assert first.get("opened") and not first.get("handled")
    assert calls[0] == ("mail", {"days": a.MAIL_LOOKBACK_DAYS, "max": 40})
    with a._db() as c:
        assert c.execute("SELECT COUNT(*) FROM email_replied WHERE msg_id = '<m2@x>'").fetchone()[0] == 1


def test_meeting_is_held_and_only_sent_after_approve(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    monkeypatch.setenv("JARVIS_GOOGLE_BRIDGE_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("JARVIS_GOOGLE_BRIDGE_KEY", "k")
    monkeypatch.delenv("DONNA_TELEGRAM_CHAT_ID", raising=False)
    from donna import app, discord_bot as b
    from jarvis.adapters import google_bridge

    calls = []

    def bridge(action="ping", body=None, **p):
        calls.append((action, body))
        if action == "event":
            return {"ok": True, "id": "hold-1"}
        if action in ("invite", "unhold"):
            return {"ok": True}
        return {"events": []}
    monkeypatch.setattr(google_bridge, "call", bridge)
    when = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d") + " 10:00"
    out = b.handle(f"!meet jo@ib-partner.com | {when} | Intro call | 45m | About the IB plan")
    assert "Nothing is sent until you approve" in out
    assert calls[0][0] == "event" and calls[0][1]["title"].startswith("[HOLD")
    assert not any(c[0] == "invite" for c in calls)                      # nothing sent yet
    aid = b.approval_ids(out)[0]
    assert "WAITING FOR YOUR OK" in b.handle("!meet") and "jo@ib-partner.com" in b.handle("!meet")
    assert b.handle(f"!approve {aid}") == "Approved and done."
    inv = next(c[1] for c in calls if c[0] == "invite")
    assert inv["guests"] == ["jo@ib-partner.com"] and inv["minutes"] == 45 and inv["hold"] == "hold-1"
    assert "Not changed" in b.handle(f"!approve {aid}")                  # can't be sent twice
    # a rejected one sends nothing and frees the slot
    out2 = b.handle(f"!meet sam@lead.io | {when} | Demo")
    aid2 = b.approval_ids(out2)[0]
    assert b.handle(f"!reject {aid2}").startswith("Rejected")
    assert sum(c[0] == "invite" for c in calls) == 1 and calls[-1][0] == "unhold"
    assert "isn't an email address" in b.handle(f"!meet not-an-address | {when} | X")
    with app.db() as c:
        assert c.execute("SELECT status FROM actions WHERE action_id = ?", (aid,)).fetchone()[0] == "EXECUTED"


def test_pa_standard_and_weekly_analysis_run_on_a_fresh_server(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    from jarvis.agents import assistant as a, desk

    monkeypatch.setattr(a, "calendar_events", lambda day=None, days=1: {"events": [
        {"title": "Focus: AML review", "start": f"{day} 08:00", "end": "11:00"},
        {"title": "IB call", "start": f"{day} 12:00", "end": "13:00"}]})
    for i in range(4):
        a.add_task(f"Task {i}", "2020-01-01")
    t = desk.desk_text()
    assert "YOUR PA - IS IT DONE?" in t and "The task list is true: 4" in t and "Every email has a home: -" in t
    assert desk.judge("overdue", 4) == "AMBER" and desk.judge("overdue", 7) == "RED"
    assert desk.judge("sorted_fast", 96) == "GREEN" and desk.judge("focus_hours", None) == "NO DATA"
    assert next(r for r in desk.scorecard() if r["key"] == "focus_hours")["value"] == 3.0
    x = desk.analysis()
    text = "\n".join(desk.analysis_lines(x))
    for head in ("YOUR TRADES", "YOUR WORK FLOW", "YOUR AGENTS", "YOUR KPIs", "YOUR PA STANDARD"):
        assert head in text
    desk.save_week(x)
    assert desk.load_week(x["today"])["desk"]["overdue"] == 4
    assert json.dumps(desk.load_week(x["today"]))
