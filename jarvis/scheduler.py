"""Background automations - the "things you didn't think to ask for".

Run with:  python -m jarvis.scheduler
Each job is deterministic (no LLM cost) except the morning brief. Results go
to the audit log, which the console's Activity feed shows; plug `notify()`
into Slack/Teams/email when you are ready.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

from jarvis.core import audit
from jarvis.core.registry import agents, load_builtin_agents

log = logging.getLogger("jarvis.scheduler")


def notify(title: str, payload: Any) -> None:
    audit.log("scheduler", title, {"result": payload})
    log.info("%s: %s", title, str(payload)[:300])


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
]


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    load_builtin_agents()
    last: dict[str, float] = {}
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
                fn()
            except Exception as e:  # a failing job must never kill the loop
                audit.log("scheduler", "job_error", {"job": name, "error": str(e)})
        time.sleep(30)


if __name__ == "__main__":
    main()
