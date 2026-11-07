"""KPIs for every DONNA agent and for trading, scored by DONNA herself.

Each KPI has a department, a goal (grow = the business gets bigger, protect = it stays safe while it does),
a target and an amber line. DONNA reads the numbers from her own records (no Claude call, no credit needed)
and scores each one GREEN / AMBER / RED, or NO DATA when the source isn't connected yet (never a fake green).

  !kpi                      the whole scorecard, worst first inside each department
  !kpi leads|marketing|growth|compliance|admin|desk|self|trading|book    one department
  !kpi targets              every target and how to change it
  !kpi set <name> <target> [amber]   change a target (kept in the state DB, survives restarts)
  !kpi reset <name>         back to the starter target
  !kpi lead 12              the lead quality check for one lead: which gates it passes and fails

Also used by: !hq (one KPI line + the reds), !leads (QUALIFIED tags, qualified counts in !leads stats),
the weekly management pack (a KPI section) and the growth brake (see brake()).
Trader KPIs only read the cTrader feed and back-office exports. Nothing here places or changes a trade.
"""
from __future__ import annotations

import math
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from jarvis.core import audit
from jarvis.core.config import settings
from jarvis.core.registry import Agent, register, tool

# ------------------------------------------------------------------ lead quality bar
LEAD_MIN_SCORE = {"ib": int(os.getenv("DONNA_LEAD_MIN_SCORE_IB", "60")),
                  "trader": int(os.getenv("DONNA_LEAD_MIN_SCORE_TRADER", "50"))}
LEAD_ACTIVE_DAYS = {"ib": 60, "trader": 14}
LEAD_MIN_AUDIENCE = int(os.getenv("DONNA_LEAD_MIN_AUDIENCE", "1000"))
BROKER_WORDS = re.compile(r"\b(broker|account|spread|deposit|platform|ctrader|mt[45]|metatrader|leverage)\b", re.I)
PERSONAL_PROFILE = re.compile(r"linkedin\.com/in/", re.I)

# growth brake: when one of these protect KPIs is red, outreach gets a warning (DONNA_KPI_BRAKE=block stops it)
BRAKE_KEYS = ("leads_flagged_contacted", "co_sanctions_open", "co_complaints_late", "co_obligations_overdue",
              "self_unconfirmed_sends", "self_audit_ok")
BRAKE_MODE = os.getenv("DONNA_KPI_BRAKE", "warn").lower()


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def qualify(r: dict[str, Any], now: datetime | None = None) -> tuple[bool, list[str], list[str]]:
    """The lead quality bar. Returns (qualified, gates passed, reasons it fails), same rules for every source."""
    from jarvis.agents import leadfinder, radar

    now = now or datetime.now(timezone.utc)
    kind = r.get("kind") or "ib"
    text = f"{r.get('name') or ''} {r.get('about') or ''}"
    ok: list[str] = []
    bad: list[str] = []

    def gate(passed: bool, good: str, why: str) -> None:
        (ok if passed else bad).append(good if passed else why)

    permitted = radar.permitted()
    gate(bool(r.get("country")) and r.get("country") in permitted, "country cleared",
         f"country {r.get('country') or 'unknown'} not cleared for marketing")
    gate(not (r.get("flags") or "").strip(), "no red flags", f"red flags: {r.get('flags')}")
    url = r.get("url") or ""
    gate(bool(url) and not PERSONAL_PROFILE.search(url), "public link",
         "personal LinkedIn profile" if PERSONAL_PROFILE.search(url) else "no public link to check")
    seen = _parse(r.get("last_active")) or _parse(r.get("first_seen"))
    days = (now - seen).days if seen else None
    limit = LEAD_ACTIVE_DAYS.get(kind, 60)
    gate(days is not None and days <= limit, "active recently",
         "activity unknown" if days is None else f"quiet {days} days (needs {limit} or fewer)")
    relevant = bool(radar.FIT_WORDS.search(text) or leadfinder.IB_WORDS.search(text)
                    or (kind == "trader" and BROKER_WORDS.search(text)))
    gate(relevant, "about trading", "not about trading or partnering")
    if kind == "ib" and r.get("audience") is not None:
        gate((r.get("audience") or 0) >= LEAD_MIN_AUDIENCE, "audience big enough",
             f"audience {r.get('audience') or 0:,} (needs {LEAD_MIN_AUDIENCE:,})")
    need = LEAD_MIN_SCORE.get(kind, 60)
    gate((r.get("score") or 0) >= need, f"score {r.get('score') or 0}", f"score {r.get('score') or 0} (needs {need})")
    return not bad, ok, bad


def lead_tag(r: dict[str, Any], now: datetime | None = None) -> str:
    q, _, bad = qualify(r, now)
    return "QUALIFIED" if q else "not counted: " + "; ".join(bad[:2])


