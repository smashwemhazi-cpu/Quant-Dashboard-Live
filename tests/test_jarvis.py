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
