"""Offline tests for bots/kraken/krakenbot (two-sleeve momentum on Kraken spot). No network, no keys."""
import dataclasses as dc
import json
import time

import numpy as np
import pandas as pd
import pytest

from bots.kraken.krakenbot import backtest as bt
from bots.kraken.krakenbot.broker import (BelowMinimum, ConversionNotAuthorised, Fill, PaperBroker, assert_tradeable,
                                          client_id)
from bots.kraken.krakenbot.config import Config, load_config
from bots.kraken.krakenbot import harvest as hv
from bots.kraken.krakenbot.engine import Engine, State
from bots.kraken.krakenbot.portfolio import execute_rebuild, market_report, plan_rebuild
from bots.kraken.krakenbot.risk import (DrawdownGuard, Holding, QuoteError, check_quote, limit_price, on_buy,
                                        plan_rebalance, ratchet, stop_level)
from bots.kraken.krakenbot.signals import features, sleeve_weights, target_weights, to_frame

DAY = 86_400_000
T0 = 1_699_833_600_000  # 2023-11-13 00:00 UTC, a Monday


def bars(n=300, drift=0.004, vol=0.02, seed=1, start_px=100.0):
    rng = np.random.default_rng(seed)
    px, rows = start_px, []
    for i in range(n):
        o = px
        c = o * np.exp(drift + vol * rng.standard_normal())
        rows.append([T0 + i * DAY, o, max(o, c) * 1.01, min(o, c) * 0.99, c, 1.0])
        px = c
    return rows


CFG = Config(dual_assets=("AAA", "GLD"), rotation_assets=("AAA", "BBB", "CCC", "GLD"), min_order_quote=5.0)


def market(n=300, up=("AAA", "BBB"), down=("CCC", "GLD")):
    d = {}
    for i, a in enumerate(up):
        d[a] = bars(n, drift=0.006, vol=0.01, seed=i + 1)
    for i, a in enumerate(down):
        d[a] = bars(n, drift=-0.006, vol=0.01, seed=i + 11)
    return d


def next_day(data, lows=None):
    """Append one flat day to every asset; `lows` optionally forces an asset's low/close."""
    for a, rows in data.items():
        last = rows[-1]
        c = last[4]
        lo, cl = (lows or {}).get(a, (c * 0.999, c))
        rows.append([last[0] + DAY, c, max(c, cl) * 1.001, min(lo, cl), cl, 1.0])


# =============================================================== signals
def test_features_have_no_lookahead():
    df = to_frame(bars(320, seed=7))
    full = features(df, CFG)
    for t in (170, 230, 319):
        part = features(df.iloc[: t + 1], CFG).iloc[-1]
        for c in full.columns:
            a, b = part[c], full.iloc[t][c]
            assert (pd.isna(a) and pd.isna(b)) or a == pytest.approx(b), (c, t)


def test_corrupt_candles_are_rejected():
    rows = bars(5)
    rows[2][2] = rows[2][4] * 0.5  # high below close
    with pytest.raises(ValueError):
        to_frame(rows)


def test_sleeve_picks_only_top_k_that_are_positive_and_trending():
    f = {a: features(to_frame(r), CFG).iloc[-1] for a, r in market().items()}
    w, tab = sleeve_weights(f, CFG, top_k=2)
    assert {a for a, v in w.items() if v > 0} == {"AAA", "BBB"}
    assert w["CCC"] == 0 and w["GLD"] == 0
    assert sum(w.values()) <= 1 + 1e-9 and max(w.values()) <= CFG.max_weight + 1e-9
    assert list(tab.index[:2]) == sorted(["AAA", "BBB"], key=lambda a: -tab.loc[a, "score"])


def test_everything_down_means_all_cash():
    f = {a: features(to_frame(r), CFG).iloc[-1] for a, r in market(up=(), down=("AAA", "BBB", "CCC", "GLD")).items()}
    w, _ = target_weights(f, CFG)
    assert sum(w.values()) == 0


def test_portfolio_combines_sleeves_and_respects_cash_reserve():
    f = {a: features(to_frame(r), CFG).iloc[-1] for a, r in market().items()}
    w, tables = target_weights(f, CFG)
    assert set(tables) == {"dual", "rotation"}
    dual, _ = sleeve_weights({a: f[a] for a in CFG.dual_assets}, CFG, 1)
    rot, _ = sleeve_weights({a: f[a] for a in CFG.rotation_assets}, CFG, 2)
    expect = {a: CFG.dual_weight * dual.get(a, 0) + CFG.rotation_weight * rot.get(a, 0) for a in CFG.assets}
    scale = min(1.0, (1 - CFG.cash_reserve) / sum(expect.values()))
    for a in CFG.assets:
        assert w[a] == pytest.approx(expect[a] * scale)
    assert sum(w.values()) <= 1 - CFG.cash_reserve + 1e-9


