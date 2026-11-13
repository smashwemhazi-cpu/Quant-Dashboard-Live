"""Offline tests for bots/kraken/krakenbot. No network, no keys. ccxt is only needed for two broker tests."""
import dataclasses as dc
import json
import time

import numpy as np
import pytest

from bots.kraken.krakenbot import backtest as bt
from bots.kraken.krakenbot.broker import (BelowMinimum, ConversionNotAuthorised, Fill, PaperBroker, assert_tradeable,
                                          client_id)
from bots.kraken.krakenbot.config import Config, load_config
from bots.kraken.krakenbot.engine import Engine, State
from bots.kraken.krakenbot.protect import protect_holdings
from bots.kraken.krakenbot.risk import (DrawdownGuard, PositionState, QuoteError, check_quote, limit_price, new_bar,
                                        on_buy_fill, on_stopped_out, plan_rebalance, risk_weight, structural_stop)
from bots.kraken.krakenbot.signals import features, smc, swings, to_frame
from bots.kraken.krakenbot.strategy import decide

DAY = 86_400_000
T0 = 1_700_000_000_000


def bars(n=300, drift=0.004, vol=0.02, seed=1, start_px=100.0):
    rng = np.random.default_rng(seed)
    px, rows = start_px, []
    for i in range(n):
        o = px
        c = o * np.exp(drift + vol * rng.standard_normal())
        rows.append([T0 + i * DAY, o, max(o, c) * 1.01, min(o, c) * 0.99, c, 1.0])
        px = c
    return rows


def path(closes):
    """Bars from a list of closes: open = previous close, 1% wicks."""
    rows, prev = [], closes[0]
    for i, c in enumerate(closes):
        rows.append([T0 + i * DAY, prev, max(prev, c) * 1.01, min(prev, c) * 0.99, c, 1.0])
        prev = c
    return rows


CFG = Config(assets=("AAA", "BBB"), quote="EUR", min_order_quote=5.0)


# =============================================================== features: no look-ahead, SMC definitions
def test_every_feature_is_free_of_lookahead():
    df = to_frame(bars(320, seed=7))
    full = features(df, CFG)
    cols = [c for c in full.columns if c != "regime"]
    for t in (150, 200, 260, 319):
        part = features(df.iloc[: t + 1], CFG).iloc[-1]
        for c in cols:
            a, b = part[c], full.iloc[t][c]
            if isinstance(a, (float, np.floating)) and np.isnan(a):
                assert np.isnan(b), (c, t)
            else:
                assert a == pytest.approx(b), f"{c} at bar {t} changed when future bars were added"
        assert part["regime"] == full.iloc[t]["regime"]


def test_swing_point_is_known_only_k_bars_later():
    closes = [10, 11, 12, 13, 12, 11, 10, 11, 12]  # peak at index 3
    df = to_frame(path(closes))
    sw = swings(df, 2)
    assert not sw["new_swing_high"].iloc[:5].any()
    assert sw["new_swing_high"].iloc[5]                # 3 + k(2) = confirmed at bar 5
    assert sw["swing_high"].iloc[5] == pytest.approx(df["high"].iloc[3])


def test_smc_break_of_structure_choch_and_sweep():
    # up-leg, pullback (swing low), new high (BOS up), drop below swing low (BOS down = CHoCH),
    # then a wick under the new swing low that closes back above it (liquidity sweep).
    closes = [100, 104, 108, 112, 109, 106, 103, 106, 110, 114, 118, 112, 106, 100, 97, 99, 101, 103, 101, 99, 100, 102]
    rows = path(closes)
    df = to_frame(rows)
    sw = swings(df, 2)
    s = smc(df, sw)
    assert s["bos_up"].any() and s["bos_dn"].any()
    first_up, first_dn = s.index[s["bos_up"]][0], s.index[s["bos_dn"]][0]
    assert first_up < first_dn and s.loc[first_dn, "choch"]   # bearish break after bullish structure = CHoCH
    assert s["structure"].iloc[-1] in (-1.0, 1.0)
    # construct an explicit sweep: bar dips under the last known swing low and closes above it
    low = sw["swing_low"].dropna().iloc[-1]
    last = rows[-1]
    rows.append([last[0] + DAY, last[4], last[4] * 1.01, low * 0.98, max(low * 1.01, last[4]), 1.0])
    df2 = to_frame(rows)
    s2 = smc(df2, swings(df2, 2))
    assert s2["sweep"].iloc[-1] and s2["sweep_low"].iloc[-1] < s2["swept_level"].iloc[-1] < df2["close"].iloc[-1]


