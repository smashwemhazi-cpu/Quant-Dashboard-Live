"""DONNA's mandate, and DONNA as the one place that coordinates every agent.

The mandate is written once, here, as data. It feeds:
  !mandate (or "!what")   the full answer to "what does DONNA do to make my life better?"
  DONNA's chat persona    so asking her in plain words gives the same, complete answer
  !agents                 every agent grouped under the duty it serves, with its state:
                          OK / waiting for setup / failing since / not run yet, and when it last ran
  !run <job>              run one agent's job now (the same thing the scheduler does on its timetable)
  !hq                     one view across all agents: what needs you, then each duty's state in one line
A test checks that every scheduled job belongs to a duty, so a new agent can't be added without a place in it.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from jarvis.core import audit
from jarvis.core.registry import Agent, register, tool

PURPOSE = ("Give SH back time and certainty. Nothing important slips, every day starts with a plan and ends "
           "closed, the routine work of the firm runs without SH, and every decision is made with the facts "
           "in front of SH.")

# Each duty: what DONNA promises, the agents she directs for it, the scheduled jobs that deliver it,
# the commands SH uses, and how SH can tell it is being done.
DUTIES: list[dict[str, Any]] = [
    {"key": "day", "name": "Run your day",
     "promise": "Sorts your inbox, keeps your task list and diary, reminds you before meetings, and opens "
                "and closes each day with one message.",
     "agents": ["assistant", "workflow", "rhythm", "mind"],
     "jobs": ["inbox_triage", "morning", "evening", "my_day", "shutdown", "nudges", "midday"],
     "commands": "!today !tasks !add !done !draft !diary !workflow !morning !evening",
     "proof": "Morning message by 08:00 your time; urgent unread email reaches you within 15 minutes."},
    {"key": "compliance", "name": "Keep the firm compliant",
     "promise": "Runs the AML/KYC and execution rules on every back-office report, keeps the case and "
                "complaints registers, screens names against sanctions and PEP lists, tracks regulatory "
                "deadlines and builds the weekly pack and STR evidence packs.",
     "agents": ["compliance", "screening", "complaints", "str_pack", "ib", "pack", "backoffice"],
     "jobs": ["backoffice_ingest", "report_mail", "compliance_report", "sanctions_lists", "weekly_pack"],
     "commands": "!compliance !cases !kri !oblig !complaints !screen !str !ib !pack",
     "proof": "No regulatory deadline reaches its due date without a reminder 2 days before; "
              "the Monday pack arrives on its own."},
    {"key": "desk", "name": "Watch the desk and the markets",
     "promise": "Watches execution quality and LP slippage, writes the desk report, the morning dispatch "
                "and the daily wrap.",
     "agents": ["dealing", "execution", "market", "briefing", "watch"],
     "jobs": ["execution", "lp_slippage", "desk_report", "morning_dispatch", "daily_wrap", "morning_brief", "watch"],
     "commands": "!mi  (and the automatic dispatch and wrap)",
     "proof": "Daily wrap at 21:00 your time; an LP slippage episode is flagged within the hour."},
    {"key": "trading", "name": "Protect your own trading",
     "promise": "Reads your cTrader account (read-only), watches open risk, stops and the daily-loss limit, "
                "keeps your trading journal and watches your cBots, and checks your own trades against the PAD "
                "policy with a monthly pack for the MLRO.",
     "agents": ["personal", "rhythm", "botlab", "trading", "pad"],
     "jobs": ["ctrader_positions", "pad_pack"],
     "commands": "!risk !journal !pad",
     "proof": "A position without a stop or a hit daily-loss limit is flagged within 15 minutes."},
    {"key": "growth", "name": "Grow the business",
     "promise": "Finds possible IBs and traders, drafts first messages for your OK, builds country campaigns "
                "and compliant post drafts, tracks which source brings funded clients, flags clients at risk "
                "of leaving and runs one growth experiment a week.",
     "agents": ["growth", "copilot", "leadfinder", "radar", "campaigns", "attribution", "clients", "pipeline"],
     "jobs": ["marketing_drafts", "growth_review", "partner_radar", "retention", "pipeline"],
     "commands": "!leads !crm !campaign !drafts !review !attribution !growth !exp",
     "proof": "A fresh lead shortlist each week; nothing is sent to a lead without your confirm."},
    {"key": "mind", "name": "Think with you",
     "promise": "Talks ideas through with you, sorts a messy head into a plan, stress-tests ideas against "
                "the real numbers, remembers your goals and rules, and runs the weekly review.",
     "agents": ["mind"],
     "jobs": [],
     "commands": "chat in #donna, !dump !think !idea !ideas !remember !memory !week",
     "proof": "Every decision you make in chat becomes a task; every idea lands on the board."},
    {"key": "self", "name": "Keep herself honest and running",
     "promise": "Keeps a tamper-evident log of everything she does, checks in every 10 minutes so you are "
                "emailed if she goes offline, backs herself up nightly, and tells you plainly what is "
                "waiting on you.",
     "agents": ["hq"],
     "jobs": ["heartbeat"],
     "commands": "!status !audit !agents !hq",
     "proof": "!status is green, or names exactly what is waiting on you."},
]

BOUNDARIES = [
    "Never places, changes or closes a trade, and never moves money.",
    "Never messages a client. Leads only get a first message after you type !leads confirm.",
    "Never sends email for you: replies go to your Gmail Drafts for you to check and send.",
    "Never invites anyone to a meeting, and never changes or deletes an existing calendar event.",
    "Passwords and keys only go into vertex1-connect on the server, never into chat.",
    "Anything that costs money waits for your yes.",
]


def mandate_text() -> str:
    L = ["DONNA'S MANDATE", PURPOSE, "", "What I do for you, and how you can tell:"]
    for i, d in enumerate(DUTIES, 1):
        L += ["", f"{i}. {d['name']}", f" {d['promise']}", f" Commands: {d['commands']}", f" You can tell: {d['proof']}"]
    L += ["", "What I never do:"] + [f" - {b}" for b in BOUNDARIES]
    L += ["", "I coordinate the other agents for you: !agents shows each one and whether it's working, "
              "!run <job> runs one now, !hq gives one view across all of them."]
    return "\n".join(L)


def mandate_prompt() -> str:
    """Compact mandate for DONNA's chat persona."""
    duties = "\n".join(f"- {d['name']}: {d['promise']} (commands: {d['commands']})" for d in DUTIES)
    return (f"Your mandate: {PURPOSE}\nYour duties, each carried out through the agents you coordinate:\n{duties}\n"
            "Hard limits: " + " ".join(BOUNDARIES) + "\nWhen asked what you can do, answer from this mandate in "
            "full. When asked to do something, route it to the right agent's tools, and name the command that "
            "gives the same answer next time.")


