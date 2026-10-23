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


def test_predictor_finds_no_edge_in_random_walk_but_finds_planted_signal():
    from jarvis.agents.predictor import walk_forward
    rng = np.random.default_rng(1)
    n = 1400
    idx = pd.bdate_range("2020-01-01", periods=n)
    mk = lambda c: pd.DataFrame({"open": c, "high": c * 1.005, "low": c * 0.995, "close": c}, index=idx)  # noqa: E731
    assert walk_forward(mk(100 * np.exp(np.cumsum(rng.normal(0, .01, n)))))["edge"] is False
    r, e = np.zeros(n), rng.normal(0, .01, n)
    for i in range(1, n):
        r[i] = .35 * r[i - 1] + e[i]
    assert walk_forward(mk(100 * np.exp(np.cumsum(r))))["edge"] is True


def test_channel_chunking_respects_limits():
    from donna.channels import chunks
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(300))
    parts = chunks(text, 4096)
    assert all(len(p) <= 4096 for p in parts) and "".join(parts) == text


def test_short_tradingview_history_falls_back_to_yahoo(monkeypatch):
    from jarvis.adapters import market as m
    few = pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=5), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0})
    many = pd.DataFrame({"date": pd.bdate_range("2020-01-01", periods=400), "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0})
    monkeypatch.setattr(m, "_from_export", lambda i: pd.DataFrame())
    monkeypatch.setattr(m, "_from_webhook", lambda i: few)
    monkeypatch.setattr(m, "_from_yahoo", lambda i, p: many)
    df, src = m.daily_bars(m.WATCHLIST[0])
    assert src.startswith("Yahoo") and len(df) == 400
    monkeypatch.setattr(m, "_from_export", lambda i: many)
    assert m.daily_bars(m.WATCHLIST[0])[1] == "TradingView"


def test_backoffice_ingests_ctrader_and_syntellicore_exports(tmp_path, monkeypatch):
    from jarvis.agents import backoffice as bo

    box = tmp_path / "inbox"
    box.mkdir()
    monkeypatch.setenv("JARVIS_INBOX_DIR", str(box))
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    cols = ["Trader Account", "Group", "Deposit currency", "Balance", "Credit", "Equity", "Margin",
            "Net Unrealized PnL (A-Book)", "Net Unrealized PnL (B-Book)"]
    day1 = [[1, "PAMM", "USD", 9000, 0, 9000, 0, 0, 0], [2, "Std", "USD", 400, 300, 320, 290, -80, 0],
            [3, "Std", "USD", 0, 0, 0, 0, 0, 0], [4, "Std", "USD", 1000, 0, 1000, 0, 0, 0]]
    pd.DataFrame(day1, columns=cols).to_csv(box / "cta_EndOfDayReport_2026-10-21_21-00.csv", index=False)
    (box / "Performance-Overview-KPIs-2026-10-21.csv").write_text(
        '*|id|*,*|caption_3|*,*|value|*\n"1","Leads","141"\n"2","FTDs","14"\n"6","Deposits ($)","32,201.21"\n')
    (box / "Customer-Documents-2026-10-21.csv").write_bytes(
        ('*|date|*,*|customer_no|*,*|assigned_to|*,*|first_name|*,*|last_name|*,*|type|*,*|document_status|*,'
         '*|verification_date|*,*|expiration_date|*\n'
         '"2026-10-01 10:00:00.0","CU1","0","QA","Automation","wire","Pending","1900-01-01 00:00:00.0","1900-01-01 00:00:00.0"\n'
         '"2026-10-01 10:00:00.0","CU2","0","Zo\xeb","A","wire","Pending","1900-01-01 00:00:00.0","1900-01-01 00:00:00.0"\n'
         '"2026-10-01 10:00:00.0","CU3","0","B","C","Utility Bill","Rejected","1900-01-01 00:00:00.0","1900-01-01 00:00:00.0"\n'
         '"2026-10-02 10:00:00.0","CU3","0","B","C","Utility Bill","Verified","2026-10-02 00:00:00.0","1900-01-01 00:00:00.0"\n'
         '"2026-10-01 10:00:00.0","CU4","0","D","E","Selfie","Rejected","1900-01-01 00:00:00.0","1900-01-01 00:00:00.0"\n'
         ).encode("cp1252"))
    res = bo.BackOfficeAgent().ingest()["files"]
    assert sorted(r["kind"] for r in res) == ["docs", "eod", "kpi"] and not list(box.glob("*.csv"))

    b = bo.book_summary()
    assert b["funded"] == 3 and b["margin_alerts"][0]["account"] == "2" and b["credit_heavy"][0]["account"] == "2"
    assert bo.kpi_summary()["kpis"]["Deposits ($)"] == pytest.approx(32201.21)
    q = bo.kyc_summary(pd.Timestamp("2026-10-22"))
    assert q["pending_customers"] == 1 and q["test_pending"] == 1      # QA Automation excluded
    assert q["rejected_open"] == {"CU4": "Selfie"}                      # CU3 re-uploaded and verified

    day2 = [r[:] for r in day1]
    day2[2][3:6] = [500, 0, 500]          # newly funded
    day2[3][3:6] = [0, 0, 0]              # emptied
    day2[0][5] = 6000                     # -33% equity
    pd.DataFrame(day2, columns=cols).to_csv(box / "cta_EndOfDayReport_2026-10-22_21-00.csv", index=False)
    bo.BackOfficeAgent().ingest()
    b = bo.book_summary()
    assert b["newly_funded"] == ["3"] and b["emptied"] == ["4"] and b["drawdowns"][0]["account"] == "1"
    text = "\n".join(bo.report_lines())
    assert "Zo" not in text and "Automation" not in text               # never names in reports
    assert "retention call" in text and "near stop-out" in text


def test_backoffice_missing_data_says_no_data(tmp_path, monkeypatch):
    from jarvis.agents import backoffice as bo

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "empty.db"))
    assert all("NO DATA" in line for line in bo.report_lines() if line and ":" in line[:12])


