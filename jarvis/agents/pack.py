"""Weekly management pack: KRIs, compliance cases, regulatory calendar, growth and the audit check in one PDF
the owner can forward to the directors. Deterministic (no Claude call), so it works without API credit.

Discord:   !pack [days]        the pack for the last N days (default 7) as a PDF attachment
Scheduler: weekly on DONNA_PACK_WEEKDAY (0 = Monday) at JARVIS_PACK_UTC_HOUR, to the personal Discord channel.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable

from jarvis.core import audit
from jarvis.core.registry import Agent, register

PACK_WEEKDAY = int(os.getenv("DONNA_PACK_WEEKDAY", "0"))
FIRM = os.getenv("DONNA_FIRM_NAME", "Vertex 1 Brokers")


def _kri_lines() -> list[str]:
    from jarvis.agents import compliance as co

    return [f"[{k['status'].upper():<6}] {k['kri']}: {k['value']}" for k in co.kris()]


def _mi_lines(days: int) -> list[str]:
    from jarvis.agents import compliance as co

    m = co.management_info(days)
    L = [f"Cases opened in the last {days} days: {m['cases']}"]
    L += [f"  {cat}: opened {v['opened']}, closed {v['closed']}, still open {v['open']}"
          for cat, v in m["by_category"].items()]
    if m["avg_hours_to_close"] is not None:
        L.append(f"Average time to close: {m['avg_hours_to_close']:.1f} hours")
    return L


def _open_case_lines() -> list[str]:
    from jarvis.agents import compliance as co

    rows = co.list_cases()  # subjects left out: they can name clients
    return [f"#{c['id']:<4} [{c['severity']}] {c['category']} - {c['status']}, due {c['due']}" for c in rows] or ["None."]


def _oblig_lines() -> list[str]:
    from jarvis.agents import compliance as co

    rows = co.list_obligations(days=30)
    return [f"{o['due']}  {o['name']}" for o in rows] or ["Nothing due in the next 30 days."]


def _growth_lines() -> list[str]:
    from jarvis.agents import copilot

    f = copilot.facts()
    return f.splitlines() if f else ["No leads export received yet."]


def _audit_lines() -> list[str]:
    v = audit.verify()
    return [f"Audit trail: {'INTACT' if v['ok'] else 'BROKEN at entry #' + str(v.get('first_bad_id'))}, "
            f"{v['checked']} chained entries checked."]


def sections(days: int = 7) -> list[tuple[str, list[str]]]:
    parts: list[tuple[str, Callable[[], list[str]]]] = [
        ("Key risk indicators", _kri_lines),
        ("Compliance cases", lambda: _mi_lines(days)),
        ("Regulatory calendar (next 30 days)", _oblig_lines),
        ("Open cases", _open_case_lines),
        ("Business growth", _growth_lines),
        ("Controls", _audit_lines),
    ]
    out = []
    for title, fn in parts:
        try:
            out.append((title, fn()))
        except Exception as e:  # one dead source must not stop the pack
            audit.log("pack", "section_error", {"section": title, "error": str(e)})
            out.append((title, [f"Unavailable ({type(e).__name__}) - no data received yet."]))
    return out


def _latin1(s: str) -> str:
    """The built-in PDF fonts are Latin-1 only; swap the few characters reports use, drop the rest."""
    for a, b in {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
                 "•": "-", "…": "...", "→": "->", "≥": ">=", "≤": "<="}.items():
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


def build_pdf(days: int = 7, now: datetime | None = None) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    from jarvis.agents.mind import LOCAL_TZ

    now = now or datetime.now(LOCAL_TZ)
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_title(f"{FIRM} management pack {now:%Y-%m-%d}")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _latin1(f"{FIRM} - management pack"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Period: last {days} days to {now:%a %d %b %Y}. Prepared by D.O.N.N.A from internal records.",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, "Internal and confidential. Case subjects and client details are left out.",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    for title, lines in sections(days):
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, _latin1(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Courier", "", 8.5)
        for line in lines or ["-"]:
            pdf.multi_cell(0, 4.2, _latin1(line) or " ", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())


def filename(now: datetime | None = None) -> str:
    from jarvis.agents.mind import LOCAL_TZ

    return f"management-pack-{(now or datetime.now(LOCAL_TZ)):%Y-%m-%d}.pdf"


@register
class PackAgent(Agent):
    name = "pack"
    description = "Weekly management pack PDF: KRIs, cases, regulatory calendar, growth, audit check."

    def weekly(self, send: bool = True, today: datetime | None = None) -> dict[str, Any]:
        """Scheduler hook (not a Brain tool): only runs on DONNA_PACK_WEEKDAY."""
        from jarvis.agents.mind import LOCAL_TZ

        today = today or datetime.now(LOCAL_TZ)
        if today.weekday() != PACK_WEEKDAY:
            return {"skipped": "not pack day"}
        pdf = build_pdf(7, today)
        res: dict[str, Any] = {"bytes": len(pdf)}
        if send:
            from donna.channels import send_discord_file
            from jarvis.agents.personal import PERSONAL_TARGETS

            res["delivery"] = {t: send_discord_file(pdf, filename(today), "Your weekly management pack.",
                                                    webhook=t.partition(":")[2])
                               for t in PERSONAL_TARGETS if t.startswith("discord")}
        audit.log("pack", "weekly", res)
        return res
