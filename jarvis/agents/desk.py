"""D.O.N.N.A as your PA: the written definition of done, the workflow numbers that prove it, and the weekly analysis.

THE GOAL (SH, 2026-11-08): "DONNA knows where everything is, is the PA and coordinates everything."
Done means these eight outcomes hold, measured every week over the LAST 7 DAYS (today included) and compared with
the 7 days before. Each one is GREEN (done), AMBER (slipping) or RED (not done), from DONNA's own records.

  1 Every email has a home          emails sorted within 30 min of arriving                  >= 95% (amber 80)
  2 Nothing owed is forgotten       replies owed for over 2 working days                     0 (amber 2)
  3 Replies go out in time          median hours from email to your reply                    <= 24 (amber 48)
  4 The task list is true           overdue tasks                                            <= 3 (amber 6)
  5 Work gets finished              tasks closed / tasks added                               >= 100% (amber 80)
  6 The diary protects your time    focus hours booked (events starting "Focus:")            >= 5 (amber 2)
  7 Meetings don't eat the week     meeting hours / work hours                               <= 40% (amber 60)
  8 Coordination doesn't stall      things waiting for your OK for over 24 hours             0 (amber 1)

!desk        the scorecard against those targets, this week and last
!analysis    the weekly analysis: your trades, your work flow, every agent and job, and the KPIs (also page 2 of
             the Friday PDF)
Plain numbers, no Claude credit.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any

from jarvis.agents import assistant
from jarvis.core import audit
from jarvis.core.config import settings

LOCAL_TZ = assistant.LOCAL_TZ
DAYS = 7
GOAL = "DONNA knows where everything is, is your PA and coordinates everything."

# key: (outcome, measure, unit, better ("max" = lower is better), green, amber)
STANDARD: dict[str, tuple[str, str, str, str, float, float]] = {
    "sorted_fast": ("Every email has a home", "emails sorted within 30 min of arriving", "%", "min", 95, 80),
    "owed_old": ("Nothing owed is forgotten", "replies owed for over 2 working days", "", "max", 0, 2),
    "reply_hours": ("Replies go out in time", "median hours from email to your reply", "h", "max", 24, 48),
    "overdue": ("The task list is true", "overdue tasks", "", "max", 3, 6),
    "close_ratio": ("Work gets finished", "tasks closed for every 100 added", "%", "min", 100, 80),
    "focus_hours": ("The diary protects your time", "focus hours booked", "h", "min", 5, 2),
    "meeting_share": ("Meetings don't eat the week", "share of work hours in meetings", "%", "max", 40, 60),
    "approvals_stuck": ("Coordination doesn't stall", "things waiting for your OK over 24h", "", "max", 0, 1),
}


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(os.getenv("JARVIS_STATE_DB", settings.state_db))
    c.row_factory = sqlite3.Row
    return c


def _rows(sql: str, *args: Any) -> list[dict[str, Any]]:
    try:
        with _db() as c:
            return [dict(r) for r in c.execute(sql, args)]
    except sqlite3.OperationalError:
        return []


def _safe(fn: Any, default: Any = None) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one missing source never stops the rest
        return default


def _ts(s: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _work_hours_per_day() -> float:
    from jarvis.agents import workflow

    a, _, b = workflow.WORK_HOURS.partition("-")
    try:
        return (datetime.strptime(b.strip(), "%H:%M") - datetime.strptime(a.strip(), "%H:%M")).seconds / 3600
    except ValueError:
        return 9.0


def measures(today: date | None = None) -> dict[str, Any]:
    """The eight numbers for the 7 days ending today (None = no data to judge yet)."""
    from jarvis.agents import followup, workflow

    today = today or datetime.now(LOCAL_TZ).date()
    a = workflow.analyse(DAYS, today)
    since = datetime.combine(today - timedelta(days=DAYS), datetime.min.time(), LOCAL_TZ).astimezone(timezone.utc)
    until = datetime.combine(today + timedelta(days=1), datetime.min.time(), LOCAL_TZ).astimezone(timezone.utc)
    mails = [m for m in _rows("SELECT msg_id, received, processed_at, category FROM email_seen WHERE processed_at >= ? "
                              "AND processed_at < ?", since.isoformat(), until.isoformat())
             if m["category"] not in workflow.BULK]
    lags = [(p - r).total_seconds() / 60 for m in mails if (r := _ts(m["received"])) and (p := _ts(m["processed_at"]))]
    replied = {r["msg_id"]: r["at"] for r in _rows("SELECT * FROM email_replied")}
    reply_h = [(t - r).total_seconds() / 3600 for m in mails if m["msg_id"] in replied
               and (r := _ts(m["received"])) and (t := _ts(replied[m["msg_id"]])) and t >= r]
    owed = _safe(lambda: followup.replies_owed(today), []) or []
    cal = assistant.calendar_events(day=today - timedelta(days=DAYS - 1), days=DAYS)
    focus = meet = None
    if "error" not in cal:
        spans = [(e, p) for e in cal["events"] if (p := workflow._parse(e))]
        focus = sum((p[1] - p[0]).total_seconds() for e, p in spans if str(e.get("title", "")).lower()
                    .startswith("focus")) / 3600
        meet = sum((p[1] - p[0]).total_seconds() for e, p in spans if not str(e.get("title", "")).lower()
                   .startswith(("focus", "[hold"))) / 3600
    workdays = sum(1 for i in range(DAYS) if (today - timedelta(days=i)).weekday() < 5) or 1
    stuck = _safe(_approvals_stuck, None)
    return {
        "from": today - timedelta(days=DAYS - 1), "to": today,
        "emails": len(mails),
        "sorted_fast": round(100 * sum(1 for x in lags if x <= 30) / len(lags)) if lags else None,
        "owed": len(owed), "owed_old": sum(1 for r in owed if r.get("days", 0) > 2),
        "reply_hours": round(median(reply_h), 1) if reply_h else None, "replies": len(reply_h),
        "overdue": a["overdue"], "added": a["tasks_created"], "closed": a["tasks_done"],
        "close_ratio": round(100 * a["tasks_done"] / a["tasks_created"]) if a["tasks_created"] else None,
        "focus_hours": round(focus, 1) if focus is not None else None,
        "meeting_hours": round(meet, 1) if meet is not None else None,
        "meeting_share": round(100 * meet / (workdays * _work_hours_per_day())) if meet is not None else None,
        "approvals_stuck": stuck,
    }


def _approvals_stuck() -> int:
    from donna import app

    app.init_db()
    day_ago = (app._now() - timedelta(hours=24)).isoformat()
    with app.db() as c:
        return c.execute("SELECT COUNT(*) FROM actions WHERE status = 'PENDING' AND created_at <= ? AND expires_at > ?",
                         (day_ago, app._now().isoformat())).fetchone()[0]


def judge(key: str, value: Any) -> str:
    if value is None:
        return "NO DATA"
    _, _, _, better, green, amber = STANDARD[key]
    if better == "max":
        return "GREEN" if value <= green else "AMBER" if value <= amber else "RED"
    return "GREEN" if value >= green else "AMBER" if value >= amber else "RED"


def scorecard(today: date | None = None) -> list[dict[str, Any]]:
    today = today or datetime.now(LOCAL_TZ).date()
    now, before = measures(today), _safe(lambda: measures(today - timedelta(days=DAYS)), {}) or {}
    out = []
    for n, (key, (outcome, what, unit, better, green, amber)) in enumerate(STANDARD.items(), 1):
        out.append({"n": n, "key": key, "outcome": outcome, "measure": what, "unit": unit, "value": now.get(key),
                    "last_week": before.get(key), "status": judge(key, now.get(key)),
                    "target": f"{'<=' if better == 'max' else '>='} {green:g}{unit}"})
    return out


ICON = {"GREEN": "🟢", "AMBER": "🟠", "RED": "🔴", "NO DATA": "⚪"}


def _v(v: Any, unit: str) -> str:
    return "-" if v is None else f"{v:g}{unit}"


def desk_text(today: date | None = None) -> str:
    today = today or datetime.now(LOCAL_TZ).date()
    rows = scorecard(today)
    c = Counter(r["status"] for r in rows)
    L = ["YOUR PA - IS IT DONE?", f"Goal: {GOAL}",
         f"Last 7 days ({today - timedelta(days=DAYS - 1):%a %d %b} to {today:%a %d %b}): {c['GREEN']} of {len(rows)} "
         f"done, {c['AMBER']} slipping, {c['RED']} not done, {c['NO DATA']} not measurable yet", ""]
    for r in rows:
        L.append(f"{ICON[r['status']]} {r['n']}. {r['outcome']}: {_v(r['value'], r['unit'])} "
                 f"(target {r['target']}; last week {_v(r['last_week'], r['unit'])})")
        L.append(f"   {r['measure']}")
    fix = [r for r in rows if r["status"] == "RED"]
    if fix:
        L += ["", "FIX FIRST"] + [f" {HOW[r['key']]}" for r in fix[:3]]
    L += ["", "The definition and the numbers: !desk   The full weekly analysis: !analysis   Alerts: !alerts policy"]
    return "\n".join(L)


HOW = {
    "sorted_fast": "Emails wait too long to be sorted: check !status (email line) and the Google bridge.",
    "owed_old": "Old replies are waiting: !waiting, then !draft <task> for each, or !waiting done E<n>.",
    "reply_hours": "Replies are slow: block 30 minutes twice a day for email (!diary block <task>).",
    "overdue": "Too many overdue tasks: 10 minutes with !tasks - finish, re-date (!edit 12 due friday) or drop.",
    "close_ratio": "More work comes in than goes out: pick three tasks for tomorrow and move the rest.",
    "focus_hours": "No focus time booked: !diary block <task id> 90m for your top task.",
    "meeting_share": "Meetings fill the week: decline or shorten the recurring ones; keep two mornings free.",
    "approvals_stuck": "Things wait for your OK: !meet (invitations) and the Approve buttons on your phone.",
}


# ------------------------------------------------------------------ the weekly analysis
def _trades() -> dict[str, Any]:
    from jarvis.agents import personal

    j = personal.journal(days=DAYS)
    if "error" in j:
        return {"error": j["error"]}
    best = max(j["by_symbol"], key=lambda r: r["net"], default=None)
    worst = min(j["by_symbol"], key=lambda r: r["net"], default=None)
    return {"trades": j["trades"], "net": round(float(j["net"]), 2), "win_rate": round(float(j["win_rate"])),
            "pf": None if j["profit_factor"] is None else round(float(j["profit_factor"]), 2),
            "max_dd": round(float(j["max_drawdown"]), 2), "big_losses": len(j["big_losses"]),
            "overtrading_days": len(j["overtrading_days"]),
            "best": best and f"{best['symbol']} {best['net']:+,.2f}", "worst": worst and f"{worst['symbol']} {worst['net']:+,.2f}"}


def _agents(now: datetime | None = None) -> dict[str, Any]:
    from jarvis.agents import hq

    now = now or datetime.now(timezone.utc)
    states = hq.job_states()
    errors: Counter[str] = Counter()
    for r in audit.since((now - timedelta(days=DAYS)).isoformat(), limit=50000):
        if r["actor"] == "scheduler" and r["event"] == "job_error":
            errors[_safe(lambda: json.loads(r["payload"]).get("job", "?"), "?")] += 1
    duties = [(d["name"], hq.duty_state(d, states)) for d in hq.DUTIES]
    alerts_by = dict(Counter(r["level"] for r in _rows("SELECT level FROM alerts WHERE ts >= ?",
                                                        (now - timedelta(days=DAYS)).isoformat())))
    return {"jobs": len(states), "ok": sum(s["state"] == "ok" for s in states.values()),
            "failing": sorted(j for j, s in states.items() if s["state"] == "failing"),
            "waiting": sorted(j for j, s in states.items() if s["state"] == "waiting"),
            "errors": errors.most_common(5), "duties": duties, "alerts": alerts_by}


def _kpis(today: date) -> dict[str, Any]:
    from jarvis.agents import kpi

    rows = kpi.scorecard()
    now = {r["key"]: r["status"] for r in rows}
    prev = (load_week(today - timedelta(days=DAYS)) or {}).get("kpi", {})
    worse = [r["name"] for r in rows if prev.get(r["key"]) == "GREEN" and r["status"] in ("AMBER", "RED")]
    better = [r["name"] for r in rows if prev.get(r["key"]) in ("AMBER", "RED") and r["status"] == "GREEN"]
    return {"counts": kpi.counts(rows), "red": [r["name"] for r in rows if r["status"] == "RED"][:5],
            "worse": worse[:5], "better": better[:5], "status": now}


def analysis(today: date | None = None) -> dict[str, Any]:
    from jarvis.agents import workflow

    today = today or datetime.now(LOCAL_TZ).date()
    a, b = workflow.analyse(DAYS, today), _safe(lambda: workflow.analyse(DAYS, today - timedelta(days=DAYS)), {})
    return {"today": today, "desk": scorecard(today), "trades": _safe(_trades, {"error": "no trade data"}),
            "work": {"now": a, "before": b or {}, "tips": workflow.suggestions(a)},
            "agents": _safe(_agents, {}), "kpi": _safe(lambda: _kpis(today), {})}


def _delta(now: Any, before: Any) -> str:
    if not isinstance(now, (int, float)) or not isinstance(before, (int, float)):
        return ""
    d = now - before
    return f" ({'+' if d >= 0 else ''}{d:g} vs last week)"


def analysis_lines(x: dict[str, Any]) -> list[str]:
    L: list[str] = []
    t = x["trades"]
    L.append("YOUR TRADES (last 7 days)")
    if "error" in t:
        L.append(" No closed trades this week." if "NO DATA" in str(t["error"]) else f" {t['error']}")
    else:
        L.append(f" {t['trades']} trades, net {t['net']:+,.2f}, {t['win_rate']}% won, profit factor "
                 f"{t['pf'] if t['pf'] is not None else '-'}, worst run {t['max_dd']:,.2f}")
        L.append(f" Best: {t['best']}. Worst: {t['worst']}.")
        if t["big_losses"] or t["overtrading_days"]:
            L.append(f" !! {t['big_losses']} oversized loss(es), {t['overtrading_days']} day(s) of overtrading")
    w, b = x["work"]["now"], x["work"]["before"]
    L += ["", "YOUR WORK FLOW (last 7 days)",
          f" Emails {w['emails']}{_delta(w['emails'], b.get('emails'))}, urgent {w['high']}, asked you something "
          f"{w['replies_owed']}",
          f" Tasks: {w['tasks_created']} in{_delta(w['tasks_created'], b.get('tasks_created'))}, "
          f"{w['tasks_done']} done{_delta(w['tasks_done'], b.get('tasks_done'))}, {w['overdue']} overdue, "
          f"{w['stale']} open over a week"]
    if w.get("meetings"):
        L.append(f" Meetings: {w['meetings']['count']}, {w['meetings']['hours']:g}h")
    L += [f" - {tip}" for tip in x["work"]["tips"][:3]]
    g = x["agents"]
    if g:
        L += ["", "YOUR AGENTS (DONNA's jobs this week)",
              f" {g['ok']} of {g['jobs']} jobs running fine" + (f"; failing now: {', '.join(g['failing'])}"
                                                               if g["failing"] else "")
              + (f"; waiting on you: {', '.join(g['waiting'])}" if g["waiting"] else "")]
        if g["errors"]:
            L.append(" Most failures: " + ", ".join(f"{j} {n}x" for j, n in g["errors"]))
        L.append(" Duties: " + "; ".join(f"{n} - {s}" for n, s in g["duties"]))
        al = g.get("alerts") or {}
        L.append(f" Alerts raised: {al.get('CRITICAL', 0)} critical, {al.get('IMPORTANT', 0)} important, "
                 f"{al.get('NOTICE', 0)} notices (held for your messages)")
    k = x["kpi"]
    if k:
        c = k["counts"]
        L += ["", "YOUR KPIs", f" {c.get('GREEN', 0)} green, {c.get('AMBER', 0)} amber, {c.get('RED', 0)} red, "
                                f"{c.get('NO DATA', 0)} waiting for data"]
        if k["worse"]:
            L.append(" Slipped this week: " + ", ".join(k["worse"]))
        if k["better"]:
            L.append(" Recovered: " + ", ".join(k["better"]))
        if k["red"]:
            L.append(" Red now: " + ", ".join(k["red"]))
    d = x["desk"]
    L += ["", "YOUR PA STANDARD", " " + "  ".join(f"{ICON[r['status']]}{r['n']}" for r in d)
          + "   (!desk for the eight outcomes)"]
    return L


def analysis_text(today: date | None = None) -> str:
    x = analysis(today)
    return "\n".join([f"WEEKLY ANALYSIS - {x['today'] - timedelta(days=DAYS - 1):%d %b} to {x['today']:%d %b}", ""]
                     + analysis_lines(x))


# ------------------------------------------------------------------ weekly history (for "vs last week")
def save_week(x: dict[str, Any]) -> None:
    snap = {"desk": {r["key"]: r["value"] for r in x["desk"]}, "kpi": (x.get("kpi") or {}).get("status", {}),
            "trades": x.get("trades"), "work": {k: v for k, v in x["work"]["now"].items()
                                                if isinstance(v, (int, float, type(None)))}}
    with _db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS desk_weeks (week TEXT PRIMARY KEY, data TEXT)")
        c.execute("INSERT OR REPLACE INTO desk_weeks VALUES (?,?)", (x["today"].isoformat(), json.dumps(snap, default=str)))


def load_week(day: date) -> dict[str, Any] | None:
    """The snapshot saved closest to (and not after) that day, within 3 days."""
    rows = _rows("SELECT * FROM desk_weeks WHERE week <= ? AND week >= ? ORDER BY week DESC LIMIT 1",
                 day.isoformat(), (day - timedelta(days=3)).isoformat())
    return json.loads(rows[0]["data"]) if rows else None


def command(cmd: str, rest: str) -> str | None:
    if cmd in ("!desk", "!pa", "!standard"):
        return desk_text()
    if cmd in ("!analysis", "!analyse", "!analyze"):
        return analysis_text()
    return None
