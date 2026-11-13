"""Offline tests for bots/kraken/krakenbot. No network, no keys. ccxt is only needed for the broker test."""
import dataclasses as dc
import time

import numpy as np
import pytest

from bots.kraken.krakenbot import backtest as bt
from bots.kraken.krakenbot.broker import PaperBroker, client_id
from bots.kraken.krakenbot.config import Config, load_config
from bots.kraken.krakenbot.engine import Engine, State
from bots.kraken.krakenbot.risk import (DrawdownGuard, PositionState, QuoteError, check_quote, limit_price,
                                        on_buy_fill, plan_rebalance, update_trail)
from bots.kraken.krakenbot.signals import target_weight, to_frame

DAY = 86_400_000


def bars(n=300, drift=0.004, vol=0.02, seed=1, start_px=100.0):
    rng = np.random.default_rng(seed)
    px, rows, t0 = start_px, [], 1_700_000_000_000
    for i in range(n):
        o = px
        c = o * np.exp(drift + vol * rng.standard_normal())
        h, l = max(o, c) * 1.01, min(o, c) * 0.99
        rows.append([t0 + i * DAY, o, h, l, c, 1.0])
        px = c
    return rows


CFG = Config(assets=("AAA", "BBB"), quote="EUR", min_order_quote=5.0)


# --- signals -----------------------------------------------------------------------------------------
def test_signal_has_no_lookahead():
    df = to_frame(bars(300))
    full = target_weight(df, CFG)["weight"]
    for t in (150, 200, 250):
        trunc = target_weight(df.iloc[: t + 1], CFG)["weight"]
        assert trunc.iloc[-1] == pytest.approx(full.iloc[t]), f"weight at bar {t} changed when future bars were added"


def test_downtrend_gives_zero_weight_and_uptrend_positive():
    down = target_weight(to_frame(bars(300, drift=-0.004)), CFG)
    up = target_weight(to_frame(bars(300, drift=0.004)), CFG)
    assert down["weight"].iloc[-1] == 0
    assert 0 < up["weight"].iloc[-1] <= CFG.max_weight


def test_weight_respects_risk_per_trade():
    w = target_weight(to_frame(bars(300, drift=0.004, vol=0.06)), CFG).iloc[-1]
    stop_frac = CFG.hard_stop_atr * w["atr"] / to_frame(bars(300, drift=0.004, vol=0.06))["close"].iloc[-1]
    assert w["weight"] * stop_frac <= CFG.risk_per_trade + 1e-9


# --- risk --------------------------------------------------------------------------------------------
def test_rebalance_band_and_cash_scaling():
    prices = {"AAA": 100.0, "BBB": 50.0}
    assert plan_rebalance(1000, prices, {"AAA": 3.0}, {"AAA": 0.31, "BBB": 0.0}, CFG) == []  # inside 4% band
    plans = plan_rebalance(1000, prices, {"AAA": 0.0, "BBB": 0.0}, {"AAA": 0.5, "BBB": 0.5}, CFG, free_quote=400)
    assert sum(p.notional for p in plans) <= 400
    plans = plan_rebalance(1000, prices, {"AAA": 5.0}, {"AAA": 0.0, "BBB": 0.4}, CFG, free_quote=500)
    assert [p.side for p in plans] == ["sell", "buy"]  # sells fund buys


def test_quote_sanity_rejects_bad_data():
    now = time.time()
    assert check_quote(99.9, 100.1, now * 1000, CFG, now) == pytest.approx(100.0)
    for bid, ask, ts in [(100, 99, now), (None, 100, now), (95, 105, now), (99.9, 100.1, now - 3600), (0, 1, now)]:
        with pytest.raises(QuoteError):
            check_quote(bid, ask, ts * 1000, CFG, now)


def test_limit_price_never_exceeds_slippage_cap():
    for attempt in range(CFG.post_only_attempts + 1):
        p, _ = limit_price("buy", 99, 105, 100, attempt, CFG)
        assert p <= 100 * (1 + CFG.max_slippage)
        p, _ = limit_price("sell", 95, 101, 100, attempt, CFG)
        assert p >= 100 * (1 - CFG.max_slippage)
    assert limit_price("buy", 99, 101, 100, 0, CFG) == (99, True)  # first try: maker, post-only


