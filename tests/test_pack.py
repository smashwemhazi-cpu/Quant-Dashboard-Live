from datetime import datetime


def test_pack_builds_a_pdf_even_with_no_data(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    from jarvis.agents import pack

    titles = [t for t, _ in pack.sections(7)]
    assert "Key risk indicators" in titles and "Controls" in titles
    pdf = pack.build_pdf(7, datetime(2026, 10, 26))
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000
    assert pack.filename(datetime(2026, 10, 26)) == "management-pack-2026-10-26.pdf"


def test_pack_only_sends_on_pack_day(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    from jarvis.agents import pack

    assert pack.PackAgent().weekly(send=False, today=datetime(2026, 10, 27))["skipped"]   # a Tuesday
    assert pack.PackAgent().weekly(send=False, today=datetime(2026, 10, 26))["bytes"] > 1000  # Monday


def test_latin1_keeps_reports_printable():
    from jarvis.agents import pack

    assert pack._latin1("a → b — ≥ \U0001F600") == "a -> b - >= ?"
