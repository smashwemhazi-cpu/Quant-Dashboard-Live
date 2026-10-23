import json
from datetime import date, datetime

import pandas as pd
import pytest


@pytest.fixture()
def cp(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    from jarvis.agents import copilot
    return copilot


def leads(statuses, ftd, owners):
    from jarvis.agents import growth

    n = len(statuses)
    d = pd.DataFrame({"customer_no": [f"CU{i}" for i in range(n)], "registration_date": ["2026-10-01 09:00"] * n,
                      "lead_source": ["Google"] * n, "lead_status": statuses, "assigned_to": owners,
                      "ftd_amount": ftd})
    growth.store_leads(d, growth.lead_columns(d), datetime.now().strftime("%Y-%m-%d %H:%M"))


class FakeClient:
    def __init__(self, payload):
        outer = self
        self.kw = None

        class Msgs:
            def create(self, **kw):
                outer.kw = kw
                return type("R", (), {"stop_reason": "end_turn",
                                      "content": [type("B", (), {"type": "text", "text": json.dumps(payload)})]})

        self.beta = type("Beta", (), {"messages": Msgs()})()


REVIEW = {"headline": "Leads steady, conversion weak", "wins": ["Google 4 leads"], "worries": ["3 of 4 unassigned"],
          "experiment": {"title": "Assign within 1 hour", "hypothesis": "Faster contact funds more leads",
                         "action": "Round-robin new leads to sales every hour", "metric": "unassigned_leads",
                         "target": 0, "days": 3}}


def test_no_data_skips_claude(cp):
    assert cp.snapshot() == {}
    assert "NO DATA" in cp.review(client=FakeClient(REVIEW))


def test_review_proposes_then_experiment_is_judged(cp):
    leads(["New", "New", "New", "Converted"], ["0", "0", "0", "250"], ["0", "0", "0", "Roy"])
    client = FakeClient(REVIEW)
    text = cp.review(client=client)
    assert "NEXT EXPERIMENT #1" in text and "!exp start 1" in text
    sent = client.kw["messages"][0]["content"]
    assert "CU0" not in sent and "unassigned_leads" in sent          # counts only, no customer ids
    e = cp.get_experiment(1)
    assert e["status"] == "proposed" and e["days"] == 7               # clamped to 7-28 days

    assert "now 3, target 0" in cp.start(1, today="2026-10-01")
    assert "already running" in cp.start(1)
    assert cp.evaluate(today="2026-10-05") == []                      # not finished yet

    leads(["Called"] * 4, ["0", "0", "0", "250"], ["Roy"] * 4)        # everyone assigned now
    judged = cp.evaluate(today="2026-10-08")
    assert judged[0]["status"] == "won" and judged[0]["result"] == 0
    assert "WON" in cp.experiments_text()


def test_lost_and_stop(cp):
    leads(["New", "Converted"], ["0", "250"], ["0", "Roy"])
    eid = cp.propose({"title": "More funded", "hypothesis": "h", "action": "a", "metric": "funded_total",
                      "target": 5, "days": 7})
    cp.start(eid, today="2026-10-01")
    assert cp.evaluate(today="2026-10-09")[0]["status"] == "lost"
    eid2 = cp.propose({"title": "x", "hypothesis": "h", "action": "a", "metric": "stale_leads", "target": 0, "days": 7})
    assert "stopped" in cp.stop(eid2, "not now") and "No open" in cp.stop(eid2)


def test_weekly_only_on_review_day(cp):
    agent = cp.CopilotAgent()
    assert agent.weekly(today=date(2026, 10, 27))["skipped"]             # a Tuesday
    assert "NO DATA" in agent.weekly(today=date(2026, 10, 26))["text"]   # Monday


def test_discord_commands(cp):
    from donna import discord_bot

    leads(["New"], ["0"], ["0"])
    eid = cp.propose({"title": "Assign fast", "hypothesis": "h", "action": "a", "metric": "unassigned_leads",
                      "target": 0, "days": 7})
    assert "Assign fast" in discord_bot.handle("!exp")
    assert "started" in discord_bot.handle(f"!exp start #{eid}")
    assert "Usage" in discord_bot.handle("!exp nonsense")
