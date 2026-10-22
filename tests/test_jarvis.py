import numpy as np
import pandas as pd
import pytest

from jarvis.agents.clients import churn_score
from jarvis.agents.execution import analyse_executions
from jarvis.agents.market import regime_from_closes
from jarvis.agents.pipeline import analyse_pipeline
from jarvis.core import risk
from jarvis.core.registry import load_builtin_agents


def test_position_size_risks_exact_pct():
    units = risk.position_size(10_000, 1.0, 0.0050)  # risk 100 over 50 pips
    assert units == pytest.approx(20_000)
    assert risk.position_size(10_000, 1.0, 0) == 0


def test_trailing_stop_only_tightens():
    assert risk.trailing_stop("buy", 1.0950, 1.1000, 0.0010, 2) == pytest.approx(1.0980)
    assert risk.trailing_stop("buy", 1.0990, 1.1000, 0.0010, 2) == 1.0990
    assert risk.trailing_stop("sell", 1.1050, 1.1000, 0.0010, 2) == pytest.approx(1.1020)


def test_plan_blocks_wide_spread_and_kill_switch():
    q = risk.Quote(1.0800, 1.0810, 0.0001)  # 10 pip spread
    plan = risk.plan_order("buy", q, 0.0060, equity=9_700, start_of_day_equity=10_000)
    assert not plan.approved
    assert any("spread" in r for r in plan.reasons)
    assert any("kill switch" in r for r in plan.reasons)


def test_plan_approves_normal_trade_and_stops_are_correct_side():
    q = risk.Quote(1.0800, 1.0801, 0.0001)
    p = risk.plan_order("sell", q, 0.0060, equity=10_000, start_of_day_equity=10_000)
    assert p.approved and p.stop > p.entry > p.take_profit
    assert p.risk_amount <= 10_000 * 0.005 + 1e-6


def test_invalid_quote_rejected():
    with pytest.raises(ValueError):
        risk.plan_order("buy", risk.Quote(1.1, 1.0, 0.0001), 0.001, 1000, 1000)


def test_regime_detects_uptrend():
    close = pd.Series(np.exp(np.linspace(0, 0.5, 300)) * (1 + 0.001 * np.sin(np.arange(300))))
    assert regime_from_closes(close)["trend"] == "up"


def test_churn_flags_inactive_withdrawing_client():
    today = pd.Timestamp("2026-01-31")
    df = pd.DataFrame([
        dict(client_id="A", last_login="2026-01-30", last_trade="2026-01-30", equity=5000, deposits_30d=1000,
             deposits_prev_30d=1000, withdrawals_30d=0, trades_30d=30, trades_prev_30d=30, open_tickets=0),
        dict(client_id="B", last_login="2025-12-01", last_trade="2025-11-20", equity=5000, deposits_30d=0,
             deposits_prev_30d=2000, withdrawals_30d=4000, trades_30d=0, trades_prev_30d=40, open_tickets=1),
    ])
    out = churn_score(df, today).set_index("client_id")
    assert out.loc["B", "score"] > 80 > 20 > out.loc["A", "score"]
    assert str(out.loc["B", "tier"]) == "at_risk"


def test_pipeline_funnel_excludes_lost():
    df = pd.DataFrame({"deal_id": range(4), "name": list("abcd"), "source": ["a", "a", "b", "b"],
                       "stage": ["lead", "funded", "lost", "lost"], "value": [100] * 4,
                       "owner": ["x"] * 4, "last_activity": ["2026-01-01"] * 4})
    r = analyse_pipeline(df, pd.Timestamp("2026-01-10"))
    assert r["stage_conversion_pct"]["lead->qualified"] == 50.0
    assert r["stage_conversion_pct"]["kyc->funded"] == 100.0


def test_slippage_sign_is_adverse_positive():
    df = pd.DataFrame({"ts": ["2026-01-01"] * 2, "symbol": ["EURUSD"] * 2, "side": ["buy", "sell"],
                       "requested_price": [1.1, 1.1], "fill_price": [1.1002, 1.0998], "pip_size": [1e-4] * 2,
                       "status": ["filled"] * 2, "error_code": ["", ""], "latency_ms": [10, 20]})
    assert analyse_executions(df)["avg_slippage_pips"] == pytest.approx(2.0)


