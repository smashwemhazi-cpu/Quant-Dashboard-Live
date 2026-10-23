"""D.O.N.N.A's daily rhythm: one morning message and one evening message instead of a stream of reports.

Morning  NEEDS YOU (ranked: overdue work, urgent email, compliance, risk, bots), focus, calendar,
         position risk, markets in three lines, and where to find the detail (!today, !compliance ...).
Evening  the shutdown (done / open / tomorrow's top 3), the trading day, and markets at the close.

With DONNA_DIGEST=1 (the default) the scheduler sends these two to JARVIS_PERSONAL_TARGETS and stops sending
the separate my-day brief, compliance report, marketing drafts, evening shutdown and AI morning brief there.
Interrupts still arrive on their own: urgent email, meeting reminders, the midday check and alerts.
Team reports (morning dispatch, daily wrap) to JARVIS_REPORT_TARGETS are unchanged.

Position risk  Live positions arrive as a snapshot (POST /api/positions on the DONNA service, or
               store_positions() from any feed). Each section works on its own: a missing feed says NO DATA.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.core import audit
from jarvis.core.config import settings
from jarvis.core.registry import Agent, register, tool

LOCAL_TZ = ZoneInfo(os.getenv("JARVIS_LOCAL_TZ", "UTC"))
DIGEST = os.getenv("DONNA_DIGEST", "1").strip().lower() not in ("0", "false", "no", "off")
# Personal-channel jobs the two digests replace (see the scheduler).
REPLACED_JOBS = {"my_day", "compliance_report", "marketing_drafts", "shutdown", "morning_brief"}
POSITIONS_STALE_HOURS = float(os.getenv("DONNA_POSITIONS_STALE_HOURS", "12"))
LINE = "-" * 34


def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS position_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, "
              "source TEXT, equity REAL, start_of_day_equity REAL, positions TEXT)")
    return c


# ------------------------------------------------------------------ position risk
def store_positions(positions: list[dict[str, Any]], equity: float, start_of_day_equity: float | None = None,
                    source: str = "manual") -> int:
    """positions: [{symbol, side (buy/sell), units, entry, stop (or null), price, quote_to_account (default 1)}]."""
    with _db() as c:
        return c.execute("INSERT INTO position_snapshots (ts, source, equity, start_of_day_equity, positions) "
                         "VALUES (?,?,?,?,?)", (datetime.now(timezone.utc).isoformat(timespec="seconds"), source,
                                                equity, start_of_day_equity, json.dumps(positions))).lastrowid


def position_risk(now: datetime | None = None) -> dict[str, Any]:
    """Risk at stop per open position, total open risk vs the limit, and room left before the daily-loss stop."""
    with _db() as c:
        row = c.execute("SELECT * FROM position_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return {"error": "NO DATA - live positions not connected (POST /api/positions)"}
    now = now or datetime.now(timezone.utc)
    age_h = (now - datetime.fromisoformat(row["ts"])).total_seconds() / 3600
    eq, sod = row["equity"], row["start_of_day_equity"]
    out: list[dict[str, Any]] = []
    for p in json.loads(row["positions"]):
        sign = 1 if p["side"].lower() == "buy" else -1
        fx = p.get("quote_to_account") or 1.0
        floating = sign * (p["price"] - p["entry"]) * p["units"] * fx
        stop = p.get("stop")
        # loss from the current price to the stop; 0 once the stop is locked in profit
        at_stop = None if stop is None else max(0.0, sign * (p["price"] - stop) * p["units"] * fx)
        locked = stop is not None and sign * (stop - p["entry"]) >= 0
        out.append({"symbol": p["symbol"], "side": p["side"], "units": p["units"], "floating": floating,
                    "risk_at_stop": at_stop, "no_stop": stop is None, "stop_in_profit": locked})
    open_risk = sum(x["risk_at_stop"] or 0 for x in out)
    res: dict[str, Any] = {
        "asof": row["ts"], "source": row["source"], "stale": age_h > POSITIONS_STALE_HOURS, "age_hours": age_h,
        "equity": eq, "positions": out, "open_risk": open_risk,
        "open_risk_pct": open_risk / eq * 100 if eq else None, "open_risk_limit_pct": settings.max_open_risk_pct,
        "floating": sum(x["floating"] for x in out), "no_stop": [x["symbol"] for x in out if x["no_stop"]],
    }
    if sod:
        limit = sod * settings.max_daily_loss_pct / 100
        res["day_pnl"] = eq - sod
        res["daily_loss_room"] = limit + (eq - sod)       # what can still be lost today before the kill switch
        res["daily_loss_limit"] = limit
    return res


def _m(v: float) -> str:
    return f"-${-v:,.2f}" if v < 0 else f"${v:,.2f}"


def risk_lines() -> list[str]:
    r = position_risk()
    if "error" in r:
        return ["POSITION RISK", f" {r['error']}"]
    L = [f"POSITION RISK (as of {r['asof'][:16]} UTC" + (f", !! {r['age_hours']:.0f}h old" if r["stale"] else "") + ")"]
    if not r["positions"]:
        L.append(" Flat - no open positions")
    for p in r["positions"]:
        risk = "NO STOP" if p["no_stop"] else "stop in profit" if p["stop_in_profit"] else f"risk {_m(p['risk_at_stop'])}"
        L.append(f" {p['symbol']} {p['side']} {p['units']:,.0f} | floating {_m(p['floating'])} | {risk}")
    if r["open_risk_pct"] is not None:
        flag = " !!" if r["open_risk_pct"] > r["open_risk_limit_pct"] else ""
        L.append(f"{flag} Open risk {_m(r['open_risk'])} = {r['open_risk_pct']:.2f}% of equity "
                 f"(limit {r['open_risk_limit_pct']:g}%)")
    if r["no_stop"]:
        L.append(f" !! No hard stop on {', '.join(r['no_stop'])} - the risk is the whole position")
    if "daily_loss_room" in r:
        L.append(f" Today {_m(r['day_pnl'])} | room before the daily-loss stop {_m(r['daily_loss_room'])}"
                 + (" !! KILL SWITCH - no new risk today" if r["daily_loss_room"] <= 0 else ""))
    return L


# ------------------------------------------------------------------ sections (each one fails on its own)
def _safe(fn: Any, label: str) -> list[str]:
    try:
        return fn()
    except Exception as e:  # one dead source must not stop the message
        audit.log("rhythm", "section_error", {"section": label, "error": str(e)})
        return [f"{label}: unavailable ({type(e).__name__})"]


def needs_you() -> list[str]:
    """The ranked 'act on this' list: each item is something only the owner can move."""
    from jarvis.agents import assistant, personal

    today = datetime.now(LOCAL_TZ).date().isoformat()
    items: list[tuple[int, str]] = []
    for t in assistant.list_tasks():
        if t["due"] and t["due"] < today:
            items.append((0 if t["priority"] == "high" else 3, f"Overdue #{t['id']}: {t['title']}"))
        elif t["due"] == today and t["priority"] == "high":
            items.append((1, f"Due today #{t['id']}: {t['title']}"))
    try:
        ib = assistant.inbox_status()
        items += [(1, f"Urgent email: {r['subject'][:80]}") for r in ib["high"][:3]]
    except Exception as e:
        items.append((5, f"Inbox check failed ({type(e).__name__})"))
    try:
        from jarvis.agents import compliance

        reds = [k for k in compliance.kris() if k["status"] == "RED"]
        items += [(1, f"Compliance KRI red: {k['kri']} = {k['value']}") for k in reds]
        overdue = [c for c in compliance.list_cases() if c["due"] and c["due"] < today]
        if overdue:
            items.append((2, f"{len(overdue)} compliance case(s) overdue, oldest #{overdue[0]['id']} "
                             f"{overdue[0]['subject']}"))
        due = compliance.list_obligations(days=0)
        items += [(1, f"Regulatory obligation due: {o['name']} ({o['due']})") for o in due[:3]]
    except Exception as e:
        items.append((5, f"Compliance check failed ({type(e).__name__})"))
    r = position_risk()
    if "error" not in r:
        if r["no_stop"]:
            items.append((0, f"Position without a stop: {', '.join(r['no_stop'])}"))
        if r.get("daily_loss_room", 1) <= 0:
            items.append((0, "Daily-loss limit hit - no new trades today"))
        if r["open_risk_pct"] and r["open_risk_pct"] > r["open_risk_limit_pct"]:
            items.append((1, f"Open risk {r['open_risk_pct']:.1f}% is over the {r['open_risk_limit_pct']:g}% limit"))
    for b in personal.bots():
        if b["state"] == "crashed" or b["hours_since"] > personal.BOT_STALE_HOURS:
            items.append((2, f"Bot {b['bot']} {b['symbol']}: {b['state']}, last seen {b['hours_since']:.0f}h ago"))
        elif b["failures"]:
            items.append((3, f"Bot {b['bot']}: {b['failures']} failures in 24h"))
    items.sort(key=lambda x: x[0])
    return ["NEEDS YOU"] + ([f" {n}. {t}" for n, (_, t) in enumerate(items[:8], 1)] or [" Nothing urgent. Good."]) \
        + ([f" ... and {len(items) - 8} more (!tasks, !cases)"] if len(items) > 8 else [])


def calendar_lines() -> list[str]:
    from jarvis.agents import assistant

    cal = assistant.calendar_events()
    if "error" in cal:
        return ["CALENDAR", f" {cal['error']}"]
    return ["CALENDAR"] + ([f" {'all day' if e['all_day'] else e['start'][11:] + '-' + e['end']}  {e['title']}"
                            for e in cal["events"]] or [" Nothing scheduled"])


def market_lines() -> list[str]:
    from jarvis.agents import briefing

    rows = [r for r in briefing.market_table() if not r.get("error") and not math.isnan(r["chg_pct"])]
    if not rows:
        return ["MARKETS", " NO DATA - market feeds unavailable"]
    rows.sort(key=lambda r: abs(r["chg_pct"]), reverse=True)
    L = ["MARKETS (biggest moves, last close)"]
    for r in rows[:3]:
        wide = " | wide range" if r["range_atr"] and r["range_atr"] > 1.5 else ""
        L.append(f" {r['inst'].key} {briefing.fmt(r['inst'], r['close'])} {r['chg_pct']:+.2f}% "
                 f"(5d {r['chg_5d_pct']:+.2f}%){wide}" + (" !! stale" if r["stale"] else ""))
    return L


def trading_day_lines() -> list[str]:
    from jarvis.agents import personal

    j = personal.journal(days=7)
    if "error" in j:
        return ["TRADING", f" {j['error']}"]
    d = j["last_day"]
    L = ["TRADING", f" {d['day']}: {d['trades']} trades, {_m(d['net'])} | 7 days {_m(j['net'])}, win rate {j['win_rate']:.0f}%"]
    if j["big_losses"]:
        L.append(f" !! {len(j['big_losses'])} oversized loss(es) this week - stop discipline first")
    if j["overtrading_days"]:
        L.append(f" !! Overtrading on {', '.join(x['day'] for x in j['overtrading_days'])}")
    return L


FOOTER = "More: !today  !tasks  !compliance  !cases  !leads  !drafts  !ideas  - or just ask me."


def morning_text() -> str:
    from jarvis.agents import mind

    now = datetime.now(LOCAL_TZ)
    L = [f"D.O.N.N.A | GOOD MORNING | {now:%a %d %b %Y}", LINE]
    for fn, label in ((needs_you, "NEEDS YOU"), (mind.focus_lines, "FOCUS"), (calendar_lines, "CALENDAR"),
                      (risk_lines, "POSITION RISK"), (market_lines, "MARKETS")):
        L += _safe(fn, label) + [""]
    return "\n".join(L + [FOOTER])


def evening_text() -> str:
    from jarvis.agents import mind

    L = _safe(lambda: mind.shutdown_text().splitlines(), "SHUTDOWN") + [""]
    for fn, label in ((trading_day_lines, "TRADING"), (risk_lines, "POSITION RISK"), (market_lines, "MARKETS")):
        L += _safe(fn, label) + [""]
    return "\n".join(L + [FOOTER])


@register
class RhythmAgent(Agent):
    name = "rhythm"
    description = "One morning and one evening message for the owner, and live position risk."

    def _send(self, kind: str, text: str, send: bool) -> dict[str, Any]:
        res: dict[str, Any] = {"text": text}
        if send:
            from jarvis.agents.personal import PERSONAL_TARGETS

            if not PERSONAL_TARGETS:
                res["delivery"] = {"error": "JARVIS_PERSONAL_TARGETS not set"}
            else:
                from donna.channels import broadcast

                res["delivery"] = broadcast(text, PERSONAL_TARGETS, title=f"D.O.N.N.A {kind}")
        audit.log("rhythm", kind, {"chars": len(text), "delivery": res.get("delivery")})
        return res

    @tool()
    def morning(self, send: bool = False) -> dict[str, Any]:
        """The morning message: what needs the owner (ranked), focus, calendar, position risk, markets.
        send=True delivers to JARVIS_PERSONAL_TARGETS."""
        return self._send("morning", morning_text(), send)

    @tool()
    def evening(self, send: bool = False) -> dict[str, Any]:
        """The evening message: done/open/tomorrow, the trading day, position risk, markets at the close."""
        return self._send("evening", evening_text(), send)

    @tool()
    def positions(self) -> dict[str, Any]:
        """Latest live-position snapshot: risk at stop per position, open risk vs limit, daily-loss room."""
        return position_risk()
