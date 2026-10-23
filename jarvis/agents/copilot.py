"""Growth co-pilot: a weekly growth review and one experiment at a time, tracked to a verdict.

Review      Once a week (and on `!growth`): leads, funnel, sources/IBs and running experiments, read from the
            Syntellicore exports already ingested. Claude writes the headline, wins, worries and ONE experiment to
            run next, measured on a metric DONNA can compute herself from the next exports.
Experiments Proposed by the review, started by you (`!exp start 3`). At the end date DONNA re-measures the metric
            against its baseline and marks it won or lost, so ideas are judged on numbers, not memory.
Only counts and source names go to Claude: no client names, account numbers or customer ids.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jarvis.core import audit
from jarvis.core.config import settings
from jarvis.core.registry import Agent, register, tool

GROWTH_WEEKDAY = int(os.getenv("DONNA_GROWTH_WEEKDAY", "0"))    # 0 = Monday
EXP_STATUSES = ["proposed", "running", "won", "lost", "stopped"]

# what an experiment can be judged on: computed from the data, never typed in by hand
METRICS = {
    "new_leads_7d": ("New leads in the last 7 days", "up"),
    "ftd_conversion_pct": ("Leads that funded, %", "up"),
    "stale_leads": (f"Leads untouched for {os.getenv('JARVIS_LEAD_STALE_DAYS', '3')}+ days", "down"),
    "unassigned_leads": ("Leads with no owner", "down"),
    "funded_total": ("Funded accounts (FTD) in the leads export", "up"),
}


def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS experiments (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT,
        title TEXT, hypothesis TEXT, action TEXT, metric TEXT, target REAL, days INTEGER, status TEXT,
        baseline REAL, started TEXT, ends TEXT, result REAL, note TEXT)""")
    return c


def _today() -> str:
    from jarvis.agents.mind import _today as local_today

    return local_today()


# ------------------------------------------------------------------ measuring
def snapshot() -> dict[str, float]:
    """Current value of every metric DONNA can measure; empty when no leads export has been ingested."""
    from jarvis.agents import growth

    s = growth.leads_summary()
    if "error" in s:
        return {}
    return {
        "new_leads_7d": float(s["new_7d"]),
        "ftd_conversion_pct": round(s["ftd"] / s["leads"] * 100, 2) if s["leads"] else 0.0,
        "stale_leads": float(s["stale"]),
        "unassigned_leads": float(s["unassigned"]),
        "funded_total": float(s["ftd"]),
    }


def _won(metric: str, baseline: float, now: float, target: float) -> bool:
    return now >= target if METRICS[metric][1] == "up" else now <= target


# ------------------------------------------------------------------ experiments
def experiments(status: str | None = None) -> list[dict[str, Any]]:
    with _db() as c:
        q = "SELECT * FROM experiments" + (" WHERE status = ?" if status else "") + " ORDER BY id DESC LIMIT 30"
        return [dict(r) for r in c.execute(q, (status,) if status else ())]


def get_experiment(eid: int) -> dict[str, Any] | None:
    with _db() as c:
        r = c.execute("SELECT * FROM experiments WHERE id = ?", (eid,)).fetchone()
    return dict(r) if r else None


def propose(exp: dict[str, Any]) -> int:
    with _db() as c:
        return c.execute("""INSERT INTO experiments (created, title, hypothesis, action, metric, target, days, status)
            VALUES (?,?,?,?,?,?,?, 'proposed')""",
                         (datetime.now(timezone.utc).isoformat(timespec="seconds"), exp["title"], exp["hypothesis"],
                          exp["action"], exp["metric"], float(exp["target"]), int(exp["days"]))).lastrowid


def start(eid: int, today: str | None = None) -> str:
    e = get_experiment(eid)
    if not e:
        return f"No experiment #{eid}."
    if e["status"] != "proposed":
        return f"Experiment #{eid} is already {e['status']}."
    now = snapshot()
    if e["metric"] not in now:
        return "Can't start it yet: there's no leads export to measure the baseline from. Upload one first."
    today = today or _today()
    ends = (date.fromisoformat(today) + timedelta(days=e["days"])).isoformat()
    with _db() as c:
        c.execute("UPDATE experiments SET status='running', baseline=?, started=?, ends=? WHERE id=?",
                  (now[e["metric"]], today, ends, eid))
    audit.log("copilot", "experiment_start", {"id": eid, "metric": e["metric"], "baseline": now[e["metric"]]})
    return (f"Experiment #{eid} started: {e['title']}\nMeasuring {METRICS[e['metric']][0]}: now {now[e['metric']]:g}, "
            f"target {e['target']:g} by {ends}.")


