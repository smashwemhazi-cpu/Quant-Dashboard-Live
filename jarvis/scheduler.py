"""Background automations - the "things you didn't think to ask for".

Run with:  python -m jarvis.scheduler
Each job is deterministic (no LLM cost) except the morning brief. Results go
to the audit log, which the console's Activity feed shows; plug `notify()`
into Slack/Teams/email when you are ready.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Callable

from jarvis.core import audit
from jarvis.core.config import settings
from jarvis.core.registry import agents, load_builtin_agents

log = logging.getLogger("jarvis.scheduler")


# Alerts that used to reach only the audit log. Set JARVIS_ALERT_TARGETS (e.g. discord:personal) to receive
# them; JARVIS_ALERT_EVENTS picks which (the full list: retention_scan, execution_alert, stale_deals,
# desk_report, lp_slippage_alert, morning_brief). Reports that already deliver themselves are not repeated.
ALERT_TARGETS = [t for t in os.getenv("JARVIS_ALERT_TARGETS", "").split(",") if t]
ALERT_EVENTS = {e.strip() for e in os.getenv(
    "JARVIS_ALERT_EVENTS", "execution_alert,lp_slippage_alert,morning_brief").split(",") if e.strip()}


def alert_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    rows = payload if isinstance(payload, list) else [payload]
    return "\n".join(", ".join(f"{k}: {v}" for k, v in r.items()) if isinstance(r, dict) else str(r)
                     for r in rows[:15])


def notify(title: str, payload: Any) -> None:
    audit.log("scheduler", title, {"result": payload})
    log.info("%s: %s", title, str(payload)[:300])
    if title in ALERT_EVENTS and ALERT_TARGETS and payload:
        from donna.channels import broadcast

        try:
            audit.log("scheduler", "alert_delivery",
                      {"event": title, "delivery": broadcast(alert_text(payload), ALERT_TARGETS,
                                                             title=f"D.O.N.N.A {title.replace('_', ' ')}")})
        except Exception as e:  # delivery problems must not stop the job loop
            audit.log("scheduler", "alert_delivery_error", {"event": title, "error": str(e)})


def job_retention() -> None:
    notify("retention_scan", agents()["clients"].at_risk(min_score=60, limit=10))


def job_execution() -> None:
    rep = agents()["execution"].report()
    if rep.get("fill_rate_pct", 100) < 98 or rep.get("slippage_breaches", 0) > 0:
        notify("execution_alert", rep)


def job_pipeline() -> None:
    notify("stale_deals", agents()["pipeline"].analyse()["stale_deals"])


def job_desk_report() -> None:
    notify("desk_report", agents()["dealing"].desk_report())


def job_lp_slippage() -> None:
    eps = [e for e in agents()["dealing"].slippage()["episodes"] if e["raise_with_lp"]]
    if eps:
        notify("lp_slippage_alert", eps[:5])


def job_backoffice_ingest() -> None:
    r = agents()["backoffice"].ingest()
    if r.get("files"):
        notify("backoffice_ingest", r)
        # new data -> run the compliance rules and turn lead backlogs into tasks straight away
        notify("compliance_scan", agents()["compliance"].run_scan())
        from jarvis.agents import growth

        s = growth.leads_summary()
        if "error" not in s:
            notify("lead_tasks", growth.lead_tasks(s))


def job_compliance_report() -> None:
    notify("compliance_report", agents()["compliance"].daily_report(send=True).get("delivery"))


def job_marketing_drafts() -> None:
    from jarvis.agents import growth
    from jarvis.agents.personal import PERSONAL_TARGETS

    if not os.getenv("ANTHROPIC_API_KEY") or not PERSONAL_TARGETS:
        return
    from donna.channels import broadcast

    notify("marketing_drafts", broadcast(growth.drafts_text(growth.draft_posts()), PERSONAL_TARGETS,
                                         title="D.O.N.N.A marketing drafts"))


def job_inbox_triage() -> None:
    from jarvis.agents.personal import PERSONAL_TARGETS

    r = agents()["assistant"].triage()
    items = r.get("processed", [])
    if not items:
        return
    notify("inbox_triage", {"processed": len(items)})
    urgent = [x for x in items if x.get("urgency") == "high"]
    if urgent and PERSONAL_TARGETS:
        from donna.channels import broadcast

        text = "\n".join(f"{x['subject'][:90]}\n  {x['summary'][:200]}" + (
            f"\n  tasks #{', #'.join(map(str, x['task_ids']))}" if x.get("task_ids") else "") for x in urgent)
        broadcast(text, PERSONAL_TARGETS, title="D.O.N.N.A urgent email")


def job_my_day() -> None:
    notify("my_day", agents()["assistant"].my_day(send=True).get("delivery"))


def job_nudges() -> None:
    r = agents()["mind"].nudge()
    if r["nudges"]:
        notify("nudges", r)


def job_midday() -> None:
    r = agents()["mind"].nudge(midday=True)
    if r["nudges"]:
        notify("midday_check", r)


def job_shutdown() -> None:
    notify("shutdown", agents()["mind"].shutdown(send=True).get("delivery"))


def job_morning_dispatch() -> None:
    notify("morning_dispatch", agents()["briefing"].morning_dispatch(send=True).get("delivery"))


def job_daily_wrap() -> None:
    notify("daily_wrap", agents()["briefing"].daily_wrap(send=True).get("delivery"))


def job_morning_brief() -> None:
    from jarvis.core.brain import Brain

    brief = Brain().ask([{"role": "user", "content":
        "Morning brief: dealing desk report (LP slippage episodes, toxic traders, exposure), "
        "market regime + macro, execution issues overnight, top 5 at-risk clients, "
        "stale deals over 14 days, my overdue tasks. Max 250 words, bullets."}], user="scheduler")
    notify("morning_brief", brief)


# (name, every_n_minutes, weekday_only_hour or None, fn)
JOBS: list[tuple[str, int, int | None, Callable[[], None]]] = [
    ("execution", 15, None, job_execution),
    ("backoffice_ingest", 15, None, job_backoffice_ingest),
    ("lp_slippage", 60, None, job_lp_slippage),
    ("desk_report", 60 * 24, 6, job_desk_report),
    ("retention", 60 * 6, None, job_retention),
    ("pipeline", 60 * 24, None, job_pipeline),
    ("morning_dispatch", 60 * 24, 6, job_morning_dispatch),   # 06:00 UTC, before London
    ("daily_wrap", 60 * 24, int(os.getenv("JARVIS_WRAP_UTC_HOUR", "22")), job_daily_wrap),  # default 22:00 UTC, after the NY close
    ("morning_brief", 60 * 24, 7, job_morning_brief),
    ("inbox_triage", 15, None, job_inbox_triage),
    ("my_day", 60 * 24, int(os.getenv("JARVIS_MYDAY_UTC_HOUR", "4")), job_my_day),
    ("compliance_report", 60 * 24, int(os.getenv("JARVIS_COMPLIANCE_UTC_HOUR", "5")), job_compliance_report),
    ("marketing_drafts", 60 * 24, int(os.getenv("JARVIS_CONTENT_UTC_HOUR", "6")), job_marketing_drafts),
    ("shutdown", 60 * 24, int(os.getenv("JARVIS_SHUTDOWN_UTC_HOUR", "16")), job_shutdown),  # evening close-out
    ("nudges", 5, None, job_nudges),                                                       # meeting reminders
    ("midday", 60 * 24, int(os.getenv("JARVIS_MIDDAY_UTC_HOUR", "9")), job_midday),         # open high-priority work
]


def _state() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS scheduler_runs (job TEXT PRIMARY KEY, last REAL)")
    return c


def load_last() -> dict[str, float]:
    """Last run times survive a restart, so a crash-and-restart doesn't send the daily reports twice."""
    with _state() as c:
        return dict(c.execute("SELECT job, last FROM scheduler_runs").fetchall())


