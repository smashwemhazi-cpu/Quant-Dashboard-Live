import asyncio
import json

import pytest

from jarvis.adapters import ctrader_feed as f


class FakeCTrader:
    """Scripted cTrader: answers each request by payload type, like the JSON WebSocket endpoint."""

    def __init__(self, scope="SCOPE_VIEW", token_ok=True, login=5551234):
        self.sent, self.queue, self.scope, self.token_ok, self.login = [], [], scope, token_ok, login

    async def send(self, text):
        m = json.loads(text)
        self.sent.append(m["payloadType"])
        pt, p, mid = m["payloadType"], m["payload"], m.get("clientMsgId")
        reply = lambda t, payload: self.queue.append({"clientMsgId": mid, "payloadType": t, "payload": payload})  # noqa: E731
        if pt == f.APP_AUTH_REQ:
            self.queue.append({"payloadType": f.HEARTBEAT})        # noise the session must skip
            reply(f.APP_AUTH_RES, {})
        elif pt == f.REFRESH_TOKEN_REQ:
            reply(f.REFRESH_TOKEN_RES, {"accessToken": "new-a", "refreshToken": "new-r", "tokenType": "bearer",
                                        "expiresIn": 2628000})
            self.token_ok = True
        elif pt == f.ACCOUNTS_BY_TOKEN_REQ:
            if not self.token_ok:
                reply(f.ERROR_RES, {"errorCode": "CH_ACCESS_TOKEN_INVALID"})
                return
            reply(f.ACCOUNTS_BY_TOKEN_RES, {"accessToken": p["accessToken"], "permissionScope": self.scope,
                                            "ctidTraderAccount": [{"ctidTraderAccountId": 99, "isLive": True,
                                                                   "traderLogin": self.login}]})
        elif pt == f.ACCOUNT_AUTH_REQ:
            reply(f.ACCOUNT_AUTH_RES, {"ctidTraderAccountId": 99})
        elif pt == f.TRADER_REQ:
            reply(f.TRADER_RES, {"ctidTraderAccountId": 99, "trader": {"balance": 1_000_000, "moneyDigits": 2,
                                                                         "depositAssetId": 1}})
        elif pt == f.RECONCILE_REQ:
            reply(f.RECONCILE_RES, {"ctidTraderAccountId": 99, "position": [
                {"positionId": 1, "positionStatus": 1, "swap": 0, "price": 150.0, "stopLoss": 150.5,
                 "tradeData": {"symbolId": 7, "volume": 1_000_000, "tradeSide": 2}},      # sell 10k USDJPY
                {"positionId": 2, "positionStatus": 1, "swap": 0, "price": 2400.0,
                 "tradeData": {"symbolId": 9, "volume": 1_000, "tradeSide": 1}}]})       # buy 10 XAUUSD, no stop
        elif pt == f.UNREALIZED_PNL_REQ:
            # USDJPY at ask 150.2 -> -0.2 yen x 10k = -2000 yen = -13.32 USD; gold +5 x 10 = +50
            reply(f.UNREALIZED_PNL_RES, {"ctidTraderAccountId": 99, "moneyDigits": 2, "positionUnrealizedPnL": [
                {"positionId": 1, "grossUnrealizedPnL": -1332, "netUnrealizedPnL": -1400},
                {"positionId": 2, "grossUnrealizedPnL": 5000, "netUnrealizedPnL": 4900}]})
        elif pt == f.SYMBOLS_LIST_REQ:
            reply(f.SYMBOLS_LIST_RES, {"ctidTraderAccountId": 99, "symbol": [
                {"symbolId": 7, "symbolName": "USDJPY"}, {"symbolId": 9, "symbolName": "XAUUSD"}]})
        elif pt == f.SUBSCRIBE_SPOTS_REQ:
            reply(f.SUBSCRIBE_SPOTS_RES, {"ctidTraderAccountId": 99})
            self.queue.append({"payloadType": f.SPOT_EVENT, "payload": {"ctidTraderAccountId": 99, "symbolId": 7,
                                                                        "bid": 15019000, "ask": 15020000}})
            self.queue.append({"payloadType": f.SPOT_EVENT, "payload": {"ctidTraderAccountId": 99, "symbolId": 9,
                                                                        "bid": 240500000, "ask": 240530000}})

    async def recv(self):
        return json.dumps(self.queue.pop(0))


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))


def run(fake, **kw):
    return asyncio.run(f.snapshot(fake, "5551234", "a", "r", "cid", "sec", **kw))