# =============================================================== risk
def test_disaster_stop_scales_with_volatility_and_has_a_floor():
    calm, wild = stop_level(100, 0.40, CFG), stop_level(100, 0.90, CFG)
    assert 75 < calm < 85 and wild < calm                     # ~-22% for 40% vol at 4 sigma; wider for wild coins
    assert stop_level(100, 5.0, CFG) == pytest.approx(10.0)   # never below 10% of price
    assert stop_level(100, float("nan"), CFG) == pytest.approx(10.0)


def test_stop_ratchets_up_only():
    h = Holding()
    on_buy(h, 100, 1, 0.4, CFG)
    s0 = h.stop
    ratchet(h, 130, 0.4, CFG)
    s1 = h.stop
    assert s1 > s0
    ratchet(h, 90, 0.9, CFG)
    assert h.stop == s1


def test_drawdown_halt_is_sticky():
    g = DrawdownGuard()
    assert not g.update(1000, CFG) and g.update(640, CFG) and g.update(2000, CFG)


def test_rebalance_planning():
    prices = {"AAA": 100.0, "BBB": 50.0}
    assert plan_rebalance(1000, prices, {"AAA": 3.0}, {"AAA": 0.32, "BBB": 0.0}, CFG) == []   # inside band
    plans = plan_rebalance(1000, prices, {"AAA": 5.0}, {"AAA": 0.0, "BBB": 0.4}, CFG, free_quote=500)
    assert [p.side for p in plans] == ["sell", "buy"] and plans[0].qty == 5.0
    plans = plan_rebalance(1000, prices, {}, {"AAA": 0.5, "BBB": 0.5}, CFG, free_quote=300)
    assert sum(p.notional for p in plans) <= 300


def test_quote_checks_and_slippage_cap():
    now = time.time()
    assert check_quote(99.9, 100.1, now * 1000, CFG, now) == pytest.approx(100.0)
    for bid, ask, ts in [(100, 99, now), (None, 100, now), (95, 105, now), (99.9, 100.1, now - 3600)]:
        with pytest.raises(QuoteError):
            check_quote(bid, ask, ts * 1000, CFG, now)
    for attempt in range(CFG.post_only_attempts + 1):
        assert limit_price("buy", 99, 105, 100, attempt, CFG)[0] <= 100 * (1 + CFG.max_slippage)
        assert limit_price("sell", 95, 101, 100, attempt, CFG)[0] >= 100 * (1 - CFG.max_slippage)


# =============================================================== currency conversion guard
@pytest.mark.parametrize("sym", ["USDC/EUR", "EUR/USDC", "USDT/USDC", "USDG/USDC", "USDC/USD", "USD/EUR"])
def test_conversions_refused_by_default(sym):
    with pytest.raises(ConversionNotAuthorised):
        assert_tradeable(sym, Config())


def test_conversion_needs_flag_and_exact_pair():
    ok = Config(allow_conversions=True, authorised_conversions=("USDC/EUR",))
    assert_tradeable("USDC/EUR", ok)
    with pytest.raises(ConversionNotAuthorised):
        assert_tradeable("USDT/USDC", ok)
    with pytest.raises(ConversionNotAuthorised):
        assert_tradeable("USDC/EUR", Config(authorised_conversions=("USDC/EUR",)))
    with pytest.raises(ConversionNotAuthorised):
        assert_tradeable("BTC/USDC", Config())
    with pytest.raises(ValueError):
        Config(rotation_assets=("BTC", "USDC")).validate()


