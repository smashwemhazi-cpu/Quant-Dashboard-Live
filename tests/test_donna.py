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
    assert r["status"] == "FAILED" and "not configured" in r["result"]  # never a fake EXECUTED
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


# ---------------------------------------------------------------- channels, leads, TradingView
def test_reply_executor_routes_slack_thread(client, monkeypatch):
    sent = {}
    monkeypatch.setattr(client.mod.channels, "send_slack",
                        lambda ch, text, ts=None: sent.update(ch=ch, ts=ts) or "slack ok")
    r = client.post("/api/comms/inbound", headers=SVC, json={
        "source": "slack", "sender": "U1", "recipient": "C42", "content": "hi", "thread_id": "123.45"}).json()
    out = client.post(f"/api/actions/{r['action_id']}/approve", headers=APPR).json()
    assert out["status"] == "EXECUTED" and sent == {"ch": "C42", "ts": "123.45"}


def test_lead_flow_consent_once_and_stop_is_permanent(client):
    lead = {"handle": "Big_Trader", "source_chat": "FX Pros", "snippet": "need FIX API",
            "matched_terms": ["fix api"], "intent_score": 5}
    r = client.post("/api/leads", headers=SVC, json=lead).json()
    assert r["staged"]
    text = client.get("/api/actions/pending", headers=APPR).json()[0]["preview_text"]
    assert "STOP" in text and "raw spread" not in text.lower()     # consent request, not a pitch
    assert client.post("/api/leads", headers=SVC, json=lead).json()["staged"] is False  # once only
    aid = r["action_id"]
    assert client.post(f"/api/actions/{aid}/approve", headers=APPR).json()["result"] == "queued for worker"
    assert client.get("/api/outreach/approved", headers=SVC).json()[0]["action_id"] == aid
    client.post(f"/api/outreach/{aid}/result", headers=SVC, json={"ok": True})
    assert client.post("/api/leads/reply", headers=SVC,
                       json={"handle": "big_trader", "text": "Please STOP messaging me"}).json()["status"] == "OPTED_OUT"
    assert client.post("/api/leads", headers=SVC, json=lead).json()["status"] == "DO_NOT_CONTACT"
    assert client.post("/api/leads/reply", headers=SVC,
                       json={"handle": "stranger", "text": "yes"}).json()["status"] == "IGNORED"


def test_tradingview_snapshot_accepts_text_plain(client):
    import json as _j
    body = _j.dumps({"passphrase": "tv", "kind": "daily_snapshot", "bars": [
        {"symbol": "OANDA:XAUUSD", "t": 1761004800000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "e200": None}]})
    r = client.post("/api/tradingview/snapshot", content=body, headers={"Content-Type": "text/plain"})
    assert r.json() == {"status": "STORED", "bars": 1}
    bad = body.replace('"tv"', '"nope"')
    assert client.post("/api/tradingview/snapshot", content=bad, headers={"Content-Type": "text/plain"}).status_code == 401


def test_lead_scorer_ignores_substrings_and_promoters():
    from donna.lead_monitor import score_message
    assert score_message("is it possible the bible is visible").value == 0
    assert score_message("Join my VIP signals t.me/abc for FIX API").value == 0
    assert score_message("Looking for a broker with FIX API for 50 lots/month?").value >= 5