def test_backoffice_activity_and_price_streams(tmp_path, monkeypatch):
    from jarvis.agents import backoffice as bo

    box = tmp_path / "inbox"
    box.mkdir()
    monkeypatch.setenv("JARVIS_INBOX_DIR", str(box))
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    acc = pd.DataFrame({"Account": [1, 2, 3], "Name": ["A", "B", "C"], "Equity (USD)": ["USD 300.00", "USD 0.00", "USD 2 000.00"],
                        "Deposit (USD)": ["USD 0.00", "USD 2 037.38", "USD 0.00"],
                        "Withdrawal (USD)": ["USD 0.00", "USD 2 037.38", "USD 0.00"],
                        "Closed positions": [5, 0, 2], "Total Deals": [10, 0, 4], "Rejected deals": [0, 0, 1],
                        "Traded Volume (USD)": ["USD 781 491.84", "USD 0.00", "USD 24 421.62"],
                        "Realized Swaps (USD)": ["USD -2.64", "USD 0.00", "USD 0.00"],
                        "Realized Commissions (USD)": ["USD -0.96", "USD 0.00", "USD 0.00"],
                        "Realised net P&L (USD)": ["USD -82.38", "USD 0.00", "USD 21.65"]})
    acc.to_excel(box / "cta_ActiveAccounts_2026-10-22_22-14.xlsx", index=False)
    ps = lambda xau: pd.DataFrame({"Valid": "YES", "Enabled": [True, True, False], "Symbol": ["XAUUSD.mp", "XAUEUR.mp", "EURUSD"],  # noqa: E731
                                   "Asset class": "Metals", "Markup Type": "Points", "Spread": [xau, "No Quotes", 0.1],
                                   "Markup Bid": [12, 10, 0], "Markup Ask": [12, 10, 0]})
    ps(3.7).to_excel(box / "cta_PriceStreams_Standard_Global__2026-10-21_22-10.xlsx", index=False)
    ps(9.0).to_excel(box / "cta_PriceStreams_Standard_Global__2026-10-22_22-10.xlsx", index=False)
    assert all(r["status"] == "STORED" for r in bo.BackOfficeAgent().ingest()["files"])

    t = bo.activity_summary()
    assert t["volume"] == pytest.approx(805913.46) and t["rejected"] == 1
    assert t["high_turnover"][0]["account"] == "1" and t["in_out_no_trading"] == ["2"]
    s = bo.streams_summary()
    g = s["groups"]["Standard Global"]
    assert g["enabled"] == 2 and g["no_quotes"] == ["XAUEUR.mp"]
    assert s["widened"] == [("Standard Global", "XAUUSD.mp", 3.7, 9.0)]
    text = "\n".join(bo.report_lines())
    assert "AML review" in text and "NO QUOTES" in text and "Name" not in text