def test_legacy_apex_does_not_convert_without_authorisation(monkeypatch):
    pytest.importorskip("ccxt")
    import asyncio
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location("apex_legacy",
                                                  pathlib.Path(__file__).parents[1] / "bots/kraken/legacy/apex_v30.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    calls = []

    class Spot:
        async def load_markets(self): pass
        async def fetch_balance(self): return {"EUR": {"free": 500.0}, "USD": {"free": 100.0}}
        async def create_order(self, *a, **k): calls.append(a)
        async def close(self): pass

    class Fut:
        async def load_markets(self): pass
        async def close(self): pass

    async def stop(_):
        raise asyncio.CancelledError
    m.spot, m.fut = Spot(), Fut()
    m.asyncio.sleep = stop
    monkeypatch.delenv("APEX_AUTHORISE_CONVERSION", raising=False)
    try:
        asyncio.run(m.main())
    except (asyncio.CancelledError, NameError):
        pass
    assert calls == []


# =============================================================== engine (simulated exchange)
class FakeMarket:
    def __init__(self, data):
        self.data = data

    def daily_bars(self, symbol, limit=400):
        return self.data[symbol.split("/")[0]][-limit:]

    def top_of_book(self, symbol):
        a = symbol.split("/")[0]
        if a not in self.data:
            raise ValueError("no market")
        c = self.data[a][-1][4]
        return c * 0.9995, c * 1.0005, time.time() * 1000


class Paper(PaperBroker):
    def __init__(self, *a, balances=None, **k):
        super().__init__(*a, **k)
        self.log, self.fail_stops, self.fail_sells, self._bal = [], False, False, balances

    def balances(self):
        return self._bal or {}

    def place_limit(self, symbol, side, qty, price, post_only, cid):
        if self.fail_sells and side == "sell":
            raise RuntimeError("exchange down")
        self.log.append(("limit", symbol, side))
        return super().place_limit(symbol, side, qty, price, post_only, cid)

    def place_stop(self, symbol, qty, trigger, cid):
        if self.fail_stops:
            raise RuntimeError("stop rejected")
        self.log.append(("stop", symbol, round(trigger, 6)))
        return super().place_stop(symbol, qty, trigger, cid)


def _engine(tmp_path, data, capital=1000.0, **kw):
    cfg = dc.replace(CFG, state_file=str(tmp_path / "s.json"), **kw)
    broker = Paper(cfg, FakeMarket(data), slippage=0.0)
    return cfg, broker, Engine(cfg, broker, State.load(cfg.state_file, cfg, capital), cfg.state_file, sleep=lambda s: None)


def _reload(cfg, broker):
    return Engine(cfg, broker, State.load(cfg.state_file, cfg, None), cfg.state_file, sleep=lambda s: None)


def test_first_run_rebalances_and_stops_every_buy_immediately(tmp_path):
    cfg, broker, eng = _engine(tmp_path, market())
    r = eng.run_once()
    assert r["status"] == "rebalanced" and r["orders"] >= 1 and "catch-up" in r["reason"]
    kinds = [(k, s) for k, s, *_ in broker.log]
    for i, (k, s) in enumerate(kinds):
        if k == "limit":
            assert kinds[i + 1] == ("stop", s), kinds
    held = {a for a, h in eng.st.holdings.items() if h.qty > 0}
    assert held and held <= {"AAA", "BBB"}
    for a in held:
        h = eng.st.holdings[a]
        assert h.stop_order_id and broker.orders[h.stop_order_id]["status"] == "open" and 0 < h.stop < h.entry


def test_idempotent_same_day_and_holds_until_the_weekly_rebalance(tmp_path):
    data = market()
    cfg, broker, eng = _engine(tmp_path, data)
    eng.run_once()
    n = len(broker.orders)
    assert _reload(cfg, broker).run_once()["status"] == "already-done" and len(broker.orders) == n
    statuses = []
    for _ in range(7):                                     # a week of daily runs
        next_day(data)
        day = pd.Timestamp(data["AAA"][-1][0], unit="ms").dayofweek
        statuses.append((day, _reload(cfg, broker).run_once()["status"]))
    for day, status in statuses:
        assert status == ("rebalanced" if day == cfg.rebalance_weekday else "hold"), statuses


def test_missed_rebalance_is_caught_up(tmp_path):
    data = market()
    cfg, broker, eng = _engine(tmp_path, data)
    eng.run_once()
    st = json.load(open(cfg.state_file))
    st["last_rebalance"] = "2000-01-01"
    json.dump(st, open(cfg.state_file, "w"))
    next_day(data)
    r = _reload(cfg, broker).run_once()
    assert r["status"] == "rebalanced"


def test_disaster_stop_fill_is_booked(tmp_path):
    data = market()
    cfg, broker, eng = _engine(tmp_path, data)
    eng.run_once()
    a = next(k for k, h in eng.st.holdings.items() if h.qty > 0)
    stop, cash = eng.st.holdings[a].stop, eng.st.cash
    next_day(data, lows={a: (stop * 0.95, stop * 0.97)})
    eng2 = _reload(cfg, broker)
    eng2.run_once()
    assert eng2.st.holdings[a].qty == 0 and eng2.st.cash > cash


def test_price_through_stop_sells_instead_of_placing_stop(tmp_path):
    cfg, broker, eng = _engine(tmp_path, market())
    eng.run_once()
    a = next(k for k, h in eng.st.holdings.items() if h.qty > 0)
    eng.st.holdings[a].stop = broker.top_of_book(f"{a}/EUR")[0] * 1.05
    broker.log.clear()
    eng.protect(a)
    assert eng.st.holdings[a].qty == 0 and ("limit", f"{a}/EUR", "sell") in broker.log
    assert not any(k == "stop" for k, *_ in broker.log)


def test_failing_exit_does_not_recurse(tmp_path):
    cfg, broker, eng = _engine(tmp_path, market())
    eng.run_once()
    a = next(k for k, h in eng.st.holdings.items() if h.qty > 0)
    eng.st.holdings[a].stop = broker.top_of_book(f"{a}/EUR")[0] * 1.05
    broker.fail_sells = True
    eng.protect(a)
    assert eng.st.holdings[a].qty > 0


def test_holding_without_a_stop_is_sold(tmp_path):
    cfg, broker, eng = _engine(tmp_path, market())
    broker.fail_stops = True
    eng.run_once()
    assert all(h.qty == 0 for h in eng.st.holdings.values())


def test_coin_fee_reduces_ledger_and_dust_and_manual_sale(tmp_path):
    cfg, broker, eng = _engine(tmp_path, market())
    orig = broker.to_fill

    def coin_fee(o, s, c):
        f = orig(o, s, c)
        return Fill(f.order_id, f.client_id, f.side, f.qty, f.avg_px, 0.0, f.status,
                    f.qty * 0.0025 if f.side == "buy" else 0.0)
    broker.to_fill = coin_fee
    eng.run_once()
    held = [k for k, h in eng.st.holdings.items() if h.qty > 0]
    a = held[0]
    filled = sum(o["filled"] for o in broker.orders.values()
                 if o.get("symbol") == f"{a}/EUR" and o.get("side") == "buy" and not o.get("stopPrice"))
    assert eng.st.holdings[a].qty == pytest.approx(filled * (1 - 0.0025))

    def too_small(*_a, **_k):
        raise BelowMinimum("below minimum")
    eng.st.holdings[a].stop_order_id = None
    broker.place_stop = too_small
    eng.protect(a)
    assert eng.st.holdings[a].qty == 0                    # dust written off
    if len(held) > 1:
        b = held[1]
        eng.reconcile({b: {"total": 0.0, "free": 0.0}})
        assert eng.st.holdings[b].qty == 0                # sold by hand -> ledger clamps


def test_validate_mode_never_touches_the_ledger(tmp_path):
    cfg, broker, eng = _engine(tmp_path, market(), mode="validate")
    eng.run_once()
    assert eng.st.cash == 1000.0 and all(h.qty == 0 for h in eng.st.holdings.values())
    assert not (tmp_path / "s.json").exists()


def test_drawdown_halt_sells_everything(tmp_path):
    data = market()
    cfg, broker, eng = _engine(tmp_path, data)
    eng.run_once()
    eng.st.guard.peak = 10_000.0                          # pretend equity was much higher
    next_day(data)
    eng.st.last_bar = ""
    r = eng.run_once()
    assert eng.st.guard.halted and all(h.qty == 0 for h in eng.st.holdings.values()) and r["status"] == "rebalanced"


# =============================================================== rebuild (sweep the current account)
class MinBroker(Paper):
    def normalise(self, symbol, qty, price):
        if qty * price < 1.0:
            raise BelowMinimum("below minimum")
        return qty, price


def test_rebuild_plan_classifies_every_balance():
    data = market()
    data["JUNK"] = bars(300, drift=0.0, seed=99)
    bal = {"EUR": {"free": 100.0, "total": 100.0}, "AAA": {"free": 2.0, "total": 2.0},
           "JUNK": {"free": 5.0, "total": 5.0}, "USDC": {"free": 150.0, "total": 150.0},
           "USDG.F": {"free": 0.0, "total": 29.0}, "BBB": {"free": 0.0000001, "total": 0.0000001},
           "XYZ": {"free": 3.0, "total": 3.0}}
    p = plan_rebuild(MinBroker(CFG, FakeMarket(data), balances=bal), CFG)
    names = lambda k: {i["asset"] for i in p[k]}
    assert names("adopt") == {"AAA"} and names("sell_to_eur") == {"JUNK"}
    assert names("currencies_untouched") == {"USDC"} and names("locked_untouched") == {"USDG.F"}
    assert names("dust") == {"BBB"} and names("needs_manual") == {"XYZ"}


def test_rebuild_execute_sells_junk_adopts_and_rebalances(tmp_path):
    data = market()
    data["JUNK"] = bars(300, drift=0.0, seed=99)
    bal = {"EUR": {"free": 100.0, "total": 100.0}, "AAA": {"free": 2.0, "total": 2.0},
           "CCC": {"free": 3.0, "total": 3.0}, "JUNK": {"free": 5.0, "total": 5.0},
           "USDC": {"free": 150.0, "total": 150.0}}
    cfg = dc.replace(CFG, state_file=str(tmp_path / "s.json"), mode="live")
    b = Paper(cfg, FakeMarket(data), balances=bal, slippage=0.0)
    out = execute_rebuild(b, cfg, plan_rebuild(b, cfg), cfg.state_file, sleep=lambda s: None)
    st = json.load(open(cfg.state_file))
    assert ("limit", "JUNK/EUR", "sell") in b.log and "JUNK" not in st["holdings"]
    assert ("limit", "CCC/EUR", "sell") in b.log and st["holdings"]["CCC"]["qty"] == 0   # adopted, then rotated out
    assert out["first_rebalance"]["momentum"]["status"] == "rebalanced"
    assert not any(s.startswith("USDC") for _, s, *_ in b.log)                          # currency untouched
    for a, h in st["holdings"].items():
        if h["qty"] > 0:
            assert h["stop_order_id"]                                                   # protected


def test_report_runs_offline():
    r = market_report(Paper(CFG, FakeMarket(market())), CFG)
    assert set(r["sleeves"]) == {"dual", "rotation"} and 0 <= r["cash_weight"] <= 1
    assert set(r["targets"]) <= {"AAA", "BBB"}


# =============================================================== backtest
def test_backtest_goes_to_cash_in_a_crash_and_beats_holding():
    data = {a: to_frame(r) for a, r in market(400, up=(), down=("AAA", "BBB", "CCC", "GLD")).items()}
    res = bt.run(data, CFG, 1000)
    assert res.equity.iloc[-1] > 0.95 * 1000 > bt.buy_and_hold(data, ["AAA", "BBB"], 1000).iloc[-1]


def test_backtest_rides_an_uptrend():
    data = {a: to_frame(r) for a, r in market(400).items()}
    res = bt.run(data, CFG, 1000)
    assert res.equity.iloc[-1] > 1100 and len(res.trades) > 0


def test_monte_carlo_is_deterministic_and_counts_deposits():
    r = pd.Series(np.full(400, 0.0005))
    m1, m2 = bt.monte_carlo(r, 1000, 100, 1, n=50), bt.monte_carlo(r, 1000, 100, 1, n=50)
    assert m1 == m2 and m1["deposited"] == 1000 + 100 * 12 and m1["p50"] > m1["deposited"]


# =============================================================== config, ids, broker
def test_config_validation_and_env():
    for bad in (dict(mode="yolo"), dict(dual_weight=0.8, rotation_weight=0.4), dict(dual_top_k=5), dict(stop_sigma=0.5)):
        with pytest.raises(ValueError):
            Config(**bad).validate()
    c = load_config(env={"KRAKENBOT_DUAL_WEIGHT": "0.5", "KRAKENBOT_ROTATION_ASSETS": "BTC,ETH,PAXG",
                         "KRAKENBOT_RANK_LOOKBACKS": "20,40"})
    assert c.dual_weight == 0.5 and c.rotation_assets == ("BTC", "ETH", "PAXG") and c.rank_lookbacks == (20, 40)
    assert Config().assets == ("BTC", "PAXG", "ETH", "SOL", "XRP", "DOGE", "LINK") and not Config().allow_conversions


def test_client_id_is_deterministic():
    assert client_id("2026-11-12", "BTC/EUR", "buy", "0") == client_id("2026-11-12", "BTC/EUR", "buy", "0")
    assert client_id("2026-11-12", "BTC/EUR", "buy", "0") != client_id("2026-11-13", "BTC/EUR", "buy", "0")


def _kb():
    ccxt = pytest.importorskip("ccxt")
    from bots.kraken.krakenbot.broker import KrakenBroker
    b = KrakenBroker.__new__(KrakenBroker)
    b.ccxt, b.cfg = ccxt, dc.replace(Config(), mode="live")
    b.transient, b.permanent = (ccxt.NetworkError, ccxt.InvalidNonce), (ccxt.InvalidOrder, ccxt.InsufficientFunds)
    return ccxt, b


class FakeEx:
    markets = {"BTC/EUR": {"limits": {"amount": {"min": 0.00006}, "cost": {"min": 0.45}}}}

    def __init__(self, ccxt, fail_first=False):
        self.live, self.ccxt, self.fail_first, self.params = [], ccxt, fail_first, []

    def amount_to_precision(self, s, q): return str(round(q, 8))
    def price_to_precision(self, s, p): return str(round(p, 1))

    def create_order(self, symbol, type, side, q, p, params):
        self.params.append((type, side, q, p, dict(params)))
        self.live.append({"id": f"O{len(self.live)}", "clientOrderId": params["clientOrderId"], "status": "open"})
        if self.fail_first and len(self.live) == 1:
            raise self.ccxt.RequestTimeout("timed out after the exchange accepted it")
        return self.live[-1]

    def fetch_open_orders(self, symbol, since=None, limit=None): return list(self.live)
    def fetch_closed_orders(self, symbol, since=None, limit=None): return []


def test_timeout_does_not_duplicate_an_order(monkeypatch):
    ccxt, b = _kb()
    b.ex = FakeEx(ccxt, fail_first=True)
    import bots.kraken.krakenbot.broker as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    o = b.place_limit("BTC/EUR", "buy", 0.001, 60000, True, "cid-1")
    assert len(b.ex.live) == 1 and o["clientOrderId"] == "cid-1"


def test_kraken_stop_order_shape_and_guards():
    ccxt, b = _kb()
    b.ex = FakeEx(ccxt)
    b.place_stop("BTC/EUR", 0.001, 55000, "cid-s")
    typ, side, q, p, params = b.ex.params[-1]
    assert (typ, side, p) == ("market", "sell", None) and params["stopLossPrice"] == 55000.0
    with pytest.raises(BelowMinimum):
        b.place_stop("BTC/EUR", 0.000001, 55000, "cid-t")
    with pytest.raises(ConversionNotAuthorised):
        b.place_limit("USDC/EUR", "buy", 10, 1.0, True, "cid-c")


# =============================================================== harvest engine (volatility harvesting)
HCFG = dc.replace(CFG, harvest_assets=("AAA", "GLD"), harvest_targets=(0.3333, 0.3333), min_order_quote=5.0)


def _harvest(tmp_path, data, capital=900.0):
    cfg = dc.replace(HCFG, harvest_state_file=str(tmp_path / "h.json"))
    broker = Paper(cfg, FakeMarket(data), slippage=0.0)
    return cfg, broker, hv.HarvestEngine.load(cfg, broker, capital, sleep=lambda s: None)


def _w(eng, broker):
    mids = {a: broker.top_of_book(f"{a}/EUR")[0] for a in eng.st.holdings}
    eq = eng.st.cash + sum(h.qty * mids[a] for a, h in eng.st.holdings.items())
    return {a: h.qty * mids[a] / eq for a, h in eng.st.holdings.items()}


def test_harvest_first_run_invests_to_target_with_wide_stops(tmp_path):
    cfg, broker, eng = _harvest(tmp_path, market())
    r = eng.run_once()
    assert r["status"] == "rebalanced" and r["reason"] == "initial"
    for a, w in _w(eng, broker).items():
        assert w == pytest.approx(1 / 3, abs=0.02)
        h = eng.st.holdings[a]
        assert h.stop_order_id and h.stop < h.entry * 0.9          # wide disaster stop, on the exchange


def test_harvest_holds_inside_band_and_sells_winner_buys_loser_outside(tmp_path):
    data = market()
    cfg, broker, eng = _harvest(tmp_path, data)
    eng.run_once()
    next_day(data)
    eng2 = hv.HarvestEngine.load(cfg, broker, None, sleep=lambda s: None)
    assert eng2.run_once()["status"] == "hold"
    aaa_before, gld_before = eng2.st.holdings["AAA"].qty, eng2.st.holdings["GLD"].qty
    c = data["AAA"][-1][4]
    next_day(data, lows={"AAA": (c, c * 3.0)})                     # AAA triples -> weight drifts far past the band
    broker.log.clear()
    eng3 = hv.HarvestEngine.load(cfg, broker, None, sleep=lambda s: None)
    r = eng3.run_once()
    assert r["status"] == "rebalanced" and r["reason"].startswith("drift")
    assert eng3.st.holdings["AAA"].qty < aaa_before                # sold the winner
    assert eng3.st.holdings["GLD"].qty > gld_before                # bought the laggard
    assert all(w == pytest.approx(1 / 3, abs=0.03) for w in _w(eng3, broker).values())


def test_two_ledgers_on_one_account_do_not_clamp_each_other():
    cfg = dc.replace(HCFG)
    e = Engine(cfg, Paper(cfg, FakeMarket(market())), State(cash=0.0), "unused.json")
    e.st.holdings["AAA"] = Holding(qty=1.0, entry=100, stop=80)
    e.reserved = {"AAA": 2.0}                                       # harvest ledger owns 2 of the 3 coins
    e.reconcile({"AAA": {"total": 3.0, "free": 3.0}})
    assert e.st.holdings["AAA"].qty == 1.0
    e.reconcile({"AAA": {"total": 2.5, "free": 2.5}})               # 0.5 sold by hand -> only 0.5 left for us
    assert e.st.holdings["AAA"].qty == pytest.approx(0.5)


def test_rebuild_splits_capital_between_momentum_and_harvest(tmp_path):
    data = market()
    bal = {"EUR": {"free": 1000.0, "total": 1000.0}}
    cfg = dc.replace(HCFG, state_file=str(tmp_path / "s.json"), harvest_state_file=str(tmp_path / "h.json"), mode="live")
    b = Paper(cfg, FakeMarket(data), balances=bal, slippage=0.0)
    out = execute_rebuild(b, cfg, plan_rebuild(b, cfg), cfg.state_file, harvest_share=0.5, sleep=lambda s: None)
    assert out["first_rebalance"]["harvest"]["capital"] == pytest.approx(500.0)
    mom, har = json.load(open(cfg.state_file)), json.load(open(cfg.harvest_state_file))
    spent_m = 500.0 - mom["cash"]
    assert 0 <= spent_m <= 500.0 and har["cash"] < 500.0           # each ledger spends only its own half


def test_harvest_backtest_buys_the_dip():
    rows_a, rows_g = bars(300, drift=0.0, vol=0.01, seed=3), bars(300, drift=0.0, vol=0.005, seed=4)
    for i in range(200, 260):                                        # AAA halves, then recovers
        f = 0.5 ** (1 / 30) if i < 230 else 2 ** (1 / 30)       # -50% in 30 days, then back up
        prev = rows_a[i - 1][4]
        c = prev * f
        rows_a[i] = [rows_a[i][0], prev, max(prev, c) * 1.001, min(prev, c) * 0.999, c, 1.0]
    for i in range(260, 300):
        prev = rows_a[i - 1][4]
        rows_a[i] = [rows_a[i][0], prev, prev * 1.001, prev * 0.999, prev, 1.0]
    data = {"AAA": to_frame(rows_a), "GLD": to_frame(rows_g)}
    res = hv.backtest(data, dc.replace(HCFG, harvest_stop_sigma=20.0), 1000)
    buys_in_dip = res.trades[(res.trades.asset == "AAA") & (res.trades.side == "buy")
                             & (res.trades.ts > data["AAA"].index[200])]
    assert len(buys_in_dip) >= 1                                     # rebalanced INTO the fallen asset
    assert res.equity.iloc[-1] > 0


def test_float_tuple_env_override():
    c = load_config(env={"KRAKENBOT_HARVEST_TARGETS": "0.25,0.25"})
    assert c.harvest_targets == (0.25, 0.25)
    with pytest.raises(ValueError):
        load_config(env={"KRAKENBOT_HARVEST_TARGETS": "0.7,0.7"})


# =============================================================== regressions for bugs found in review
class MakerMisses(Paper):
    """Post-only orders never fill (book keeps moving away); crossing IOC orders fill. Like a fast market."""
    def place_limit(self, symbol, side, qty, price, post_only, cid):
        o = super().place_limit(symbol, side, qty, price, post_only, cid)
        if post_only:
            self.orders[cid].update(filled=0.0, status="canceled")
        return o


def test_ioc_fill_on_final_attempt_is_booked_and_protected(tmp_path):
    cfg = dc.replace(CFG, state_file=str(tmp_path / "s.json"))
    broker = MakerMisses(cfg, FakeMarket(market()), slippage=0.0)
    eng = Engine(cfg, broker, State.load(cfg.state_file, cfg, 1000.0), cfg.state_file, sleep=lambda s: None)
    eng.run_once()
    held = {a: h for a, h in eng.st.holdings.items() if h.qty > 0}
    assert held, "IOC fills were not booked (Kraken's AddOrder reply has no fill info)"
    for a, h in held.items():
        assert h.stop_order_id and broker.orders[h.stop_order_id]["status"] == "open"
    spent = sum(h.qty * h.entry for h in held.values())
    assert eng.st.cash == pytest.approx(1000.0 - spent - spent * cfg.fee_rate, rel=1e-6)


def test_stop_filled_while_offline_credits_cash_even_though_coins_are_gone(tmp_path):
    data = market()
    cfg = dc.replace(CFG, state_file=str(tmp_path / "s.json"))
    broker = Paper(cfg, FakeMarket(data), slippage=0.0)
    eng = Engine(cfg, broker, State.load(cfg.state_file, cfg, 1000.0), cfg.state_file, sleep=lambda s: None)
    eng.run_once()
    a = next(k for k, h in eng.st.holdings.items() if h.qty > 0)
    stop, qty, cash = eng.st.holdings[a].stop, eng.st.holdings[a].qty, eng.st.cash
    next_day(data, lows={a: (stop * 0.95, stop * 0.97)})
    broker._bal = {k: {"total": (0.0 if k == a else h.qty), "free": 0.0} for k, h in eng.st.holdings.items()}
    eng2 = Engine(cfg, broker, State.load(cfg.state_file, cfg, None), cfg.state_file, sleep=lambda s: None)
    eng2.run_once()
    assert eng2.st.holdings[a].qty == 0
    assert eng2.st.cash >= cash + qty * stop * 0.9          # the sale proceeds were booked, not lost
    assert eng2.st.holdings[a].sidelined


class StuckOrder(Paper):
    """Orders rest and cannot be cancelled (exchange hiccup)."""
    def place_limit(self, symbol, side, qty, price, post_only, cid):
        o = super().place_limit(symbol, side, qty, price, post_only, cid)
        self.orders[cid].update(filled=0.0, status="open")
        return o

    def cancel(self, order_id, symbol):
        pass


def test_order_stuck_open_never_triggers_a_second_order(tmp_path):
    cfg = dc.replace(CFG, state_file=str(tmp_path / "s.json"))
    broker = StuckOrder(cfg, FakeMarket(market()), slippage=0.0)
    eng = Engine(cfg, broker, State.load(cfg.state_file, cfg, 1000.0), cfg.state_file, sleep=lambda s: None)
    eng.run_once()
    per_symbol = {}
    for kind, sym, side in broker.log:
        if kind == "limit":
            per_symbol[sym] = per_symbol.get(sym, 0) + 1
    assert per_symbol and all(n == 1 for n in per_symbol.values()), per_symbol


def test_harvest_does_not_rebuy_after_a_stop_until_trend_turns_up(tmp_path):
    data = market()
    cfg, broker, eng = _harvest(tmp_path, data)
    eng.run_once()
    stop = eng.st.holdings["AAA"].stop
    for _ in range(3):                                         # crash through the stop, then keep falling
        c = data["AAA"][-1][4]
        next_day(data, lows={"AAA": (min(c, stop) * 0.9, min(c, stop) * 0.92)})
        e = hv.HarvestEngine.load(cfg, broker, None, sleep=lambda s: None)
        r = e.run_once()
    assert e.st.holdings["AAA"].qty == 0 and e.st.holdings["AAA"].sidelined
    assert "AAA" in r.get("sidelined_after_stop", ["AAA"])
    for _ in range(200):                                       # strong recovery -> trend vote turns positive
        c = data["AAA"][-1][4]
        next_day(data, lows={"AAA": (c, c * 1.02)})
    r = hv.HarvestEngine.load(cfg, broker, None, sleep=lambda s: None).run_once()
    st = json.load(open(cfg.harvest_state_file))
    assert not st["holdings"]["AAA"]["sidelined"] and st["holdings"]["AAA"]["qty"] > 0


def test_harvest_skips_cleanly_on_a_bad_quote(tmp_path):
    cfg, broker, eng = _harvest(tmp_path, market())
    eng.run_once()
    broker.md.top_of_book = lambda symbol: (100.0, 120.0, time.time() * 1000)   # 18% spread: unusable
    r = hv.HarvestEngine.load(cfg, broker, None, sleep=lambda s: None).run_once()
    assert r["status"] == "skipped"