def test_trailing_stop_only_ratchets_up():
    st = PositionState()
    on_buy_fill(st, 100, 1, 2.0, CFG)
    assert st.stop == pytest.approx(100 - CFG.hard_stop_atr * 2)
    update_trail(st, 120, 2.0, CFG)
    high_stop = st.stop
    update_trail(st, 90, 2.0, CFG)
    assert st.stop == high_stop


def test_drawdown_halt_is_sticky():
    g = DrawdownGuard()
    assert not g.update(1000, CFG)
    assert g.update(740, CFG)
    assert g.update(2000, CFG)  # recovery does not un-halt; a human must


def test_config_validation_and_env_override():
    with pytest.raises(ValueError):
        Config(mode="yolo").validate()
    with pytest.raises(ValueError):
        Config(max_gross=5).validate()
    c = load_config(env={"KRAKENBOT_ASSETS": "BTC,ETH", "KRAKENBOT_RISK_PER_TRADE": "0.01", "KRAKENBOT_LOOKBACKS": "10,20"})
    assert c.assets == ("BTC", "ETH") and c.risk_per_trade == 0.01 and c.lookbacks == (10, 20)


def test_client_id_is_deterministic():
    assert client_id("2026-11-12", "BTC/EUR", "buy", "enter", "0") == client_id("2026-11-12", "BTC/EUR", "buy", "enter", "0")
    assert client_id("2026-11-12", "BTC/EUR", "buy", "enter", "0") != client_id("2026-11-13", "BTC/EUR", "buy", "enter", "0")


# --- engine end to end with simulated exchange ---------------------------------------------------------
class FakeMarket:
    def __init__(self, data):
        self.data = data

    def daily_bars(self, symbol, limit=400):
        return self.data[symbol.split("/")[0]][-limit:]

    def top_of_book(self, symbol):
        c = self.data[symbol.split("/")[0]][-1][4]
        return c * 0.9995, c * 1.0005, time.time() * 1000


def _engine(tmp_path, data, capital=1000.0):
    cfg = dc.replace(CFG, state_file=str(tmp_path / "s.json"))
    broker = PaperBroker(cfg, FakeMarket(data), slippage=0.0)
    st = State.load(cfg.state_file, cfg, capital)
    return cfg, broker, Engine(cfg, broker, st, cfg.state_file, sleep=lambda s: None)


def test_engine_buys_protects_and_is_idempotent(tmp_path):
    data = {"AAA": bars(300, seed=1), "BBB": bars(300, seed=2)}
    cfg, broker, eng = _engine(tmp_path, data)
    r = eng.run_once()
    assert r["status"] == "ok" and r["orders"] > 0
    held = {a: p.qty for a, p in eng.st.positions.items()}
    assert any(q > 0 for q in held.values())
    for a, p in eng.st.positions.items():
        if p.qty > 0:
            assert p.stop_order_id and broker.orders[p.stop_order_id]["status"] == "open"  # every position has a stop
    assert eng.st.cash >= 0
    n_orders = len(broker.orders)
    # crash/restart on the same day: a fresh engine from the saved state must not trade again
    eng2 = Engine(cfg, broker, State.load(cfg.state_file, cfg, None), cfg.state_file, sleep=lambda s: None)
    assert eng2.run_once()["status"] == "already-done"
    assert len(broker.orders) == n_orders
    assert {a: p.qty for a, p in eng2.st.positions.items()} == held