def lead_check_text(pid: int) -> str:
    from jarvis.agents import leadfinder

    r = leadfinder._prospect(pid)
    if not r:
        return f"No prospect #{pid}."
    q, ok, bad = qualify(r)
    return "\n".join([f"LEAD QUALITY CHECK #{pid} {r['name'][:60]}",
                      f" Result: {'QUALIFIED - counts toward the lead KPIs' if q else 'NOT COUNTED'}"]
                     + [f"  pass  {g}" for g in ok] + [f"  FAIL  {g}" for g in bad]
                     + ["", "The bar: cleared country, no red flags, a public link, active recently "
                            f"({LEAD_ACTIVE_DAYS['ib']}d IBs / {LEAD_ACTIVE_DAYS['trader']}d traders), about trading, "
                            f"audience {LEAD_MIN_AUDIENCE:,}+ when shown (IBs), score {LEAD_MIN_SCORE['ib']}+ IBs / "
                            f"{LEAD_MIN_SCORE['trader']}+ traders."])


# ------------------------------------------------------------------ the KPI book
DEPTS = [("leads", "Lead finder"), ("marketing", "Marketing"), ("growth", "Growth / CRM"),
         ("compliance", "Compliance"), ("admin", "Admin desk"), ("desk", "Desk / daily wrap"),
         ("self", "DONNA herself"), ("trading", "Your trading"), ("book", "Client book")]
DEPT_ALIASES = {"lead": "leads", "leadfinder": "leads", "mkt": "marketing", "crm": "growth", "co": "compliance",
                "day": "admin", "wrap": "desk", "donna": "self", "trader": "trading", "traders": "trading",
                "me": "trading", "clients": "book"}

# key: (dept, name, goal, direction, target, amber, unit). direction "min" = higher is better.
# amber == target means there is no warning band: missing the target is red straight away.
STARTER: dict[str, tuple[str, str, str, str, float, float, str]] = {
    "leads_qualified_week": ("leads", "Qualified leads found in 7 days", "grow", "min", 15, 8, ""),
    "leads_qualified_rate": ("leads", "Share of leads found (30 days) that qualify", "grow", "min", 40, 25, "%"),
    "leads_approval_rate": ("leads", "Leads you approve out of those you review (90 days)", "grow", "min", 50, 30, "%"),
    "leads_contact_speed": ("leads", "Approved leads contacted within 3 days", "grow", "min", 80, 50, "%"),
    "leads_reply_rate": ("leads", "Reply rate, replied / contacted (90 days)", "grow", "min", 15, 8, "%"),
    "leads_wins_month": ("leads", "Wins in 30 days (IB signed or trader funded)", "grow", "min", 2, 1, ""),
    "leads_flagged_contacted": ("leads", "Red-flag leads contacted", "protect", "max", 0, 0, ""),
    "mkt_drafts_week": ("marketing", "Post drafts written in 7 days", "grow", "min", 5, 3, ""),
    "mkt_drafts_clean": ("marketing", "Drafts passing the compliance review (30 days)", "protect", "min", 80, 60, "%"),
    "mkt_campaigns_month": ("marketing", "Country campaigns built in 30 days", "grow", "min", 2, 1, ""),
    "crm_conversion": ("growth", "CRM lead-to-first-deposit conversion", "grow", "min", 5, 2, "%"),
    "crm_stale_leads": ("growth", "CRM leads left untouched too long", "grow", "max", 0, 5, ""),
    "crm_net_deposits": ("growth", "Net deposits, latest activity export", "grow", "min", 0, 0, "$"),
    "co_overdue_cases": ("compliance", "Overdue compliance cases", "protect", "max", 0, 4, ""),
    "co_complaints_late": ("compliance", "Complaints late (acknowledgement or final answer)", "protect", "max", 0, 0, ""),
    "co_obligations_overdue": ("compliance", "Regulatory obligations past due", "protect", "max", 0, 0, ""),
    "co_kyc_pending": ("compliance", "KYC documents pending more than 2 days", "protect", "max", 3, 9, ""),
    "co_case_hours": ("compliance", "Average hours to close a case (30 days)", "protect", "max", 72, 120, "h"),
    "co_sanctions_open": ("compliance", "Open sanctions / PEP matches", "protect", "max", 0, 0, ""),
    "co_data_age": ("compliance", "Age of the newest back-office export", "protect", "max", 3, 5, "d"),
    "day_overdue_tasks": ("admin", "Overdue tasks", "grow", "max", 0, 3, ""),
    "day_on_time": ("admin", "Tasks with a due date finished on time (30 days)", "grow", "min", 90, 75, "%"),
    "day_messages": ("admin", "Days with your morning and evening message (7 days)", "grow", "min", 5, 4, ""),
    "day_inbox_ok": ("admin", "Inbox triage running", "protect", "min", 1, 1, "yes"),
    "desk_wraps": ("desk", "Days the daily wrap went out (7 days)", "grow", "min", 5, 4, ""),
    "desk_rejected_pct": ("desk", "Rejected deals as % of all deals", "protect", "max", 0.5, 1, "%"),
    "self_jobs_ok": ("self", "Scheduled jobs not failing", "protect", "min", 95, 85, "%"),
    "self_audit_ok": ("self", "Audit trail intact", "protect", "min", 1, 1, "yes"),
    "self_heartbeat": ("self", "Minutes since the last heartbeat", "protect", "max", 20, 60, "m"),
    "self_unconfirmed_sends": ("self", "Messages sent to leads without your confirm", "protect", "max", 0, 0, ""),
    "tr_lots_min": ("trading", "Lots traded in 7 days (activity floor)", "grow", "min", 1, 0.5, "lots"),
    "tr_lots_max": ("trading", "Lots traded in 7 days (overtrading cap)", "protect", "max", 10, 15, "lots"),
    "tr_max_loss": ("trading", "Largest single loss as % of balance (30 days)", "protect", "max", 1, 3, "%"),
    "tr_stops": ("trading", "Open positions with a hard stop", "protect", "min", 100, 100, "%"),
    "tr_open_risk": ("trading", "Open risk at stops as % of equity", "protect", "max",
                     settings.max_open_risk_pct, settings.max_open_risk_pct, "%"),
    "tr_loss_days": ("trading", f"Days the {settings.max_daily_loss_pct:g}% daily-loss limit was hit (14 days)",
                     "protect", "max", 0, 0, ""),
    "tr_drawdown": ("trading", "Worst drawdown as % of starting balance (30 days)", "protect", "max", 10, 15, "%"),
    "tr_profit_factor": ("trading", "Profit factor, money won / money lost (30 days)", "grow", "min", 1.3, 1.0, ""),
    "tr_revenge": ("trading", "Re-entries within 5 min of a loss (30 days)", "protect", "max", 0, 2, ""),
    "tr_overtrading": ("trading", "Days with too many trades (30 days)", "protect", "max", 0, 1, ""),
    "book_lots": ("book", "Client lots traded, latest activity export", "grow", "min", 100, 50, "lots"),
    "book_active": ("book", "Client accounts that traded", "grow", "min", 20, 10, ""),
    "book_stopout": ("book", "Accounts near stop-out", "protect", "max", 1, 2, ""),
    "book_concentration": ("book", "Largest client's share of equity", "protect", "max", 30, 50, "%"),
    "book_turnover": ("book", "Accounts with very high turnover (churning or abuse review)", "protect", "max", 0, 2, ""),
    "book_in_out": ("book", "Money in and out with no trading (AML trigger)", "protect", "max", 0, 0, ""),
}

