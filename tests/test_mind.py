import json

import pytest


@pytest.fixture()
def mind(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    from jarvis.agents import mind as m
    return m


class FakeBrain:
    def __init__(self):
        self.calls = []

    def ask(self, messages, user="user", allow_writes=False, system=None):
        self.calls.append(dict(msgs=messages, user=user, writes=allow_writes, system=system))
        return f"reply {len(self.calls)}"


def test_memory_feeds_the_persona(mind):
    mid = mind.remember("Wants to grow IB revenue 30% by June", "goal")
    assert mind.remember("Wants to grow IB revenue 30% by June", "goal") == mid  # no duplicates
    assert "(goal) Wants to grow IB revenue" in mind.persona()
    assert mind.forget(mid) and not mind.forget(mid)
    assert "nothing stored yet" in mind.persona()


def test_chat_keeps_history_per_channel_and_reset(mind):
    brain = FakeBrain()
    mind.chat("idea: a copy-trading product", "a", brain)
    mind.chat("what would it cost?", "a", brain)
    mind.chat("unrelated", "b", brain)
    assert [m["content"] for m in brain.calls[1]["msgs"]] == ["idea: a copy-trading product", "reply 1",
                                                             "what would it cost?"]
    assert len(brain.calls[2]["msgs"]) == 1 and not brain.calls[0]["writes"]
    assert mind.reset("a") == 4 and mind.history("a") == []


def test_history_window_starts_with_user(mind, monkeypatch):
    monkeypatch.setattr(mind, "HISTORY_TURNS", 3)
    brain = FakeBrain()
    for q in ("one", "two", "three"):
        mind.chat(q, "c", brain)
    h = mind.history("c")
    assert h[0]["role"] == "user" and len(h) == 2


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        outer = self

        class Msgs:
            def create(self, **kw):
                outer.kw = kw
                return type("R", (), {"stop_reason": "end_turn",
                                      "content": [type("B", (), {"type": "text", "text": json.dumps(outer.payload)})]})

        self.beta = type("Beta", (), {"messages": Msgs()})()


DUMP = {"top3": ["Finish FSCA return", "Call LP about XAU", "Gym"],
        "tasks": [{"title": "Submit FSCA quarterly return", "due": "2026-10-30", "priority": "high"},
                  {"title": "Call LP about XAU spreads", "due": "", "priority": "medium"}],
        "ideas": [{"text": "IB tier with rebates for funded accounts only", "area": "business"}],
        "worries": [{"worry": "Bot keeps missing fills", "next_step": "Upload today's cBot log"}],
        "park": ["New website"], "note": "The return is the only hard deadline."}


def test_brain_dump_creates_tasks_ideas_and_focus(mind):
    from jarvis.agents import assistant

    r = mind.brain_dump("so much going on... FSCA return, LP, website, gym", client=FakeClient(DUMP))
    assert len(r["task_ids"]) == 2 and len(r["idea_ids"]) == 1
    assert {t["source"] for t in assistant.list_tasks()} == {"brain_dump"}
    assert mind.latest_focus()[0] == "Finish FSCA return"
    text = mind.dump_text(r)
    assert "FOCUS" in text and "-> Upload today's cBot log" in text and "PARKED" in text
    assert "FOCUS (from your last !dump)" in "\n".join(mind.focus_lines())
    assert mind.brain_dump("  ")["error"].startswith("nothing")


def test_ideas_board_and_shutdown(mind):
    from jarvis.agents import assistant

    iid = mind.add_idea("Weekly webinar for funded IB clients", "business")
    assert mind.add_idea("Weekly webinar for funded IB clients") is None
    assert mind.set_idea(iid, "doing", "Ask marketing for a slot")
    assert mind.get_idea(iid)["status"] == "doing"
    with pytest.raises(ValueError):
        mind.set_idea(iid, "maybe")
    assert mind.parse_idea_ref("#12 because reasons") == (12, "because reasons")
    t1 = assistant.add_task("Done thing")
    assistant.add_task("Overdue thing", "2020-01-01", "high")
    assistant.complete_task(t1)
    s = mind.shutdown_text()
    assert "DONE TODAY (1)" in s and "!! #2 Overdue thing" in s and "IDEAS CAPTURED TODAY" in s
    assert "1. #2 Overdue thing" in s


def test_discord_thinking_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("DONNA_DISCORD_CHAT_CHANNELS", "donna")
    from donna import discord_bot as b
    from jarvis.core import brain

    fb = FakeBrain()
    monkeypatch.setattr(brain, "Brain", lambda: fb)
    assert b.allowed(1, "donna")                         # chat channel is enough to be answered
    assert b.handle("hello", "general") is None            # plain text outside chat channels: silent
    assert b.handle("hello", "donna") == "reply 1"
    assert b.handle("!idea Launch a Discord signals tier", "donna") == "Idea #1 on the board."
    assert "Launch a Discord signals tier" in b.handle("!ideas", "donna")
    assert b.handle("!think #1", "donna") == "reply 2"
    from jarvis.agents import mind
    assert mind.get_idea(1)["status"] == "exploring" and "[think" in mind.get_idea(1)["note"]
    assert b.handle("!park #1 not before Q1", "donna") == "Idea #1 is now parked."
    assert b.handle("!remember I trade gold in the London session", "donna") == "Noted (#1)."
    assert "gold" in b.handle("!memory", "donna") and "#1 forgotten" in b.handle("!forget 1", "donna")
    assert "SHUTDOWN" in b.handle("!shutdown", "donna")
    assert b.handle("!reset", "donna").startswith("Fresh page")


def test_fx_sizing_converts_quote_currency():
    from jarvis.agents.trading import quote_to_account

    assert quote_to_account("EURUSD=X", 1.08) == 1.0
    assert quote_to_account("USDJPY=X", 150.0) == pytest.approx(1 / 150)
    assert quote_to_account("EURGBP=X", 0.85) is None and quote_to_account("^GDAXI", 18000) is None
    assert quote_to_account("XAUUSD", 2400) == 1.0


def test_usdjpy_plan_risks_the_right_dollars(monkeypatch):
    from jarvis.agents import trading
    from jarvis.core import risk

    monkeypatch.setattr(trading, "atr", lambda t: (1.0, 150.0))
    plan = trading.TradingAgent().plan_trade("USDJPY=X", "buy", 10_000, 10_000, 149.99, 150.0, 0.01)
    # 0.5% of 10k = $50 at risk; 1.5 ATR = 1.5 yen stop -> ~5000 units, not 33 units
    assert 4900 <= plan["units"] <= 5000 and plan["risk_amount"] <= 50
    cross = trading.TradingAgent().plan_trade("EURGBP=X", "buy", 10_000, 10_000, 0.85, 0.8501, 0.0001)
    assert not cross["approved"] and "quote_to_account_rate" in cross["reasons"][0]
    assert risk  # imported for the settings defaults used above


def test_alert_delivery_only_for_chosen_events(monkeypatch):
    from jarvis import scheduler

    sent = []
    monkeypatch.setattr(scheduler, "ALERT_TARGETS", ["discord:personal"])
    import donna.channels as ch
    monkeypatch.setattr(ch, "broadcast", lambda text, targets, title: sent.append((title, text)) or {})
    monkeypatch.setattr(scheduler.audit, "log", lambda *a, **k: None)
    scheduler.notify("execution_alert", {"fill_rate_pct": 95.1})
    scheduler.notify("retention_scan", [{"client": "x"}])   # not in the default alert list
    scheduler.notify("morning_brief", "Good morning")
    assert sent == [("D.O.N.N.A execution alert", "fill_rate_pct: 95.1"), ("D.O.N.N.A morning brief", "Good morning")]