def test_engine_books_stop_out_and_blocks_reentry(tmp_path):
    data = {"AAA": bars(300, seed=1), "BBB": bars(300, seed=2)}
    cfg, broker, eng = _engine(tmp_path, data)
    eng.run_once()
    a = next(k for k, p in eng.st.positions.items() if p.qty > 0)
    stop = eng.st.positions[a].stop
    last = data[a][-1]
    data[a].append([last[0] + DAY, last[4], last[4], stop * 0.95, stop * 0.97, 1.0])  # crash through the stop
    other = "BBB" if a == "AAA" else "AAA"
    lo = data[other][-1]
    data[other].append([lo[0] + DAY, lo[4], lo[4] * 1.001, lo[4] * 0.999, lo[4], 1.0])
    cash_before = eng.st.cash
    eng.run_once()
    assert eng.st.positions[a].qty == 0  # stopped out and NOT bought straight back
    assert eng.st.cash > cash_before
    assert any(o.get("stopPrice") and o["status"] == "closed" for o in broker.orders.values())


def test_reentry_block_rules():
    from bots.kraken.krakenbot.risk import on_stopped_out, update_block
    st = PositionState(qty=1, entry=100, high_close=130, stop=120)
    on_stopped_out(st)
    assert st.blocked and st.qty == 0
    update_block(st, close=125, weight=0.3)
    assert st.blocked                      # still a live signal, no new high: stay out
    update_block(st, close=131, weight=0.3)
    assert not st.blocked                  # new high since the stop: trend resumed
    on_stopped_out(PositionState())
    st2 = PositionState(qty=1, high_close=130, stop=120)
    on_stopped_out(st2)
    update_block(st2, close=110, weight=0.0)
    assert not st2.blocked                 # signal reset: next entry needs a fresh positive signal


def test_engine_clamps_ledger_when_coins_were_removed_by_hand(tmp_path):
    data = {"AAA": bars(300, seed=1), "BBB": bars(300, seed=2)}
    cfg, broker, eng = _engine(tmp_path, data)
    eng.run_once()
    a = next(k for k, p in eng.st.positions.items() if p.qty > 0)
    eng._reconcile_holdings({a: {"total": 0.0, "free": 0.0}})
    assert eng.st.positions[a].qty == 0


def test_backtest_beats_nothing_by_construction_in_a_crash():
    """In a steady crash the strategy must end near flat (in cash), never ride it down like buy-and-hold."""
    data = {"AAA": to_frame(bars(400, drift=-0.005, seed=3)), "BBB": to_frame(bars(400, drift=-0.005, seed=4))}
    res = bt.run(data, CFG, 1000)
    bh = bt.buy_and_hold(data, 1000)
    assert res.equity.iloc[-1] > 0.9 * 1000 > bh.iloc[-1]


# --- broker idempotency (needs ccxt) -------------------------------------------------------------------
def test_ambiguous_timeout_does_not_duplicate_order():
    ccxt = pytest.importorskip("ccxt")
    from bots.kraken.krakenbot.broker import KrakenBroker

    class Ex:
        markets = {"BTC/EUR": {"limits": {"amount": {"min": 0.00006}, "cost": {"min": 0.45}}}}

        def __init__(self):
            self.live = []

        def amount_to_precision(self, s, q): return str(round(q, 8))
        def price_to_precision(self, s, p): return str(round(p, 1))

        def create_order(self, symbol, type, side, q, p, params):
            self.live.append({"id": f"O{len(self.live)}", "clientOrderId": params["clientOrderId"], "status": "open"})
            if len(self.live) == 1:
                raise ccxt.RequestTimeout("timed out after the exchange accepted it")
            return self.live[-1]

        def fetch_open_orders(self, symbol, since=None, limit=None): return list(self.live)
        def fetch_closed_orders(self, symbol, since=None, limit=None): return []

    b = KrakenBroker.__new__(KrakenBroker)
    b.ccxt, b.cfg, b.ex = ccxt, dc.replace(CFG, mode="live"), Ex()
    b.transient, b.permanent = (ccxt.NetworkError, ccxt.InvalidNonce), (ccxt.InvalidOrder, ccxt.InsufficientFunds)
    import bots.kraken.krakenbot.broker as mod
    real_sleep, mod.time.sleep = mod.time.sleep, lambda s: None
    try:
        o = b.place_limit("BTC/EUR", "buy", 0.001, 60000, True, "cid-1")
    finally:
        mod.time.sleep = real_sleep
    assert len(b.ex.live) == 1 and o["clientOrderId"] == "cid-1"