def test_snapshot_reads_positions_prices_and_converts_currency():
    fake = FakeCTrader()
    snap = run(fake)
    jpy, gold = snap["positions"]
    assert jpy == {"symbol": "USDJPY", "side": "sell", "units": 10_000, "entry": 150.0, "stop": 150.5,
                   "price": 150.2, "quote_to_account": pytest.approx(1332 / 2000 / 100, rel=1e-6)}
    assert gold["side"] == "buy" and gold["price"] == 2405.0 and gold["stop"] is None
    assert gold["quote_to_account"] == pytest.approx(1.0)
    assert snap["balance"] == 10_000 and snap["equity"] == pytest.approx(10_000 - 14 + 49)
    # only read requests went out - nothing that can place, amend or close
    assert set(fake.sent) <= {f.APP_AUTH_REQ, f.ACCOUNTS_BY_TOKEN_REQ, f.ACCOUNT_AUTH_REQ, f.TRADER_REQ,
                              f.RECONCILE_REQ, f.UNREALIZED_PNL_REQ, f.SYMBOLS_LIST_REQ,
                              f.SUBSCRIBE_SPOTS_REQ, f.UNSUBSCRIBE_SPOTS_REQ}


def test_refuses_a_token_with_trading_permission():
    with pytest.raises(f.FeedError, match="TRADING permission"):
        run(FakeCTrader(scope="SCOPE_TRADE"))
    with pytest.raises(f.FeedError, match="TRADING permission"):
        run(FakeCTrader(scope=1))


def test_wrong_account_is_explained():
    with pytest.raises(f.FeedError, match="5551234 is not granted.*granted: 777"):
        run(FakeCTrader(login=777))


def test_invalid_token_raises_then_refresh_is_stored():
    with pytest.raises(f.TokenInvalid):
        run(FakeCTrader(token_ok=False))
    fake = FakeCTrader(token_ok=False)
    run(fake, refresh_due=True)
    assert fake.sent[1] == f.REFRESH_TOKEN_REQ
    access, refresh, issued = f.tokens()
    assert (access, refresh) == ("new-a", "new-r") and issued is not None


def test_risk_view_from_the_feed(monkeypatch):
    from jarvis.agents import rhythm

    snap = run(FakeCTrader())
    rhythm.store_positions(snap["positions"], snap["equity"], None, source="ctrader:5551234")
    r = rhythm.position_risk()
    jpy = r["positions"][0]
    assert jpy["risk_at_stop"] == pytest.approx(0.3 * 10_000 * 1332 / 2000 / 100, rel=1e-6)  # ~$19.98
    assert r["no_stop"] == ["XAUUSD"]
    assert r["daily_loss_room"] == pytest.approx(r["daily_loss_limit"])   # first snapshot today = start of day


def test_poll_without_settings_is_a_clear_message(monkeypatch):
    for k in ("DONNA_CTRADER_CLIENT_ID", "DONNA_CTRADER_CLIENT_SECRET", "DONNA_CTRADER_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert "not configured" in f.poll()["error"]


def test_repeated_job_failures_alert_once(monkeypatch, tmp_path):
    from jarvis import scheduler

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    sent = []
    import donna.channels as ch
    monkeypatch.setattr(ch, "broadcast", lambda text, targets, title: sent.append(text) or {})
    monkeypatch.setattr(scheduler, "ALERT_TARGETS", ["discord:personal"])
    monkeypatch.setattr(scheduler.audit, "log", lambda *a, **k: None)
    for _ in range(3):
        scheduler.job_failed("ctrader_positions", RuntimeError("cTrader unreachable (ClientConnectorError)"))
    assert len(sent) == 1
    scheduler.job_failed("execution", FileNotFoundError("no data for 'executions'"))   # nothing set up: no alert
    scheduler.job_failed("my_day", RuntimeError("NO DATA - calendar not connected"))
    assert len(sent) == 1
    scheduler.job_failed("ctrader_positions", RuntimeError("CH_CLIENT_AUTH_FAILURE: wrong clientSecret"))
    assert len(sent) == 2


def test_new_env_tokens_replace_a_refreshed_pair(tmp_path, monkeypatch):
    from jarvis.adapters import ctrader_feed as f

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("DONNA_CTRADER_ACCESS_TOKEN", "old-access")
    monkeypatch.setenv("DONNA_CTRADER_REFRESH_TOKEN", "old-refresh")
    f._save_tokens("refreshed-access", "refreshed-refresh")
    assert f.tokens()[0] == "refreshed-access"          # the refreshed pair wins while the env pair is unchanged
    monkeypatch.setenv("DONNA_CTRADER_ACCESS_TOKEN", "new-access")
    monkeypatch.setenv("DONNA_CTRADER_REFRESH_TOKEN", "new-refresh")
    assert f.tokens()[:2] == ("new-access", "new-refresh")