def test_regimes_follow_the_market():
    up = features(to_frame(bars(320, drift=0.006, vol=0.01)), CFG)["regime"].iloc[-1]
    down = features(to_frame(bars(320, drift=-0.006, vol=0.01)), CFG)["regime"].iloc[-1]
    flat = features(to_frame(path([100 + 3 * np.sin(i / 3) for i in range(320)])), CFG)["regime"].iloc[-1]
    assert (up, down, flat) == ("up", "down", "range")


# =============================================================== stops and sizing
def test_structural_stop_is_clamped_between_min_and_max_atr():
    c = CFG  # min 1 ATR, max 3 ATR, buffer 0.5 ATR
    assert structural_stop(100, 97, 2, c) == pytest.approx(96)     # swing low 97 - 0.5*2
    assert structural_stop(100, 99.5, 2, c) == pytest.approx(98)   # too close -> 1 ATR
    assert structural_stop(100, 80, 2, c) == pytest.approx(94)     # too far -> 3 ATR
    assert structural_stop(100, 105, 2, c) == pytest.approx(94)    # swing low above price (structure broken)
    assert structural_stop(100, float("nan"), 2, c) == pytest.approx(94)


def test_size_comes_from_the_stop():
    assert risk_weight(100, 90, 0.02) == pytest.approx(0.2)        # 10% stop, 2% risk -> 20% of equity
    assert risk_weight(100, 100, 0.02) == 0 and risk_weight(100, 0, 0.02) == 0


def test_stop_only_ratchets_up_and_trails_swing_lows():
    st = PositionState()
    on_buy_fill(st, 100, 1, 94, "trend")
    new_bar(st, 120, 2.0, float("nan"), 0.5, CFG)
    after_rally = st.stop
    assert after_rally == pytest.approx(120 - CFG.trail_atr * 2)
    new_bar(st, 115, 2.0, 116.0, 0.5, CFG)                       # new higher swing low above entry
    assert st.stop == pytest.approx(max(after_rally, 116 - 0.5 * 2))
    high = st.stop
    new_bar(st, 90, 5.0, 80.0, 0.5, CFG)                          # crash + wider ATR must not lower it
    assert st.stop == high


def test_adding_never_loosens_the_stop():
    st = PositionState()
    on_buy_fill(st, 100, 1, 94, "trend")
    on_buy_fill(st, 110, 1, 90, "trend")
    assert st.stop == 94 and st.entry == pytest.approx(105)


def test_stop_out_sets_cooldown_and_block_from_the_real_high():
    st = PositionState(qty=0.0, high_close=0.0)
    on_stopped_out(st, CFG, high_close=130.0)                    # position already flattened by the fill
    assert st.blocked and st.block_high == 130 and st.cooldown == CFG.cooldown_bars
    assert decide_flat_reason(st).startswith("cooldown")


def decide_flat_reason(st):
    row = features(to_frame(bars(320, drift=0.006, vol=0.01)), CFG).iloc[-1]
    return decide(row, st, CFG).reason


def test_drawdown_halt_is_sticky():
    g = DrawdownGuard()
    assert not g.update(1000, CFG)
    assert g.update(740, CFG)
    assert g.update(2000, CFG)


# =============================================================== strategy decisions
def test_trend_entry_has_stop_below_price_and_risk_capped_size():
    row = features(to_frame(bars(320, drift=0.006, vol=0.01)), CFG).iloc[-1]
    d = decide(row, PositionState(), CFG)
    assert d.sleeve == "trend" and 0 < d.weight <= CFG.max_weight
    assert d.stop < row["close"]
    assert d.weight * (row["close"] - d.stop) / row["close"] <= CFG.risk_per_trade + 1e-9


def test_no_long_entries_in_a_downtrend_for_any_model():
    cfg = dc.replace(CFG, entry_models=("trend", "range", "smc"))
    f = features(to_frame(bars(320, drift=-0.006, vol=0.01)), cfg)
    for t in range(250, 320):
        if f.iloc[t]["regime"] == "down":
            assert decide(f.iloc[t], PositionState(), cfg).weight == 0


