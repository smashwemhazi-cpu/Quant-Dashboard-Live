import asyncio
import json
from datetime import datetime, timezone

import pytest

from jarvis.adapters import ctrader_feed as f


class FakeCTrader:
    """Scripted cTrader: answers each request by payload type, like the JSON WebSocket endpoint."""

    def __init__(self, scope="SCOPE_VIEW", token_ok=True, login=5551234):
        self.sent, self.queue, self.scope, self.token_ok, self.login = [], [], scope, token_ok, login
        self.windows, self.deals = [], []

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
        elif pt == f.DEAL_LIST_REQ:
            self.windows.append((p["fromTimestamp"], p["toTimestamp"]))
            deals = [d for d in self.deals if p["fromTimestamp"] <= d["executionTimestamp"] < p["toTimestamp"]]
            reply(f.DEAL_LIST_RES, {"ctidTraderAccountId": 99, "deal": deals, "hasMore": False})
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
    monkeypatch.setattr(f, "HISTORY_GAP", 0)


def run(fake, **kw):
    return asyncio.run(f.snapshot(fake, "5551234", "a", "r", "cid", "sec", **kw))


def test_snapshot_reads_positions_prices_and_converts_currency():
    fake = FakeCTrader()
    snap = run(fake)
    jpy, gold = snap["positions"]
    assert jpy == {"symbol": "USDJPY", "side": "sell", "units": 10_000, "entry": 150.0, "stop": 150.5,
                   "price": 150.2, "quote_to_account": pytest.approx(1332 / 2000 / 100, rel=1e-6),
                   "position_id": "1", "opened_ms": None}
    assert gold["side"] == "buy" and gold["price"] == 2405.0 and gold["stop"] is None
    assert gold["quote_to_account"] == pytest.approx(1.0)
    assert snap["balance"] == 10_000 and snap["equity"] == pytest.approx(10_000 - 14 + 49)
    # only read requests went out - nothing that can place, amend or close
    assert set(fake.sent) <= {f.APP_AUTH_REQ, f.ACCOUNTS_BY_TOKEN_REQ, f.ACCOUNT_AUTH_REQ, f.TRADER_REQ,
                              f.RECONCILE_REQ, f.UNREALIZED_PNL_REQ, f.SYMBOLS_LIST_REQ,
                              f.SUBSCRIBE_SPOTS_REQ, f.UNSUBSCRIBE_SPOTS_REQ, f.DEAL_LIST_REQ}


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
    from jarvis.agents import alerts   # failures now go through the alert policy: count what is raised
    monkeypatch.setattr(alerts, "raise_alert", lambda kind, text, key="", now=None: sent.append(text) or "held")
    monkeypatch.setattr(scheduler, "ALERT_TARGETS", ["discord:personal"])
    monkeypatch.setattr(scheduler.audit, "log", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "FAIL_GRACE_MINUTES", 0)
    monkeypatch.setattr(scheduler, "market_closed", lambda name, now=None: False)
    for _ in range(3):
        scheduler.job_failed("ctrader_positions", RuntimeError("cTrader unreachable (ClientConnectorError)"))
    assert len(sent) == 1
    scheduler.job_failed("execution", FileNotFoundError("no data for 'executions'"))   # nothing set up: no alert
    scheduler.job_failed("my_day", RuntimeError("NO DATA - calendar not connected"))
    assert len(sent) == 1
    assert scheduler.job_failed("ctrader_positions", RuntimeError("CH_CLIENT_AUTH_FAILURE: wrong clientSecret"))
    assert len(sent) == 2 and "paused" in sent[-1]
    for _ in range(3):  # login refused again: no new alert, no new audit line
        scheduler.job_failed("ctrader_positions", RuntimeError("CH_CLIENT_AUTH_FAILURE: wrong clientSecret"))
    assert len(sent) == 2
    assert "Client ID" in scheduler.waiting_jobs()["ctrader_positions"][0]
    scheduler.job_ok("ctrader_positions")
    assert "ctrader_positions" not in scheduler.waiting_jobs()


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


NOW = 1_762_466_400_000   # 2025-11-06 22:00 UTC, in ms
DAY = 24 * 3600 * 1000