# ---------------------------------------------------------------- personal journal, bots, assistant
def test_personal_statement_and_bot_log(tmp_path, monkeypatch):
    from datetime import datetime

    from jarvis.agents import backoffice as bo, personal

    box = tmp_path / "inbox"
    box.mkdir()
    monkeypatch.setenv("JARVIS_INBOX_DIR", str(box))
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("JARVIS_CTRADER_UTC_OFFSET", "4")
    nets = [2.0, 3.0, -1.0, 2.5, -40.0, 2.0, 1.5]
    bal, rows = 300.0, []
    for i, n in enumerate(nets):
        bal += n + (250 if i == 5 else 0)  # deposit before trade 6
        rows.append({"Symbol": "XAUUSD.mp", "Opening direction": "Buy", "Closing time": f"0{1 + i}/10/2026 10:0{i}:00.000",
                     "Entry price": 4000, "Closing price": 4001, "Closing Quantity": 0.01, "Closing volume": 1,
                     "Net $": n, "Balance $": bal})
    pd.DataFrame(rows).to_excel(box / "cT_6000013_2026-10-22_23-40.xlsx", index=False, sheet_name="Records")
    (box / "2026-10-22_23-49_-_MicroLotBreakoutBot_Instance_42.log").write_text(
        "22/10/2026 15:44:14.342 Info | CLOSED MLB-LD Sell | reason=Closed | net=7.77 | equity=414.73\n\n"
        "22/10/2026 15:18:26.262 Info | FILLED MLB-LD Sell @ 4060.11 | SL 4067.27 | TP 4045.4\n"
        "22/10/2026 15:15:00.308 Trade | → Placing Limit Order to Sell 1 XAUUSD.mp (Price: 4059.98) SUCCEEDED, PendingOrder OID9030\n"
        "22/10/2026 15:10:00.308 Trade | → Placing Limit Order to Sell 1 XAUUSD.mp (Price: 4059.98) FAILED with error \"NotEnoughMoney\"\n"
        "22/10/2026 02:37:42.119 Info | BUILD MLB-2026-10-16c\n"
        "22/10/2026 02:37:39.187 Info | CBot instance [MicroLotBreakoutBot, XAUUSD.mp, m5] started.\n", encoding="utf-8")
    kinds = sorted(r["kind"] for r in bo.BackOfficeAgent().ingest()["files"])
    assert kinds == ["botlog", "statement"]
    j = personal.journal()
    assert j["trades"] == 7 and j["account"] == "6000013" and j["cash_flows"] == pytest.approx(250)
    assert len(j["big_losses"]) == 1 and j["big_losses"][0]["net"] == -40.0
    assert j["net_without_big_losses"] == pytest.approx(10.0)
    assert j["quick_reentries"]["trades"] == 0
    b = personal.bots(now=datetime(2026, 10, 23, 16, 0))
    assert b[0]["state"] == "running" and b[0]["closed"] == 1 and b[0]["failures"] == 1 and b[0]["build"] == "MLB-2026-10-16c"
    text = "\n".join(personal.report_lines())
    assert "lost more than 3%" in text and "NotEnoughMoney" in text
    bo.BackOfficeAgent().ingest()  # re-uploading the same files must not duplicate
    assert personal.journal()["trades"] == 7