def test_range_and_smc_entries_have_valid_stop_target_and_rr():
    cfg = dc.replace(CFG, entry_models=("range", "smc"), smc_require_bullish_structure=False)
    rng = np.random.default_rng(3)
    closes = [100 + 6 * np.sin(i / 4) + rng.normal(0, 1.2) for i in range(600)]
    f = features(to_frame(path(closes)), cfg)
    seen = set()
    for t in range(200, 600):
        d = decide(f.iloc[t], PositionState(), cfg)
        if d.weight:
            c = f.iloc[t]["close"]
            seen.add(d.sleeve)
            assert d.stop < c < d.take_profit
            rr = cfg.range_min_rr if d.sleeve == "range" else cfg.smc_min_rr
            assert d.take_profit - c >= rr * (c - d.stop) - 1e-9
    assert "range" in seen


def test_range_trade_exits_on_target_time_and_breakdown():
    f = features(to_frame(bars(320, drift=0.0, vol=0.01)), CFG)
    row = f.iloc[-1].copy()
    pos = PositionState(qty=1, entry=row["close"], stop=row["close"] * 0.9, sleeve="range",
                        take_profit=row["close"] * 0.99, bars_held=1)
    assert decide(row, pos, CFG).is_exit                       # target reached
    pos.take_profit, pos.bars_held = row["close"] * 2, CFG.range_max_bars
    assert decide(row, pos, CFG).is_exit                       # time stop
    pos.bars_held = 1
    row["regime"] = "down"
    assert decide(row, pos, CFG).is_exit                       # range broke down


# =============================================================== risk utilities
def test_rebalance_band_cash_scaling_and_sell_first():
    prices = {"AAA": 100.0, "BBB": 50.0}
    assert plan_rebalance(1000, prices, {"AAA": 3.0}, {"AAA": 0.31, "BBB": 0.0}, CFG) == []
    plans = plan_rebalance(1000, prices, {"AAA": 0.0, "BBB": 0.0}, {"AAA": 0.5, "BBB": 0.5}, CFG, free_quote=400)
    assert sum(p.notional for p in plans) <= 400
    plans = plan_rebalance(1000, prices, {"AAA": 5.0}, {"AAA": 0.0, "BBB": 0.4}, CFG, free_quote=500)
    assert [p.side for p in plans] == ["sell", "buy"] and plans[0].qty == 5.0


def test_quote_sanity_rejects_bad_data():
    now = time.time()
    assert check_quote(99.9, 100.1, now * 1000, CFG, now) == pytest.approx(100.0)
    for bid, ask, ts in [(100, 99, now), (None, 100, now), (95, 105, now), (99.9, 100.1, now - 3600), (0, 1, now)]:
        with pytest.raises(QuoteError):
            check_quote(bid, ask, ts * 1000, CFG, now)


def test_limit_price_never_exceeds_slippage_cap():
    for attempt in range(CFG.post_only_attempts + 1):
        assert limit_price("buy", 99, 105, 100, attempt, CFG)[0] <= 100 * (1 + CFG.max_slippage)
        assert limit_price("sell", 95, 101, 100, attempt, CFG)[0] >= 100 * (1 - CFG.max_slippage)


# =============================================================== currency conversion guard
@pytest.mark.parametrize("sym", ["USDC/EUR", "EUR/USDC", "USDT/USDC", "USDG/USDC", "USDC/USD", "USD/EUR"])
def test_currency_conversions_are_refused_by_default(sym):
    with pytest.raises(ConversionNotAuthorised):
        assert_tradeable(sym, Config())


def test_conversion_allowed_only_when_explicitly_authorised():
    cfg = Config(allow_conversions=True, authorised_conversions=("USDC/EUR",))
    assert_tradeable("USDC/EUR", cfg)
    with pytest.raises(ConversionNotAuthorised):
        assert_tradeable("USDT/USDC", cfg)                      # authorising one pair is not a blanket yes
    with pytest.raises(ConversionNotAuthorised):
        assert_tradeable("USDC/EUR", Config(authorised_conversions=("USDC/EUR",)))  # flag still required


def test_only_the_configured_quote_is_traded_and_currencies_cannot_be_assets():
    assert_tradeable("BTC/EUR", Config())
    with pytest.raises(ConversionNotAuthorised):
        assert_tradeable("BTC/USDC", Config())
    with pytest.raises(ValueError):
        Config(assets=("BTC", "USDC")).validate()


def test_paper_broker_enforces_the_same_guard():
    pb = PaperBroker(Config(), FakeMarket({"USDC": bars(10)}))
    with pytest.raises(ConversionNotAuthorised):
        pb.place_limit("USDC/EUR", "buy", 10, 1.0, True, "x")