def _deal(i, t, side, close=None):
    d = {"dealId": i, "orderId": i, "positionId": i, "volume": 1_000, "filledVolume": 1_000, "symbolId": 9,
         "createTimestamp": t, "executionTimestamp": t, "executionPrice": 2410.0, "tradeSide": side,
         "dealStatus": 2, "moneyDigits": 2}
    if close:
        d["closePositionDetail"] = close
    return d


def test_closed_trades_flow_into_the_journal(monkeypatch):
    from jarvis.agents import personal

    fake = FakeCTrader()
    fake.deals = [
        _deal(1, NOW - 3 * DAY, 1),                                                   # opening buy: not a trade
        _deal(2, NOW - 2 * DAY, 2, {"entryPrice": 2400.0, "grossProfit": 10_000, "swap": -150, "commission": -70,
                                    "balance": 1_009_780, "closedVolume": 1_000, "moneyDigits": 2}),
        {**_deal(3, NOW - DAY, 1, {"entryPrice": 2420.0, "grossProfit": -5_000, "swap": 0, "commission": 70,
                                   "balance": 1_004_710, "closedVolume": 1_000, "moneyDigits": 2}),
         "dealStatus": 4},                                                            # rejected: ignored
    ]
    monkeypatch.setattr(f, "HISTORY_GAP", 0)
    monkeypatch.setattr(f, "HISTORY_REQUESTS", 100)
    snap = asyncio.run(f.snapshot(fake, "5551234", "a", "r", "cid", "sec", now_ms=NOW))
    # first sync reaches back HISTORY_DAYS, a week per request
    assert fake.windows[0][0] == NOW - f.HISTORY_DAYS * DAY and fake.windows[-1][1] == NOW
    assert all(b - a <= f.WEEK_MS for a, b in fake.windows) and snap["synced_to_ms"] == NOW
    (t,) = snap["closed"]
    assert t["symbol"] == "XAUUSD" and t["side"] == "buy" and t["entry"] == 2400.0 and t["exit"] == 2410.0
    assert t["net"] == pytest.approx(97.80) and t["balance"] == pytest.approx(10_097.80) and t["qty"] == 10
    assert personal.store_trades("5551234", snap["closed"]) == 1
    assert personal.store_trades("5551234", snap["closed"]) == 0                  # overlap is de-duplicated
    monkeypatch.setenv("JARVIS_PERSONAL_ACCOUNT", "5551234")
    j = personal.journal()
    assert j["trades"] == 1 and j["net"] == pytest.approx(97.80)
    # the next sync only asks from the last one (minus an hour of overlap)
    f._mark_synced("5551234", NOW)
    fake2 = FakeCTrader()
    asyncio.run(f.snapshot(fake2, "5551234", "a", "r", "cid", "sec", now_ms=NOW + 300_000))
    assert fake2.windows == [(NOW - 3600_000, NOW + 300_000)]


