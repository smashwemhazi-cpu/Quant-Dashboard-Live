"""Discipline guard: live taps while SH trades (cool-off, too soon, big loss, trade count, losing run, PAD flags)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)


def _t(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _pos(pid, opened_ago, closed_ago=None, net=None, sym="XAUUSD"):
    return {"position_id": str(pid), "symbol": sym, "opened_utc": _t(opened_ago),
            "closed_utc": _t(closed_ago) if closed_ago is not None else None, "net": net}


def test_fresh_loss_starts_a_cool_off_with_size_warning():
    from jarvis.agents import guard

    out = dict(guard.assess([_pos(1, 20, 3, -9.0)], balance=450.0, now=NOW))
    msg = out["cooloff:1"]
    assert "Cool-off: no new trade until 10:12" in msg and "-$9.00" in msg
    assert "2.0% of the balance" in msg                                   # 9/450 > 1%
    small = dict(guard.assess([_pos(1, 20, 3, -1.0)], balance=450.0, now=NOW))
    assert "of the balance" not in small["cooloff:1"]


def test_trade_inside_cool_off_taps_once_and_extra_losses_stay_quiet():
    from jarvis.agents import guard

    rows = [_pos(1, 20, 10, -2.0), _pos(2, 8, 6, -1.5), _pos(3, 5), _pos(4, 4)]
    out = guard.assess(rows, balance=1000.0, now=NOW)
    keys = [k for k, _ in out]
    too_soon = [m for k, m in out if k.startswith("toosoon:")]
    assert len(too_soon) == 2                     # one per cool-off (loss 1 -> trade 2, loss 2 -> trades 3 and 4)
    assert "after a loss, inside the 15-min cool-off" in too_soon[0]
    # loss 2 closed inside loss 1's cool-off: it extends it silently, no second cool-off message
    assert dict(out).get("cooloff:2") in (None, "") and "cooloff:1" in keys


def test_old_trades_never_tap():
    from jarvis.agents import guard

    rows = [_pos(1, 300, 200, -50.0), _pos(2, 190)]
    assert guard.assess(rows, balance=100.0, now=NOW) == []


def test_trade_count_and_losing_run():
    from jarvis.agents import guard

    rows = [_pos(i, 120 - i * 10, 115 - i * 10, 1.0) for i in range(5)]          # 5 winners today
    rows += [_pos(10 + i, 60 - i * 15, 55 - i * 15, -0.5) for i in range(3)]       # then 3 losers, last 25 min ago
    out = dict(guard.assess(rows, balance=1000.0, now=NOW, limit=10))
    assert "8 of 10 trades used today" in out["near:2026-11-09"]
    assert "3 losses in a row today (-$1.50)" in out["streak:2026-11-09"]
    rows += [_pos(20, 2), _pos(21, 1)]
    out = dict(guard.assess(rows, balance=1000.0, now=NOW, limit=10))
    assert "PAD limit of 10 is reached" in out["limit:2026-11-09"]


def test_check_sends_each_tap_once_through_the_alert_protocol(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "donna.db"))
    from jarvis.agents import alerts, guard, pad

    sent = []
    monkeypatch.setattr(alerts, "_send", lambda kind, level, text, repeat=0: sent.append((kind, text)) or {})
    monkeypatch.setattr(alerts, "HOURS", "07:00-08:00")          # outside alert hours: discipline still goes out
    with pad._db() as c:
        c.execute("INSERT INTO pad_positions (login, position_id, symbol, side, units, opened_utc, closed_utc, net) "
                  "VALUES ('42', '1', 'EURUSD', 'buy', 1000, ?, ?, -3.0)", (_t(30), _t(2)))
    flags = [{"position_id": "1", "symbol": "EURUSD", "check": "no_stop", "detail": "EURUSD opened without a stop"},
             {"position_id": "day:x", "symbol": "-", "check": "activity", "detail": "11 trades"}]
    msgs = guard.check("42", 1000.0, flags, now=NOW)
    assert len(msgs) == 2 and any("No stop-loss" in m for m in msgs) and not any("11 trades" in m for m in msgs)
    assert [k for k, _ in sent] == ["discipline", "discipline"]
    assert guard.check("42", 1000.0, flags, now=NOW + timedelta(minutes=5)) == []      # a restart never repeats
    assert "Trades today" in guard.command("!guard", "") or "isn't connected" in guard.command("!guard", "")
    assert "cool-off after a loss" in alerts.policy_text()


def test_quiet_mode_holds_discipline_taps(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "donna.db"))
    from jarvis.agents import alerts

    monkeypatch.setattr(alerts, "_send", lambda *a, **k: {})
    alerts.command("!quiet", "2h")
    assert alerts.raise_alert("discipline", "Cool-off until 10:15") == "held"
