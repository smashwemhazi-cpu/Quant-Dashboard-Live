import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.setenv("DONNA_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("DONNA_API_KEY", "svc")
    monkeypatch.setenv("DONNA_APPROVER_KEYS", "alice:k1")
    monkeypatch.setenv("DONNA_TV_PASSPHRASE", "tv")
    monkeypatch.setenv("DONNA_TELEGRAM_WEBHOOK_SECRET", "tg")
    monkeypatch.setenv("DONNA_TELEGRAM_APPROVER_IDS", "42")
    import donna.app as m
    m = importlib.reload(m)
    with TestClient(m.app) as c:
        c.mod = m
        yield c


SVC, APPR = {"X-API-Key": "svc"}, {"X-Approver-Key": "k1"}
TRADE = dict(trade_id="t1", account_id="A1", symbol="EURUSD", side="buy", lots=1, balance=10000)


def test_everything_requires_auth(client):
    assert client.post("/api/ingest/trade", json=TRADE).status_code == 401
    assert client.post("/api/actions/x/approve").status_code == 401
    assert client.post("/api/actions/x/approve", headers=SVC).status_code == 401  # service key can't approve
    bad = dict(passphrase="nope", ticker="X", price=1, event="e")
    assert client.post("/api/tradingview/alert", json=bad).status_code == 401


def test_unknown_account_defaults_to_a_book_and_real_risk_pct(client):
    r = client.post("/api/ingest/trade", headers=SVC,
                    json={**TRADE, "stop_loss_pips": 20, "pip_value_per_lot": 10}).json()
    assert r["recommendation"] == "A-BOOK" and r["risk_pct"] == 2.0
    assert client.post("/api/ingest/trade", headers=SVC, json=TRADE).json()["status"] == "DUPLICATE"


def test_b_book_needs_history_and_review(client):
    with client.mod.db() as c:
        c.execute("INSERT INTO account_profiles VALUES ('L', 200, 10, 'consistent loser', 'x')")
    r = client.post("/api/ingest/trade", headers=SVC, json={**TRADE, "trade_id": "t2", "account_id": "L"}).json()
    assert r["recommendation"] == "B-BOOK-CANDIDATE" and r["requires_review"]


def _stage(client):
    r = client.post("/api/tradingview/alert", json=dict(passphrase="tv", ticker="XAGUSD", price=30, event="cross"))
    return r.json()["action_id"]


def test_approval_is_atomic_and_honest(client):
    aid = _stage(client)
    assert len(aid) == 32
    r = client.post(f"/api/actions/{aid}/approve", headers=APPR).json()
    assert r["status"] == "FAILED" and "no executor wired" in r["result"]  # never a fake EXECUTED
    assert client.post(f"/api/actions/{aid}/approve", headers=APPR).status_code == 409
    assert client.post(f"/api/actions/{aid}/reject", headers=APPR).status_code == 409  # can't un-execute
    with client.mod.db() as c:
        row = c.execute("SELECT decided_by, payload FROM actions WHERE action_id=?", (aid,)).fetchone()
    assert row["decided_by"] == "alice" and "tv" not in row["payload"]


def test_expired_actions_cannot_be_approved(client):
    aid = _stage(client)
    with client.mod.db() as c:
        c.execute("UPDATE actions SET expires_at = '2000-01-01' WHERE action_id = ?", (aid,))
    r = client.post(f"/api/actions/{aid}/approve", headers=APPR)
    assert r.status_code == 409 and "EXPIRED" in r.text


def test_telegram_webhook_secret_and_allowlist(client):
    aid = _stage(client)
    body = lambda uid: {"message": {"from": {"id": uid}, "text": f"/reject {aid}"}}  # noqa: E731
    assert client.post("/api/telegram/webhook", json=body(42)).status_code == 401
    h = {"X-Telegram-Bot-Api-Secret-Token": "tg"}
    assert client.post("/api/telegram/webhook", json=body(7), headers=h).json()["status"] == "ignored"
    assert client.post("/api/telegram/webhook", json=body(42), headers=h).json()["status"] == "ok"


def test_static_checks_catch_unsafe_cbot(client):
    m = client.mod
    bad = "```csharp\n[Robot(AccessRights = AccessRights.FullAccess)] class X { void F(){ volume * 2; } }\n```"
    issues = m.static_check(m.extract_csharp("Here you go:\n" + bad + "\nThanks"))
    assert "elevated AccessRights requested" in issues
    assert "possible martingale / volume doubling" in issues
    assert "no stop loss handling found" in issues


def test_strategy_job_runs_in_background_and_gates_deploy(client, monkeypatch):
    m = client.mod
    monkeypatch.setattr(m, "run_strategy_crew",
                        lambda p: "```csharp\n[Robot(AccessRights = AccessRights.None)] // stopLoss\n```")
    job = client.post("/api/strategy/generate", headers=SVC, json={"prompt": "silver pullback scalper"}).json()
    res = client.get(f"/api/jobs/{job['job_id']}", headers=SVC).json()
    assert res["status"] == "DONE" and res["result"]["compiled"] is False
    assert "action_id" not in res["result"]  # not compiled -> never staged for deployment
