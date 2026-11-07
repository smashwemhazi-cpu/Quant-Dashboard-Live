from datetime import date, datetime, timezone

import pytest


@pytest.fixture
def pad(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    from jarvis.agents import pad as p

    monkeypatch.setattr(p, "EFFECTIVE", "2026-11-01")
    monkeypatch.setattr(p, "_local_tz", lambda: timezone.utc)
    return p


def ms(s):
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp() * 1000)


def pos(pid, symbol, side="buy", units=1000.0, entry=1.10, price=1.10, stop=1.09, opened="2026-11-09 08:00", rate=1.0):
    return {"position_id": pid, "symbol": symbol, "side": side, "units": units, "entry": entry, "price": price,
            "stop": stop, "opened_ms": ms(opened), "quote_to_account": rate}


def closed(pid, symbol, net, closed_at, opened_at=None, side="buy"):
    return {"position_id": pid, "symbol": symbol, "side": side, "qty": 1000, "entry": 1.1, "exit": 1.11,
            "net": net, "closed_utc": closed_at + ":00.000000", "opened_utc": opened_at and opened_at + ":00"}


def checks(new):
    new = new["new_flags"] if isinstance(new, dict) else new
    return sorted(f["check"] for f in new)


def test_classes(pad):
    assert pad.asset_class("EURUSD") == "forex"
    assert pad.asset_class("XAUUSD") == "metal"
    assert pad.asset_class("BTCUSD") == "crypto"
    assert pad.asset_class("US30") == "index" and pad.asset_class("GER40") == "index"
    assert pad.asset_class("USOIL") == "commodity"
    assert pad.asset_class("AAPL.US") == "share" and pad.asset_class("TSLA") == "share"
    assert pad.norm("eur/usd.r") == "EURUSD" and pad.norm("XAUUSD+") == "XAUUSD"
    pad.set_class("TSLA", "index")
    assert pad.asset_class("TSLA") == "index"


def test_clean_fx_trade_raises_nothing(pad):
    now = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)
    r = pad.record("1", [pos("1", "EURUSD")], [], 10_000.0, now=now)
    assert r["new_flags"] == []


def test_no_stop_after_grace_and_risk_and_leverage(pad):
    now = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)
    # 3m units of EURUSD on 10k equity = 1:330 (> 1:200); stop 1.00 risks 300k... far over 2%
    big = pos("2", "EURUSD", units=3_000_000, stop=1.00)
    nostop = pos("3", "GBPUSD", stop=None)
    new = pad.record("1", [big, nostop], [], 10_000.0, now=now)
    assert checks(new) == ["leverage", "no_stop", "risk"]
    # same snapshot again: flags are raised once
    assert pad.record("1", [big, nostop], [], 10_000.0, now=now)["new_flags"] == []


def test_stop_grace_period(pad):
    now = datetime(2026, 11, 9, 8, 2, tzinfo=timezone.utc)
    assert pad.record("1", [pos("4", "EURUSD", stop=None, opened="2026-11-09 08:00")], [], 10_000.0,
                      now=now)["new_flags"] == []


def test_crypto_needs_clearance_before_the_trade(pad):
    now = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)
    btc = pos("5", "BTCUSD", units=0.01, entry=90_000, price=90_000, stop=89_000)
    assert checks(pad.record("1", [btc], [], 10_000.0, now=now)["new_flags"]) == ["clearance"]

    pad.add_clearance("BTCUSD", "buy", 0.01, "test", now=datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc))
    btc2 = pos("6", "BTCUSD", units=0.01, entry=90_000, price=90_000, stop=89_000, opened="2026-11-10 08:00")
    assert pad.record("1", [btc2], [], 10_000.0, now=datetime(2026, 11, 10, 9, tzinfo=timezone.utc))["new_flags"] == []


def test_restricted_list_blocks_clearance_and_flags(pad):
    assert "Restricted List" in pad.command("!pad", "restrict EURUSD LP negotiation")
    assert "cannot be cleared" in pad.command("!pad", "clear EURUSD buy 0.1 test")
    pad._db().execute("UPDATE pad_lists SET added_utc = '2026-11-01 00:00:00'").connection.commit()
    new = pad.record("1", [pos("7", "EURUSD")], [], 10_000.0, now=datetime(2026, 11, 9, 9, tzinfo=timezone.utc))
    assert checks(new) == ["restricted"]