# lot sizes in units (cTrader volume / 100); symbols not listed are forex at 100,000 units a lot
LOT_SIZES = {k.strip().upper(): float(v) for k, _, v in
             (p.partition("=") for p in os.getenv(
                 "JARVIS_LOT_SIZES", "XAU=100,XAG=5000,XPT=100,XPD=100,BTC=1,ETH=1,US30=1,NAS100=1,USTEC=1,"
                                     "US500=1,SPX500=1,GER40=1,DE40=1,UK100=1,JP225=1,USOIL=1000,UKOIL=1000,"
                                     "WTI=1000,BRENT=1000").split(",") if "=" in p)}
FX_LOT = 100_000.0


def lots(symbol: str, units: float) -> float:
    s = (symbol or "").upper()
    size = next((v for k, v in sorted(LOT_SIZES.items(), key=lambda kv: -len(kv[0])) if s.startswith(k)), FX_LOT)
    return (units or 0) / size


def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS kpi_targets (key TEXT PRIMARY KEY, target REAL, amber REAL, set_at TEXT)")
    return c


def targets() -> dict[str, dict[str, Any]]:
    with _db() as c:
        over = {k: (t, a) for k, t, a in c.execute("SELECT key, target, amber FROM kpi_targets")}
    out = {}
    for k, (dept, name, goal, direction, t, a, unit) in STARTER.items():
        tt, aa = over.get(k, (t, a))
        out[k] = {"key": k, "dept": dept, "name": name, "goal": goal, "dir": direction, "target": tt,
                  "amber": aa, "unit": unit, "changed": k in over}
    return out


