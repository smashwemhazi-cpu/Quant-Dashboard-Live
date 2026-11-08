"""Data freshness (!feeds), policy search (!policy) and the weekly one-page picture (!weekly)."""
from __future__ import annotations

from datetime import date, datetime, timezone


def test_feeds_say_what_is_old_and_how_to_automate(tmp_path, monkeypatch):
    from donna import discord_bot as b
    from jarvis.agents import backoffice, feeds

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("JARVIS_REPORT_SENDERS", "reports@backoffice.com")
    with backoffice._db() as c:
        c.execute("INSERT OR REPLACE INTO bo_meta VALUES ('last_docs', '2026-10-22')")
        c.execute("INSERT OR REPLACE INTO bo_meta VALUES ('last_eod', '2026-11-12')")
    rows = {r["kind"]: r for r in feeds.status(date(2026, 11, 13))}
    assert rows["docs"]["state"] == "old" and rows["docs"]["age_days"] == 22
    assert rows["eod"]["state"] == "fresh" and rows["kpi"]["state"] == "never"
    text = feeds.feeds_text(date(2026, 11, 13))
    assert "🔴 Customer documents (KYC): last one 2026-10-22 (22 days old)" in text
    assert "🟢 End-of-day accounts" in text and "PUT REPORTS ON AUTOPILOT" in text
    assert "reports@backoffice.com" in text and "YOUR DATA" in b.handle("!feeds")


def _policy_pdf() -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_font("Helvetica", size=11)
    pdf.add_page()
    pdf.multi_cell(0, 6, "1. Purpose. This policy sets out how staff deal in their own accounts.")
    pdf.add_page()
    pdf.multi_cell(0, 6, "4. Politically exposed persons. A PEP must be approved by senior management before "
                         "onboarding. Enhanced due diligence applies and the source of wealth is verified.")
    return bytes(pdf.output())


def test_policy_search_finds_the_page(tmp_path, monkeypatch):
    from donna import console, discord_bot as b
    from jarvis.agents import policies

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    assert "haven't read any policies" in b.handle("!policy what about PEPs?")
    assert policies.add("AML Policy.pdf", _policy_pdf()) == \
        "Read AML Policy.pdf: 2 pages. Ask me: !policy <your question>"
    assert "PDF, Word" in policies.add("x.exe", b"MZ")
    policies.add("notes.txt", b"Gifts over 100 USD must be declared to compliance within 5 days.")
    out = b.handle("!policy what does our AML policy say about PEPs and senior management approval?")
    assert out.splitlines()[2] == "1. AML Policy.pdf, page 2" and "senior management before onboarding" in out
    assert "notes.txt, page 1" in b.handle("!policy gifts declared")
    assert "AML Policy.pdf (2 pages" in b.handle("!policy list")
    assert "Nothing in your 2 policies" in b.handle("!policy zebra")
    assert "senior management" in console.reply("what does our policy say about PEPs")   # spoken question
    assert b.handle("!policy remove notes") == "Forgot notes." and "notes.txt" not in b.handle("!policy list")


def test_weekly_page_is_one_page_with_numbers(tmp_path, monkeypatch):
    import pdfplumber
    import io

    from donna import discord_bot as b
    from jarvis.agents import assistant as a, weekly

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    t1 = a.add_task("Send Kenya IB agreement", "2026-11-10", "high")
    t2 = a.add_task("Board pack figures")
    a.add_task("Check rebate statement", "2026-11-16")
    with a._db() as c:
        c.execute("UPDATE tasks SET status = 'done', done_at = '2026-11-12T10:00:00+00:00' WHERE id = ?", (t2,))
        c.execute("INSERT INTO email_seen VALUES (?,?,?,?,?,?,?,?,?)", ("<a@x>", "2026-11-12T09:00:00+00:00",
                  "client.com", "Deposit", "client", "high", "x", 0, datetime.now(timezone.utc).isoformat()))
    f = weekly.facts(date(2026, 11, 13))
    assert f["tasks_done"] == 1 and f["done_per_day"][-2] == 1 and f["mail_per_day"][-2] == 1 and f["urgent"] == 1
    assert f["overdue"] == [f"Task {t1}: Send Kenya IB agreement - urgent, overdue since Tue 10 Nov"]
    assert "Check rebate statement" in f["due_next"][0]
    assert weekly.first_moves(f)[0].startswith("Clear the oldest overdue task")
    pdf = weekly.build_pdf(date(2026, 11, 13))
    with pdfplumber.open(io.BytesIO(pdf)) as p:
        words = p.pages[0].extract_text()
        assert len(p.pages) == 2 and "Your week on one page" in words and "What slipped" in words
    assert "YOUR WEEK" in b.handle("!weekly") and "console/weekly.pdf" in b.handle("!weekly")


def test_weekly_runs_on_fridays_only(tmp_path, monkeypatch):
    from jarvis.agents import weekly

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    agent = weekly.WeeklyAgent()
    assert agent.friday(send=False, today=date(2026, 11, 12)) == {"skipped": "not the weekly day"}
    assert agent.friday(send=False, today=date(2026, 11, 13))["bytes"] > 1000