def stop(eid: int, note: str = "") -> str:
    e = get_experiment(eid)
    if not e or e["status"] not in ("proposed", "running"):
        return f"No open experiment #{eid}."
    with _db() as c:
        c.execute("UPDATE experiments SET status='stopped', note=? WHERE id=?", (note, eid))
    return f"Experiment #{eid} stopped."


def evaluate(today: str | None = None) -> list[dict[str, Any]]:
    """Judge every running experiment whose end date has passed, against the newest data."""
    today = today or _today()
    now, judged = snapshot(), []
    for e in experiments("running"):
        if e["ends"] > today or e["metric"] not in now:
            continue
        status = "won" if _won(e["metric"], e["baseline"], now[e["metric"]], e["target"]) else "lost"
        with _db() as c:
            c.execute("UPDATE experiments SET status=?, result=? WHERE id=?", (status, now[e["metric"]], e["id"]))
        judged.append({**e, "status": status, "result": now[e["metric"]]})
        audit.log("copilot", "experiment_result", {"id": e["id"], "status": status, "result": now[e["metric"]]})
    return judged


def exp_line(e: dict[str, Any]) -> str:
    m = METRICS.get(e["metric"], (e["metric"],))[0]
    if e["status"] == "proposed":
        tail = f"target {e['target']:g} in {e['days']}d - !exp start {e['id']}"
    elif e["status"] == "running":
        tail = f"{e['baseline']:g} -> target {e['target']:g} by {e['ends']}"
    elif e["status"] in ("won", "lost"):
        tail = f"{e['baseline']:g} -> {e['result']:g} (target {e['target']:g})"
    else:
        tail = e.get("note") or ""
    return f"#{e['id']:<3} {e['status'].upper():<8} {e['title']} | {m}: {tail}"


def experiments_text() -> str:
    rows = experiments()
    return "\n".join(exp_line(e) for e in rows) or "No experiments yet. !growth writes a review and proposes one."


# ------------------------------------------------------------------ weekly review
def facts() -> str:
    """The numbers the review is written from: counts and sources only, no client identifiers."""
    from jarvis.agents import growth

    s = growth.leads_summary()
    if "error" in s:
        return ""
    L = [f"Leads export {s['asof']}: {s['leads']} leads, {s['ftd']} funded, new 24h {s['new_24h']}, new 7d {s['new_7d']}",
         f"Unassigned {s['unassigned']} (new today {s['unassigned_new']}), untouched {s['untouched']}, "
         f"stale {s['stale']}",
         "By source (leads, funded, conversion): " + "; ".join(
             f"{x['source']} {x['leads']}/{x['ftd']:g}/{x['conv']:.0f}%" for x in s["by_source"]),
         "By status: " + ", ".join(f"{k} {v}" for k, v in s["by_status"].items())]
    trend = growth.funnel_trend(14)
    if trend:
        L.append("Daily KPI export, last 14 days:")
        L += [", ".join(f"{k} {v:g}" if isinstance(v, (int, float)) else f"{v}" for k, v in r.items()) for r in trend]
    exps = experiments()
    if exps:
        L.append("Experiments so far:")
        L += [exp_line(e) for e in exps[:10]]
    L.append("Metrics an experiment can be judged on: " + "; ".join(
        f"{k} = {v[0]} (better: {v[1]}), now {snapshot().get(k, 0):g}" for k, v in METRICS.items()))
    return "\n".join(L)