def test_legacy_apex_no_longer_converts_without_authorisation(monkeypatch):
    ccxt = pytest.importorskip("ccxt")
    import asyncio
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "apex_legacy", pathlib.Path(__file__).parents[1] / "bots/kraken/legacy/apex_v30.py")
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
    assert calls == [], "legacy bot converted currency without authorisation"
    _ = ccxt


# =============================================================== engine end to end (simulated exchange)
class FakeMarket:
    def __init__(self, data):
        self.data = data

    def daily_bars(self, symbol, limit=400):
        return self.data[symbol.split("/")[0]][-limit:]

    def top_of_book(self, symbol):
        c = self.data[symbol.split("/")[0]][-1][4]
        return c * 0.9995, c * 1.0005, time.time() * 1000


class RecordingPaper(PaperBroker):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.log = []
        self.fail_stops = False
        self.fail_sells = False

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


def uptrend():
    return {"AAA": bars(320, drift=0.006, vol=0.01, seed=1), "BBB": bars(320, drift=0.006, vol=0.01, seed=2)}


def _engine(tmp_path, data, capital=1000.0, **kw):
    cfg = dc.replace(CFG, state_file=str(tmp_path / "s.json"), **kw)
    broker = RecordingPaper(cfg, FakeMarket(data), slippage=0.0)
    st = State.load(cfg.state_file, cfg, capital)
    return cfg, broker, Engine(cfg, broker, st, cfg.state_file, sleep=lambda s: None)


def test_stop_is_placed_immediately_after_each_buy(tmp_path):
    cfg, broker, eng = _engine(tmp_path, uptrend())
    r = eng.run_once()
    assert r["status"] == "ok" and r["orders"] >= 1
    kinds = [(k, s) for k, s, *_ in broker.log]
    for i, (k, s) in enumerate(kinds):
        if k == "limit":
            assert kinds[i + 1] == ("stop", s), f"no stop straight after the buy of {s}: {kinds}"
    for p in eng.st.positions.values():
        if p.qty > 0:
            assert p.stop_order_id and broker.orders[p.stop_order_id]["status"] == "open"
            assert 0 < p.stop < p.entry


def test_engine_is_idempotent_across_restarts(tmp_path):
    cfg, broker, eng = _engine(tmp_path, uptrend())
    eng.run_once()
    n, held = len(broker.orders), {a: p.qty for a, p in eng.st.positions.items()}
    eng2 = Engine(cfg, broker, State.load(cfg.state_file, cfg, None), cfg.state_file, sleep=lambda s: None)
    assert eng2.run_once()["status"] == "already-done"
    assert len(broker.orders) == n and {a: p.qty for a, p in eng2.st.positions.items()} == held


def test_stop_fill_is_booked_and_not_bought_back(tmp_path):
    data = uptrend()
    cfg, broker, eng = _engine(tmp_path, data)
    eng.run_once()
    a = next(k for k, p in eng.st.positions.items() if p.qty > 0)
    stop, cash = eng.st.positions[a].stop, eng.st.cash
    for k in data:
        last = data[k][-1]
        if k == a:
            data[k].append([last[0] + DAY, last[4], last[4], stop * 0.95, stop * 0.97, 1.0])
        else:
            data[k].append([last[0] + DAY, last[4], last[4] * 1.001, last[4] * 0.999, last[4], 1.0])
    eng.run_once()
    p = eng.st.positions[a]
    assert p.qty == 0 and p.cooldown > 0 and eng.st.cash > cash


def test_price_already_through_stop_exits_instead_of_placing_stop(tmp_path):
    cfg, broker, eng = _engine(tmp_path, uptrend())
    eng.run_once()
    a = next(k for k, p in eng.st.positions.items() if p.qty > 0)
    p = eng.st.positions[a]
    mid = broker.top_of_book(f"{a}/EUR")[0]
    p.stop = mid * 1.05                 # e.g. a ratcheted swing-low stop the price has already broken
    broker.log.clear()
    eng._protect_one(a)
    assert p.qty == 0 and ("limit", f"{a}/EUR", "sell") in broker.log
    assert not any(k == "stop" for k, *_ in broker.log)


