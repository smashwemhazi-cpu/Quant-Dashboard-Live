import sqlite3

import pytest


@pytest.fixture()
def audit(tmp_path, monkeypatch):
    from jarvis.core import audit as a

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "audit.db"))
    return a


def test_chain_verifies_and_detects_tampering(audit):
    for i in range(5):
        audit.log("test", "event", {"i": i})
    assert audit.verify() == {"ok": True, "checked": 5, "unhashed_older_rows": 0}
    import os

    with sqlite3.connect(os.environ["JARVIS_STATE_DB"]) as c:
        c.execute("UPDATE audit SET payload = '{\"i\": 99}' WHERE id = 3")
    v = audit.verify()
    assert not v["ok"] and v["first_bad_id"] == 3


def test_deleting_a_row_breaks_the_chain(audit):
    for i in range(4):
        audit.log("test", "event", {"i": i})
    import os

    with sqlite3.connect(os.environ["JARVIS_STATE_DB"]) as c:
        c.execute("DELETE FROM audit WHERE id = 2")
    assert audit.verify()["first_bad_id"] == 3


def test_old_unhashed_rows_are_upgraded(audit, tmp_path):
    import os

    with sqlite3.connect(os.environ["JARVIS_STATE_DB"]) as c:
        c.execute("CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, "
                  "event TEXT NOT NULL, payload TEXT NOT NULL)")
        c.execute("INSERT INTO audit (ts, actor, event, payload) VALUES ('t', 'a', 'e', '{}')")
    audit.log("test", "after")
    assert audit.verify() == {"ok": True, "checked": 1, "unhashed_older_rows": 1}


def test_pii_masked_for_claude():
    from jarvis.core import privacy

    rows = [{"client_id": "C123", "name": "Jane van der Berg", "account_manager": "Roy", "equity": 1200.5,
             "phone": "+27 82 555 0101", "reasons": "emailed jane.berg@gmail.com twice"}]
    m = privacy.mask(rows)[0]
    assert m["name"] == "J.V.D.B." and m["phone"] == "***" and m["client_id"] == "C123"
    assert m["account_manager"] == "Roy" and m["equity"] == 1200.5
    assert "j***@gmail.com" in m["reasons"] and "jane.berg" not in m["reasons"]


def test_discord_audit_command(audit):
    from donna import discord_bot

    audit.log("test", "tool_call", {"tool": "x"})
    out = discord_bot.handle("!audit")
    assert "INTACT" in out and "test: tool_call" in out
