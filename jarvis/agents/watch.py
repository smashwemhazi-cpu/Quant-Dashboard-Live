"""Proactive nudges, round 2: DONNA speaks up during the day when something needs you, once per event.

Market      A watchlist instrument whose range today is DONNA_MOVE_ATR x its normal daily range (ATR) or more.
Compliance  A regulatory obligation due within DONNA_OBLIGATION_WARN_DAYS, or a case due today or tomorrow
            (before it goes overdue - overdue items are already in the morning message).
Leads       New leads still unassigned DONNA_LEAD_HOT_HOURS after the export that showed them.
Bots        A cBot that crashed or went quiet.
Positions   A live position without a stop, or the daily-loss limit hit.
Every nudge is keyed (mind._once), so a restart or the 15-minute loop never repeats it.
"""
from __future__ import annotations

import math
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from jarvis.core import audit
from jarvis.core.registry import Agent, register

MOVE_ATR = float(os.getenv("DONNA_MOVE_ATR", "1.5"))
OBLIGATION_WARN_DAYS = int(os.getenv("DONNA_OBLIGATION_WARN_DAYS", "2"))
LEAD_HOT_HOURS = float(os.getenv("DONNA_LEAD_HOT_HOURS", "2"))


def _once(key: str) -> bool:
    from jarvis.agents.mind import _once as once

    return once(f"watch:{key}")


def _first_seen(key: str) -> datetime:
    """When DONNA first saw this thing (UTC); the export's own date has no time of day."""
    from jarvis.agents import mind

    _once(f"seen:{key}")
    with mind._db() as c:
        ts = c.execute("SELECT ts FROM nudges WHERE key = ?", (f"watch:seen:{key}",)).fetchone()[0]
    t = datetime.fromisoformat(ts)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def market_nudges(rows: list[dict[str, Any]] | None = None) -> list[str]:
    from jarvis.agents import briefing

    rows = briefing.market_table() if rows is None else rows
    out = []
    for r in rows:
        ratio = r.get("range_atr")
        if r.get("error") or r.get("stale") or ratio is None or math.isnan(ratio) or ratio < MOVE_ATR:
            continue
        if _once(f"move:{r['inst'].label}:{r['date']}"):
            out.append(f"{r['inst'].label} is moving: today's range is {ratio:.1f}x its normal day "
                       f"({r['chg_pct']:+.2f}%). Check open positions and the news.")
    return out


def compliance_nudges(today: date | None = None) -> list[str]:
    from jarvis.agents import compliance

    today = today or date.today()
    out = []
    for o in compliance.list_obligations(days=OBLIGATION_WARN_DAYS):
        if o["due"] >= today.isoformat() and _once(f"obligation:{o['id']}:{o['due']}"):
            days = (date.fromisoformat(o["due"]) - today).days
            when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
            out.append(f"Regulatory obligation due {when}: {o['name']} ({o['due']}).")
    from jarvis.agents import complaints

    for r in complaints.active(today):
        if r["status"] == "escalated":
            continue
        if not r["acknowledged"] and r["ack_due"] <= today.isoformat() and _once(f"complaint-ack:{r['id']}"):
            out.append(f"Complaint #{r['id']} must be acknowledged today ({r['category']}): !complaint {r['id']} ack")
        left = (date.fromisoformat(r["resolve_due"]) - today).days
        if 0 <= left <= 7 and _once(f"complaint-due:{r['id']}"):
            out.append(f"Complaint #{r['id']} needs its final answer within {left} day(s) ({r['resolve_due']}).")
    soon = {today.isoformat(), (today + timedelta(days=1)).isoformat()}
    for c in compliance.list_cases():
        if c["due"] in soon and _once(f"case:{c['id']}:{c['due']}"):
            when = "today" if c["due"] == today.isoformat() else "tomorrow"
            out.append(f"Compliance case #{c['id']} is due {when}: {c['subject']} ({c['severity']}).")
    return out


def lead_nudges(now: datetime | None = None) -> list[str]:
    from jarvis.agents import growth

    s = growth.leads_summary()
    if "error" in s or not s.get("unassigned_new"):
        return []
    now = now or datetime.now(timezone.utc)
    age_h = (now - _first_seen(f"leads:{s['asof']}")).total_seconds() / 3600
    if age_h < LEAD_HOT_HOURS or not _once(f"leads:{s['asof']}"):
        return []
    return [f"{s['unassigned_new']} new lead(s) from today's export still have no owner after {age_h:.0f}h. "
            "Speed-to-lead matters most in the first hours: assign them."]


def bot_nudges() -> list[str]:
    from jarvis.agents import personal

    out = []
    for b in personal.bots():
        bad = b["state"] == "crashed" or b["hours_since"] > personal.BOT_STALE_HOURS
        if bad and _once(f"bot:{b['instance']}:{b['state']}:{b['last_event']}"):
            out.append(f"Bot {b['bot']} {b['symbol']} is {b['state']}, last seen {b['hours_since']:.0f}h ago.")
    return out


def position_nudges(today: str | None = None) -> list[str]:
    from jarvis.agents import rhythm

    r = rhythm.position_risk()
    if "error" in r or r.get("stale"):
        return []
    today = today or date.today().isoformat()
    out = [f"{s} has no stop loss. Add one or close it." for s in r["no_stop"] if _once(f"nostop:{s}:{today}")]
    if r.get("daily_loss_room", 1) <= 0 and _once(f"dailyloss:{today}"):
        out.append("Daily-loss limit hit. No new trades today.")
    return out


CHECKS: list[tuple[str, Callable[[], list[str]]]] = [
    ("positions", position_nudges), ("compliance", compliance_nudges), ("leads", lead_nudges),
    ("bots", bot_nudges), ("market", market_nudges),
]


def check() -> list[str]:
    out = []
    for name, fn in CHECKS:
        try:
            out += fn()
        except Exception as e:  # one dead source must not silence the others
            audit.log("watch", "check_error", {"check": name, "error": str(e)})
    return out


@register
class WatchAgent(Agent):
    name = "watch"
    description = "Daytime heads-ups: big market moves, compliance deadlines, unassigned leads, stopped bots, stops."

    def watch(self, send: bool = True) -> dict[str, Any]:
        """Scheduler hook (not a Brain tool)."""
        msgs = check()
        res: dict[str, Any] = {"nudges": msgs, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        if msgs and send:
            from jarvis.agents.personal import PERSONAL_TARGETS

            if PERSONAL_TARGETS:
                from donna.channels import broadcast

                res["delivery"] = broadcast("\n\n".join(msgs), PERSONAL_TARGETS, title="D.O.N.N.A heads-up")
        return res