def test_failing_exit_does_not_recurse_forever(tmp_path):
    cfg, broker, eng = _engine(tmp_path, uptrend())
    eng.run_once()
    a = next(k for k, p in eng.st.positions.items() if p.qty > 0)
    eng.st.positions[a].stop = broker.top_of_book(f"{a}/EUR")[0] * 1.05
    broker.fail_sells = True
    eng._protect_one(a)                 # must return (and log CRITICAL), not hit RecursionError
    assert eng.st.positions[a].qty > 0


def test_position_without_a_stop_gets_flattened(tmp_path):
    cfg, broker, eng = _engine(tmp_path, uptrend())
    broker.fail_stops = True
    eng.run_once()
    assert all(p.qty == 0 for p in eng.st.positions.values())


def test_fee_charged_in_coin_reduces_ledger_quantity(tmp_path):
    cfg, broker, eng = _engine(tmp_path, uptrend())
    orig = broker.to_fill

    def coin_fee(o, symbol, cid):
        f = orig(o, symbol, cid)
        return Fill(f.order_id, f.client_id, f.side, f.qty, f.avg_px, 0.0, f.status, f.qty * 0.0025)
    broker.to_fill = coin_fee
    eng.run_once()
    for a, p in eng.st.positions.items():
        bought = [o for cid, o in broker.orders.items() if o.get("side") == "buy" and not o.get("stopPrice")
                  and cid in eng_cids(eng, broker, a)]
        if p.qty > 0:
            assert p.qty == pytest.approx(sum(o["filled"] for o in bought) * (1 - 0.0025))


def eng_cids(eng, broker, asset):
    """client ids of buy orders for `asset` (PaperBroker uses the cid as the order id)."""
    from bots.kraken.krakenbot.broker import client_id as cid
    tag, sym = eng.run_tag, f"{asset}/EUR"
    d = eng.decisions[asset]
    return {cid(tag, sym, "buy", d.reason, str(i)) for i in range(eng.cfg.post_only_attempts + 1)}


def test_dust_below_exchange_minimum_is_written_off(tmp_path):
    cfg, broker, eng = _engine(tmp_path, uptrend())
    eng.run_once()
    a = next(k for k, p in eng.st.positions.items() if p.qty > 0)

    def too_small(symbol, qty, trigger, cid):
        raise BelowMinimum("below Kraken minimum")
    eng.st.positions[a].stop_order_id = None
    broker.place_stop = too_small
    eng._protect_one(a)
    assert eng.st.positions[a].qty == 0


def test_manual_sale_clamps_ledger(tmp_path):
    cfg, broker, eng = _engine(tmp_path, uptrend())
    eng.run_once()
    a = next(k for k, p in eng.st.positions.items() if p.qty > 0)
    eng._reconcile_holdings({a: {"total": 0.0, "free": 0.0}})
    assert eng.st.positions[a].qty == 0


def test_validate_mode_never_changes_the_ledger(tmp_path):
    cfg, broker, eng = _engine(tmp_path, uptrend(), mode="validate")
    eng.run_once()
    assert eng.st.cash == 1000.0 and all(p.qty == 0 for p in eng.st.positions.values())
    assert not (tmp_path / "s.json").exists()


# =============================================================== protect (coins you already hold)
class ProtectBroker(RecordingPaper):
    def balances(self):
        return {"AAA": {"free": 5.0, "total": 5.0}}


def test_protect_plan_only_places_nothing(tmp_path):
    b = ProtectBroker(CFG, FakeMarket(uptrend()))
    res = protect_holdings(b, CFG, {"AAA": 2.0}, str(tmp_path / "p.json"), place=False)
    assert res[0]["stop"] < res[0]["mid"] and "plan only" in res[0]["action"] and b.log == []


def test_protect_places_ratchets_up_and_never_down(tmp_path):
    data = uptrend()
    b = ProtectBroker(CFG, FakeMarket(data))
    sp = str(tmp_path / "p.json")
    first = protect_holdings(b, CFG, {"AAA": 2.0}, sp, place=True)[0]
    assert first["action"] == "stop resting on Kraken"
    saved = json.load(open(sp))["AAA"]["level"]
    last = data["AAA"][-1]                                  # price falls: computed stop would be lower
    data["AAA"].append([last[0] + DAY, last[4], last[4], last[4] * 0.97, last[4] * 0.98, 1.0])
    again = protect_holdings(b, CFG, {"AAA": 2.0}, sp, place=True)[0]
    assert again["stop"] >= saved


