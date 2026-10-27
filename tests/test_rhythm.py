from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def rhythm(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    from jarvis.agents import rhythm as r
    return r


POS = [
    {"symbol": "XAUUSD", "side": "buy", "units": 10, "entry": 2400.0, "stop": 2390.0, "price": 2405.0},
    {"symbol": "USDJPY", "side": "sell", "units": 10_000, "entry": 150.0, "stop": 150.5, "price": 150.2,
     "quote_to_account": 1 / 150.2},
    {"symbol": "EURUSD", "side": "buy", "units": 10_000, "entry": 1.08, "stop": 1.081, "price": 1.083},
]


def test_position_risk_maths(rhythm):
    rhythm.store_positions(POS, equity=10_000, start_of_day_equity=10_100)
    r = rhythm.position_risk()
    gold, jpy, eur = r["positions"]
    assert gold["risk_at_stop"] == pytest.approx(150.0) and gold["floating"] == pytest.approx(50.0)
    assert jpy["risk_at_stop"] == pytest.approx(0.3 * 10_000 / 150.2)          # yen converted to dollars
    assert eur["stop_in_profit"] and eur["risk_at_stop"] == pytest.approx(20.0)  # still gives back 20 to the stop
    assert r["open_risk_pct"] == pytest.approx((150 + 0.3 * 10_000 / 150.2 + 20) / 100)
    # 2% of 10,100 = 202 allowed; already -100 today -> 102 left
    assert r["daily_loss_room"] == pytest.approx(102.0)


def test_risk_lines_flag_missing_stop_and_staleness(rhythm):
    rhythm.store_positions([{**POS[0], "stop": None}], equity=10_000)
    text = "\n".join(rhythm.risk_lines())
    assert "NO STOP" in text and "No hard stop on XAUUSD" in text
    later = datetime.now(timezone.utc) + timedelta(hours=20)
    assert rhythm.position_risk(now=later)["stale"]


def test_no_feed_says_no_data(rhythm):
    assert "NO DATA" in "\n".join(rhythm.risk_lines())


def test_needs_you_ranks_what_only_the_owner_can_move(rhythm, monkeypatch):
    from jarvis.agents import assistant, personal

    monkeypatch.setattr(personal, "bots", lambda: [])
    assistant.add_task("Low overdue", "2020-01-01", "low")
    assistant.add_task("FSCA return", "2020-01-01", "high")
    rhythm.store_positions([{**POS[0], "stop": None}], equity=10_000)
    lines = rhythm.needs_you()
    assert lines[0] == "NEEDS YOU"
    body = lines[1:]
    assert "FSCA return" in body[0] or "without a stop" in body[0]
    assert any("Low overdue" in x for x in body[2:])               # low-priority overdue ranks after


def test_digests_survive_dead_sources(rhythm, monkeypatch):
    from jarvis.agents import briefing, personal

    def boom(*a, **k):
        raise ConnectionError("feed down")

    monkeypatch.setattr(briefing, "market_table", boom)
    monkeypatch.setattr(personal, "bots", lambda: [])
    monkeypatch.setenv("JARVIS_GCAL_ICS_URL", "")
    m = rhythm.morning_text()
    assert "GOOD MORNING" in m and "NEEDS YOU" in m and "MARKETS: unavailable (ConnectionError)" in m
    # sections with nothing connected collapse into one line instead of empty NO DATA blocks
    assert "NOT SET UP YET: calendar, live positions (cTrader)" in m and "CALENDAR\n" not in m and "!compliance" in m
    e = rhythm.evening_text()
    assert "SHUTDOWN" in e and "NOT SET UP YET:" in e and "trading journal" in e and "POST /api" not in e


def test_digest_replaces_the_separate_personal_reports(monkeypatch):
    from jarvis import scheduler
    from jarvis.agents import rhythm

    monkeypatch.setattr(rhythm, "DIGEST", True)
    names = {j[0] for j in scheduler.active_jobs()}
    assert {"morning", "evening", "morning_dispatch", "daily_wrap", "nudges", "inbox_triage"} <= names
    assert not names & {"my_day", "shutdown", "morning_brief", "compliance_report", "marketing_drafts"}
    monkeypatch.setattr(rhythm, "DIGEST", False)
    names = {j[0] for j in scheduler.active_jobs()}
    assert "my_day" in names and "morning" not in names


def test_positions_endpoint(tmp_path, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.setenv("DONNA_API_KEY", "svc")
    import donna.app as m
    m = importlib.reload(m)
    body = {"equity": 10_000, "positions": [{**POS[0], "stop": None}]}
    with TestClient(m.app) as c:
        assert c.post("/api/positions", json=body).status_code == 401
        r = c.post("/api/positions", json=body, headers={"X-API-Key": "svc"}).json()
        assert r["no_stop"] == ["XAUUSD"]
        bad = {"equity": 10_000, "positions": [{**POS[0], "side": "long"}]}
        assert c.post("/api/positions", json=bad, headers={"X-API-Key": "svc"}).status_code == 422


def test_health_lines_show_what_waits_on_the_owner(tmp_path, monkeypatch):
    from jarvis import scheduler
    from jarvis.agents import rhythm as r
    from jarvis.core import brain

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "h.db"))
    monkeypatch.setattr(scheduler.audit, "log", lambda *a, **k: None)
    monkeypatch.setattr(brain, "claude_check", lambda client=None: "OK")
    assert r.health_lines() == ["SYSTEM: all connections OK"]
    scheduler.job_failed("execution", FileNotFoundError("no data"))  # not set up on purpose: not nagged
    assert r.health_lines() == ["SYSTEM: all connections OK"]
    scheduler.job_failed("inbox_triage", RuntimeError("b'[AUTHENTICATIONFAILED] Invalid credentials'"))
    monkeypatch.setattr(brain, "claude_check", lambda client=None: "NO CREDIT - top up")
    out = "\n".join(r.health_lines())
    assert "Google refused" in out and "NO CREDIT" in out