REVIEW_PROMPT = """You are DONNA, growth co-pilot to {owner}, who runs compliance at {firm}, a retail FX/CFD broker.
From the numbers below write this week's growth review: a one-line headline, up to 3 wins and up to 3 worries,
each tied to a number. Then propose ONE experiment for next week that a small team can run without new tools or
spend: a concrete action, the hypothesis, the metric (one of the listed keys), a realistic target value for that
metric and how many days it runs (7-28). Don't repeat an experiment that already ran. Stay within what a regulated
broker may do: no bonuses or incentives to trade, no pressure selling, no claims about profits.
Return only the JSON object."""
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "wins": {"type": "array", "items": {"type": "string"}},
        "worries": {"type": "array", "items": {"type": "string"}},
        "experiment": {"type": "object", "properties": {
            "title": {"type": "string"}, "hypothesis": {"type": "string"}, "action": {"type": "string"},
            "metric": {"type": "string", "enum": list(METRICS)}, "target": {"type": "number"},
            "days": {"type": "integer"}},
            "required": ["title", "hypothesis", "action", "metric", "target", "days"], "additionalProperties": False},
    },
    "required": ["headline", "wins", "worries", "experiment"], "additionalProperties": False,
}


def review(client: Any = None) -> str:
    from jarvis.agents.growth import FIRM
    from jarvis.agents.mind import OWNER

    judged = evaluate()
    data = facts()
    L = ["D.O.N.N.A | GROWTH REVIEW", "-" * 34]
    for e in judged:
        L.append(f"RESULT: experiment #{e['id']} {e['status'].upper()} - {exp_line(e)}")
    if not data:
        return "\n".join(L + ["NO DATA - upload the Syntellicore leads/customers export so I can review growth."])
    if client is None and not os.getenv("ANTHROPIC_API_KEY"):
        return "\n".join(L + ["ANTHROPIC_API_KEY not set - the growth review needs the Claude API.", "", data])
    import anthropic

    client = client or anthropic.Anthropic()
    try:
        resp = client.beta.messages.create(
            model=settings.model, max_tokens=8000, system=REVIEW_PROMPT.format(owner=OWNER, firm=FIRM),
            messages=[{"role": "user", "content": data}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": REVIEW_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
    except anthropic.APIError as e:
        return "\n".join(L + [f"Claude API error: {type(e).__name__}"])
    if resp.stop_reason == "refusal":
        return "\n".join(L + ["The request was declined."])
    r = json.loads(next(b.text for b in resp.content if b.type == "text"))
    x = r["experiment"]
    x["days"] = min(max(int(x["days"]), 7), 28)
    eid = propose(x)
    L += [r["headline"], ""]
    L += ["WINS"] + ([f" + {w}" for w in r["wins"]] or [" (none)"])
    L += ["", "WORRIES"] + ([f" - {w}" for w in r["worries"]] or [" (none)"])
    L += ["", f"NEXT EXPERIMENT #{eid}: {x['title']}", f" Do: {x['action']}", f" Why: {x['hypothesis']}",
          f" Judged on: {METRICS[x['metric']][0]}, now {snapshot().get(x['metric'], 0):g}, "
          f"target {x['target']:g} in {x['days']} days",
          f" Start it with !exp start {eid} (or !exp stop {eid} to skip)."]
    audit.log("copilot", "growth_review", {"experiment": eid})
    return "\n".join(L)


@register
class CopilotAgent(Agent):
    name = "copilot"
    description = "Weekly growth review and growth experiments tracked to a won/lost verdict."

    @tool()
    def growth_experiments(self) -> list[dict[str, Any]]:
        """All growth experiments: proposed, running (baseline, target, end date) and finished (result)."""
        return experiments()

    @tool()
    def growth_review(self) -> dict[str, Any]:
        """Write this week's growth review from the latest exports and propose one experiment (not started)."""
        return {"text": review()}

    def weekly(self, send: bool = False, today: date | None = None) -> dict[str, Any]:
        """Scheduled: the review on DONNA_GROWTH_WEEKDAY only."""
        today = today or date.fromisoformat(_today())
        if today.weekday() != GROWTH_WEEKDAY:
            return {"skipped": "not review day"}
        text = review()
        if not send:
            return {"text": text}
        from donna.channels import broadcast

        targets = [t for t in os.getenv("JARVIS_PERSONAL_TARGETS", "discord:personal").split(",") if t]
        return {"text": text, "delivery": broadcast(text, targets, title="DONNA growth review")}
