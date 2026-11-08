"""Market-hours aware checks (5-day FX / metals / indices, 7/7 crypto), Syntellicore switched off, and the cTrader
API as the source of truth for the trade history (statements are backup only)."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

FRI_LATE = datetime(2026, 11, 6, 22, 30, tzinfo=timezone.utc)
SAT = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
SUN_NOON = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
MON = datetime(2026, 11, 9, 9, tzinfo=timezone.utc)


def test_market_week_and_crypto():
    from jarvis.core import markets

    assert markets.fx_open(datetime(2026, 11, 6, 21, 59, tzinfo=timezone.utc))
    assert not markets.fx_open(FRI_LATE) and not markets.fx_open(SAT) and not markets.fx_open(SUN_NOON)
    assert not markets.fx_open(datetime(2026, 11, 8, 20, 59, tzinfo=timezone.utc))
    assert markets.fx_open(datetime(2026, 11, 8, 21, 0, tzinfo=timezone.utc)) and markets.fx_open(MON)
    assert not markets.fx_open(datetime(2026, 12, 25, 12, tzinfo=timezone.utc))         # holiday
    assert markets.is_open("BTCUSD", SAT) and not markets.is_open("XAUUSD.mp", SAT)
    assert markets.open_hours_between(FRI_LATE, SUN_NOON) == 0                          # the weekend doesn't count
    assert markets.open_hours_between(FRI_LATE, SUN_NOON, "ETHUSD") > 37                 # crypto does
    assert markets.working_days_between(date(2026, 11, 6), date(2026, 11, 8)) == 0      # Fri -> Sun
    assert markets.working_days_between(date(2026, 11, 6), date(2026, 11, 9)) == 1      # Fri -> Mon
    assert "weekend" in markets.closed_note(SAT) and markets.closed_note(MON) == ""


def test_weekend_ctrader_failures_rest_quietly(tmp_path, monkeypatch):
    from jarvis import scheduler
    from jarvis.agents import alerts, hq

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    sent = []
    monkeypatch.setattr(alerts, "raise_alert", lambda kind, text, key="", now=None: sent.append(text) or "held")
    monkeypatch.setattr(scheduler.audit, "log", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "FAIL_GRACE_MINUTES", 60)
    clock = [SAT.timestamp()]
    monkeypatch.setattr(scheduler.time, "time", lambda: clock[0])
    scheduler.save_last("ctrader_positions", clock[0])
    for err in ("cTrader unreachable (ClientConnectorError)", "some odd maintenance error"):
        for _ in range(30):                                   # a whole Saturday of failures: nothing sent
            scheduler.job_failed("ctrader_positions", RuntimeError(err))
            clock[0] += 1800
    assert sent == []
    st = hq.job_states(SUN_NOON.timestamp())["ctrader_positions"]
    assert st["state"] == "resting" and "weekend" in st["why"]
    assert "RESTING" in hq.agents_text(SUN_NOON.timestamp())
    # reopened Sunday 21:00: still quiet for the first hour, then a real problem is reported
    assert hq.job_states(datetime(2026, 11, 8, 21, 30, tzinfo=timezone.utc).timestamp())["ctrader_positions"][
        "state"] == "resting"
    assert hq.job_states(datetime(2026, 11, 8, 23, 0, tzinfo=timezone.utc).timestamp())["ctrader_positions"][
        "state"] == "failing"


def _syntellicore_off(monkeypatch):
    monkeypatch.setenv("JARVIS_SYNTELLICORE", "off")


def test_feeds_weekend_and_syntellicore_off(tmp_path, monkeypatch):
    from jarvis.agents import backoffice, feeds

    _syntellicore_off(monkeypatch)
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    with backoffice._db() as c:
        c.execute("INSERT OR REPLACE INTO bo_meta VALUES ('last_eod', '2026-11-05')")      # Thursday
        c.execute("INSERT OR REPLACE INTO bo_meta VALUES ('last_docs', '2026-10-01')")
    with sqlite3.connect(tmp_path / "s.db") as c:
        c.execute("CREATE TABLE position_snapshots (ts TEXT)")
        c.execute("INSERT INTO position_snapshots VALUES ('2026-11-06T21:55:00')")       # last one before the close
        c.execute("CREATE TABLE pt_trades (closed_utc TEXT)")
        c.execute("INSERT INTO pt_trades VALUES ('2026-11-06 20:00:00')")
    rows = {r["name"]: r for r in feeds.status(now=SUN_NOON)}
    assert rows["End-of-day accounts (equity, balance, margin)"]["state"] == "fresh"   # 1 working day old on Sunday
    for name in ("Customer documents (KYC)", "CRM KPIs", "CRM leads", "IB commissions"):
        assert rows[name]["state"] == "off"
    assert rows["Your cTrader account (read-only)"]["state"] == "live"                 # weekend: no snapshots, fine
    assert rows["Your trade history"]["state"] == "live"
    text = feeds.feeds_text(now=SUN_NOON)
    assert "markets closed for the weekend" in text and "NOT USED" in text
    assert "⚫ CRM KPIs: not used (Syntellicore is switched off)" in text
    assert "of 3 reports are missing" in text                                         # off sources not counted
    assert "🔴" not in text and "Customer documents (KYC): last one" not in text
    assert "optional backup only" in text
    # Monday: the cTrader account must have answered again by now
    assert {r["name"]: r for r in feeds.status(now=datetime(2026, 11, 9, 9, tzinfo=timezone.utc))}[
        "Your cTrader account (read-only)"]["state"] == "old"
    assert backoffice.data_age("docs") == (None, None) and backoffice.kyc_summary().get("off")
    assert backoffice.report_lines() and not any("CRM KPIs" in x or x.startswith("KYC") for x in backoffice.report_lines())


def test_kpis_and_kris_leave_syntellicore_out(tmp_path, monkeypatch):
    from jarvis.agents import compliance, growth, kpi

    _syntellicore_off(monkeypatch)
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    keys = {r["key"] for r in kpi.scorecard()}
    assert set(kpi.unused()) == {"crm_conversion", "crm_stale_leads", "co_kyc_pending"}
    assert not keys & set(kpi.unused())
    assert "NOT USED (Syntellicore is switched off" in kpi.scorecard_text()
    assert not any("KYC" in k["kri"] or "IB" in k["kri"] for k in compliance.kris())
    assert growth.report_lines() == []


def test_statement_never_doubles_the_api_history(tmp_path, monkeypatch):
    from jarvis.agents import personal

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    personal.store_trades("123", [{"closed_utc": "2026-11-05 10:00:00.000000", "symbol": "XAUUSD", "side": "buy",
                                   "entry": 4000.0, "exit": 4001.0, "qty": 0.01, "net": 1.0, "balance": 100.0}])
    with sqlite3.connect(tmp_path / "s.db") as c:
        c.execute("CREATE TABLE IF NOT EXISTS position_snapshots (ts TEXT)")
        c.execute("INSERT INTO position_snapshots VALUES (?)",
                  (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
    d = pd.DataFrame({"Symbol": ["XAUUSD", "XAUUSD"], "Opening direction": ["Buy", "Buy"],
                      "Closing time": ["05/11/2026 10:00:00.123", "01/11/2026 09:00:00.000"],   # same trade, rounded
                      "Entry price": ["4000.00", "3990.00"], "Closing price": ["4001.00", "3991.00"],
                      "Closing Quantity": ["0.01", "0.01"], "Net USD": ["1.00", "1.00"], "Balance USD": ["100", "99"]})
    acc, added = personal.store_statement(d, Path("cT_123_2026-11-06.xlsx"))
    assert (acc, added) == ("123", 1)                    # only the older trade the API hadn't given yet