def set_target(key: str, target: float, amber: float | None = None) -> str:
    key = key.strip().lower()
    if key not in STARTER:
        close = [k for k in STARTER if key and key in k][:5]
        return f"No KPI called '{key}'." + (f" Did you mean: {', '.join(close)}?" if close else " See !kpi targets.")
    k = STARTER[key]
    if amber is None:  # keep the same warning gap as the starter target
        amber = target + (k[5] - k[4])
    if (k[3] == "min" and amber > target) or (k[3] == "max" and amber < target):
        return (f"For {key} {'higher' if k[3] == 'min' else 'lower'} is better, so the amber line must be "
                f"{'at or below' if k[3] == 'min' else 'at or above'} the target.")
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO kpi_targets VALUES (?,?,?,?)",
                  (key, target, amber, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    audit.log("officer", "kpi_target_set", {"kpi": key, "target": target, "amber": amber})
    t = targets()[key]
    return f"{t['name']}: target now {_target_text(t)}."


def reset_target(key: str) -> str:
    key = key.strip().lower()
    with _db() as c:
        n = c.execute("DELETE FROM kpi_targets WHERE key = ?", (key,)).rowcount
    if n:
        audit.log("officer", "kpi_target_reset", {"kpi": key})
        return f"{key}: back to the starter target ({_target_text(targets()[key])})."
    return f"{key} already uses its starter target." if key in STARTER else f"No KPI called '{key}'."


# ------------------------------------------------------------------ measuring
Measure = Callable[[datetime], Any]  # returns a number, (number, note), or None for no data
MEASURES: dict[str, Measure] = {}


def measure(key: str) -> Callable[[Measure], Measure]:
    def deco(fn: Measure) -> Measure:
        MEASURES[key] = fn
        return fn
    return deco


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _pct(a: float, b: float) -> float | None:
    return a / b * 100 if b else None


def _leads(now: datetime) -> dict[str, Any]:
    from jarvis.agents import leadfinder

    with leadfinder._db() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM lead_prospects")]
        ev = [dict(r) for r in db.execute("SELECT prospect, status, ts FROM lead_events")]
    return {"rows": rows, "events": ev}


@measure("leads_qualified_week")
def _m_lq_week(now: datetime) -> Any:
    d = _leads(now)
    if not d["rows"]:
        return None
    since = now - timedelta(days=7)
    return sum(1 for r in d["rows"] if (_parse(r["first_seen"]) or now) >= since and qualify(r, now)[0])


@measure("leads_qualified_rate")
def _m_lq_rate(now: datetime) -> Any:
    since = now - timedelta(days=30)
    found = [r for r in _leads(now)["rows"] if (_parse(r["first_seen"]) or now) >= since]
    q = sum(qualify(r, now)[0] for r in found)
    return (_pct(q, len(found)), f"{q} of {len(found)}") if found else None


@measure("leads_approval_rate")
def _m_lapprove(now: datetime) -> Any:
    from jarvis.agents import leadfinder

    g = list(leadfinder.stats(90, now)["groups"].values())
    approved, rejected = sum(x["approved"] for x in g), sum(x["rejected"] for x in g)
    return (_pct(approved, approved + rejected), f"{approved + rejected} reviewed") if approved + rejected else None


@measure("leads_contact_speed")
def _m_lspeed(now: datetime) -> Any:
    ev = _leads(now)["events"]
    first: dict[str, dict[int, datetime]] = {"approved": {}, "contacted": {}}
    for e in ev:
        t = _parse(e["ts"])
        if e["status"] in first and t and (e["prospect"] not in first[e["status"]] or t < first[e["status"]][e["prospect"]]):
            first[e["status"]][e["prospect"]] = t
    fast = due = 0
    for pid, t in first["approved"].items():
        if t < now - timedelta(days=30):
            continue
        c = first["contacted"].get(pid)
        if c is not None and c - t <= timedelta(days=3):
            fast += 1
            due += 1
        elif now - t > timedelta(days=3):  # 3 days have passed and it's still not (or late) contacted
            due += 1
    return (_pct(fast, due), f"{fast} of {due}") if due else None


@measure("leads_reply_rate")
def _m_lreply(now: datetime) -> Any:
    from jarvis.agents import leadfinder

    g = list(leadfinder.stats(90, now)["groups"].values())
    contacted, replied = sum(x["contacted"] for x in g), sum(x["replied"] for x in g)
    return (_pct(replied, contacted), f"{replied} of {contacted}") if contacted else None


@measure("leads_wins_month")
def _m_lwins(now: datetime) -> Any:
    d = _leads(now)
    if not d["rows"]:
        return None
    since = now - timedelta(days=30)
    return len({e["prospect"] for e in d["events"] if e["status"] in ("partner", "funded")
                and (_parse(e["ts"]) or now) >= since})


@measure("leads_flagged_contacted")
def _m_lflagged(now: datetime) -> Any:
    rows = _leads(now)["rows"]
    if not rows:
        return None
    return sum(1 for r in rows if (r["flags"] or "").strip()
               and r["status"] in ("contacted", "replied", "meeting", "partner", "funded"))


def _drafts(days: int, now: datetime) -> list[dict[str, Any]] | None:
    from jarvis.agents import growth

    with growth._db() as c:
        c.row_factory = sqlite3.Row
        if not c.execute("SELECT 1 FROM content_drafts LIMIT 1").fetchone():
            return None
        return [dict(r) for r in c.execute("SELECT verdict FROM content_drafts WHERE created >= ?",
                                           (_iso(now - timedelta(days=days)),))]


@measure("mkt_drafts_week")
def _m_drafts(now: datetime) -> Any:
    d = _drafts(7, now)
    return None if d is None else len(d)


@measure("mkt_drafts_clean")
def _m_clean(now: datetime) -> Any:
    d = _drafts(30, now)
    if not d:
        return None
    ok = sum(1 for r in d if r["verdict"] != "reject")
    return _pct(ok, len(d)), f"{ok} of {len(d)}"


@measure("mkt_campaigns_month")
def _m_campaigns(now: datetime) -> Any:
    from jarvis.agents import campaigns

    with campaigns._db() as c:
        return c.execute("SELECT COUNT(*) FROM campaigns WHERE created >= ?",
                         (_iso(now - timedelta(days=30)),)).fetchone()[0]


@measure("crm_conversion")
def _m_conv(now: datetime) -> Any:
    from jarvis.agents import growth

    s = growth.leads_summary()
    return None if "error" in s or not s["leads"] else (_pct(s["ftd"], s["leads"]), f"{s['ftd']} of {s['leads']}")


@measure("crm_stale_leads")
def _m_stale(now: datetime) -> Any:
    from jarvis.agents import growth

    s = growth.leads_summary()
    return None if "error" in s else s["stale"]


def _activity() -> dict[str, Any] | None:
    from jarvis.agents import backoffice

    a = backoffice.activity_summary()
    return None if "error" in a else a


@measure("crm_net_deposits")
def _m_net(now: datetime) -> Any:
    a = _activity()
    return None if a is None else (a["deposits"] - a["withdrawals"], f"export {a['asof']}")


def _today(now: datetime) -> str:
    from jarvis.agents.mind import LOCAL_TZ

    return now.astimezone(LOCAL_TZ).date().isoformat()


@measure("co_overdue_cases")
def _m_cases(now: datetime) -> Any:
    from jarvis.agents import compliance

    today = _today(now)
    return sum(1 for c in compliance.list_cases(limit=100_000) if c["due"] and c["due"] < today)


@measure("co_complaints_late")
def _m_complaints(now: datetime) -> Any:
    from jarvis.agents import complaints

    return sum(1 for r in complaints.active() if r["ack_overdue"] or r["overdue"])


@measure("co_obligations_overdue")
def _m_oblig(now: datetime) -> Any:
    from jarvis.agents import compliance

    today = _today(now)
    return sum(1 for o in compliance.list_obligations(days=0) if o["due"] < today)


@measure("co_kyc_pending")
def _m_kyc(now: datetime) -> Any:
    from jarvis.agents import backoffice

    q = backoffice.kyc_summary()
    return None if "error" in q else len(q["pending_over_2d"])


@measure("co_case_hours")
def _m_case_hours(now: datetime) -> Any:
    from jarvis.agents import compliance

    return compliance.management_info(30)["avg_hours_to_close"]


@measure("co_sanctions_open")
def _m_sanctions(now: datetime) -> Any:
    from jarvis.agents import compliance

    return sum(1 for c in compliance.list_cases(limit=100_000) if c["category"] == "SANCTIONS")


@measure("co_data_age")
def _m_age(now: datetime) -> Any:
    from jarvis.agents import backoffice

    ages = [(n, k) for k in ("eod", "activity", "docs", "kpi") for _, n in [backoffice.data_age(k)] if n is not None]
    if not ages:
        return None
    n, k = min(ages)
    return n, f"{k} export"


@measure("day_overdue_tasks")
def _m_overdue(now: datetime) -> Any:
    from jarvis.agents import assistant

    today = _today(now)
    return sum(1 for t in assistant.list_tasks() if t["due"] and t["due"] < today)


@measure("day_on_time")
def _m_on_time(now: datetime) -> Any:
    from jarvis.agents import assistant

    since = _iso(now - timedelta(days=30))
    done = [t for t in assistant.list_tasks(include_done=True)
            if t["status"] == "done" and t["due"] and t["done_at"] and t["done_at"] >= since]
    ok = sum(1 for t in done if t["done_at"][:10] <= t["due"])
    return (_pct(ok, len(done)), f"{ok} of {len(done)}") if done else None


def _event_days(actor: str, events: tuple[str, ...], days: int, now: datetime) -> dict[str, set[str]]:
    from jarvis.agents.mind import LOCAL_TZ

    out: dict[str, set[str]] = {e: set() for e in events}
    for r in audit.since(_iso(now - timedelta(days=days)), limit=50_000):
        if r["actor"] == actor and r["event"] in out:
            t = _parse(r["ts"])
            if t:
                out[r["event"]].add(t.astimezone(LOCAL_TZ).date().isoformat())
    return out


@measure("day_messages")
def _m_messages(now: datetime) -> Any:
    d = _event_days("rhythm", ("morning", "evening"), 7, now)
    return len(d["morning"] & d["evening"])


def _job(name: str) -> str | None:
    from jarvis.agents import hq

    st = hq.job_states().get(name)
    return st["state"] if st else None


@measure("day_inbox_ok")
def _m_inbox(now: datetime) -> Any:
    st = _job("inbox_triage")
    return None if st in (None, "not_run") else (1 if st == "ok" else 0, st)


@measure("desk_wraps")
def _m_wraps(now: datetime) -> Any:
    return len(_event_days("briefing", ("daily_wrap",), 7, now)["daily_wrap"])


@measure("desk_rejected_pct")
def _m_rejected(now: datetime) -> Any:
    a = _activity()
    return None if a is None or not a["deals"] else (_pct(a["rejected"], a["deals"]), f"{a['rejected']} of {a['deals']}")


@measure("self_jobs_ok")
def _m_jobs(now: datetime) -> Any:
    from jarvis.agents import hq

    sts = [s["state"] for s in hq.job_states().values() if s["state"] in ("ok", "failing")]
    bad = sts.count("failing")
    return (_pct(len(sts) - bad, len(sts)), f"{bad} failing") if sts else None


@measure("self_audit_ok")
def _m_audit(now: datetime) -> Any:
    v = audit.verify()
    return (1, f"{v['checked']} entries") if v["ok"] else (0, f"broken at #{v.get('first_bad_id')}")


@measure("self_heartbeat")
def _m_heartbeat(now: datetime) -> Any:
    from jarvis import scheduler

    last = scheduler.load_last().get("heartbeat")
    return None if not last else (now.timestamp() - last) / 60


@measure("self_unconfirmed_sends")
def _m_unconfirmed(now: datetime) -> Any:
    from jarvis.agents import leadfinder

    with leadfinder._outbox() as db:
        return db.execute("SELECT COUNT(*) FROM lead_outbox WHERE status IN ('queued','sent') "
                          "AND (confirmed IS NULL OR confirmed = '')").fetchone()[0]


# --- your trading (read-only)
def _trades(days: int, now: datetime) -> list[dict[str, Any]] | None:
    from jarvis.agents import personal

    with personal._db() as c:
        c.row_factory = sqlite3.Row
        account = os.getenv("JARVIS_PERSONAL_ACCOUNT") or (
            (c.execute("SELECT account FROM pt_trades ORDER BY closed_utc DESC LIMIT 1").fetchone() or [None])[0])
        if not account:
            return None
        since = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        return [dict(r) for r in c.execute("SELECT * FROM pt_trades WHERE account = ? AND closed_utc >= ? "
                                           "ORDER BY closed_utc", (account, since))]


def _lots_week(now: datetime) -> Any:
    t = _trades(7, now)
    return None if t is None else (round(sum(lots(x["symbol"], x["qty"]) for x in t), 2), f"{len(t)} trades")


MEASURES["tr_lots_min"] = _lots_week
MEASURES["tr_lots_max"] = _lots_week


@measure("tr_max_loss")
def _m_max_loss(now: datetime) -> Any:
    t = _trades(30, now)
    if not t:
        return None if t is None else (0.0, "no trades in 30 days")
    worst = 0.0
    for x in t:
        before = (x["balance"] or 0) - (x["net"] or 0)
        if (x["net"] or 0) < 0 and before > 0:
            worst = max(worst, -x["net"] / before * 100)
    return worst


def _risk() -> dict[str, Any] | None:
    from jarvis.agents import rhythm

    r = rhythm.position_risk()
    return None if "error" in r else r


@measure("tr_stops")
def _m_stops(now: datetime) -> Any:
    r = _risk()
    if r is None:
        return None
    n = len(r["positions"])
    if not n:
        return 100.0, "flat"
    return _pct(n - len(r["no_stop"]), n), f"{n - len(r['no_stop'])} of {n}"


@measure("tr_open_risk")
def _m_open_risk(now: datetime) -> Any:
    r = _risk()
    return None if r is None or r["open_risk_pct"] is None else r["open_risk_pct"]


@measure("tr_loss_days")
def _m_loss_days(now: datetime) -> Any:
    from jarvis.agents import rhythm
    from jarvis.agents.mind import LOCAL_TZ

    with rhythm._db() as c:
        rows = c.execute("SELECT ts, equity, start_of_day_equity FROM position_snapshots WHERE ts >= ?",
                         (_iso(now - timedelta(days=14)),)).fetchall()
    if not rows:
        return None
    hit = {(_parse(ts) or now).astimezone(LOCAL_TZ).date() for ts, eq, sod in rows
           if sod and eq - sod <= -sod * settings.max_daily_loss_pct / 100}
    return len(hit)


@measure("tr_drawdown")
def _m_drawdown(now: datetime) -> Any:
    t = _trades(30, now)
    if not t:
        return None if t is None else (0.0, "no trades in 30 days")
    start = (t[0]["balance"] or 0) - (t[0]["net"] or 0)
    peak = curve = worst = 0.0
    for x in t:
        curve += x["net"] or 0
        peak = max(peak, curve)
        worst = min(worst, curve - peak)
    return (-worst / start * 100) if start > 0 else None


@measure("tr_profit_factor")
def _m_pf(now: datetime) -> Any:
    t = _trades(30, now)
    if not t:
        return None
    won = sum(x["net"] for x in t if x["net"] > 0)
    lost = -sum(x["net"] for x in t if x["net"] < 0)
    if not lost:
        return (None, "no losing trades yet") if not won else (99.0, "no losing trades")
    return won / lost, f"{len(t)} trades"


@measure("tr_revenge")
def _m_revenge(now: datetime) -> Any:
    from jarvis.agents import personal

    t = _trades(30, now)
    if t is None:
        return None
    n = 0
    for a, b in zip(t, t[1:]):
        ta, tb = _parse(a["closed_utc"]), _parse(b["closed_utc"])
        if a["net"] < 0 and ta and tb and (tb - ta) < timedelta(minutes=personal.REENTRY_MIN):
            n += 1
    return n


@measure("tr_overtrading")
def _m_overtrading(now: datetime) -> Any:
    from jarvis.agents import personal

    t = _trades(30, now)
    if t is None:
        return None
    per: dict[str, int] = {}
    for x in t:
        per[x["closed_utc"][:10]] = per.get(x["closed_utc"][:10], 0) + 1
    return sum(1 for v in per.values() if v > personal.MAX_TRADES_DAY), f"limit {personal.MAX_TRADES_DAY} a day"


# --- the client book (read-only, from back-office exports)
@measure("book_lots")
def _m_book_lots(now: datetime) -> Any:
    a = _activity()
    return None if a is None else (a["volume"] / FX_LOT, f"export {a['asof']}")


@measure("book_active")
def _m_book_active(now: datetime) -> Any:
    a = _activity()
    return None if a is None else (a["trading"], f"of {a['accounts']} accounts")


def _book() -> dict[str, Any] | None:
    from jarvis.agents import backoffice

    b = backoffice.book_summary()
    return None if "error" in b else b


@measure("book_stopout")
def _m_stopout(now: datetime) -> Any:
    b = _book()
    return None if b is None else len(b["margin_alerts"])


@measure("book_concentration")
def _m_conc(now: datetime) -> Any:
    b = _book()
    return None if b is None or not b["top_account"] else b["top_account"]["share_pct"]


@measure("book_turnover")
def _m_turnover(now: datetime) -> Any:
    a = _activity()
    return None if a is None else len(a["high_turnover"])


@measure("book_in_out")
def _m_in_out(now: datetime) -> Any:
    a = _activity()
    return None if a is None else len(a["in_out_no_trading"])


# ------------------------------------------------------------------ scoring
def rag(t: dict[str, Any], value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NO DATA"
    if t["dir"] == "min":
        return "GREEN" if value >= t["target"] else "AMBER" if value >= t["amber"] and t["amber"] != t["target"] else "RED"
    return "GREEN" if value <= t["target"] else "AMBER" if value <= t["amber"] and t["amber"] != t["target"] else "RED"


def _num(v: float, unit: str, plus: bool = False) -> str:
    if unit == "yes":
        return "yes" if v >= 1 else "no"
    if unit == "$":
        return (f"-${-v:,.0f}" if v < 0 else f"${v:,.0f}") + ("+" if plus else "")
    txt = f"{v:,.0f}" if abs(v) >= 100 or float(v).is_integer() else f"{v:,.1f}" if abs(v) >= 10 else f"{v:,.2f}"
    if unit == "%":
        return txt + ("%+" if plus and v < 100 else "%")
    return txt + ("+" if plus else "") + {"h": "h", "d": " days", "m": " min", "lots": " lots"}.get(unit, "")


def _target_text(t: dict[str, Any]) -> str:
    if t["unit"] == "yes":
        return "yes"
    if t["dir"] == "max":
        base = "0" if t["target"] == 0 else _num(t["target"], t["unit"]) + " or less"
    else:
        base = _num(t["target"], t["unit"], plus=True)
    return base + ("" if t["amber"] == t["target"] else f", amber at {_num(t['amber'], t['unit'])}")


def score_one(key: str, now: datetime | None = None, tg: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    t = dict((tg or targets())[key])
    note = ""
    try:
        v = MEASURES[key](now)
    except Exception as e:  # one dead source must not stop the scorecard
        audit.log("kpi", "measure_error", {"kpi": key, "error": str(e)[:200]})
        v, note = None, f"unavailable ({type(e).__name__})"
    if isinstance(v, tuple):
        v, note = v
    t.update(value=v, note=note, status=rag(t, v))
    return t


def scorecard(dept: str | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    tg = targets()
    return [score_one(k, now, tg) for k, t in tg.items() if dept is None or t["dept"] == dept]


ORDER = {"RED": 0, "AMBER": 1, "NO DATA": 2, "GREEN": 3}


def _line(r: dict[str, Any]) -> str:
    val = "-" if r["value"] is None else _num(r["value"], r["unit"])
    note = f" ({r['note']})" if r["note"] else ""
    return f" [{r['status']:<7}] {r['name']}: {val}{note}  | target {_target_text(r)}"


def counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {s: 0 for s in ORDER}
    for r in rows:
        out[r["status"]] += 1
    return out


def brake(rows: list[dict[str, Any]] | None = None, now: datetime | None = None) -> list[str]:
    """Protect KPIs that are red and should slow growth down: the names of what's wrong, or []."""
    if rows is None:
        tg = targets()
        rows = [score_one(k, now, tg) for k in BRAKE_KEYS]
    return [r["name"] for r in rows if r["key"] in BRAKE_KEYS and r["status"] == "RED"]


def scorecard_text(dept: str | None = None, now: datetime | None = None) -> str:
    rows = scorecard(dept, now)
    c = counts(rows)
    names = dict(DEPTS)
    L = [f"D.O.N.N.A | KPI SCORECARD{' - ' + names[dept].upper() if dept else ''}",
         f"{c['GREEN']} green, {c['AMBER']} amber, {c['RED']} red, {c['NO DATA']} no data yet"]
    for goal in ("grow", "protect"):
        g = [r for r in rows if r["goal"] == goal]
        if g:
            L.append(f" {'Grow' if goal == 'grow' else 'Protect'}: {sum(r['status'] == 'GREEN' for r in g)} of {len(g)} green")
    stop = brake(rows)
    if stop:
        L.append(f"!! GROWTH BRAKE: fix first - {'; '.join(stop)}")
    for key, name in DEPTS:
        g = sorted([r for r in rows if r["dept"] == key], key=lambda r: ORDER[r["status"]])
        if g:
            L += ["", name.upper()] + [_line(r) for r in g]
    L += ["", "Change a target: !kpi set <name> <target> [amber]   All names: !kpi targets   One lead: !kpi lead 12"]
    return "\n".join(L)


def targets_text() -> str:
    tg = targets()
    L = ["KPI TARGETS - change one with !kpi set <name> <target> [amber], undo with !kpi reset <name>"]
    for key, name in DEPTS:
        L += ["", name.upper()]
        L += [f" {t['key']:<24} {t['name']}: {_target_text(t)}{'  (changed by you)' if t['changed'] else ''}"
              for t in tg.values() if t["dept"] == key]
    return "\n".join(L)


def summary_lines(now: datetime | None = None) -> list[str]:
    """For !hq: one line of counts, the brake if on, and the worst reds."""
    rows = scorecard(None, now)
    c = counts(rows)
    L = ["KPIS", f" {c['GREEN']} green, {c['AMBER']} amber, {c['RED']} red, {c['NO DATA']} no data yet (!kpi)"]
    stop = brake(rows)
    if stop:
        L.append(" !! Growth brake: " + "; ".join(stop))
    reds = sorted([r for r in rows if r["status"] == "RED"], key=lambda r: r["goal"] != "protect")
    L += [f" RED {r['name']}: {_num(r['value'], r['unit'])} (target {_target_text(r)})" for r in reds[:4]]
    return L


def pack_lines(now: datetime | None = None) -> list[str]:
    rows = scorecard(None, now)
    c = counts(rows)
    L = [f"{c['GREEN']} green, {c['AMBER']} amber, {c['RED']} red, {c['NO DATA']} no data yet."]
    stop = brake(rows)
    if stop:
        L.append("Growth brake on: " + "; ".join(stop))
    names = dict(DEPTS)
    for key, _ in DEPTS:
        g = [r for r in rows if r["dept"] == key and r["status"] != "NO DATA"]
        if g:
            L.append(f"{names[key]}:")
            L += [_line(r) for r in sorted(g, key=lambda r: ORDER[r["status"]])]
    missing = [names[k] for k, _ in DEPTS if all(r["status"] == "NO DATA" for r in rows if r["dept"] == k)]
    if missing:
        L.append("No data yet for: " + ", ".join(missing))
    return L


def command(cmd: str, rest: str) -> str | None:
    if cmd not in ("!kpi", "!kpis", "!scorecard"):
        return None
    parts = rest.split()
    if not parts:
        return scorecard_text()
    sub = parts[0].lower()
    if sub == "targets":
        return targets_text()
    if sub == "set" and len(parts) >= 3:
        try:
            nums = [float(p.rstrip("%$").replace(",", "")) for p in parts[2:4]]
        except ValueError:
            return "Usage: !kpi set <name> <target> [amber]   e.g. !kpi set leads_qualified_week 20 10"
        return set_target(parts[1], nums[0], nums[1] if len(nums) > 1 else None)
    if sub == "reset" and len(parts) >= 2:
        return reset_target(parts[1])
    if sub == "lead" and len(parts) >= 2 and parts[1].lstrip("#").isdigit():
        return lead_check_text(int(parts[1].lstrip("#")))
    dept = DEPT_ALIASES.get(sub, sub)
    if dept in dict(DEPTS):
        return scorecard_text(dept)
    return ("!kpi   !kpi " + "|".join(k for k, _ in DEPTS) + "   !kpi targets\n"
            "!kpi set <name> <target> [amber]   !kpi reset <name>   !kpi lead 12")


@register
class KPIAgent(Agent):
    name = "kpi"
    description = "KPIs for every agent and for trading (grow and protect), scored green/amber/red from DONNA's records."

    @tool()
    def kpi_scorecard(self, department: str = "") -> list[dict[str, Any]]:
        """The KPI scorecard. department: leads, marketing, growth, compliance, admin, desk, self, trading, book,
        or empty for all. Each row: name, goal (grow/protect), value, target, amber line, status."""
        d = DEPT_ALIASES.get(department.lower(), department.lower()) or None
        return [{k: r[k] for k in ("key", "dept", "name", "goal", "value", "target", "amber", "unit", "status", "note")}
                for r in scorecard(d if d in dict(DEPTS) else None)]

    @tool()
    def lead_quality(self, prospect_id: int) -> str:
        """Run the lead quality bar on one Lead Finder prospect: which gates it passes and which it fails."""
        return lead_check_text(prospect_id)
