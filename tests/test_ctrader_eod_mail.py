"""cTrader's emailed 'Accounts EOD report' (EOD_Report_USD.html.zip from trade@alerts.ctrader.com) feeds the book."""
import zipfile

ROW = "<tr><td>{}</td>" + "<td>0.0</td>" * 6 + "<td>{}</td><td>{}</td><td>0.0</td><td>{}</td><td>USD</td></tr>"
HEAD = ("<tr><th>Login</th><th>Gross Unrealised PnL (A-book)</th><th>Net Unrealised PnL (A-book)</th>"
        "<th>Gross Unrealised PnL (B-book)</th><th>Net Unrealised PnL (B-book)</th><th>Total Gross Unrealised PnL</th>"
        "<th>Total Net Unrealised PnL</th><th>Balance</th><th>Equity</th><th>Margin</th><th>Free Margin</th>"
        "<th>Account CCY</th></tr>")


def _zip(tmp_path, name="EOD_Report_USD.html.zip"):
    page = ("<html><head><style>td{color:red}</style></head><body><h1>Accounts End of Day Report</h1>"
            "<p>Report generated at: 04-11-2026 20:55:00:008 UTC</p><p>Totals are shown in: USD</p><table>" + HEAD
            + ROW.format("6000002", "0.0", "0.0", "0.0") + ROW.format("6000003", "421.59", "421.59", "421.59")
            + ROW.format("6000010", "1,000.00", "990.50", "990.50") + "</table></body></html>")
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("EOD_Report_USD.html", page)
    return p


def test_zipped_html_eod_is_recognised_and_stored_with_the_report_date(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    from jarvis.agents import backoffice as b

    p = _zip(tmp_path)
    assert b.recognised(p) == "eod"
    r = b.ingest_file(p)
    assert r["kind"] == "eod" and r["asof"] == "2026-11-04" and r["rows"] == 3
    s = b.book_summary()
    assert s["asof"] == "2026-11-04" and s["funded"] == 2 and round(s["equity"], 2) == 1412.09
    assert s["top_account"]["account"] == "6000010"


def test_cTrader_alerts_sender_is_always_allowed_once_senders_are_set(monkeypatch):
    from jarvis.agents import backoffice as b

    monkeypatch.setenv("JARVIS_REPORT_SENDERS", "crm@vertex1brokers.com")
    assert "alerts.ctrader.com" in b._report_senders()
    monkeypatch.setenv("JARVIS_REPORT_SENDERS", "")
    assert b._report_senders() == set()          # nothing set = any sender, as before


def test_a_zip_without_a_report_is_not_a_report(tmp_path):
    from jarvis.agents import backoffice as b

    p = tmp_path / "photos.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("a.jpg", b"x")
    assert b.recognised(p) is None