def test_holding_period(pad):
    now = datetime(2026, 11, 12, 9, tzinfo=timezone.utc)
    new = pad.record("1", [], [closed("8", "EURUSD", 25.0, "2026-11-09 20:00", "2026-11-09 08:00"),   # 12 h, profit
                               closed("9", "EURUSD", -25.0, "2026-11-09 20:00", "2026-11-09 08:00"),  # loss: allowed
                               closed("10", "XAUUSD", 40.0, "2026-11-12 08:00", "2026-11-09 08:00")],  # 72 h: fine
                     10_000.0, now=now)["new_flags"]
    assert [(f["position_id"], f["check"]) for f in new] == [("8", "holding")]


def test_share_hold_in_working_days(pad):
    pad.add_clearance("AAPL.US", "buy", 1, "earnings", now=datetime(2026, 11, 2, 7, tzinfo=timezone.utc))
    t = closed("11", "AAPL.US", 50.0, "2026-11-13 10:00", "2026-11-02 08:00")    # 9 working days
    new = pad.record("1", [], [t], 10_000.0, now=datetime(2026, 11, 13, 11, tzinfo=timezone.utc))
    assert checks(new) == ["holding"]


def test_partial_and_repeated_closes_count_once(pad):
    now = datetime(2026, 11, 12, 9, tzinfo=timezone.utc)
    a = closed("12", "EURUSD", 10.0, "2026-11-11 09:00", "2026-11-09 08:00")
    b = closed("12", "EURUSD", 5.0, "2026-11-11 10:00", "2026-11-09 08:00")
    pad.record("1", [], [a, b], 10_000.0, now=now)
    pad.record("1", [], [b], 10_000.0, now=now)          # the feed's hour of overlap re-reads b
    row = pad._db().execute("SELECT net, closed_utc FROM pad_positions WHERE position_id = '12'").fetchone()
    assert row["net"] == 15.0 and row["closed_utc"] == "2026-11-11 10:00:00"


def test_activity_and_opposite(pad):
    now = datetime(2026, 11, 9, 20, tzinfo=timezone.utc)
    ps = [pos(str(100 + i), "EURUSD", opened=f"2026-11-09 {8 + i:02d}:00") for i in range(11)]
    ps.append(pos("200", "EURUSD", side="sell", stop=1.11, opened="2026-11-09 07:00"))
    assert checks(pad.record("1", ps, [], 1_000_000.0, now=now)["new_flags"]) == ["activity", "opposite"]


def test_before_effective_date_is_ignored(pad):
    new = pad.record("1", [pos("13", "BTCUSD", stop=None, opened="2026-10-20 08:00")], [], 10_000.0,
                     now=datetime(2026, 11, 9, tzinfo=timezone.utc))
    assert new["new_flags"] == []


def test_equity_tier_lowers_cap(pad):
    assert pad.cap_for("forex", 10_000) == 200 and pad.cap_for("forex", 60_000) == 100
    assert pad.cap_for("crypto", 60_000) == 50


def test_commands(pad):
    assert "PAD STATUS" in pad.command("!pad", "")
    assert "Usage" in pad.command("!pad", "clear BTCUSD")
    out = pad.command("!pad", "clear btc/usd long 0.05 swing idea")
    assert "Clearance #1" in out and "does not place" in out
    assert "Watch List" in pad.command("!pad", "watch XAUUSD client book")
    assert "XAUUSD" in pad.command("!pad", "list")
    assert "removed" in pad.command("!pad", "unlist XAUUSD")
    assert "1:200" in pad.command("!pad", "rules")
    assert pad.command("!journal", "") is None


def test_monthly_pack(pad):
    pad.record("1", [], [closed("14", "EURUSD", 25.0, "2026-11-09 20:00", "2026-11-09 08:00")], 10_000.0,
               now=datetime(2026, 11, 10, tzinfo=timezone.utc))
    label, secs = pad.pack_sections("2026-11")
    titles = [t for t, _ in secs]
    assert label == "2026-11" and "Flags" in titles and "Trades" in titles
    assert any("YES" in line for line in dict(secs)["Trades"])
    pdf = pad.build_pdf("2026-11")
    assert pdf.startswith(b"%PDF")
    assert pad.month_bounds(None, date(2027, 1, 1)) == ("2026-12", "2026-12-01", "2027-01-01")
    assert pad.PadAgent().monthly(send=False, today=date(2026, 12, 2))["skipped"]
    assert pad.PadAgent().monthly(send=False, today=date(2026, 12, 1))["month"] == "2026-11"


def test_discord_routes_pad(pad):
    from donna import discord_bot

    assert "PAD STATUS" in discord_bot.handle("!pad", "donna")
    assert "!pad" in discord_bot.HELP
