from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest


@pytest.fixture()
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    from jarvis.agents import watch
    return watch


def test_market_move_once_per_day(w):
    inst = type("I", (), {"label": "XAUUSD"})()
    rows = [{"inst": inst, "date": "2026-10-23", "range_atr": 2.1, "chg_pct": -1.8, "stale": False},
            {"inst": type("I", (), {"label": "EURUSD"})(), "date": "2026-10-23", "range_atr": 0.8, "chg_pct": 0.1,
             "stale": False},
            {"inst": inst, "error": True}]
    out = w.market_nudges(rows)
    assert len(out) == 1 and "XAUUSD" in out[0] and "2.1x" in out[0]
    assert w.market_nudges(rows) == []


def test_compliance_deadlines(w):
    from jarvis.agents import compliance

    today = date.today()
    with compliance._db() as c:
        c.execute("INSERT INTO obligations (name, due) VALUES (?,?)", ("FSCA quarterly return", (today + timedelta(1)).isoformat()))
        c.execute("INSERT INTO obligations (name, due) VALUES (?,?)", ("Far away", (today + timedelta(30)).isoformat()))
        c.execute("INSERT INTO cases (opened, severity, subject, due, status) VALUES (?,?,?,?, 'open')",
                  ("x", "high", "Large deposit, no SOF", today.isoformat()))
    out = w.compliance_nudges(today)
    assert any("due tomorrow: FSCA" in m for m in out) and any("case #1 is due today" in m for m in out)
    assert not any("Far away" in m for m in out) and w.compliance_nudges(today) == []


def test_unassigned_leads_after_hot_window(w):
    from jarvis.agents import growth

    d = pd.DataFrame({"customer_no": ["CU1"], "registration_date": [datetime.now().strftime("%Y-%m-%d %H:%M")],
                      "lead_source": ["Google"], "lead_status": ["New"], "assigned_to": ["0"], "ftd_amount": ["0"]})
    growth.store_leads(d, growth.lead_columns(d), date.today().isoformat())
    now = datetime.now(timezone.utc)
    assert w.lead_nudges(now) == []                                    # first seen just now
    out = w.lead_nudges(now + timedelta(hours=3))
    assert "1 new lead(s)" in out[0] and w.lead_nudges(now + timedelta(hours=4)) == []


def test_positions_without_stop(w):
    from jarvis.agents import rhythm

    rhythm.store_positions([{"symbol": "XAUUSD", "side": "buy", "units": 10, "entry": 2400, "price": 2405,
                             "stop": None}], equity=10000)
    assert w.position_nudges("2026-10-24") == ["XAUUSD has no stop loss. Add one or close it."]
    assert w.position_nudges("2026-10-24") == []


def test_check_survives_a_broken_source(w, monkeypatch):
    def boom():
        raise RuntimeError("feed down")

    monkeypatch.setattr(w, "CHECKS", [("bad", boom), ("ok", lambda: ["hello"])])
    assert w.check() == ["hello"]
