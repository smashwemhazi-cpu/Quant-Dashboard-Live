"""Discipline guard: DONNA taps you on the shoulder while you trade, using your own rules and your own history.

Runs after every read-only cTrader snapshot (every 5 minutes) on the PAD records of your own account. It never
places, changes or closes a trade; it only tells you, on your phone and in Discord, the moment a rule is at risk:

  Cool-off      a losing trade just closed: no new trade for DONNA_COOLOFF_MIN (default 15) minutes. Your own
                history (!edge) shows trades opened within 5 minutes of a loss lose money and the rest make money.
  Too soon      a new trade was opened inside that cool-off.
  Big loss      a single loss bigger than DONNA_GUARD_LOSS_PCT (default 1) % of the balance.
  Trade count   a heads-up 2 trades before the PAD limit (s.6.2, 10 a day), and a stop when it is reached.
  Losing run    DONNA_GUARD_STREAK (default 3) losses in a row today: stop for the day.
  PAD flag      any new PAD policy flag (no stop, risk over 2%, restricted symbol...) as soon as it is seen.

Each tap goes out once (a restart never repeats it) and only for trades from the last 30 minutes, so a history
backfill stays quiet. They go through the alert protocol as "discipline": sent at any hour you are trading
(you are at the screen), still silenced by !quiet. Plain rules, no Claude credit.
  !guard        today's state: cool-off, trades used, losses in a row, and the settings
DONNA_GUARD=0 switches it off.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from jarvis.core import audit

ON = os.getenv("DONNA_GUARD", "1") not in ("0", "off", "false", "no")
COOLOFF_MIN = float(os.getenv("DONNA_COOLOFF_MIN", "15"))
LOSS_PCT = float(os.getenv("DONNA_GUARD_LOSS_PCT", "1"))
STREAK = int(os.getenv("DONNA_GUARD_STREAK", "3"))
FRESH_MIN = 30          # only trades opened or closed this recently can trigger a tap


def _dt(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _money(v: float) -> str:
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def _limit() -> int:
    from jarvis.agents import pad

    return pad.MAX_TRADES_DAY


def _tz() -> Any:
    from jarvis.agents.mind import LOCAL_TZ

    return LOCAL_TZ


def assess(rows: list[dict[str, Any]], balance: float | None, now: datetime, tz: Any = timezone.utc,
           limit: int = 10) -> list[tuple[str, str]]:
    """Pure rules. rows: this account's positions (position_id, symbol, opened_utc, closed_utc, net).
    Returns (once-key, message) pairs; the caller sends each key once."""
    out: list[tuple[str, str]] = []
    fresh = now - timedelta(minutes=FRESH_MIN)
    today = now.astimezone(tz).date()
    closes = sorted((r for r in rows if r.get("closed_utc") and r.get("net") is not None), key=lambda r: r["closed_utc"])
    opens = sorted((r for r in rows if r.get("opened_utc")), key=lambda r: r["opened_utc"])
    losses = [r for r in closes if r["net"] < 0]

    # cool-off after the latest fresh loss (one tap per loss; several losses in one snapshot -> one message)
    fresh_losses = [r for r in losses if _dt(r["closed_utc"]) >= fresh]
    if fresh_losses:
        last = fresh_losses[-1]
        until = _dt(last["closed_utc"]) + timedelta(minutes=COOLOFF_MIN)
        prev = [r for r in losses if r["closed_utc"] < last["closed_utc"]]
        # a loss inside a running cool-off only extends it silently: the "too soon" tap already covered that trade
        inside = bool(prev) and _dt(last["closed_utc"]) - _dt(prev[-1]["closed_utc"]) < timedelta(minutes=COOLOFF_MIN)
        if until > now and not inside:
            msg = (f"Loss closed: {last['symbol']} {_money(last['net'])}. Cool-off: no new trade until "
                   f"{until.astimezone(tz):%H:%M} ({COOLOFF_MIN:g} min). In your own history, trades opened within "
                   f"5 min of a loss lose money and the rest make money (!edge).")
            if balance and balance > 0 and -last["net"] / balance * 100 > LOSS_PCT:
                msg += (f" That one loss was {-last['net'] / balance * 100:.1f}% of the balance; your rule is "
                        f"{LOSS_PCT:g}%: put the hard stop in before the next entry.")
            out.append((f"cooloff:{last['position_id']}", msg))
        for r in fresh_losses[:-1]:
            out.append((f"cooloff:{r['position_id']}", ""))   # mark as handled, no extra message
        # a big loss that closed after its cool-off ended still deserves the size warning
        if until <= now and balance and balance > 0 and -last["net"] / balance * 100 > LOSS_PCT:
            out.append((f"bigloss:{last['position_id']}",
                        f"{last['symbol']} lost {_money(last['net'])}, {-last['net'] / balance * 100:.1f}% of the "
                        f"balance (your rule: {LOSS_PCT:g}%). Put a hard stop on every trade."))

    # a trade opened inside a cool-off
    for o in opens:
        t = _dt(o["opened_utc"])
        if t < fresh:
            continue
        before = [r for r in losses if _dt(r["closed_utc"]) <= t and r["position_id"] != o["position_id"]]
        if before:
            gap = (t - _dt(before[-1]["closed_utc"])).total_seconds() / 60
            if gap < COOLOFF_MIN:      # one tap per cool-off, however many trades follow
                out.append((f"toosoon:{before[-1]['position_id']}",
                            f"You opened {o['symbol']} {gap:.0f} min after a loss, inside the {COOLOFF_MIN:g}-min "
                            f"cool-off. This is the pattern that costs you most. Check the stop is in, or step away."))

    # trades used today (PAD s.6.2)
    n = sum(1 for o in opens if _dt(o["opened_utc"]).astimezone(tz).date() == today)
    if n >= limit:
        out.append((f"limit:{today}", f"{n} trades today: the PAD limit of {limit} is reached (s.6.2). "
                                      f"Stop for today; tomorrow is a fresh day."))
    elif limit > 3 and n >= limit - 2:
        out.append((f"near:{today}", f"{n} of {limit} trades used today (PAD s.6.2). Make the last ones count."))

    # losing run today
    today_closes = [r for r in closes if _dt(r["closed_utc"]).astimezone(tz).date() == today]
    if len(today_closes) >= STREAK and all(r["net"] < 0 for r in today_closes[-STREAK:]) \
            and _dt(today_closes[-1]["closed_utc"]) >= fresh:
        run = sum(r["net"] for r in today_closes[-STREAK:])
        out.append((f"streak:{today}", f"{STREAK} losses in a row today ({_money(run)}). Stop for the day: in your "
                                       f"rule replay, stopping after {STREAK} losses in a row made money (!edge)."))
    return list(dict(reversed(out)).items())[::-1]   # one message per key, the first one found


def _rows(login: str, now: datetime) -> list[dict[str, Any]]:
    from jarvis.agents import pad

    since = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    with pad._db() as c:
        return [dict(r) for r in c.execute(
            "SELECT position_id, symbol, opened_utc, closed_utc, net FROM pad_positions WHERE login = ? AND "
            "(opened_utc >= ? OR closed_utc >= ?)", (login, since, since))]


def _once(key: str) -> bool:
    from jarvis.agents.mind import _once as once

    return once(f"guard:{key}")


def check(login: str, balance: float | None, pad_flags: list[dict[str, Any]] | None = None,
          now: datetime | None = None, send: bool = True) -> list[str]:
    """Called by the cTrader feed after each snapshot. Returns the messages it raised. Never raises."""
    if not ON:
        return []
    now = now or datetime.now(timezone.utc)
    msgs: list[str] = []
    try:
        found = assess(_rows(login, now), balance, now, _tz(), _limit())
        from jarvis.agents import pad

        for f in pad_flags or []:
            chk = f.get("check", "")
            if chk == "activity":      # the trade-count tap above already says it, in plainer words
                continue
            found.append((f"pad:{f.get('position_id')}:{chk}",
                          f"PAD flag: {pad.CHECK_WORDS.get(chk, 'PAD rule')}. {f.get('detail', '')}".strip()))
        fresh = [(k, m) for k, m in found if _once(k) and m]
        msgs = [m for _, m in fresh]
        if send:
            from jarvis.agents import alerts

            for k, m in fresh:
                alerts.raise_alert("discipline", m, key=f"guard:{k}"[:64], now=now)
    except Exception as e:  # the guard must never stop the feed
        audit.log("guard", "error", {"error": f"{type(e).__name__}: {str(e)[:200]}"})
    return msgs


def status_lines(login: str = "", now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    login = login or os.getenv("DONNA_CTRADER_LOGIN", os.getenv("JARVIS_PERSONAL_ACCOUNT", "")).strip()
    tz, limit = _tz(), _limit()
    L = [f"Discipline guard: {'on' if ON else 'off (DONNA_GUARD=0)'}. Read-only: it only tells you, it never trades."]
    if not login:
        return L + ["Your cTrader account isn't connected, so there is nothing to watch yet."]
    rows = _rows(login, now)
    today = now.astimezone(tz).date()
    n = sum(1 for r in rows if r.get("opened_utc") and _dt(r["opened_utc"]).astimezone(tz).date() == today)
    closes = sorted((r for r in rows if r.get("closed_utc") and r.get("net") is not None), key=lambda r: r["closed_utc"])
    losses = [r for r in closes if r["net"] < 0]
    if losses:
        until = _dt(losses[-1]["closed_utc"]) + timedelta(minutes=COOLOFF_MIN)
        L.append(f" Cool-off: ON until {until.astimezone(tz):%H:%M}." if until > now else " Cool-off: none now.")
    run = 0
    for r in reversed([r for r in closes if _dt(r["closed_utc"]).astimezone(tz).date() == today]):
        if r["net"] >= 0:
            break
        run += 1
    L.append(f" Trades today: {n} of {limit}. Losses in a row today: {run} (stop at {STREAK}).")
    L.append(f" Rules: {COOLOFF_MIN:g}-min cool-off after a loss; a single loss over {LOSS_PCT:g}% of the balance; "
             f"{limit} trades a day; stop after {STREAK} losses in a row; every PAD flag.")
    return L


def command(cmd: str, rest: str) -> str | None:
    if cmd not in ("!guard", "!cooloff"):
        return None
    return "\n".join(status_lines())
