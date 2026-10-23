"""Compliance & risk officer desk.

Turns the back-office data the desk already receives into an auditable case register:

  scan()          runs every rule below over the latest data and opens a case per finding
                  (one case per rule + subject + ISO week, so a finding is never logged twice)
  cases           open / investigating / escalated / closed, owner, due date, resolution note -
                  the evidence trail a regulator or auditor asks for
  KRIs            key risk indicators with red/amber/green thresholds
  obligations     your own regulatory calendar (reports, filings, reviews) with reminders
  promo review    marketing copy checked against a financial-promotion checklist (Claude)

Rules (all thresholds are settings, nothing is hard-wired to one regulator):
  AML    money in and out with no trading | turnover far above equity | large deposits
  KYC    documents pending > 2 days | latest upload rejected | ID expiring
  RISK   margin level near stop-out | equity concentration | day-on-day equity drawdown
  CONDUCT bonus-heavy accounts
  EXEC   rejected deals | symbols enabled without quotes | spread widening | markup changes
  DATA   CRM volume 0 while cTrader shows volume
Reports carry account/customer numbers only.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jarvis.agents import backoffice as bo
from jarvis.core import audit
from jarvis.core.config import settings
from jarvis.core.registry import Agent, register, tool

LARGE_DEPOSIT = float(os.getenv("JARVIS_AML_LARGE_DEPOSIT_USD", "10000"))
CONCENTRATION_PCT = float(os.getenv("JARVIS_CONCENTRATION_ALERT_PCT", "40"))
SEVERITY_DAYS = {"high": 1, "medium": 3, "low": 7}          # due date = opened + this
TARGETS = [t for t in os.getenv("JARVIS_COMPLIANCE_TARGETS", os.getenv("JARVIS_PERSONAL_TARGETS", "")).split(",") if t]
OWNER = os.getenv("JARVIS_COMPLIANCE_OWNER", "compliance")


def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS cases (id INTEGER PRIMARY KEY AUTOINCREMENT, opened TEXT, category TEXT,
            rule TEXT, severity TEXT, subject TEXT, finding TEXT, evidence TEXT, status TEXT DEFAULT 'open',
            owner TEXT, due TEXT, closed_at TEXT, resolution TEXT, dedupe TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS case_log (case_id INTEGER, ts TEXT, who TEXT, action TEXT, note TEXT);
        CREATE TABLE IF NOT EXISTS obligations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, due TEXT,
            repeat TEXT, last_done TEXT);
    """)
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _week() -> str:
    y, w, _ = date.today().isocalendar()
    return f"{y}-W{w:02d}"


# ------------------------------------------------------------------ case register
def open_case(category: str, rule: str, severity: str, subject: str, finding: str,
              evidence: dict[str, Any] | None = None, period: str | None = None) -> int | None:
    """Open a case unless the same rule already fired for this subject in this period."""
    key = f"{rule}|{subject}|{period or _week()}"
    due = (date.today() + timedelta(days=SEVERITY_DAYS.get(severity, 7))).isoformat()
    with _db() as c:
        cur = c.execute("INSERT OR IGNORE INTO cases (opened, category, rule, severity, subject, finding, evidence, "
                        "owner, due, dedupe) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (_now(), category, rule, severity, subject, finding, json.dumps(evidence or {}, default=str),
                         OWNER, due, key))
        if not cur.rowcount:
            return None
        c.execute("INSERT INTO case_log VALUES (?,?,?,?,?)", (cur.lastrowid, _now(), "donna", "opened", finding))
        return cur.lastrowid