def test_unconverted_position_is_not_added_to_dollar_totals(rhythm):
    # the feed couldn't find a JPY->USD rate: 0.3 * 10,000 = 3,000 yen at stop must not count as $3,000
    rhythm.store_positions([POS[0], {**POS[1], "quote_to_account": None}], equity=10_000)
    r = rhythm.position_risk()
    assert r["open_risk"] == pytest.approx(150.0) and r["unconverted"] == ["USDJPY"]
    text = "\n".join(rhythm.risk_lines())
    assert "USDJPY: no conversion rate" in text and "(quote currency)" in text


def test_evening_compliance_block_and_market_label(rhythm, monkeypatch):
    from datetime import date

    import pandas as pd

    from jarvis.agents import briefing, compliance

    monkeypatch.setattr(compliance, "kris", lambda: [{"kri": "Largest client share of equity %", "value": "53",
                                                      "status": "RED"}])
    compliance.open_case("AML", "AML_TEST", "high", "#1", "test")
    assert rhythm.compliance_lines()[1].startswith(" 1 open case(s): 1 high, 0 overdue, 1 new today")
    assert "Red KRIs: Largest client share" in rhythm.compliance_lines()[2]
    inst = briefing.WATCHLIST[0]
    row = {"inst": inst, "close": 1.0, "chg_pct": 1.0, "chg_5d_pct": 1.0, "range_atr": 1.0, "stale": False,
           "date": date.today().isoformat(), "df": pd.DataFrame()}
    monkeypatch.setattr(briefing, "market_table", lambda: [row])
    assert "today so far" in rhythm.market_lines()[0]
    monkeypatch.setattr(briefing, "market_table", lambda: [{**row, "date": "2020-01-02"}])
    assert "last close" in rhythm.market_lines()[0]
