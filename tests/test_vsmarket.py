from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.delenv("JARVIS_PERSONAL_ACCOUNT", raising=False)
    from jarvis.agents import vsmarket

    monkeypatch.setattr(vsmarket, "_cache", {})
    return tmp_path


def _gold(monkeypatch, end="2026-11-06", n=200, drift=-0.004):
    """Fake daily gold: a steady fall with alternating noise, so the trend going in is 'down'."""
    from jarvis.adapters import market

    dates = pd.bdate_range(end=end, periods=n)
    rng = np.random.default_rng(1)
    close = 2000 * np.exp(np.cumsum(drift + rng.normal(0, 0.006, n)))
    df = pd.DataFrame({"open": close, "high": close * 1.006, "low": close * 0.994, "close": close}, index=dates)
    df.index.name = "date"
    calls = []

    def fake(inst, period="6y"):
        calls.append(inst.key)
        if inst.key != "XAUUSD":
            return pd.DataFrame(), "unavailable: no data"
        return df, "Yahoo (GC=F)"

    monkeypatch.setattr(market, "daily_bars", fake)
    return df, calls


def _store(day_sides, symbol="XAUUSD.mp", bal=1000.0):
    """day_sides: {date: [(side, net, minutes_after_previous), ...]}"""
    from jarvis.agents import personal

    rows = []
    for day, trades in day_sides.items():
        t = datetime.fromisoformat(day) + timedelta(hours=8)
        for side, net, gap in trades:
            t += timedelta(minutes=gap)
            bal += net
            rows.append({"closed_utc": t.strftime("%Y-%m-%d %H:%M:%S.%f"), "symbol": symbol, "side": side,
                         "entry": 1 + len(rows) / 1e4, "exit": 1, "qty": 1000, "net": net, "balance": bal})
    personal.store_trades("123", rows)


def test_no_trades_says_so(db, monkeypatch):
    from jarvis.agents import vsmarket

    _gold(monkeypatch)
    assert "NO DATA" in vsmarket.report()["error"]
    assert "NO DATA" in vsmarket.command("!vsmarket", "")
    assert "NO DATA" in vsmarket.command("!day", "")


def test_market_days_labels():
    from jarvis.agents import vsmarket

    idx = pd.bdate_range("2026-01-01", periods=80)
    c = np.linspace(100, 60, 80)                       # straight down
    c[-1] = c[-2] * 1.05                               # last day: a big up day
    df = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c}, index=idx)
    md = vsmarket.market_days(df)
    assert md.trend_in.iloc[-1] == "down"              # trend going in is yesterday's: still down
    assert md.direction.iloc[-1] == "up" and md.direction.iloc[-5] == "down"
    assert md.trend_in.iloc[0] == "unknown"            # not enough history for a 50-day average yet


def test_buying_a_falling_market_is_called_out(db, monkeypatch):
    from jarvis.agents import vsmarket

    df, _ = _gold(monkeypatch)
    down = [d for d, r in zip(df.index[-40:], (df.close / df.close.shift() - 1).iloc[-40:]) if r < -0.004]
    assert len(down) >= 6
    trades = {}
    for d in down[:6]:   # each down day: 4 losing buys (2 of them straight after a loss) and 1 winning sell
        trades[d.date().isoformat()] = [("BUY", -3, 30), ("BUY", -3, 2), ("BUY", -2, 2), ("BUY", 1, 30), ("SELL", 4, 30)]
    _store(trades)
    x = vsmarket.report(90)
    row = x["rows"][0]
    assert row["alignment"] == "against" and row["stance"] == "buyer" and row["buy_share"] == 80
    assert row["net_buys"] == -7 and row["net_sells"] == 4 and row["net"] == -3 and row["net_against"] == -7
    assert row["tilt"] == 2
    assert "fell" in row["explanation"] and "buying into a falling market" in row["explanation"]
    assert "cost -$7.00" in row["explanation"] and "within 5 min of a loss" in row["explanation"]
    against = next(a for a in x["by_alignment"] if a["key"] == "against")
    assert against["days"] == 6 and against["net"] == -18
    dm = {g["group"]: g for g in x["day_move"]}
    assert dm["against the day's move"]["trades"] == 24 and dm["with the day's move"]["net"] == 24
    assert x["main"]["buy_share"] == 80 and x["main"]["chg_pct"] < 0
    assert x["bottom_line"]["market"].startswith("Gold fell")
    assert "guide, not a promise" in x["bottom_line"]["caveat"]
    text = vsmarket.command("!vsmarket", "90")
    assert "MARKET VS ME" in text and "BOTTOM LINE" in text and "buys in a downtrend" in text


