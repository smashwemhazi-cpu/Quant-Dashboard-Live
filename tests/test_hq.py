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
    assert "RUN YOUR DAY  [partly waiting on setup]" in text and "WATCH THE DESK AND THE MARKETS  [needs a fix]" in text
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
