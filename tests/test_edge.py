from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.delenv("JARVIS_PERSONAL_ACCOUNT", raising=False)
    return tmp_path


def _store(nets, start=datetime(2026, 11, 2, 8, 0), gaps=None, qty=None, symbols=None, bal=1000.0):
    from jarvis.agents import personal

    rows, t = [], start
    for i, n in enumerate(nets):
        t += timedelta(minutes=(gaps[i] if gaps else 30))
        bal += n
        rows.append({"closed_utc": t.strftime("%Y-%m-%d %H:%M:%S.%f"), "symbol": symbols[i] if symbols else "EURUSD",
                     "side": "BUY" if i % 2 else "SELL", "entry": 1 + i / 1e4, "exit": 1, "qty": qty[i] if qty else 1000,
                     "net": n, "balance": bal})
    personal.store_trades("123", rows)


def test_no_trades_says_so(db):
    from jarvis.agents import edge

    assert "NO DATA" in edge.report()["error"]
    assert "NO DATA" in edge.report_text()


def test_breakeven_win_rate_and_loss_gap(db):
    from jarvis.agents import edge

    # 6 wins of 2, 4 losses of 4: win 60%, payoff 0.5 -> break-even 66.7%; expectancy -0.4
    _store([2, 2, -4, 2, -4, 2, 2, -4, 2, -4])
    x = edge.report()
    s = x["overall"]
    assert s["win_rate"] == 60 and s["payoff"] == 0.5 and s["breakeven_win_rate"] == 66.7
    assert x["gap_to_breakeven"] == -6.7
    # break-even needs |avg loss| = 0.6*2/0.4 = 3 -> 1.00 smaller
    assert x["loss_to_breakeven"] == 1.0
    assert s["expectancy"] == -0.4 and s["profit_factor"] == 0.75


def test_small_groups_are_too_few_to_tell(db):
    from jarvis.agents import edge

    _store([1, -1, 1])
    assert all(r["verdict"] == "too few to tell" for r in edge.report()["symbol"])


def test_quick_reentry_after_a_loss_is_measured(db):
    from jarvis.agents import edge

    # a loss, then a fast losing re-entry (2 min), then normal 30-min spaced wins
    nets = [-5, -3, 2, 2, -5, -3, 2, 2]
    gaps = [30, 2, 30, 30, 30, 2, 30, 30]
    _store(nets, gaps=gaps)
    b = edge.report()["behaviour"]
    assert b["after_loss_fast"]["trades"] == 2 and b["after_loss_fast"]["net"] == -6
    assert b["others"]["trades"] == 6


def test_size_up_after_a_loss(db):
    from jarvis.agents import edge

    _store([-5, -10, 3, 3], qty=[1000, 3000, 1000, 1000])
    assert edge.report()["behaviour"]["size_up_after_loss"]["trades"] == 1


def test_rule_replay(db):
    from jarvis.agents import edge

    # one bad day: a 1% cap would turn the -50 into -10 (balance before ~1000)
    nets = [3, 3, -50, 3, 3, -2, -2, -2, -2, 3]
    _store(nets)
    r = {x["rule"]: x for x in edge.report()["replay"]}
    assert r["cap_losses"]["skipped"] == 0 and r["cap_losses"]["change"] == pytest.approx(40, abs=0.6)
    # loss streak: after 3 losses in a row (-50 isn't adjacent; -2 x3) the 4th -2 and the last +3 are skipped
    assert r["loss_streak"]["skipped"] == 2 and r["loss_streak"]["change"] == -1
    # cooldown 15 min with 30-min spacing never skips
    assert r["cooldown"]["skipped"] == 0
    # daily stop at 2% (20): the -50 trips it, the 7 trades after are skipped
    assert r["daily_stop"]["skipped"] == 7
    assert set(r) == {k for k, _ in edge.RULES}


def test_cooldown_skips_fast_trades_after_a_loss(db):
    from jarvis.agents import edge

    _store([-2, -2, 5, 1], gaps=[30, 3, 3, 30])
    r = {x["rule"]: x for x in edge.report()["replay"]}
    # -2 at 08:30 blocks until 08:45: the -2 at 08:33 and the +5 at 08:36 are skipped
    assert r["cooldown"]["skipped"] == 2 and r["cooldown"]["change"] == -3


def test_hold_time_from_the_pad_records(db):
    from jarvis.agents import edge, pad

    _store([2, -1])
    with pad._db() as c:
        c.execute("INSERT INTO pad_positions (login, position_id, symbol, side, units, opened_utc) "
                  "VALUES ('123','p1','EURUSD','sell',1000,'2026-11-02 08:27:00')")
        c.execute("INSERT INTO pad_closes VALUES ('123','p1','2026-11-02 08:30:00',1,2)")
    x = edge.report()
    assert x["hold_known"] == 1 and x["hold"][0]["group"] == "1-5 min"


def test_command_and_help(db):
    from donna import discord_bot

    _store([2, -1, 2, -1])
    out = discord_bot.handle("!edge")
    assert "MY EDGE" in out and "RULE REPLAY" in out and "break even" in out
    assert "MY EDGE" in discord_bot.handle("!edge 30")
    assert "!edge" in discord_bot.HELP
