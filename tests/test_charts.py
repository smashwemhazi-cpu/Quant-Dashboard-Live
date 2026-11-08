import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.setenv("DONNA_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("DONNA_API_KEY", "svc")
    from donna import charts, console

    monkeypatch.setattr(console, "LOCAL_CLIENTS", {"testclient"})  # TestClient's address stands in for 127.0.0.1
    monkeypatch.setattr(charts, "_cache", {"at": 0.0, "cards": None})
    import donna.app as m

    m = importlib.reload(m)
    with TestClient(m.app) as c:
        yield c


LOCAL, API = {"host": "localhost:8765"}, {"host": "localhost:8765", "X-Donna": "console"}


def test_charts_only_answer_through_the_tunnel(client):
    page = client.get("/charts", headers=LOCAL)
    assert page.status_code == 200 and "DONNA" in page.text and page.headers["x-frame-options"] == "DENY"
    assert client.get("/charts", headers={**LOCAL, "X-Forwarded-For": "1.2.3.4"}).status_code == 404
    assert client.get("/charts", headers={"host": "evil.example:8765"}).status_code == 404
    assert client.get("/api/charts/data", headers=LOCAL).status_code == 404          # no X-Donna header
    assert client.get("/api/charts/markets", headers=LOCAL).status_code == 404


def test_charts_with_no_data_say_so(client):
    d = client.get("/api/charts/data", headers=API).json()
    assert "NO DATA" in d["account"]["error"] and "NO DATA" in d["risk"]["error"]
    assert "NO DATA" in d["trades"]["error"]
    assert d["compliance"]["pad"]["flags_30d"] == 0


def test_account_risk_and_trades(client):
    from jarvis.agents import personal, rhythm

    pos = [{"symbol": "XAUUSD", "side": "buy", "units": 10, "entry": 2000.0, "stop": 1990.0, "price": 2005.0},
           {"symbol": "EURUSD", "side": "sell", "units": 1000, "entry": 1.10, "stop": None, "price": 1.09}]
    for eq in (1000.0, 1050.0, 990.0, 1020.0):
        rhythm.store_positions(pos, eq, start_of_day_equity=1000.0, source="test")
    trades = [{"closed_utc": f"2026-11-0{i} 10:00:00.000000", "symbol": s, "side": "BUY", "entry": 1, "exit": 1,
               "qty": 1, "net": n, "balance": 1000 + n} for i, (s, n) in enumerate([("XAUUSD", 30), ("EURUSD", -10),
                                                                                    ("XAUUSD", 20)], 1)]
    personal.store_trades("123", trades)
    d = client.get("/api/charts/data", headers=API).json()
    a, r, t = d["account"], d["risk"], d["trades"]
    assert a["equity"] == [1000.0, 1050.0, 990.0, 1020.0] and a["now"] == 1020.0
    assert round(a["max_drawdown_pct"], 2) == round((990 / 1050 - 1) * 100, 2)
    assert a["daily_stop_level"] < 1000.0
    assert r["no_stop"] == ["EURUSD"] and len(r["positions"]) == 2
    assert t["cumulative"] == [30, 20, 40] and t["day_net"] == [30, -10, 20] and t["trades"] == 3
    assert {s["symbol"]: s["net"] for s in t["by_symbol"]} == {"XAUUSD": 50, "EURUSD": -10}


def test_markets_cached_and_show_open_levels(client, monkeypatch):
    from donna import charts
    from jarvis.agents import rhythm

    calls = []

    def fake_load():
        calls.append(1)
        closes = [100 + i for i in range(60)]
        return [charts.market_card("XAUUSD", "Gold", [f"d{i}" for i in range(60)], closes, "test", 2),
                {"key": "WTI", "label": "Oil", "error": "unavailable: no data"}]

    monkeypatch.setattr(charts, "_load_markets", fake_load)
    rhythm.store_positions([{"symbol": "XAUUSD.r", "side": "buy", "units": 1, "entry": 150.0, "stop": 140.0,
                             "price": 159.0}], 1000.0, source="test")
    m = client.get("/api/charts/markets", headers=API).json()
    gold = m["cards"][0]
    assert gold["trend"] == "up" and gold["sma20"][-1] == sum(range(140, 160)) / 20
    assert gold["positions"] == [{"side": "buy", "units": 1, "entry": 150.0, "stop": 140.0}]
    assert m["cards"][1]["error"]
    client.get("/api/charts/markets?refresh=true", headers=API)   # a refresh straight after is served from cache
    assert len(calls) == 1


def test_helpers():
    from donna import charts

    assert charts.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    assert charts.drawdown([100, 110, 99, 120])[1] == pytest.approx(-10.0)
    assert charts.thin(list(range(10)), 4) == [0, 3, 6, 9]
    assert charts._watch_key("US100") == "NAS100" and charts._watch_key("GBPUSD.r") == "GBPUSD"
    assert charts._watch_key("AAPL") is None


def test_my_edge_tab(client):
    from jarvis.agents import personal

    trades = [{"closed_utc": f"2026-11-0{1 + i // 4} {9 + i % 4}:00:00.000000", "symbol": "EURUSD", "side": "BUY",
               "entry": 1 + i / 1e4, "exit": 1, "qty": 1000, "net": n, "balance": 1000 + n}
              for i, n in enumerate([3, -5, 2, 2, -4, 3, 3, -6])]
    personal.store_trades("123", trades)
    e = client.get("/api/charts/data", headers=API).json()["edge"]
    assert e["overall"]["trades"] == 8 and e["overall"]["breakeven_win_rate"] > 50
    assert {r["rule"] for r in e["replay"]} >= {"cooldown", "cap_losses"} and e["behaviour"]["others"]["trades"]
    assert 'data-tab="edge"' in client.get("/charts", headers=LOCAL).text


def _sh_like_trades(n_days=40, per_day=14, deposit_day=20):
    from datetime import datetime, timedelta, timezone

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=n_days)
    out, bal = [], 300.0
    for d in range(n_days):
        t = start + timedelta(days=d, hours=8)
        if d == deposit_day:
            bal += 500   # a deposit: must not show up as a drawdown or a gain
        for k in range(per_day):
            t += timedelta(minutes=3 if k % 3 else 20)
            net = 3.0 if k % 3 else -4.0
            bal += net
            out.append({"closed_utc": t.strftime("%Y-%m-%d %H:%M:%S.000000"), "symbol": "XAUUSD.mp",
                        "side": "BUY" if d % 2 else "SELL", "entry": 4000 + k, "exit": 4001 + k, "qty": 1, "net": net,
                        "balance": round(bal, 2), "opened_utc": (t - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")})
    return out


def test_pad_and_kpis_are_plain_statements(client):
    from jarvis.agents import kpi, personal

    personal.store_trades("123", _sh_like_trades())
    c = client.get("/api/charts/data", headers=API).json()["compliance"]
    rows = c["kpi"]["rows"]
    assert len(rows) == len(kpi.STARTER) - len(kpi.unused()) and all(r["why"] and r["word"] for r in rows)
    order = [kpi.ORDER[r["status"]] for r in rows]
    assert order == sorted(order)                                    # worst first
    over = next(r for r in rows if r["key"] == "tr_overtrading")
    assert over["status"] == "RED" and over["value"] > 0 and "PAD" in over["note"]   # 14 a day > the PAD limit of 10
    assert over["trend"] is not None and "days" in over["trend"]
    nod = [r for r in rows if r["status"] == "NO DATA"]
    assert nod and all(r["needs"] for r in nod)
    # PAD: no trades since the policy start -> "no trades", never "kept"; the look-back says how it would score
    p = c["pad"]
    assert p["trades_checked"] == 0 and {r["status"] for r in p["rules"]} == {"no trades"}
    assert p["practice"]["busy_days"] > 0 and p["practice"]["wins_before_hold"] == p["practice"]["wins_hold_known"] > 0
    d = c["discipline"]
    assert d["summary"]["days"] and d["summary"]["clean"] == 0 and d["summary"]["broken"]["trades"] == d["summary"]["days"]


def test_drawdown_ignores_deposits(client):
    from datetime import datetime, timezone

    from jarvis.agents import kpi, personal

    personal.store_trades("123", _sh_like_trades())
    v = kpi.MEASURES["tr_drawdown"](datetime.now(timezone.utc))
    assert isinstance(v, tuple) and 0 < v[0] < 5          # small, steady losses: not a fake 80% from the deposit


def test_setup_gap_is_no_data_not_red(client, monkeypatch):
    from jarvis.agents import kpi

    states = {"daily_wrap": {"state": "waiting", "why": "JARVIS_REPORT_TARGETS not set"}}
    r = kpi.score_one("desk_wraps", states=states)
    assert r["status"] == "NO DATA" and "waiting for setup" in r["note"]
    assert kpi.score_one("desk_wraps", states={"daily_wrap": {"state": "ok"}})["status"] == "RED"


def test_kpi_history_and_streak(client):
    from jarvis.agents import kpi

    h = [("2026-11-01", 1, "RED"), ("2026-11-03", 1, "GREEN"), ("2026-11-04", 1, "GREEN"), ("2026-11-05", 1, "GREEN")]
    assert kpi.streak(h) == {"status": "GREEN", "days": 3, "since": "2026-11-03"}
    assert kpi.daily_snapshot() is True and kpi.daily_snapshot() is False
    assert kpi.history()


def test_agents_tab(client):
    from jarvis.agents import personal

    personal.store_trades("123", _sh_like_trades())
    a = client.get("/api/charts/agents", headers=API).json()
    assert a["purpose"] and a["boundaries"] and len(a["duties"]) == 7
    trading = next(d for d in a["duties"] if d["key"] == "trading")
    assert trading["kpis"] and set(trading["kpi_counts"]) == {"RED", "AMBER", "NO DATA", "GREEN"}
    page = client.get("/charts", headers=LOCAL).text
    assert 'data-tab="agents"' in page

