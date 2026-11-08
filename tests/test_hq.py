"""DONNA's mandate and the agent coordination board."""
from __future__ import annotations


def test_every_scheduled_job_has_a_duty():
    from jarvis import scheduler
    from jarvis.agents import hq

    jobs = {n for n, *_ in scheduler.JOBS}
    used = [j for d in hq.DUTIES for j in d["jobs"]]
    assert set(used) == jobs and len(used) == len(set(used))   # each job in exactly one duty


def test_mandate_answers_in_full_and_reaches_the_chat_persona(tmp_path, monkeypatch):
    from jarvis.agents import hq, mind

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    text = hq.mandate_text()
    for d in hq.DUTIES:
        assert d["name"] in text and d["proof"] in text
    assert "Never places, changes or closes a trade" in text and "!agents" in text
    p = mind.persona()
    assert "Your mandate:" in p and all(d["name"] in p for d in hq.DUTIES)
    from donna import discord_bot

    assert discord_bot.handle("!mandate") == text and "!hq" in discord_bot.HELP


def test_agents_board_shows_ok_waiting_failing_and_run_now(tmp_path, monkeypatch):
    import time

    from jarvis import scheduler
    from jarvis.agents import hq

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setattr(scheduler, "ALERT_TARGETS", [])
    monkeypatch.setattr(scheduler, "market_closed", lambda name, now=None: False)   # a weekday, market open
    now = time.time()
    scheduler.save_last("daily_wrap", now - 3600)
    scheduler.save_last("inbox_triage", now - 600)
    scheduler.job_failed("inbox_triage", RuntimeError("b'[AUTHENTICATIONFAILED] Invalid credentials'"))
    scheduler.save_last("execution", now - 600)
    scheduler.job_failed("execution", KeyError("fill_rate"))
    states = hq.job_states(now)
    assert states["daily_wrap"]["state"] == "ok" and states["inbox_triage"]["state"] == "waiting"
    assert states["execution"]["state"] == "failing" and states["weekly_pack"]["state"] == "not_run"
    text = hq.agents_text(now)
    assert "BE YOUR PA  [partly waiting on setup]" in text and "WATCH THE DESK AND THE MARKETS  [needs a fix]" in text
    assert "OK       daily_wrap" in text and "last ran 60m ago" in text and "Google refused" in text

    ran = []
    monkeypatch.setattr(scheduler, "JOBS", [("execution", 15, None, lambda: ran.append(1))])
    assert "execution ran" in hq.run_job("execution") and ran == [1]
    assert "execution" not in scheduler.failing_jobs()               # a success clears "failing"
    assert "No job called" in hq.run_job("nonsense")
    monkeypatch.setattr(scheduler, "JOBS", [("execution", 15, None, lambda: 1 / 0)])
    assert "failed: ZeroDivisionError" in hq.run_job("execution")
    assert "execution" in scheduler.failing_jobs()


def test_hq_view_puts_needs_you_first(tmp_path, monkeypatch):
    from jarvis.agents import hq, rhythm

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setattr(rhythm, "needs_you", lambda: ["NEEDS YOU", " 1. Overdue #3: FSCA return"])
    monkeypatch.setattr(rhythm, "health_lines", lambda: ["SYSTEM: all connections OK"])
    text = hq.hq_text()
    assert text.index("NEEDS YOU") < text.index("YOUR AGENTS") and "Grow the business:" in text


def test_the_manual_carries_the_mandate():
    from pathlib import Path

    from jarvis.agents import hq

    manual = (Path(__file__).resolve().parents[1] / "DONNA.md").read_text()
    assert hq.PURPOSE in manual
    assert all(d["name"] in manual and d["proof"] in manual for d in hq.DUTIES)
    assert all(b in manual for b in hq.BOUNDARIES)


def test_desktop_console_shows_the_agents_and_answers_the_mandate(tmp_path, monkeypatch):
    from donna import console
    from jarvis.agents import hq

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    s = console.state()
    assert [a["name"] for a in s["agents"]] == [d["name"] for d in hq.DUTIES]
    assert {a["state"] for a in s["agents"]} <= {"running", "partly waiting on setup", "needs a fix", "on demand", "not started yet"}
    assert console.intent("what can you do?") == "!mandate" and console.intent("how are the agents") == "!hq"
    assert console.reply("what can you do for me") == hq.mandate_text()
    page = console.PAGE.read_text()
    assert 'id="agents"' in page and 'id="map-svg"' in page and "openMap()" in page and 'say("!agents")' in page
    assert [d["key"] for d in s["board"]] == [d["key"] for d in hq.DUTIES]
    assert all({"name", "state", "agents", "jobs", "promise", "proof"} <= set(d) for d in s["board"])


def test_wake_up_briefing_without_claude(tmp_path, monkeypatch):
    from datetime import datetime

    from jarvis.agents import assistant, hq

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_GOOGLE_BRIDGE_URL", raising=False)
    monkeypatch.delenv("JARVIS_GCAL_ICS_URL", raising=False)
    assistant.add_task("File the quarterly return", "2026-11-01", "high")
    now = datetime(2026, 11, 9, 7, 30, tzinfo=assistant.LOCAL_TZ)
    lines = hq.wake_lines(now)
    text = "\n".join(lines)
    assert lines[0].startswith("Good morning") and "DONNA is online" in lines[0]
    assert "Email and calendar not connected" in text and "Claude" in text
    assert "Tasks: 1 open, 0 due today, 1 overdue." in text
    assert "First thing: Overdue #1: File the quarterly return." in text
    assert hq.command("!wake", "").startswith("Good ") and hq.command("!briefing", "").startswith("Good ")


def test_wake_after_downtime_only_when_the_server_was_off(monkeypatch):
    from jarvis.agents import hq

    sent = []
    monkeypatch.setattr(hq, "wake_text", lambda now=None: "briefing")
    monkeypatch.setattr("jarvis.agents.personal.PERSONAL_TARGETS", ["discord:personal"])
    monkeypatch.setattr("donna.channels.broadcast", lambda text, targets, title="": sent.append((title, text)))
    assert hq.wake_after_downtime({"nudges": 1000.0}, now=1000.0 + 120) is None          # an update restart
    assert hq.wake_after_downtime({}, now=5000.0) is None                                # first start ever
    text = hq.wake_after_downtime({"nudges": 1000.0, "watch": 900.0}, now=1000.0 + 3 * 3600)
    assert text.startswith("I'm back after being offline for 3.0 hours") and sent[0][0] == "D.O.N.N.A is awake"


def test_console_wake_route_and_intents(tmp_path, monkeypatch):
    from donna import console
    from jarvis.agents import hq

    monkeypatch.setattr(hq, "wake_lines", lambda now=None: ["Good morning, SH.", "All systems are connected."])
    assert console.console_wake() == {"lines": ["Good morning, SH.", "All systems are connected."]}
    monkeypatch.setattr(hq, "wake_lines", lambda now=None: 1 / 0)
    assert "briefing failed" in console.console_wake()["lines"][0]
    assert console.intent("brief me") == "!wake" and console.intent("what did I miss") == "!wake"
