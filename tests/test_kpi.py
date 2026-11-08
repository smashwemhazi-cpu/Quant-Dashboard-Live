"""KPIs for every agent and for trading, and the lead quality bar."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 11, 7, 10, tzinfo=timezone.utc)


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))


def test_every_kpi_has_a_measure_and_an_empty_install_scores_cleanly(tmp_path, monkeypatch):
    from jarvis.agents import kpi

    _env(tmp_path, monkeypatch)
    assert set(kpi.MEASURES) == set(kpi.STARTER)
    assert {d for d, *_ in kpi.STARTER.values()} <= set(dict(kpi.DEPTS))
    rows = kpi.scorecard(now=NOW)
    assert len(rows) == len(kpi.STARTER) - len(kpi.unused())
    assert all(r["status"] in kpi.ORDER for r in rows)
    assert not [r for r in rows if r["note"].startswith("unavailable")]     # no measure crashes on a fresh DB
    by = {r["key"]: r for r in rows}
    assert by["tr_lots_min"]["status"] == "NO DATA" and by["book_lots"]["status"] == "NO DATA"   # never a fake green
    text = kpi.scorecard_text(now=NOW)
    assert "KPI SCORECARD" in text and "LEAD FINDER" in text and "YOUR TRADING" in text and "CLIENT BOOK" in text


def test_rag_directions_and_no_amber_band():
    from jarvis.agents import kpi

    mn = {"dir": "min", "target": 15, "amber": 8}
    assert [kpi.rag(mn, v) for v in (15, 9, 7, None)] == ["GREEN", "AMBER", "RED", "NO DATA"]
    mx = {"dir": "max", "target": 0, "amber": 0}
    assert [kpi.rag(mx, v) for v in (0, 1)] == ["GREEN", "RED"]


def test_targets_can_be_changed_and_reset(tmp_path, monkeypatch):
    from jarvis.agents import kpi

    _env(tmp_path, monkeypatch)
    assert "target now 20+, amber at 10" in kpi.command("!kpi", "set leads_qualified_week 20 10")
    t = kpi.targets()["leads_qualified_week"]
    assert (t["target"], t["amber"], t["changed"]) == (20, 10, True)
    assert "amber line must be" in kpi.set_target("tr_max_loss", 2, 1)       # lower is better here
    assert "amber at 2.50%" in kpi.set_target("tr_max_loss", 0.5)          # keeps the starter gap of 2 points
    assert "No KPI called" in kpi.set_target("nonsense", 1)
    assert "starter target" in kpi.command("!kpi", "reset leads_qualified_week")
    assert kpi.targets()["leads_qualified_week"]["target"] == 15
    assert "(changed by you)" in kpi.targets_text()                        # tr_max_loss is still changed


def test_lead_quality_bar(tmp_path, monkeypatch):
    from jarvis.agents import kpi, leadfinder, radar

    _env(tmp_path, monkeypatch)
    radar.set_permitted(["KE"])
    good = {"kind": "ib", "country": "KE", "flags": "", "url": "https://t.me/kenyafx", "name": "Kenya FX Academy",
            "about": "forex trading education and mentorship community", "audience": 12000,
            "last_active": (NOW - timedelta(days=3)).isoformat(), "score": 72, "first_seen": NOW.isoformat()}
    q, ok, bad = kpi.qualify(good, NOW)
    assert q and not bad and "country cleared" in ok
    cases = [{"country": "ZA"}, {"flags": "promises returns"}, {"url": "https://ke.linkedin.com/in/jane"},
             {"last_active": (NOW - timedelta(days=200)).isoformat()}, {"audience": 300}, {"score": 40},
             {"about": "gym and fitness tips", "name": "Gym"}]
    for case in cases:
        q, _, bad = kpi.qualify({**good, **case}, NOW)
        assert not q and len(bad) == 1, case
    trader = {**good, "kind": "trader", "about": "which broker has low spreads?", "name": "", "audience": None,
              "score": 55, "last_active": (NOW - timedelta(days=20)).isoformat()}
    assert "quiet 20 days" in "; ".join(kpi.qualify(trader, NOW)[2])          # traders must be fresh: 14 days
    assert kpi.qualify({**trader, "last_active": NOW.isoformat()}, NOW)[0]

    pid = leadfinder.add({"source": "telegram", "ext_id": "kenyafx", **good}, now=NOW)
    assert "QUALIFIED" in leadfinder.line(leadfinder._prospect(pid), NOW)
    assert "Result: QUALIFIED" in kpi.command("!kpi", f"lead {pid}")
    stats = leadfinder.stats_text(now=NOW + timedelta(days=1))
    assert "Qualified" in stats and "AGAINST TARGET" in stats


def test_lead_kpis_count_qualified_speed_and_flagged(tmp_path, monkeypatch):
    from jarvis.agents import kpi, leadfinder, radar

    _env(tmp_path, monkeypatch)
    radar.set_permitted(["KE"])
    base = {"source": "telegram", "kind": "ib", "country": "KE", "url": "https://t.me/x",
            "about": "forex trading academy partner community", "audience": 50000, "last_active": NOW.isoformat()}
    a = leadfinder.add({**base, "ext_id": "a", "name": "A forex academy"}, now=NOW - timedelta(days=5))
    leadfinder.add({**base, "ext_id": "b", "name": "B", "about": "guaranteed daily profit signals"}, now=NOW - timedelta(days=2))
    leadfinder.update(a, "approved", now=NOW - timedelta(days=5))
    leadfinder.update(a, "contacted", now=NOW - timedelta(days=4))
    leadfinder.update(a, "replied", now=NOW - timedelta(days=3))
    by = {r["key"]: r for r in kpi.scorecard("leads", NOW)}
    assert by["leads_qualified_week"]["value"] == 1                         # the flagged one does not count
    assert by["leads_qualified_rate"]["value"] == 50
    assert by["leads_contact_speed"]["value"] == 100 and by["leads_reply_rate"]["value"] == 100
    assert by["leads_flagged_contacted"]["status"] == "GREEN"
    with leadfinder._db() as db:
        db.execute("UPDATE lead_prospects SET status = 'contacted' WHERE ext_id = 'b'")
    rows = kpi.scorecard(None, NOW)
    assert {r["key"]: r for r in rows}["leads_flagged_contacted"]["status"] == "RED"
    assert "Red-flag leads contacted" in kpi.brake(rows)
    assert "GROWTH BRAKE" in kpi.scorecard_text(now=NOW)


def test_lots_and_trader_kpis_from_the_journal(tmp_path, monkeypatch):
    from jarvis.agents import kpi, personal

    _env(tmp_path, monkeypatch)
    assert kpi.lots("EURUSD", 100_000) == 1 and kpi.lots("XAUUSD", 50) == 0.5 and kpi.lots("US30", 2) == 2

    def t(mins_ago, symbol, qty, net, bal):
        return {"closed_utc": (NOW - timedelta(minutes=mins_ago)).strftime("%Y-%m-%d %H:%M:%S.%f"),
                "symbol": symbol, "side": "buy", "entry": 1, "exit": 1, "qty": qty, "net": net, "balance": bal}

    personal.store_trades("123", [t(600, "EURUSD", 50_000, -20, 980), t(597, "EURUSD", 50_000, 40, 1020),
                                  t(300, "XAUUSD", 100, 30, 1050)])
    by = {r["key"]: r for r in kpi.scorecard("trading", NOW)}
    assert by["tr_lots_min"]["value"] == 2.0 and by["tr_lots_min"]["status"] == "GREEN"
    assert round(by["tr_max_loss"]["value"], 2) == 2.0 and by["tr_max_loss"]["status"] == "AMBER"
    assert by["tr_revenge"]["value"] == 1                                    # back in 3 minutes after a loss
    assert round(by["tr_profit_factor"]["value"], 1) == 3.5
    assert by["tr_stops"]["status"] == "NO DATA"                             # no live positions feed in this test


def test_kpis_reach_hq_discord_and_the_pack(tmp_path, monkeypatch):
    from jarvis import scheduler
    from jarvis.agents import hq, pack

    _env(tmp_path, monkeypatch)
    monkeypatch.setattr(scheduler, "ALERT_TARGETS", [])
    assert "KPIS" in hq.hq_text()
    from donna import discord_bot

    assert "KPI SCORECARD" in discord_bot.handle("!kpi") and "!kpi" in discord_bot.HELP
    assert "COMPLIANCE" in discord_bot.handle("!kpi co")
    titles = [t for t, _ in pack.sections(7)]
    assert "KPI scorecard (grow and protect)" in titles