def save_last(job: str, ts: float) -> None:
    with _state() as c:
        c.execute("INSERT OR REPLACE INTO scheduler_runs (job, last) VALUES (?,?)", (job, ts))


def job_failed(name: str, e: Exception) -> None:
    audit.log("scheduler", "job_error", {"job": name, "error": str(e)})
    if ALERT_TARGETS:  # a silent failure is how a missing report goes unnoticed for a week
        from donna.channels import broadcast

        broadcast(f"The {name} job failed: {type(e).__name__}: {str(e)[:300]}\nIt will retry on its next run.",
                  ALERT_TARGETS, title="D.O.N.N.A job failed")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    load_builtin_agents()
    last: dict[str, float] = load_last()
    while True:
        now = time.time()
        hour = datetime.now(timezone.utc)  # all schedules are UTC
        for name, every, at_hour, fn in JOBS:
            if at_hour is not None and (hour.hour != at_hour or hour.weekday() >= 5):
                continue
            if now - last.get(name, 0) < every * 60:
                continue
            last[name] = now
            try:
                save_last(name, now)
                fn()
            except Exception as e:  # a failing job must never kill the loop
                try:
                    job_failed(name, e)
                except Exception:
                    log.exception("could not report the failure of %s", name)
        time.sleep(30)


if __name__ == "__main__":
    main()