# ------------------------------------------------------------------ coordination
def _ago(ts: float, now: float) -> str:
    m = int((now - ts) / 60)
    return f"{m}m ago" if m < 90 else f"{m // 60}h ago" if m < 48 * 60 else f"{m // 1440}d ago"


def job_states(now: float | None = None) -> dict[str, dict[str, Any]]:
    """State of every scheduled job: ok / waiting / failing / not_run, with when it last ran."""
    from jarvis import scheduler

    now = now or time.time()
    last = scheduler.load_last()
    waiting = scheduler.waiting_jobs()
    failing = scheduler.failing_jobs()
    errors: dict[str, str] = {}
    since = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    for r in audit.since(since, limit=20000):
        if r["actor"] == "scheduler" and r["event"] == "job_error":
            try:
                p = json.loads(r["payload"])
            except (TypeError, ValueError):
                continue
            errors[p.get("job", "?")] = str(p.get("error", ""))[:120]
    out = {}
    for name, every, at_hour, _ in scheduler.active_jobs():
        st: dict[str, Any] = {"every": every, "at_hour": at_hour, "last": last.get(name)}
        if name in waiting:
            st.update(state="waiting", why=waiting[name][0])
        elif name in failing:
            st.update(state="failing", since=failing[name], why=errors.get(name, ""))
        elif name not in last:
            st["state"] = "not_run"
        else:
            st["state"] = "ok"
        out[name] = st
    return out


ICON = {"ok": "OK", "waiting": "WAITING", "failing": "FAILING", "not_run": "not run yet"}