def test_closed_trade_carries_its_open_time_for_pad(monkeypatch):
    """The PAD holding-period check needs when a closed position was opened: from its opening deal."""
    fake = FakeCTrader()
    close = {"entryPrice": 2400.0, "grossProfit": 10_000, "swap": 0, "commission": 0, "balance": 1_010_000,
             "closedVolume": 1_000, "moneyDigits": 2}
    fake.deals = [_deal(5, NOW - 3 * DAY, 1), {**_deal(6, NOW - 2 * DAY, 2, close), "positionId": 5},
                  _deal(7, NOW - DAY, 2, close)]                     # opened before the history read: unknown
    monkeypatch.setattr(f, "HISTORY_REQUESTS", 100)
    snap = asyncio.run(f.snapshot(fake, "5551234", "a", "r", "cid", "sec", now_ms=NOW))
    a, b = snap["closed"]
    assert a["position_id"] == "5" and a["opened_utc"] == datetime.fromtimestamp(
        (NOW - 3 * DAY) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    assert b["position_id"] == "7" and b["opened_utc"] is None


def test_history_backfill_is_paced_and_survives_rate_limits(monkeypatch):
    monkeypatch.setattr(f, "HISTORY_GAP", 0)
    start = NOW - f.HISTORY_DAYS * DAY
    # a few requests per run: the backfill resumes where it stopped, and positions still come through
    fake = FakeCTrader()
    snap = asyncio.run(f.snapshot(fake, "5551234", "a", "r", "cid", "sec", now_ms=NOW))
    assert len(fake.windows) == f.HISTORY_REQUESTS and len(snap["positions"]) == 2
    assert snap["synced_to_ms"] == start + f.HISTORY_REQUESTS * f.WEEK_MS

    class Limited(FakeCTrader):   # cTrader refuses the second history request
        async def send(self, text):
            import json as j
            m = j.loads(text)
            if m["payloadType"] == f.DEAL_LIST_REQ and self.windows:
                self.sent.append(m["payloadType"])
                self.queue.append({"clientMsgId": m["clientMsgId"], "payloadType": f.ERROR_RES, "payload": {
                    "errorCode": "BLOCKED_PAYLOAD_TYPE", "description": "You are being rate limited"}})
                return
            await super().send(text)

    fake = Limited()
    snap = asyncio.run(f.snapshot(fake, "5551234", "a", "r", "cid", "sec", now_ms=NOW))
    assert len(fake.windows) == 1 and snap["synced_to_ms"] == start + f.WEEK_MS and len(snap["positions"]) == 2


def test_rate_limit_skips_the_run_quietly(monkeypatch):
    monkeypatch.setenv("DONNA_CTRADER_CLIENT_ID", "c")
    monkeypatch.setenv("DONNA_CTRADER_CLIENT_SECRET", "s")
    monkeypatch.setenv("DONNA_CTRADER_ACCESS_TOKEN", "a")
    monkeypatch.setenv("DONNA_CTRADER_LOGIN", "5551234")

    async def limited(login):
        raise f.FeedError("BLOCKED_PAYLOAD_TYPE: You are being rate limited")

    monkeypatch.setattr(f, "_run", limited)
    r = f.poll()
    assert "error" not in r and "rate limited" in r["skipped"]


def test_blips_and_weekend_failures_stay_quiet(monkeypatch, tmp_path):
    """The repeating 'job failed' alerts: one-off cTrader blips and weekend maintenance must not alert."""
    from datetime import datetime, timezone

    from jarvis import scheduler

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    sent = []
    import donna.channels as ch
    monkeypatch.setattr(ch, "broadcast", lambda text, targets, title: sent.append(text) or {})
    from jarvis.agents import alerts   # failures now go through the alert policy: count what is raised
    monkeypatch.setattr(alerts, "raise_alert", lambda kind, text, key="", now=None: sent.append(text) or "held")
    monkeypatch.setattr(scheduler, "ALERT_TARGETS", ["discord:personal"])
    monkeypatch.setattr(scheduler.audit, "log", lambda *a, **k: None)
    sat = datetime(2026, 11, 7, 6, 4, tzinfo=timezone.utc)
    assert scheduler.market_closed("ctrader_positions", sat)
    assert not scheduler.market_closed("ctrader_positions", datetime(2026, 11, 9, 6, tzinfo=timezone.utc))
    assert not scheduler.market_closed("daily_wrap", sat)
    monkeypatch.setattr(scheduler, "market_closed", lambda name, now=None: False)
    clock = [1_000_000.0]
    monkeypatch.setattr(scheduler.time, "time", lambda: clock[0])
    errors = ["cTrader unreachable (ClientConnectorError)", "CANT_ROUTE_REQUEST: Cannot route request",
              "BLOCKED_PAYLOAD_TYPE: You are being rate limited"]
    for err in errors:  # within the first hour: silent, whatever the wording
        scheduler.job_failed("ctrader_positions", RuntimeError(err))
        clock[0] += 600
    assert sent == []
    scheduler.job_ok("ctrader_positions")   # one success resets the clock
    clock[0] += 3000
    scheduler.job_failed("ctrader_positions", RuntimeError(errors[0]))
    assert sent == []
    clock[0] += 3700                        # failing for over an hour without a success: one alert
    for err in errors:
        scheduler.job_failed("ctrader_positions", RuntimeError(err))
    assert len(sent) == 1
    # a real fault (not a blip) still alerts on the first failure
    scheduler.job_failed("daily_wrap", KeyError("equity"))
    assert len(sent) == 2