def test_assistant_masks_triages_and_creates_tasks(tmp_path, monkeypatch):
    import email.message

    from jarvis.agents import assistant as a

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    assert a.mask("call +27 82 555 1234, ID 9001015009087, a@b.co.za, acc 6000013") == \
        "call [number], ID [number], [email], acc 6000013"

    msg = email.message.EmailMessage()
    msg["Subject"], msg["From"], msg["Message-ID"] = "Withdrawal still pending", "Client <x@client.com>", "<m1@x>"
    msg["Date"] = "Wed, 22 Oct 2026 08:00:00 +0000"
    msg.set_content("My withdrawal for account 6000013 is late, card 4111 1111 1111 1111. Please fix today.")
    raw = msg.as_bytes()

    class FakeIMAP:
        def __init__(self, host): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, u, p): pass
        def select(self, box, readonly): assert readonly
        def search(self, *a): return "OK", [b"1"]
        def fetch(self, num, what):
            assert "PEEK" in what
            return "OK", [(b"1", b"Message-ID: <m1@x>\r\n\r\n" if "HEADER" in what else raw)]

    seen = {}

    class FakeClient:
        class beta:
            class messages:
                @staticmethod
                def create(**kw):
                    seen["content"] = kw["messages"][0]["content"]
                    assert kw["output_config"]["format"]["type"] == "json_schema"
                    out = {"category": "payments", "urgency": "high", "summary": "Client withdrawal late",
                           "needs_reply": True, "tasks": [{"title": "Chase withdrawal with payments", "due": "2026-10-22",
                                                           "priority": "high"}]}
                    blk = type("B", (), {"type": "text", "text": __import__("json").dumps(out)})
                    return type("R", (), {"stop_reason": "end_turn", "content": [blk]})

    monkeypatch.setattr(a.imaplib, "IMAP4_SSL", FakeIMAP)
    import dataclasses
    monkeypatch.setattr(a, "settings", dataclasses.replace(a.settings, imap_host="imap.gmail.com",
                                                           imap_user="me@vertex1.example", imap_password="app-pass"))
    r = a.triage_inbox(client=FakeClient())["processed"]
    assert r[0]["urgency"] == "high" and r[0]["task_ids"]
    assert "4111" not in seen["content"] and "x@client.com" not in seen["content"] and "client.com" in seen["content"]
    assert a.triage_inbox(client=FakeClient())["processed"] == []          # same email never re-processed
    t = a.list_tasks()
    assert t[0]["title"] == "Chase withdrawal with payments" and t[0]["due"] == "2026-10-22"
    assert a.complete_task(t[0]["id"]) and not a.list_tasks()
    assert a.add_task("bad date", "tomorrow-ish") and a.list_tasks()[0]["due"] is None


def test_calendar_from_ics(monkeypatch):
    from datetime import date

    from jarvis.agents import assistant as a

    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n"
           "BEGIN:VEVENT\r\nUID:1\r\nDTSTART:20261023T060000Z\r\nDTEND:20261023T063000Z\r\nSUMMARY:LP call\r\n"
           "RRULE:FREQ=DAILY;COUNT=5\r\nEND:VEVENT\r\n"
           "BEGIN:VEVENT\r\nUID:2\r\nDTSTART;VALUE=DATE:20261023\r\nSUMMARY:Month-end\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    monkeypatch.setenv("JARVIS_GCAL_ICS_URL", "https://calendar.google.com/x/basic.ics")
    monkeypatch.setattr(a, "LOCAL_TZ", __import__("zoneinfo").ZoneInfo("Etc/GMT-4"))
    monkeypatch.setattr(a.requests, "get", lambda url, timeout: type("R", (), {
        "content": ics.encode(), "raise_for_status": lambda self: None})())
    ev = a.calendar_events(date(2026, 10, 24))["events"]          # recurring instance on the next day
    assert ev == [{"title": "LP call", "all_day": False, "start": "2026-10-24 10:00", "end": "10:30", "location": ""}]
    assert [e["title"] for e in a.calendar_events(date(2026, 10, 23))["events"]] == ["Month-end", "LP call"]


# ---------------------------------------------------------------- compliance & growth
def _fake_client(payloads):
    """Anthropic-like client returning the given JSON payloads in order."""
    import json as _j

    class C:
        class beta:
            class messages:
                @staticmethod
                def create(**kw):
                    assert kw["output_config"]["format"]["type"] == "json_schema"
                    blk = type("B", (), {"type": "text", "text": _j.dumps(payloads.pop(0))})
                    return type("R", (), {"stop_reason": "end_turn", "content": [blk]})
    return C()


def test_compliance_scan_cases_and_obligations(tmp_path, monkeypatch):
    from jarvis.agents import compliance as co

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setattr(co.bo, "activity_summary", lambda: {
        "asof": "2026-10-22", "in_out_no_trading": ["79"], "high_turnover": [{"account": "3", "x": 2222.0}],
        "rejections": [], "volume": 1e6})
    monkeypatch.setattr(co.bo, "kyc_summary", lambda: {"pending_over_2d": ["CU21"], "rejected_open": {"CU4": "Selfie"},
                                                      "expiring": [], "unassigned_pct": 92.0, "oldest_pending_days": 34})
    monkeypatch.setattr(co.bo, "book_summary", lambda: {
        "asof": "2026-10-22", "margin_alerts": [{"account": "3", "grp": "Std", "level": 111.0, "equity": 321}],
        "top_account": {"account": "10", "share_pct": 53.0}, "credit_heavy": [], "drawdowns": []})
    monkeypatch.setattr(co.bo, "streams_summary", lambda: {"error": "NO DATA"})
    monkeypatch.setattr(co.bo, "kpi_summary", lambda: {"kpis": {"Volume": 0}})
    r = co.scan()
    assert r["new_cases"] == 8 and co.scan()["new_cases"] == 0          # never logged twice
    rules = {c["rule"] for c in co.list_cases()}
    assert {"AML_IN_OUT_NO_TRADE", "RISK_STOPOUT", "RISK_CONCENTRATION", "DATA_VOLUME_SYNC"} <= rules
    high = [c for c in co.list_cases() if c["severity"] == "high"]
    assert len(high) == 2 and all(c["due"] for c in high)
    cid = high[0]["id"]
    assert co.update_case(cid, "closed", "documents received") and co.get_case(cid)["resolution"] == "documents received"
    assert [h["action"] for h in co.get_case(cid)["log"]] == ["opened", "closed"]
    assert {k["kri"]: k["status"] for k in co.kris()}["Largest client share of equity %"] == "RED"
    oid = co.add_obligation("Quarterly return", "2026-01-31", "monthly")
    assert co.complete_obligation(oid) == "done - next due 2026-02-28"   # month-end rolls safely
    with pytest.raises(ValueError):
        co.add_obligation("x", "31/01/2026")


