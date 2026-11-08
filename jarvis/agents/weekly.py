"""Your week on one page: a one-page PDF with pictures, built from DONNA's own records (no Claude credit needed).

!weekly        the page for the last 7 days (PDF in Discord; in the console, the link opens it)
Scheduler      every DONNA_WEEKLY_WEEKDAY (4 = Friday) at JARVIS_WEEKLY_UTC_HOUR, to your personal channel and phone.

What's on it: four big numbers (tasks closed, replies owed, emails sorted, KPIs red), a bar chart of tasks closed
and emails per day, what slipped (overdue tasks, oldest replies), what's due next week, your account and risk,
compliance indicators and how fresh the data is.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

from jarvis.core import audit
from jarvis.core.config import settings
from jarvis.core.registry import Agent, register

WEEKDAY = int(os.getenv("DONNA_WEEKLY_WEEKDAY", "4"))
FIRM = os.getenv("DONNA_FIRM_NAME", "Vertex 1 Brokers")


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(os.getenv("JARVIS_STATE_DB", settings.state_db))
    c.row_factory = sqlite3.Row
    return c


def _rows(sql: str, *args: Any) -> list[dict[str, Any]]:
    try:
        with _db() as c:
            return [dict(r) for r in c.execute(sql, args)]
    except sqlite3.OperationalError:          # that part of DONNA hasn't run yet
        return []


def _safe(fn: Any, default: Any) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one missing source must never stop the page
        return default


def facts(today: date | None = None) -> dict[str, Any]:
    """Everything on the page, as numbers and short lines (also used by tests and the text version)."""
    from jarvis.agents import assistant

    today = today or datetime.now(assistant.LOCAL_TZ).date()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    since = days[0].isoformat()
    tasks = _rows("SELECT * FROM tasks")
    done = [t for t in tasks if t["status"] == "done" and str(t.get("done_at") or "")[:10] >= since]
    added = [t for t in tasks if str(t.get("created") or "")[:10] >= since]
    open_ = [t for t in tasks if t["status"] == "open"]
    overdue = [t for t in open_ if t["due"] and t["due"] < today.isoformat()]
    due_next = [t for t in open_ if t["due"] and today.isoformat() <= t["due"] <= (today + timedelta(days=7)).isoformat()]
    mails = _rows("SELECT received, urgency FROM email_seen WHERE received >= ?", since)
    from jarvis.agents import followup

    owed = _safe(lambda: followup.replies_owed(today), [])
    from jarvis.agents import kpi

    kc = _safe(lambda: kpi.counts(kpi.scorecard()), {})
    from jarvis.agents import compliance

    kris = _safe(compliance.kris, [])
    from jarvis.agents import feeds

    fd = _safe(lambda: feeds.status(today), [])
    snaps = _rows("SELECT ts, equity FROM position_snapshots WHERE ts >= ? ORDER BY ts", since)
    trades = _rows("SELECT net FROM pt_trades WHERE closed_utc >= ?", since)
    return {
        "today": today, "days": days,
        "done_per_day": [sum(1 for t in done if str(t["done_at"])[:10] == d.isoformat()) for d in days],
        "mail_per_day": [sum(1 for m in mails if str(m["received"])[:10] == d.isoformat()) for d in days],
        "tasks_done": len(done), "tasks_added": len(added), "tasks_open": len(open_),
        "overdue": [assistant.task_line(t, today.isoformat()) for t in overdue][:5],
        "due_next": [assistant.task_line(t, today.isoformat()) for t in due_next][:5],
        "emails": len(mails), "urgent": sum(1 for m in mails if m["urgency"] == "high"),
        "owed": len(owed),
        "owed_oldest": [f"{r['subject'][:70]} ({r['sender_domain']}) - {r['days']} days" for r in
                        sorted(owed, key=lambda r: -r["days"])[:3]],
        "kpi": kc,
        "kri": {s: sum(1 for k in kris if str(k["status"]).upper() == s) for s in ("RED", "AMBER", "GREEN", "STALE")},
        "kri_red": [f"{k['kri']}: {k['value']}"[:110] for k in kris if str(k["status"]).upper() == "RED"][:3],
        "feeds_old": [r["name"] for r in fd if r["state"] in ("old", "never") and r["kind"] != "live"],
        "feeds_total": sum(1 for r in fd if r["kind"] != "live"),
        "equity": (snaps[0]["equity"], snaps[-1]["equity"]) if snaps else None,
        "closed_pnl": round(sum(t["net"] or 0 for t in trades), 2), "closed_trades": len(trades),
    }


def first_moves(f: dict[str, Any]) -> list[str]:
    """The three things to do first next week, in plain words."""
    out = []
    if f["overdue"]:
        out.append(f"Clear the oldest overdue task: {f['overdue'][0]}")
    if f["owed_oldest"]:
        out.append(f"Answer the oldest email: {f['owed_oldest'][0]} (!waiting)")
    if f["kri_red"]:
        out.append(f"Look at the red indicator: {f['kri_red'][0]}")
    if f["feeds_old"] and len(out) < 3:
        out.append("Ask the back office to email the missing reports daily (!feeds shows which)")
    return out[:3]


def text(today: date | None = None) -> str:
    f = facts(today)
    L = [f"YOUR WEEK - {f['days'][0]:%d %b} to {f['today']:%d %b}",
         f"Tasks closed {f['tasks_done']} (added {f['tasks_added']}, {f['tasks_open']} open) | "
         f"replies owed {f['owed']} | emails sorted {f['emails']} | KPIs red {f['kpi'].get('RED', 0)}"]
    if f["overdue"]:
        L += ["Slipped:"] + [" " + x for x in f["overdue"]]
    moves = first_moves(f)
    if moves:
        L += ["Start next week with:"] + [f" {n}. {m}" for n, m in enumerate(moves, 1)]
    L.append("The full page with charts: !weekly (PDF)")
    return "\n".join(L)


# ------------------------------------------------------------------ the page
NAVY, TEAL, RED, AMBER, GREEN, GREY = (16, 24, 48), (0, 150, 160), (210, 60, 60), (230, 150, 30), (40, 160, 90), \
    (120, 130, 145)


def _latin1(s: str) -> str:
    for a, b in {"–": "-", "—": "-", "’": "'", "‘": "'", "“": '"', "”": '"',
                 "…": "...", "✓": "v", "→": "->"}.items():
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


def build_pdf(today: date | None = None) -> bytes:
    from fpdf import FPDF

    f = facts(today)
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    W = 190
    # header band
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, 210, 28, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(10, 7)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 8, _latin1("Your week on one page"))
    pdf.set_xy(10, 16)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, _latin1(f"{FIRM} - {f['days'][0]:%a %d %b} to {f['today']:%a %d %b %Y} - prepared by D.O.N.N.A"))
    # four big numbers
    red_kpi = f["kpi"].get("RED", 0)
    tiles = [("Tasks closed", str(f["tasks_done"]), f"{f['tasks_open']} still open", GREEN if f["tasks_done"] else GREY),
             ("Replies you owe", str(f["owed"]), "emails waiting on you", RED if f["owed"] > 5 else AMBER if f["owed"] else GREEN),
             ("Emails sorted", str(f["emails"]), f"{f['urgent']} urgent", TEAL),
             ("KPIs in red", str(red_kpi), f"{f['kpi'].get('GREEN', 0)} green", RED if red_kpi else GREEN)]
    for i, (label, big, small, col) in enumerate(tiles):
        x = 10 + i * 47.5
        pdf.set_fill_color(245, 247, 250)
        pdf.rect(x, 33, 45, 30, "F")
        pdf.set_fill_color(*col)
        pdf.rect(x, 33, 2, 30, "F")
        pdf.set_text_color(*GREY)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_xy(x + 5, 35)
        pdf.cell(38, 5, _latin1(label))
        pdf.set_text_color(*col)
        pdf.set_font("Helvetica", "B", 22)
        pdf.set_xy(x + 5, 41)
        pdf.cell(38, 11, big)
        pdf.set_text_color(*GREY)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_xy(x + 5, 55)
        pdf.cell(38, 5, _latin1(small))
    # bar chart: tasks closed and emails per day
    pdf.set_text_color(*NAVY)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_xy(10, 68)
    pdf.cell(0, 6, "Each day: tasks closed and emails in")
    top, base, chart_h = 76, 118, 40
    peak = max(f["done_per_day"] + f["mail_per_day"] + [1])
    for i, d in enumerate(f["days"]):
        x = 14 + i * 26.5
        for j, (v, col) in enumerate(((f["done_per_day"][i], GREEN), (f["mail_per_day"][i], TEAL))):
            h = chart_h * v / peak
            pdf.set_fill_color(*col)
            pdf.rect(x + j * 9, base - h, 8, max(h, 0.4), "F")
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*GREY)
            pdf.set_xy(x + j * 9, base - h - 4)
            pdf.cell(8, 4, str(v), align="C")
        pdf.set_xy(x, base + 1)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(17, 4, d.strftime("%a %d"), align="C")
    pdf.set_draw_color(*GREY)
    pdf.line(10, base, 200, base)
    for k, (lab, col) in enumerate((("tasks closed", GREEN), ("emails in", TEAL))):
        pdf.set_fill_color(*col)
        pdf.rect(150 + k * 25, top - 6, 3, 3, "F")
        pdf.set_xy(154 + k * 25, top - 7)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.cell(20, 5, lab)

    def section(y: float, title: str, lines: list[str], col: tuple[int, int, int], empty: str, x: float = 10,
                w: float = 92) -> float:
        pdf.set_fill_color(*col)
        pdf.rect(x, y, w, 0.8, "F")
        pdf.set_xy(x, y + 2)
        pdf.set_text_color(*NAVY)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.cell(w, 6, _latin1(title))
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(40, 40, 40)
        yy = y + 9
        for line in (lines or [empty])[:6]:
            pdf.set_xy(x, yy)
            pdf.multi_cell(w, 4.2, _latin1("- " + line), align="L")
            yy = pdf.get_y() + 0.8
        return yy

    y = 128
    left = section(y, "What slipped", f["overdue"] + [f"Reply owed: {x}" for x in f["owed_oldest"]], RED,
                   "Nothing slipped this week.")
    right = section(y, "Due next week", f["due_next"], TEAL, "Nothing with a date next week.", x=108)
    y = max(left, right) + 4
    eq = f["equity"]
    acct = ([f"Equity {eq[0]:,.2f} -> {eq[1]:,.2f} ({eq[1] - eq[0]:+,.2f})"] if eq else ["No account data this week."]) + \
        ([f"Closed trades: {f['closed_trades']}, net {f['closed_pnl']:+,.2f}"] if f["closed_trades"] else [])
    left = section(y, "Your account", acct, GREEN, "-")
    kr = f["kri"]
    comp = [f"Indicators: {kr['RED']} red, {kr['AMBER']} amber, {kr['GREEN']} green, {kr['STALE']} on old data"] + \
        f["kri_red"]
    right = section(y, "Compliance indicators", comp, AMBER, "-", x=108)
    y = max(left, right) + 4
    old = f["feeds_old"]
    data = ([f"{len(old)} of {f['feeds_total']} reports missing or old: " + ", ".join(old)] if old else
            ["Every report is fresh."]) + ["Put reports on autopilot: !feeds shows how."]
    left = section(y, "How fresh the data is", data, GREY, "-")
    kc = f["kpi"]
    kp = [f"{kc.get('GREEN', 0)} green, {kc.get('AMBER', 0)} amber, {kc.get('RED', 0)} red, "
          f"{kc.get('NO DATA', 0)} waiting for data", "Details: !kpi"]
    right = section(y, "KPI scorecard", kp, TEAL, "-", x=108)
    y = max(left, right) + 4
    section(y, "Start next week with", first_moves(f), NAVY, "A clean slate - nothing pressing.", w=190)
    pdf.set_xy(10, 285)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*GREY)
    pdf.cell(0, 4, _latin1("Internal and confidential. Built from DONNA's own records; client details are left out."))
    _analysis_page(pdf, today)
    return bytes(pdf.output())


def _analysis_page(pdf: Any, today: date | None) -> None:
    """Page 2: the weekly analysis - trades, work flow, agents and jobs, KPIs, the PA standard (jarvis/agents/desk)."""
    from jarvis.agents import desk

    x = _safe(lambda: desk.analysis(today), None)
    if not x:
        return
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, 210, 20, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(10, 6)
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 8, _latin1("Weekly analysis: trades, work flow, agents, KPIs"))
    y = 26
    colours = {"YOUR TRADES": GREEN, "YOUR WORK FLOW": TEAL, "YOUR AGENTS": AMBER, "YOUR KPIs": NAVY,
               "YOUR PA STANDARD": RED}
    for line in desk.analysis_lines(x):
        if y > 280:
            break
        if not line.strip():
            y += 2
            continue
        head = next((c for k, c in colours.items() if line.startswith(k)), None)
        pdf.set_xy(10, y)
        if head:
            pdf.set_fill_color(*head)
            pdf.rect(10, y, 190, 0.8, "F")
            pdf.set_xy(10, y + 1.5)
            pdf.set_text_color(*NAVY)
            pdf.set_font("Helvetica", "B", 10.5)
            pdf.cell(190, 6, _latin1(line))
            y += 8.5
            continue
        pdf.set_text_color(40, 40, 40)
        pdf.set_font("Helvetica", "", 9)
        text = line.strip()
        for a, b in {"🟢": "[G]", "🟠": "[A]", "🔴": "[R]", "⚪": "[-]"}.items():
            text = text.replace(a, b)
        pdf.multi_cell(190, 4.6, _latin1(text), align="L")
        y = pdf.get_y() + 1


def filename(today: date | None = None) -> str:
    return f"your-week-{(today or datetime.now(timezone.utc).date()):%Y-%m-%d}.pdf"


def command(cmd: str, rest: str) -> str | None:
    if cmd != "!weekly":
        return None
    return text() + "\nThe analysis of trades, work flow, agents and KPIs: !analysis (page 2 of the PDF)" + \
        "\nIn the console the PDF opens here: http://localhost:8765/console/weekly.pdf"


@register
class WeeklyAgent(Agent):
    name = "weekly"
    description = "Your week on one page: a one-page PDF with charts, sent on Fridays."

    def friday(self, send: bool = True, today: date | None = None) -> dict[str, Any]:
        """Scheduler hook: only runs on DONNA_WEEKLY_WEEKDAY."""
        from jarvis.agents import assistant

        today = today or datetime.now(assistant.LOCAL_TZ).date()
        if today.weekday() != WEEKDAY:
            return {"skipped": "not the weekly day"}
        pdf = build_pdf(today)
        res: dict[str, Any] = {"bytes": len(pdf)}
        try:   # this week's numbers, so next week can say what changed
            from jarvis.agents import desk

            desk.save_week(desk.analysis(today))
        except Exception as e:  # noqa: BLE001
            res["snapshot_error"] = f"{type(e).__name__}: {e}"
        if send:
            from donna.channels import send_discord_file, send_telegram_file
            from jarvis.agents.personal import PERSONAL_TARGETS

            out = {}
            for t in PERSONAL_TARGETS:
                kind, _, arg = t.partition(":")
                try:
                    if kind == "discord":
                        out[t] = send_discord_file(pdf, filename(today), "Your week on one page.", webhook=arg)
                    elif kind == "telegram":
                        out[t] = send_telegram_file(arg, pdf, filename(today), "Your week on one page.")
                except Exception as e:  # noqa: BLE001 - one dead channel must not stop the other
                    out[t] = f"ERROR {type(e).__name__}"
            res["delivery"] = out
        audit.log("weekly", "friday", res)
        return res