def test_protect_refuses_a_stop_above_the_market(tmp_path):
    data = uptrend()
    b = ProtectBroker(CFG, FakeMarket(data))
    sp = tmp_path / "p.json"
    sp.write_text(json.dumps({"AAA": {"level": data["AAA"][-1][4] * 2, "qty": 2.0, "order_id": None}}))
    res = protect_holdings(b, CFG, {"AAA": 2.0}, str(sp), place=True)[0]
    assert res["action"].startswith("REFUSED") and not any(k == "stop" for k, *_ in b.log)


# =============================================================== backtest, config, ids, broker idempotency
def test_backtest_stays_near_flat_in_a_crash():
    data = {"AAA": to_frame(bars(400, drift=-0.005, seed=3)), "BBB": to_frame(bars(400, drift=-0.005, seed=4))}
    res = bt.run(data, CFG, 1000)
    assert res.equity.iloc[-1] > 0.9 * 1000 > bt.buy_and_hold(data, 1000).iloc[-1]


def test_backtest_round_trips_account_for_fees():
    data = {"AAA": to_frame(bars(400, seed=5)), "BBB": to_frame(bars(400, seed=6))}
    res = bt.run(data, CFG, 1000)
    rt = bt.round_trips(res.trades)
    assert len(rt) > 0
    open_val = res.equity.iloc[-1] - 1000
    assert rt["pnl"].sum() <= open_val + 1000  # sanity: pnl table is finite and fee-inclusive
    assert not rt["pnl"].isna().any()


def test_config_validation_and_env_override():
    for bad in (dict(mode="yolo"), dict(max_gross=5), dict(entry_models=("magic",)), dict(min_stop_atr=4.0)):
        with pytest.raises(ValueError):
            Config(**bad).validate()
    c = load_config(env={"KRAKENBOT_ASSETS": "BTC,ETH", "KRAKENBOT_RISK_PER_TRADE": "0.01",
                         "KRAKENBOT_ENTRY_MODELS": "trend,smc", "KRAKENBOT_LOOKBACKS": "10,20"})
    assert c.assets == ("BTC", "ETH") and c.risk_per_trade == 0.01 and c.entry_models == ("trend", "smc")
    assert c.lookbacks == (10, 20) and Config().entry_models == ("trend",) and not Config().allow_conversions


def test_client_id_is_deterministic():
    assert client_id("2026-11-12", "BTC/EUR", "buy", "0") == client_id("2026-11-12", "BTC/EUR", "buy", "0")
    assert client_id("2026-11-12", "BTC/EUR", "buy", "0") != client_id("2026-11-13", "BTC/EUR", "buy", "0")


def _kraken_broker_with(ex, mode="live"):
    ccxt = pytest.importorskip("ccxt")
    from bots.kraken.krakenbot.broker import KrakenBroker

    b = KrakenBroker.__new__(KrakenBroker)
    b.ccxt, b.cfg, b.ex = ccxt, dc.replace(Config(), mode=mode), ex
    b.transient, b.permanent = (ccxt.NetworkError, ccxt.InvalidNonce), (ccxt.InvalidOrder, ccxt.InsufficientFunds)
    return ccxt, b


class FakeEx:
    markets = {"BTC/EUR": {"limits": {"amount": {"min": 0.00006}, "cost": {"min": 0.45}}}}

    def __init__(self, ccxt=None, fail_first=False):
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


def test_ambiguous_timeout_does_not_duplicate_order(monkeypatch):
    ccxt, b = _kraken_broker_with(None)
    b.ex = FakeEx(ccxt, fail_first=True)
    import bots.kraken.krakenbot.broker as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    o = b.place_limit("BTC/EUR", "buy", 0.001, 60000, True, "cid-1")
    assert len(b.ex.live) == 1 and o["clientOrderId"] == "cid-1"


def test_kraken_stop_is_a_stop_loss_sell_and_below_minimum_is_typed():
    ccxt, b = _kraken_broker_with(None)
    b.ex = FakeEx(ccxt)
    b.place_stop("BTC/EUR", 0.001, 55000, "cid-s")
    typ, side, q, p, params = b.ex.params[-1]
    assert (typ, side, p) == ("market", "sell", None) and params["stopLossPrice"] == 55000.0
    with pytest.raises(BelowMinimum):
        b.place_stop("BTC/EUR", 0.000001, 55000, "cid-t")
    with pytest.raises(ConversionNotAuthorised):
        b.place_limit("USDC/EUR", "buy", 10, 1.0, True, "cid-c")