def _job_line(name: str, st: dict[str, Any], now: float) -> str:
    every = st["every"]
    when = (f"weekdays {st['at_hour']:02d}:00 UTC" if st["at_hour"] is not None
            else f"every {every // 60}h" if every >= 60 and every % 60 == 0 else f"every {every}m")
    last = f", last ran {_ago(st['last'], now)}" if st.get("last") else ""
    line = f" {ICON[st['state']]:<8} {name} ({when}{last})"
    if st["state"] == "waiting":
        line += f"\n           waiting for: {st['why']}"
    elif st["state"] == "failing":
        line += f"\n           failing since {_ago(st['since'], now)}: {st['why']}"
    return line


def duty_state(d: dict[str, Any], states: dict[str, dict[str, Any]]) -> str:
    sts = [states[j]["state"] for j in d["jobs"] if j in states]
    if not sts:
        return "on demand"
    if "failing" in sts:
        return "needs a fix"
    if "waiting" in sts:
        return "partly waiting on setup"
    return "running"


def agents_text(now: float | None = None) -> str:
    now = now or time.time()
    states = job_states(now)
    L = ["AGENTS - who does what for you, and whether it's working"]
    for d in DUTIES:
        L += ["", f"{d['name'].upper()}  [{duty_state(d, states)}]", f" agents: {', '.join(d['agents'])}"]
        L += [_job_line(j, states[j], now) for j in d["jobs"] if j in states]
    L += ["", "Run one now: !run <job name>   One view of everything: !hq   My mandate: !mandate"]
    return "\n".join(L)


def hq_text() -> str:
    """One view across all agents: what needs SH first, then each duty in one line."""
    from jarvis.agents import rhythm

    states = job_states()
    L = ["DONNA HQ"] + rhythm._safe(rhythm.needs_you, "NEEDS YOU") + ["", "YOUR AGENTS"]
    for d in DUTIES:
        bad = [j for j in d["jobs"] if states.get(j, {}).get("state") in ("failing", "waiting")]
        L.append(f" {d['name']}: {duty_state(d, states)}" + (f" ({', '.join(bad)})" if bad else ""))
    from jarvis.agents import kpi

    L += [""] + rhythm._safe(kpi.summary_lines, "KPIS")
    L += [""] + rhythm._safe(rhythm.health_lines, "SYSTEM") + ["", "Details: !agents   Run one: !run <job>   KPIs: !kpi"]
    return "\n".join(L)


def run_job(name: str) -> str:
    """Run one scheduled job now, the way the scheduler would, and say what happened."""
    from jarvis import scheduler
    from jarvis.core.registry import load_builtin_agents

    jobs = {n: fn for n, _, _, fn in scheduler.JOBS}
    name = name.strip().lower().replace(" ", "_").replace("-", "_")
    if name not in jobs:
        return f"No job called '{name}'. The names are in !agents, for example: !run daily_wrap"
    load_builtin_agents()
    audit.log("hq", "run_job", {"job": name})
    try:
        jobs[name]()
    except Exception as e:  # report it the same way the scheduler would, so !agents and !status agree
        scheduler.job_failed(name, e)
        return f"{name} failed: {type(e).__name__}: {str(e)[:300]}"
    scheduler.job_ok(name)
    return f"{name} ran. Anything it reports has gone to its usual channel."


def command(cmd: str, rest: str) -> str | None:
    if cmd in ("!mandate", "!what", "!donna"):
        return mandate_text()
    if cmd == "!agents":
        return agents_text()
    if cmd == "!hq":
        return hq_text()
    if cmd == "!run":
        return run_job(rest) if rest.strip() else "Usage: !run <job name>  (names are in !agents)"
    return None


@register
class HQAgent(Agent):
    name = "hq"
    description = "DONNA's mandate and the coordination board: every agent, the duty it serves and whether it works."

    @tool()
    def mandate(self) -> str:
        """DONNA's full mandate: purpose, duties, the agents and commands behind each, and her hard limits."""
        return mandate_text()

    @tool()
    def agents_board(self) -> dict[str, Any]:
        """Every scheduled agent job grouped by duty, with its state (ok/waiting/failing/not_run)."""
        states = job_states()
        return {d["name"]: {"state": duty_state(d, states), "jobs": {j: states[j] for j in d["jobs"] if j in states}}
                for d in DUTIES}