def test_day_detail_checks_the_rules(db, monkeypatch):
    from jarvis.agents import vsmarket

    df, _ = _gold(monkeypatch)
    day = df.index[-3].date().isoformat()
    # 12 trades: 3 losses in a row early (second one 2 min after the first), then 9 small wins
    sides = [("BUY", -4, 30), ("BUY", -4, 2), ("BUY", -4, 30)] + [("SELL", 1, 30)] * 9
    _store({day: sides})
    x = vsmarket.day_report(day)
    assert x["trades"] == 12 and x["net"] == -3
    r = {k["rule"]: k for k in x["rules"]}
    assert not r["At most 10 trades a day"]["kept"] and "made $2.00" in r["At most 10 trades a day"]["text"]
    assert not r["Wait 15 min after a loss"]["kept"]
    assert not r["Stop after 3 losses in a row"]["kept"] and "after trade 3" in r["Stop after 3 losses in a row"]["text"]
    assert x["rules_kept"] < x["rules_total"]
    text = vsmarket.command("!day", day)
    assert "MY DAY VS THE MARKET" in text and "YOUR RULES TODAY" in text
    assert "no trades on 2026-01-01" in vsmarket.command("!day", "2026-01-01")
    assert vsmarket.command("!day", "not-a-date").startswith("Use !day")
    # the latest trading day is the default
    assert vsmarket.day_report()["date"] == day


def test_unpriced_symbols_are_listed_not_guessed(db, monkeypatch):
    from jarvis.agents import vsmarket

    df, calls = _gold(monkeypatch)
    d = df.index[-2].date().isoformat()
    _store({d: [("BUY", 1, 30)] * 3})
    _store({d: [("SELL", 1, 31)]}, symbol="US500.mp")      # not on the watchlist and not FX
    x = vsmarket.report(30)
    assert x["missing"] == {"US500.mp": 1} and x["trades"] == 3
    vsmarket.report(30)
    assert calls.count("XAUUSD") == 1                         # prices are cached, not fetched every time


def test_weekend_trade_counts_on_the_next_market_day(db, monkeypatch):
    from jarvis.agents import vsmarket

    df, _ = _gold(monkeypatch)
    monday = df.index[-5]
    assert monday.dayofweek == 0
    sunday = (monday - pd.Timedelta(days=1)).date().isoformat()
    _store({sunday: [("BUY", 1, 30)]})
    x = vsmarket.report(30)
    assert x["rows"][0]["date"] == monday.date().isoformat()


def test_instrument_for_maps_broker_symbols():
    from jarvis.adapters.market import instrument_for, watch_key

    assert watch_key("XAUUSD.mp") == "XAUUSD" and watch_key("USTEC") == "NAS100" and watch_key("US500.mp") is None
    assert instrument_for("EURJPY.mp").yahoo == "EURJPY=X"
    assert instrument_for("XAGUSD.mp").key == "XAGUSD"
    assert instrument_for("AAPL") is None


def test_charts_tab(db, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.setenv("DONNA_FILES_DIR", str(db / "files"))
    monkeypatch.setenv("DONNA_API_KEY", "svc")
    from donna import console

    monkeypatch.setattr(console, "LOCAL_CLIENTS", {"testclient"})
    df, _ = _gold(monkeypatch)
    _store({df.index[-2].date().isoformat(): [("BUY", -2, 30), ("SELL", 3, 30)]})
    import donna.app as m

    m = importlib.reload(m)
    with TestClient(m.app) as c:
        assert c.get("/api/charts/vsmarket", headers={"host": "localhost:8765"}).status_code == 404   # no X-Donna header
        x = c.get("/api/charts/vsmarket", headers={"host": "localhost:8765", "X-Donna": "console"}).json()
        page = c.get("/charts", headers={"host": "localhost:8765"}).text
    assert x["rows"] and x["bottom_line"]["market"] and x["detail"][x["rows"][-1]["date"]]["rules"]
    assert x["main"]["close"] and len(x["main"]["t"]) == len(x["main"]["close"])
    assert "Market vs me" in page
