from pathlib import Path


def _statement_pdf(path: Path) -> None:
    from fpdf import FPDF

    pdf = FPDF(orientation="L")
    pdf.add_page()
    pdf.set_font("Helvetica", size=8)
    rows = [["Symbol", "Opening direction", "Closing time", "Entry price", "Closing price", "Closing Quantity",
             "Net $", "Balance $"]]
    rows += [["XAUUSD", "Buy", f"0{d}/11/2026 10:15:00.000", "4000.10", "4010.20", "0.10 Lots", "101.00",
              f"{10000 + d * 101:.2f}"] for d in range(1, 6)]
    with pdf.table() as t:
        for r in rows:
            row = t.row()
            for c in r:
                row.cell(c)
    pdf.output(str(path))


def test_pdf_statement_is_read_like_the_excel_one(tmp_path):
    from jarvis.agents import backoffice, personal

    p = tmp_path / "cT_123456_2026-11-06.pdf"
    _statement_pdf(p)
    d = backoffice._read(p)
    assert personal.is_statement(d) and len(d) == 5


def test_discord_upload_lands_in_the_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("JARVIS_INBOX_DIR", str(tmp_path / "inbox"))
    from donna import discord_bot
    from jarvis.agents import personal

    src = tmp_path / "x.pdf"
    _statement_pdf(src)
    path = discord_bot.save_upload("cT_123456_2026-11-06.pdf", src.read_bytes())
    assert path and path.parent == tmp_path / "inbox"
    assert discord_bot.save_upload("evil.exe", b"x") is None
    reply = discord_bot.upload_reply([path.name], ["big.zip"])
    assert "trade statement" in reply and "!journal" in reply and "big.zip: skipped" in reply


def test_unreadable_pdf_says_what_to_do(tmp_path):
    from fpdf import FPDF

    from jarvis.agents import backoffice

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "Just a letter, no table.")
    p = tmp_path / "letter.pdf"
    pdf.output(str(p))
    try:
        backoffice._read(p)
    except ValueError as e:
        assert "Excel or CSV" in str(e)
    else:
        raise AssertionError("expected a ValueError")


def test_journal_command_after_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("JARVIS_INBOX_DIR", str(tmp_path / "inbox"))
    from donna import discord_bot

    src = tmp_path / "x.pdf"
    _statement_pdf(src)
    discord_bot.upload_reply([discord_bot.save_upload("cT_123456_2026-11-06.pdf", src.read_bytes()).name], [])
    out = discord_bot.handle("!journal")
    assert "PERSONAL TRADING (#123456" in out and "5 trades" in out