def test_registry_exposes_write_gated_tools():
    tools = {t.name: t for a in load_builtin_agents().values() for t in a.tools}
    assert tools["ops__draft_note"].writes and not tools["clients__at_risk"].writes


# ---------------------------------------------------------------- dealing desk & bot lab
from jarvis.agents.botlab import analyse, parse_rlog  # noqa: E402
from jarvis.agents.dealing import classify_traders, lp_scorecard, prepare, slippage_episodes  # noqa: E402


def _fill(ts, lp, slip, acc="A1", side="buy", age=50, mk1=0.0, hold=600.0, pnl=0.0):
    sign = 1 if side == "buy" else -1
    return dict(ts_request=ts, ts_fill=ts, account=acc, group="std", equity=1000, symbol="EURUSD", side=side,
                order_type="market", units=10000, request_price=1.1, fill_price=1.1 + sign * slip * 1e-4,
                pip_size=1e-4, quote_to_usd=1.0, lp=lp, quote_age_ms=age, mid_fill=1.1,
                mid_1s=1.1 + sign * mk1 * 1e-4, mid_5s=1.1, mid_60s=1.1, markup_usd=1, commission_usd=1,
                client_pnl_usd=pnl, hold_seconds=hold)


def test_lp_specific_episode_found_and_ranked_first():
    t0 = pd.Timestamp("2026-01-05 09:00")
    rows = [_fill(t0 + pd.Timedelta(minutes=m), "LP-B", 2.0) for m in range(0, 10, 2)]
    rows += [_fill(t0 + pd.Timedelta(minutes=m), "LP-A", 0.0) for m in range(0, 10, 2)]
    rows += [_fill(t0 + pd.Timedelta(hours=3, minutes=m), "LP-A", 1.5) for m in range(3)]  # news window
    ev = pd.DataFrame({"ts": [t0 + pd.Timedelta(hours=3)], "event": ["CPI"], "impact": ["high"]})
    eps = slippage_episodes(prepare(pd.DataFrame(rows)), ev)
    assert eps[0]["lp"] == "LP-B" and eps[0]["raise_with_lp"]
    assert eps[0]["probable_cause"][0].startswith("LP-specific")
    assert any(e["lp"] == "LP-A" and not e["raise_with_lp"] for e in eps)


def test_asymmetric_lp_slippage_visible():
    t0 = pd.Timestamp("2026-01-05 09:00")
    rows = [_fill(t0, "LP-X", 1.0)] * 9 + [_fill(t0, "LP-X", -1.0)]
    assert lp_scorecard(prepare(pd.DataFrame(rows)))[0]["adverse_share_of_slipped_pct"] == 90.0


def test_latency_arb_is_toxic_and_loser_needs_significance():
    t0 = pd.Timestamp("2026-01-05 09:00")
    rng = np.random.default_rng(0)
    rows = [_fill(t0, "L", 0, acc="ARB", mk1=1.0, hold=5, pnl=5) for _ in range(40)]
    rows += [_fill(t0, "L", 0, acc="LOSER", pnl=float(rng.normal(-20, 10))) for _ in range(40)]
    rows += [_fill(t0, "L", 0, acc="NOISE", pnl=float(rng.normal(-1, 50))) for _ in range(40)]
    t = classify_traders(prepare(pd.DataFrame(rows)), pd.DataFrame(columns=["ts", "event", "impact"]))
    assert t.loc["ARB", "profile"] == "toxic"
    assert t.loc["LOSER", "recommendation"] == "B-book candidate"
    assert t.loc["NOISE", "recommendation"] != "B-book candidate"


def test_rlog_parser_and_verdicts():
    lines = ["RLOG2,id,side,entryUtc,exitUtc,hours,R,mfeR,maeR,exit,hour,stopPips,spreadPips,slipPips,costPips,net"]
    for i in range(50):
        r = -1.0 if i % 2 else -0.2
        lines.append(f"x | RLOG2,{i},Buy,2026-01-{1 + i % 28:02d} 08:00,2026-01-01 09:00,1,{r},0.5,1,StopLoss,8,10,2,0.1,0.5,-5")
    out = analyse(parse_rlog("\n".join(lines)))
    assert out["trades"] == 50 and out["expectancy_R"] == pytest.approx(-0.6)
    assert "negative with 95% confidence" in out["verdict"][-1]
