"""STR evidence pack: one compliance case -> everything the officer needs to decide on, and file, a
suspicious transaction report (goAML / FIC in South Africa, or the local FIU elsewhere).

  build(case_id)        the pack as sections: case, why it looks suspicious, account activity, balances,
                        KYC status, other cases and complaints on the same subject, the deadline and
                        the goAML checklist. Deterministic (no Claude call), so it works without API credit.
  build_pdf(case_id)    the same as a PDF for the file
  decide(case_id, ...)  record the decision: filed (with the FIU reference) or not filed (with the reason).
                        Both are kept - "why we did not report" is evidence too.

Reporting deadline: STR_DEADLINE_DAYS business days after the suspicion was formed (default 15 - the FIC
Act regulation 24(3) period; set it to your FIU's rule). Clients appear by account / customer number only:
identity details come from the CRM when the report is filed, never from DONNA.
Never tell the client a report is being considered or was made (tipping-off).
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from jarvis.core import audit

FIRM = os.getenv("DONNA_FIRM_NAME", "Vertex 1 Brokers")
FIU = os.getenv("JARVIS_FIU_NAME", "Financial Intelligence Centre (goAML)")
DEADLINE_DAYS = int(os.getenv("JARVIS_STR_DEADLINE_DAYS", "15"))
GROUNDS = {
    "AML_IN_OUT_NO_TRADE": "Funds were deposited and withdrawn with little or no trading. A trading account used "
                           "as a pass-through for money is a recognised layering indicator.",
    "AML_TURNOVER": "Trading turnover is far out of proportion to the account's equity, which can indicate "
                    "value transfer through trading or activity inconsistent with the client's profile.",
    "AML_LARGE_DEPOSIT": "A large deposit relative to the client's usual activity or declared profile, with the "
                         "source of funds not yet evidenced.",
}
CHECKLIST = [
    "Reporting entity and compliance officer details (pre-filled in goAML)",
    "Subject identity from the CRM: full name, ID/passport, date of birth, address, nationality, occupation",
    "Account number(s), opening date, products and platform",
    "Every transaction relied on: date, amount, currency, method (card / EFT / wallet), counterparty or source",
    "Grounds for suspicion in plain words - what was unusual and why (use the section above as the start)",
    "What was done: enquiries made, documents requested, account restrictions applied",
    "Supporting documents: KYC, source-of-funds evidence, statements",
]


def _business_days_after(start: date, n: int) -> date:
    d = start
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _account(subject: str) -> str:
    return subject.strip().lstrip("#")


def _activity(account: str) -> list[dict[str, Any]]:
    from jarvis.agents import backoffice as bo

    with bo._db() as c:  # noqa: SLF001 - same package, read-only
        cur = c.execute("SELECT asof, equity, deposit, withdrawal, deals, volume, net_pnl FROM bo_activity "
                        "WHERE account = ? ORDER BY asof", (account,))
        cols = [x[0] for x in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _balances(account: str) -> list[dict[str, Any]]:
    from jarvis.agents import backoffice as bo

    with bo._db() as c:  # noqa: SLF001
        cur = c.execute("SELECT asof, balance, credit, equity FROM bo_eod WHERE account = ? ORDER BY asof DESC LIMIT 10",
                        (account,))
        cols = [x[0] for x in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _kyc(account: str) -> list[dict[str, Any]]:
    from jarvis.agents import backoffice as bo

    with bo._db() as c:  # noqa: SLF001
        cur = c.execute("SELECT doc_type, status, uploaded, expires FROM bo_docs WHERE customer = ?", (account,))
        cols = [x[0] for x in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def build(case_id: int, today: date | None = None) -> dict[str, Any]:
    from jarvis.agents import complaints, compliance as co

    case = co.get_case(case_id)
    if not case:
        return {"error": f"no case #{case_id}"}
    today = today or date.today()
    acc = _account(case["subject"])
    formed = date.fromisoformat(case["opened"][:10])
    deadline = _business_days_after(formed, DEADLINE_DAYS)
    activity, balances, kyc = _activity(acc), _balances(acc), _kyc(acc)
    related = [c for c in co.list_cases("all", limit=1000) if c["subject"] == case["subject"] and c["id"] != case_id]
    with complaints._db() as c:  # noqa: SLF001
        comp = [dict(r) for r in c.execute("SELECT id, received, category, status FROM complaints WHERE client = ?",
                                           (acc.upper(),))]
    dep = sum(a["deposit"] or 0 for a in activity)
    wd = sum(a["withdrawal"] or 0 for a in activity)
    vol = sum(a["volume"] or 0 for a in activity)
    deals = sum(a["deals"] or 0 for a in activity)
    grounds = [GROUNDS.get(case["rule"], "See the finding below."), f"DONNA's finding: {case['finding']}"]
    try:
        evidence = json.loads(case["evidence"] or "{}")
    except ValueError:
        evidence = {}
    if evidence:
        grounds.append("Figures DONNA recorded when the case opened: "
                       + ", ".join(f"{k} {v:,.2f}" if isinstance(v, (int, float)) else f"{k} {v}" for k, v in evidence.items()))
    notes = [h for h in case["log"] if h["note"] and h["action"] != "opened"]
    grounds += [f"Officer note ({h['ts'][:10]}): {h['note']}" for h in notes]
    sections: list[tuple[str, list[str]]] = [
        ("Case", [f"Case #{case['id']} - {case['category']} / {case['rule']} - severity {case['severity']}, status "
                  f"{case['status']}", f"Subject: account / customer {acc} (identity details: take from the CRM)",
                  f"Opened {case['opened'][:16]} UTC, owner {case['owner']}"]),
        ("Why this may be suspicious", grounds),
        ("Account activity on file", [
            f"Exports covered: {activity[0]['asof']} to {activity[-1]['asof']} ({len(activity)} reports)" if activity
            else "No account-activity export on file for this account.",
            f"Total deposits {dep:,.2f} | withdrawals {wd:,.2f} | net {dep - wd:,.2f}",
            f"Deals {deals:,} | traded volume {vol:,.0f}"
            + (f" | volume / deposits {vol / dep:,.1f}x" if dep else "")]
         + [f" {a['asof']}: equity {a['equity'] or 0:,.2f}, in {a['deposit'] or 0:,.2f}, out {a['withdrawal'] or 0:,.2f}, "
            f"deals {a['deals'] or 0}, volume {a['volume'] or 0:,.0f}, P&L {a['net_pnl'] or 0:,.2f}" for a in activity[-12:]]),
        ("Balances (latest end-of-day reports)",
         [f" {b['asof']}: balance {b['balance'] or 0:,.2f}, credit {b['credit'] or 0:,.2f}, equity {b['equity'] or 0:,.2f}"
          for b in balances] or ["No end-of-day report on file for this account."]),
        ("KYC on file", [f" {k['doc_type']}: {k['status']} (uploaded {k['uploaded']}"
                         + (f", expires {k['expires']}" if k["expires"] and k["expires"] != "1900-01-01" else "") + ")"
                         for k in kyc] or ["No KYC document export lists this customer number - check the CRM."]),
        ("Other cases and complaints on this subject",
         [f" Case #{c['id']} {c['rule']} ({c['status']}, opened {c['opened'][:10]})" for c in related]
         + [f" Complaint #{c['id']} {c['category']} ({c['status']}, received {c['received']})" for c in comp]
         or ["None."]),
        ("Deadline", [f"Suspicion formed {formed}: report to the {FIU} by {deadline} "
                      f"({DEADLINE_DAYS} business days; check your FIU's rule and public holidays).",
                      "Overdue!" if today > deadline else f"{(deadline - today).days} calendar day(s) left."]),
        ("goAML checklist", [f" [ ] {x}" for x in CHECKLIST]),
        ("Tipping-off", ["Do not tell the client, or anyone outside compliance and the MLRO, that a report is being "
                         "considered or has been made. Keep the account relationship normal unless told otherwise."]),
    ]
    audit.log("compliance", "str_pack_built", {"case": case_id})
    return {"case_id": case_id, "account": acc, "deadline": deadline.isoformat(), "overdue": today > deadline,
            "sections": sections, "decision": decision(case_id)}


def text(case_id: int) -> str:
    p = build(case_id)
    if "error" in p:
        return p["error"]
    L = [f"STR EVIDENCE PACK - case #{case_id} - report by {p['deadline']}" + ("  !! OVERDUE" if p["overdue"] else "")]
    for title, lines in p["sections"][:7]:
        L += ["", title.upper()] + [line if line.startswith(" ") else " " + line for line in lines[:14]]
    d = p["decision"]
    L += ["", f"Decision: {d['decision']} on {d['decided_at'][:10]}" + (f" - {d['detail']}" if d["detail"] else "")
          if d else "Decision: none yet - !str " + str(case_id) + " filed <FIC reference>  or  !str "
          + str(case_id) + " nofile <reason>"]
    L += ["", "Do not tell the client a report is being considered or was made (tipping-off)."]
    return "\n".join(L)


def build_pdf(case_id: int) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    from jarvis.agents.pack import _latin1

    p = build(case_id)
    if "error" in p:
        raise ValueError(p["error"])
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_title(f"STR evidence pack case {case_id}")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 9, _latin1(f"{FIRM} - STR evidence pack, case #{case_id}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.cell(0, 6, _latin1(f"Prepared {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by D.O.N.N.A for the compliance "
                           f"officer. STRICTLY CONFIDENTIAL - tipping-off rules apply."), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    for title, lines in p["sections"]:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 11.5)
        pdf.cell(0, 7, _latin1(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Courier", "", 8.5)
        for line in lines or ["-"]:
            pdf.multi_cell(0, 4.2, _latin1(line) or " ", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11.5)
    pdf.cell(0, 7, "Decision (officer)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9.5)
    d = p["decision"]
    pdf.multi_cell(0, 5, _latin1(f"{d['decision']} on {d['decided_at'][:10]}: {d['detail']}" if d else
                                 "[ ] Filed - FIU reference: ____________   [ ] Not filed - reason: ____________________"
                                 "\nSigned: ______________________   Date: ____________"),
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())


def filename(case_id: int) -> str:
    return f"str-pack-case-{case_id}-{date.today():%Y-%m-%d}.pdf"


def _db():  # the decision register lives with the cases
    from jarvis.agents import compliance as co

    c = co._db()  # noqa: SLF001
    c.execute("CREATE TABLE IF NOT EXISTS str_decisions (case_id INTEGER PRIMARY KEY, decision TEXT, detail TEXT, "
              "decided_at TEXT, who TEXT)")
    return c


def decision(case_id: int) -> dict[str, Any] | None:
    with _db() as c:
        r = c.execute("SELECT * FROM str_decisions WHERE case_id = ?", (case_id,)).fetchone()
    return dict(r) if r else None


def decide(case_id: int, filed: bool, detail: str, who: str = "officer") -> str:
    from jarvis.agents import compliance as co

    if not co.get_case(case_id):
        return f"No case #{case_id}."
    detail = detail.strip()
    if not detail:
        return ("Give the FIU reference: !str 12 filed <reference>" if filed else
                "Give the reason it isn't reportable - it is kept as evidence: !str 12 nofile <reason>")
    label = "STR filed" if filed else "Not reported"
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO str_decisions VALUES (?,?,?,?,?)",
                  (case_id, label, detail[:500], datetime.now(timezone.utc).isoformat(timespec="seconds"), who))
    co.update_case(case_id, "escalated" if filed else "investigating", f"{label}: {detail}", who=who)
    return f"Case #{case_id}: {label.lower()} recorded ({detail[:80]})." + (
        "" if filed else f" Close the case when done: !close {case_id} <note>")


def command(cmd: str, rest: str) -> str | None:
    if cmd != "!str":
        return None
    m = re.match(r"\s*#?(\d+)\s*(\w+)?\s*(.*)", rest, re.S)
    if not m:
        return ("!str 12              STR evidence pack for case 12 (PDF in Discord)\n"
                "!str 12 filed <FIU reference>   !str 12 nofile <why it is not reportable>")
    cid, action, arg = int(m.group(1)), (m.group(2) or "").lower(), m.group(3)
    if action in ("filed", "file"):
        return decide(cid, True, arg)
    if action in ("nofile", "notfiled", "no"):
        return decide(cid, False, arg)
    return text(cid)