def test_promo_review_and_discord_case_commands(tmp_path, monkeypatch):
    from donna import discord_bot as b
    from jarvis.agents import compliance as co

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    r = co.review_promotion("Guaranteed 20% a month!", _fake_client([{
        "verdict": "reject", "issues": [{"phrase": "Guaranteed", "problem": "profit guarantee", "fix": "remove"}],
        "compliant_version": "Trade gold with tight spreads. CFDs are complex..."}]))
    assert r["verdict"] == "reject" and r["issues"][0]["phrase"] == "Guaranteed"
    cid = co.open_case("AML", "AML_TEST", "high", "#1", "test finding")
    assert b.handle(f"!close {cid}").startswith("A case can only be closed")
    assert b.handle(f"!escalate {cid} sent to MLRO") == f"Case #{cid} -> escalated"
    assert "escalated" in b.handle(f"!case {cid}") and "#1" in b.handle("!cases")
    assert "Obligation #1 added" == b.handle("!oblig add FIC return | 2026-11-15 | monthly")


def test_leads_export_detected_and_tasks(tmp_path, monkeypatch):
    from datetime import datetime

    from jarvis.agents import backoffice as bo, growth

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    d = pd.DataFrame({"*|customer_no|*": ["CU1", "CU2", "CU3", "CU4"],
                      "*|registration_date|*": ["2026-10-23 09:00", "2026-10-15 10:00", "2026-10-14 10:00", "2026-10-22 08:00"],
                      "*|lead_source|*": ["Google", "IB-77", "Google", ""], "*|lead_status|*": ["New", "No Answer", "Converted", "New"],
                      "*|assigned_to|*": ["0", "Roy", "Roy", "0"], "*|ftd_amount|*": ["0", "0", "250", "0"]})
    p = tmp_path / "Leads-2026-10-23.csv"
    d.to_csv(p, index=False)
    assert bo.ingest_file(p)["kind"] == "leads"
    s = growth.leads_summary(now=datetime(2026, 10, 23, 12, 0))
    assert s["leads"] == 4 and s["ftd"] == 1 and s["unassigned"] == 2 and s["new_24h"] == 1
    assert s["stale"] == 1 and s["stale_ids"] == ["CU2"]
    assert {x["source"]: x["conv"] for x in s["by_source"]}["Google"] == 50.0
    ids = growth.lead_tasks(s)
    assert len(ids) == 2 and growth.lead_tasks(s) == []                    # one task per batch per day
    posts = {"posts": [{"channel": "x", "text": "Gold fell 1.9% as yields rose. CFDs are complex..."}]}
    review = {"verdict": "approve", "issues": [], "compliant_version": ""}
    drafts = growth.draft_posts(_fake_client([posts, review]), brief="Gold -1.9%")
    assert drafts[0]["verdict"] == "approve" and "compliance OK" in growth.drafts_text(drafts)


def test_lead_columns_do_not_misread_similar_names():
    from jarvis.agents import growth

    m = growth.lead_columns(pd.DataFrame(columns=["customer_no", "sales_status", "date_of_birth", "registration_date"]))
    assert m["owner"] is None and m["status"] == "sales_status" and m["created"] == "registration_date"
    m = growth.lead_columns(pd.DataFrame(columns=["customer_no", "lead_status", "date_of_birth", "tribe", "IB"]))
    assert m["source"] == "IB" and m["created"] is None