def update_case(case_id: int, status: str, note: str = "", who: str = "officer") -> bool:
    if status not in ("open", "investigating", "escalated", "closed"):
        raise ValueError("status must be open, investigating, escalated or closed")
    with _db() as c:
        row = c.execute("SELECT status FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not row:
            return False
        c.execute("UPDATE cases SET status = ?, closed_at = ?, resolution = COALESCE(NULLIF(?, ''), resolution) "
                  "WHERE id = ?", (status, _now() if status == "closed" else None, note, case_id))
        c.execute("INSERT INTO case_log VALUES (?,?,?,?,?)", (case_id, _now(), who, status, note))
    audit.log(who, "case_" + status, {"case": case_id, "note": note})
    return True


def list_cases(status: str = "active", limit: int = 50) -> list[dict[str, Any]]:
    where = {"active": "status != 'closed'", "closed": "status = 'closed'", "all": "1=1"}.get(status, "status = ?")
    params: tuple = () if status in ("active", "closed", "all") else (status,)
    with _db() as c:
        return [dict(r) for r in c.execute(
            f"SELECT * FROM cases WHERE {where} ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 "  # noqa: S608
            "ELSE 2 END, due, id LIMIT ?", (*params, limit))]


def get_case(case_id: int) -> dict[str, Any] | None:
    with _db() as c:
        row = c.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not row:
            return None
        log = [dict(r) for r in c.execute("SELECT * FROM case_log WHERE case_id = ? ORDER BY ts", (case_id,))]
    return {**dict(row), "log": log}


# ------------------------------------------------------------------ rules
def scan() -> dict[str, Any]:
    """Run every rule over the latest back-office data; returns the new case ids by rule."""
    new: dict[str, list[int]] = {}

    def add(category, rule, severity, subject, finding, evidence=None, period=None):
        cid = open_case(category, rule, severity, str(subject), finding, evidence, period)
        if cid:
            new.setdefault(rule, []).append(cid)

    t = bo.activity_summary()
    if "error" not in t:
        for a in t["in_out_no_trading"]:
            add("AML", "AML_IN_OUT_NO_TRADE", "high", f"#{a}", f"#{a} deposited and withdrew with no trading "
                "in the period - check source of funds, purpose and whether a report to the FIU is required", t)
        for a in t["high_turnover"]:
            add("AML", "AML_TURNOVER", "medium", f"#{a['account']}", f"#{a['account']} traded {a['x']:,.0f}x its "
                "equity - check for churning, wash trading or leverage abuse", a)
        with bo._db() as c:
            big = c.execute("SELECT account, deposit FROM bo_activity WHERE asof = ? AND deposit >= ?",
                            (t["asof"], LARGE_DEPOSIT)).fetchall()
        for acc, dep in big:
            add("AML", "AML_LARGE_DEPOSIT", "medium", f"#{acc}", f"#{acc} deposited ${dep:,.0f} "
                f"(threshold ${LARGE_DEPOSIT:,.0f}) - confirm source of funds is on file", {"deposit": dep})
        for a in t["rejections"]:
            add("EXECUTION", "EXEC_REJECTED_DEALS", "medium", f"#{a['account']}", f"#{a['account']}: "
                f"{a['rejected']} of {a['deals']} deals rejected - check pricing / margin settings", a)

    q = bo.kyc_summary()
    if "error" not in q:
        for cu in q["pending_over_2d"]:
            add("KYC", "KYC_PENDING_STALE", "medium", cu, f"{cu} has KYC documents pending more than 2 days")
        for cu, docs in q["rejected_open"].items():
            add("KYC", "KYC_REJECTED_OPEN", "medium", cu, f"{cu}: latest {docs} rejected - request a new document "
                "and restrict funding until verified")
        for e in q["expiring"]:
            add("KYC", "KYC_ID_EXPIRING", "low", e["customer"], f"{e['customer']} {e['doc_type']} expires "
                f"{e['expires']} - request an updated ID", e, period=e["expires"])
        if q["unassigned_pct"] > 50:
            add("KYC", "KYC_NO_OWNER", "low", "KYC queue", f"{q['unassigned_pct']:.0f}% of KYC documents have no "
                "assigned owner - allocate the queue")

    b = bo.book_summary()
    if "error" not in b:
        for a in b["margin_alerts"]:
            add("MARKET_RISK", "RISK_STOPOUT", "high", f"#{a['account']}", f"#{a['account']} margin level "
                f"{a['level']:.0f}% - near stop-out, contact the client / check negative-balance exposure", a,
                period=b["asof"])
        if b["top_account"] and b["top_account"]["share_pct"] >= CONCENTRATION_PCT:
            ta = b["top_account"]
            add("MARKET_RISK", "RISK_CONCENTRATION", "medium", f"#{ta['account']}", f"#{ta['account']} holds "
                f"{ta['share_pct']:.0f}% of client equity - single-client dependency", ta)
        for a in b.get("drawdowns", []):
            add("MARKET_RISK", "RISK_DRAWDOWN", "medium", f"#{a['account']}", f"#{a['account']} equity "
                f"{a['chg_pct']:+.0f}% day-on-day - suitability / retention review", a, period=b["asof"])
        for a in b["credit_heavy"]:
            add("CONDUCT", "CONDUCT_BONUS_HEAVY", "medium", f"#{a['account']}", f"#{a['account']} credit "
                f"${a['credit']:,.0f} vs balance ${a['balance']:,.0f} - check bonus terms and any regulatory "
                "restriction on incentives", a)

    s = bo.streams_summary()
    if "error" not in s:
        for grp, g in s["groups"].items():
            if g["no_quotes"]:
                add("EXECUTION", "EXEC_NO_QUOTES", "medium", grp, f"{grp}: {len(g['no_quotes'])} symbols enabled "
                    f"without quotes ({', '.join(g['no_quotes'][:8])}) - disable or fix the feed", g["no_quotes"])
        for grp, sym, a, z in s.get("widened", []):
            add("EXECUTION", "EXEC_SPREAD_WIDENED", "low", f"{grp}/{sym}", f"{sym} ({grp}) spread {a:g} -> {z:g} - "
                "record the reason (best-execution evidence)", period=s["asof"])
        if s.get("markup_changed"):
            add("EXECUTION", "EXEC_MARKUP_CHANGED", "low", "price streams", f"Markup changed on "
                f"{len(s['markup_changed'])} symbols - confirm it was an approved change", s["markup_changed"],
                period=s["asof"])

    k = bo.kpi_summary()
    if "error" not in k and "error" not in t and k["kpis"].get("Volume", 1) == 0 and t["volume"] > 0:
        add("DATA", "DATA_VOLUME_SYNC", "low", "CRM", f"CRM reports volume 0 while cTrader shows "
            f"${t['volume']:,.0f} - management information is understated")
    total = sum(len(v) for v in new.values())
    if total:
        audit.log("compliance", "scan", {"new_cases": new})
    return {"new_cases": total, "by_rule": new}


# ------------------------------------------------------------------ KRIs, obligations, MI
def kris() -> list[dict[str, Any]]:
    """Key risk indicators with red/amber/green status."""
    out = []

    def kri(name, value, amber, red, fmt="{:,.0f}"):
        rag = "RED" if value is not None and value >= red else "AMBER" if value is not None and value >= amber else "GREEN"
        out.append({"kri": name, "value": "n/a" if value is None else fmt.format(value), "status": rag if value is not None else "NO DATA"})

    active = list_cases(limit=100_000)
    overdue = [c for c in active if c["due"] and c["due"] < date.today().isoformat()]
    kri("Open high-severity cases", sum(c["severity"] == "high" for c in active), 1, 3)
    kri("Overdue cases", len(overdue), 1, 5)
    q = bo.kyc_summary()
    kri("KYC docs pending > 2 days", None if "error" in q else len(q["pending_over_2d"]), 3, 10)
    kri("Oldest pending KYC (days)", None if "error" in q else q["oldest_pending_days"], 3, 14)
    b = bo.book_summary()
    kri("Accounts near stop-out", None if "error" in b else len(b["margin_alerts"]), 1, 3)
    kri("Largest client share of equity %", None if "error" in b or not b["top_account"] else
        b["top_account"]["share_pct"], 30, 50)
    t = bo.activity_summary()
    kri("Deposit-withdraw-no-trade accounts", None if "error" in t else len(t["in_out_no_trading"]), 1, 3)
    return out


def add_obligation(name: str, due: str, repeat: str = "") -> int:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
        raise ValueError("due must be YYYY-MM-DD")
    if repeat not in ("", "weekly", "monthly", "quarterly", "yearly"):
        raise ValueError("repeat must be weekly, monthly, quarterly, yearly or blank")
    with _db() as c:
        return c.execute("INSERT INTO obligations (name, due, repeat) VALUES (?,?,?)", (name, due, repeat)).lastrowid


def _roll(due: date, repeat: str) -> date:
    if repeat == "weekly":
        return due + timedelta(days=7)
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}[repeat]
    m = due.month - 1 + months
    y, m = due.year + m // 12, m % 12 + 1
    for d in (due.day, 30, 29, 28):
        try:
            return date(y, m, d)
        except ValueError:
            continue
    raise ValueError("bad date")


def complete_obligation(ob_id: int) -> str:
    with _db() as c:
        row = c.execute("SELECT * FROM obligations WHERE id = ?", (ob_id,)).fetchone()
        if not row:
            return "not found"
        if row["repeat"]:
            nxt = _roll(date.fromisoformat(row["due"]), row["repeat"]).isoformat()
            c.execute("UPDATE obligations SET due = ?, last_done = ? WHERE id = ?", (nxt, _now(), ob_id))
            return f"done - next due {nxt}"
        c.execute("DELETE FROM obligations WHERE id = ?", (ob_id,))
        return "done"


def list_obligations(days: int = 3650) -> list[dict[str, Any]]:
    until = (date.today() + timedelta(days=days)).isoformat()
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM obligations WHERE due <= ? ORDER BY due", (until,))]


def management_info(days: int = 30) -> dict[str, Any]:
    """Cases opened/closed by category and time to close - for the board / regulator file."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM cases WHERE opened >= ?", (since,))]
    by: dict[str, dict[str, int]] = {}
    hours = []
    for r in rows:
        d = by.setdefault(r["category"], {"opened": 0, "closed": 0, "open": 0})
        d["opened"] += 1
        if r["status"] == "closed":
            d["closed"] += 1
            if r["closed_at"]:
                hours.append((datetime.fromisoformat(r["closed_at"]) - datetime.fromisoformat(r["opened"])).total_seconds() / 3600)
        else:
            d["open"] += 1
    return {"days": days, "cases": len(rows), "by_category": by,
            "avg_hours_to_close": sum(hours) / len(hours) if hours else None}


# ------------------------------------------------------------------ promotions review
PROMO_PROMPT = """You are the compliance reviewer for a retail FX/CFD brokerage (Vertex 1 Brokers).
Review the marketing copy against financial-promotion standards common to FSCA, FCA, CySEC and similar regulators:
fair, clear and not misleading; prominent risk warning that CFDs are complex and losses can exceed or equal deposits;
no guaranteed, risk-free or "easy" profit claims; past performance not presented as a forecast; no pressure or
urgency tactics; bonuses/incentives described with their conditions (and flagged where they may be restricted);
firm name and licence/FSP number disclosed; balanced benefits vs risks; suitable for a retail audience.
Return only the JSON object. Quote the exact offending phrase in each issue and give a compliant rewrite."""
PROMO_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "approve_with_changes", "reject"]},
        "issues": {"type": "array", "items": {"type": "object", "properties": {
            "phrase": {"type": "string"}, "problem": {"type": "string"}, "fix": {"type": "string"}},
            "required": ["phrase", "problem", "fix"], "additionalProperties": False}},
        "compliant_version": {"type": "string"},
    },
    "required": ["verdict", "issues", "compliant_version"],
    "additionalProperties": False,
}


def review_promotion(text: str, client: Any = None) -> dict[str, Any]:
    import anthropic

    client = client or anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=settings.model, max_tokens=8000, system=PROMO_PROMPT,
        messages=[{"role": "user", "content": text}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": PROMO_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        return {"verdict": "reject", "issues": [], "compliant_version": "", "note": "declined by the safety system"}
    out = json.loads(next(b.text for b in resp.content if b.type == "text"))
    audit.log("compliance", "promo_review", {"verdict": out["verdict"], "issues": len(out["issues"])})
    return out


# ------------------------------------------------------------------ report
def report_text() -> str:
    res = scan()
    today = date.today().isoformat()
    L = [f"D.O.N.N.A | COMPLIANCE & RISK | {datetime.now(timezone.utc):%a %d %b %Y}", "-" * 34]
    L.append(f"New cases since last scan: {res['new_cases']}")
    L += ["", "KEY RISK INDICATORS"]
    L += [f" [{k['status']:<7}] {k['kri']}: {k['value']}" for k in kris()]
    active = list_cases(limit=100_000)
    L += ["", f"OPEN CASES ({len(active)})"]
    for c in active[:15]:
        late = "  !! OVERDUE" if c["due"] and c["due"] < today else ""
        L.append(f" #{c['id']} [{c['severity']}] {c['category']} {c['subject']}: {c['finding'][:110]} "
                 f"(due {c['due']}, {c['status']}){late}")
    if len(active) > 15:
        L.append(f" ... and {len(active) - 15} more - !cases in Discord")
    if not active:
        L.append(" None")
    obs = list_obligations(days=14)
    L += ["", "REGULATORY CALENDAR (next 14 days)"]
    L += [f" #{o['id']} {o['due']}  {o['name']}" + ("  !! OVERDUE" if o["due"] < today else "") for o in obs] or \
        [" Nothing due - add your deadlines with !oblig add <name> | YYYY-MM-DD | monthly"]
    L += ["", "Work a case: !case 12   !investigate 12   !close 12 <what you found / did>   !escalate 12 <why>"]
    return "\n".join(L)


@register
class ComplianceAgent(Agent):
    name = "compliance"
    description = "Compliance & risk officer: AML/KYC/market-risk/execution rules, case register, KRIs, calendar, promo review."

    @tool()
    def run_scan(self) -> dict[str, Any]:
        """Run all compliance and risk rules over the latest data and open cases for new findings."""
        return scan()

    @tool()
    def cases(self, status: str = "active") -> list[dict[str, Any]]:
        """Case register. status: active (default), open, investigating, escalated, closed or all."""
        return list_cases(status)

    @tool()
    def case(self, case_id: int) -> dict[str, Any]:
        """One case with its evidence and full history."""
        return get_case(case_id) or {"error": "not found"}

    @tool()
    def risk_indicators(self) -> list[dict[str, Any]]:
        """Key risk indicators with RED/AMBER/GREEN status."""
        return kris()

    @tool()
    def calendar(self, days: int = 60) -> list[dict[str, Any]]:
        """Regulatory obligations due in the next N days."""
        return list_obligations(days)

    @tool()
    def mi(self, days: int = 30) -> dict[str, Any]:
        """Management information: cases opened/closed by category and average time to close."""
        return management_info(days)

    @tool()
    def review_promo(self, text: str) -> dict[str, Any]:
        """Check marketing copy against financial-promotion rules; returns issues and a compliant rewrite."""
        return review_promotion(text)

    @tool()
    def daily_report(self, send: bool = False) -> dict[str, Any]:
        """Compliance & risk report (new cases, KRIs, open cases, calendar). send=True posts to the private channel."""
        text = report_text()
        res: dict[str, Any] = {"text": text}
        if send:
            from donna.channels import broadcast

            res["delivery"] = broadcast(text, TARGETS, title="D.O.N.N.A compliance & risk") if TARGETS \
                else {"error": "JARVIS_COMPLIANCE_TARGETS / JARVIS_PERSONAL_TARGETS not set"}
        return res
