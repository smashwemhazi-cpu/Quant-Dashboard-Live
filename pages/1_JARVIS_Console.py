"""JARVIS Console - the visual front end. Streamlit auto-adds this as a page
next to dashboard.py, sharing its login session."""
import json
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Streamlit Cloud secrets -> env so the jarvis package (env-driven) sees them.
try:
    for k in ("ANTHROPIC_API_KEY", "JARVIS_DATABASE_URL", "JARVIS_IMAP_HOST",
              "JARVIS_IMAP_USER", "JARVIS_IMAP_PASSWORD"):
        if k in st.secrets and k not in os.environ:
            os.environ[k] = str(st.secrets[k])
except Exception:
    pass  # no secrets file locally - fine in demo mode

from jarvis.core import audit  # noqa: E402
from jarvis.core.config import settings  # noqa: E402
from jarvis.core.registry import agents, load_builtin_agents  # noqa: E402

st.set_page_config(page_title="JARVIS Console", page_icon="🤖", layout="wide")
ACCENT, MUTED = "#00E676", "#8892B0"

if not st.session_state.get("logged_in"):
    st.warning("Please log in on the main dashboard page first.")
    st.stop()

A = load_builtin_agents()


@st.cache_data(ttl=300, show_spinner=False)
def run(agent: str, tool: str, **kw):
    try:
        return getattr(A[agent], tool)(**kw)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


st.title("🤖 JARVIS · Operations Console")

# --- KPI strip -------------------------------------------------------------
book, pipe, exe, mac = (run("clients", "book_summary"), run("pipeline", "analyse"),
                        run("execution", "report"), run("market", "macro"))
k = st.columns(5)
k[0].metric("Client equity", f"{book.get('total_equity', 0):,.0f}", f"net flow {book.get('net_flow_30d', 0):,.0f}")
k[1].metric("At-risk clients", book.get("tiers", {}).get("at_risk", "–"), f"{book.get('active_30d_pct', 0)}% active")
k[2].metric("Weighted pipeline", f"{pipe.get('weighted_forecast', 0):,.0f}", f"{pipe.get('open_deals', 0)} open deals")
k[3].metric("Fill rate", f"{exe.get('fill_rate_pct', '–')}%", f"{exe.get('slippage_breaches', 0)} slippage breaches", delta_color="inverse")
light = mac.get("risk_light", "unknown")
k[4].metric("Macro light", {"green": "🟢", "amber": "🟡", "red": "🔴"}.get(light, "⚪") + f" {light}",
            f"VIX {mac.get('VIX', {}).get('last', '–')}")

tabs = st.tabs(["💬 Ask JARVIS", "🏦 Dealing desk", "🧪 Bot lab", "👥 Retention", "📈 Pipeline",
                "⚙️ Execution", "🌍 Markets", "🎯 Trade planner", "🗂 Tasks", "📜 Activity"])
T_CHAT, T_DEAL, T_LAB, T_RET, T_PIPE, T_EXE, T_MKT, T_PLAN, T_TASK, T_LOG = tabs

# --- Chat ------------------------------------------------------------------
with T_CHAT:
    st.caption(f"Model: `{settings.model}` · effort `{settings.effort}` · tools: "
               + ", ".join(t.name for a in A.values() for t in a.tools))
    hist = st.session_state.setdefault("jarvis_chat", [])
    for m in hist:
        st.chat_message(m["role"]).markdown(m["content"])
    allow = st.toggle("Allow JARVIS to save drafts (write actions)", value=False)
    if prompt := st.chat_input("e.g. 'Which clients should Marco call today and why?'"):
        st.chat_message("user").markdown(prompt)
        hist.append({"role": "user", "content": prompt})
        from jarvis.core.brain import Brain

        with st.chat_message("assistant"):
            status = st.status("Thinking…")
            answer = Brain().ask(hist, user=st.session_state.get("user_email", "user"),
                                 allow_writes=allow, on_step=lambda n: status.write(f"🔧 {n}"))
            status.update(label="Done", state="complete")
            st.markdown(answer)
        hist.append({"role": "assistant", "content": answer})

# --- Dealing desk ------------------------------------------------------------
with T_DEAL:
    desk = run("dealing", "desk_report")
    if "error" in desk:
        st.error(desk["error"])
    else:
        st.subheader(f"Desk report · session {desk['session']}")
        c = st.columns(5)
        c[0].metric("Revenue", f"${desk['revenue_usd']:,.0f}")
        c[1].metric("Client P&L", f"${desk['client_pnl_usd']:,.0f}")
        c[2].metric("Active traders", desk["active_traders"])
        c[3].metric("Volume", f"${desk['volume_usd'] / 1e6:,.1f}M")
        c[4].metric("Slippage cost", f"${desk['slippage_cost_usd']:,.0f}", f"{desk['slippage_episodes']} episodes",
                    delta_color="off")
        st.markdown("**Recommended actions**")
        for a in desk["recommended_actions"] or ["Nothing needs attention."]:
            st.markdown(f"- {a}")
        exp = pd.Series(desk["net_client_exposure_usd"])
        st.plotly_chart(go.Figure(go.Bar(x=exp.index, y=exp.values,
                                         marker_color=[ACCENT if v >= 0 else "#FF5252" for v in exp.values]))
                        .update_layout(height=240, title="Net client exposure by symbol (USD)", margin=dict(t=40, b=0)),
                        width="stretch")

    st.markdown("#### Slippage root cause")
    scope = st.radio("Scope", ["latest session", "all data"], horizontal=True)
    sl = run("dealing", "slippage", day="all" if scope == "all data" else "")
    if "error" not in sl:
        st.markdown("**LP scorecard** – adverse share well above 50% means the LP keeps positive slippage")
        st.dataframe(pd.DataFrame(sl["lp_scorecard"]), hide_index=True, width="stretch")
        st.markdown("**Episodes** (LP-actionable first)")
        eps = pd.DataFrame(sl["episodes"])
        if len(eps):
            eps["probable_cause"] = eps["probable_cause"].str.join("; ")
            st.dataframe(eps, hide_index=True, width="stretch")
        else:
            st.info("No slippage episodes.")
        wk = pd.DataFrame(run("dealing", "slippage_week_compare"))
        if len(wk):
            fig = go.Figure([go.Bar(name=lp, x=g["day"], y=g["cost_usd"]) for lp, g in wk.groupby("lp")])
            st.plotly_chart(fig.update_layout(barmode="group", height=260, title="Daily slippage cost by LP",
                                              margin=dict(t=40, b=0)), width="stretch")

    st.markdown("#### Traders & book")
    tr = pd.DataFrame(run("dealing", "traders", limit=500))
    if len(tr):
        st.plotly_chart(go.Figure(go.Scatter(
            x=tr["mk1"], y=tr["client_pnl"], mode="markers", text=tr["account"] + " · " + tr["recommendation"],
            marker=dict(size=(tr["trades"] ** 0.5) * 2, color=tr["toxicity"], colorscale="RdYlGn_r", showscale=True,
                        colorbar=dict(title="toxicity"))))
            .update_layout(height=340, xaxis_title="1s markout (pips, + = client wins instantly)",
                           yaxis_title="client P&L (USD)", margin=dict(t=10, b=0)), width="stretch")
        st.dataframe(tr, hide_index=True, width="stretch",
                     column_config={"toxicity": st.column_config.ProgressColumn("toxicity", min_value=0, max_value=100)})
        st.caption("B-book moves are recommendations only: they need dealing-desk and compliance sign-off "
                   "and must match your licence and conflicts-of-interest policy.")
    acc = st.text_input("One-click account report (e.g. before approving a withdrawal)")
    if acc:
        st.json(run("dealing", "account_report", account=acc.strip()))

# --- Bot lab ---------------------------------------------------------------------
with T_LAB:
    st.caption("Upload a cTrader log (backtest or live) containing RLOG/RLOG2 lines from the scalper. "
               "Without an upload the synthetic demo log is used.")
    up = st.file_uploader("cBot log", type=["txt", "log", "csv"])
    if up is not None:
        from jarvis.agents.botlab import analyse, parse_rlog

        lab = analyse(parse_rlog(up.read().decode(errors="ignore")))
    else:
        lab = run("botlab", "analyse_log")
    if "error" in lab:
        st.error(lab["error"])
    else:
        c = st.columns(5)
        c[0].metric("Trades", lab["trades"])
        c[1].metric("Expectancy (R)", lab["expectancy_R"], f"95% CI {lab['expectancy_95ci']}", delta_color="off")
        c[2].metric("P(edge > 0)", lab["prob_edge_positive"])
        c[3].metric("Max DD (R)", lab["max_drawdown_R"])
        c[4].metric("Profit factor", lab["profit_factor"])
        for v in lab["verdict"]:
            st.warning(v)
        h = pd.DataFrame(lab["by_hour"])
        st.plotly_chart(go.Figure(go.Bar(x=h["hour"], y=h["expectancy"], text=h["trades"],
                                         marker_color=[ACCENT if v >= 0 else "#FF5252" for v in h["expectancy"]]))
                        .update_layout(height=260, title="Expectancy by entry hour (UTC) · labels = trades",
                                       margin=dict(t=40, b=0)), width="stretch")
        c1, c2 = st.columns(2)
        c1.markdown("**By exit**")
        c1.dataframe(pd.DataFrame(lab["by_exit"]), hide_index=True, width="stretch")
        c2.markdown("**TP sweep (MFE replay – upper bound, re-backtest before using)**")
        c2.dataframe(pd.DataFrame(lab["tp_sweep_mfe_replay"]), hide_index=True, width="stretch")
        st.caption(f"Trades scratched after reaching 1R: {lab['breakeven_scratched_after_1R']} · "
                   f"MFE quantiles (R): {lab['mfe_quantiles_R']}"
                   + (f" · costs = {lab['cost_share_of_stop_pct']}% of the stop" if "cost_share_of_stop_pct" in lab else ""))
    ev = run("botlab", "events_param")
    st.text_input("cBot 'Events UTC' parameter from the calendar (next 60 days, high impact)",
                  ev.get("param", "") or "(no upcoming events in the loaded calendar)")

# --- Retention -------------------------------------------------------------
with T_RET:
    min_s = st.slider("Minimum risk score", 0, 100, 35)
    rows = run("clients", "at_risk", min_score=min_s, limit=100)
    if isinstance(rows, list) and rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, width="stretch", hide_index=True,
                     column_config={"score": st.column_config.ProgressColumn("score", min_value=0, max_value=100)})
        by_mgr = df.groupby("account_manager").size()
        st.plotly_chart(go.Figure(go.Bar(x=by_mgr.index, y=by_mgr.values, marker_color=ACCENT))
                        .update_layout(height=260, title="Flagged clients by manager", margin=dict(t=40, b=0)),
                        width="stretch")
    else:
        st.info(rows if isinstance(rows, dict) else "No clients above this score.")

# --- Pipeline --------------------------------------------------------------
with T_PIPE:
    if "error" in pipe:
        st.error(pipe["error"])
    else:
        stages = {k: v for k, v in pipe["by_stage"].items() if k != "lost"}
        st.plotly_chart(go.Figure(go.Funnel(y=list(stages), x=list(stages.values()), marker_color=ACCENT))
                        .update_layout(height=320, margin=dict(t=10, b=0)), width="stretch")
        c1, c2 = st.columns(2)
        c1.markdown("**Stale deals – follow up**")
        c1.dataframe(pd.DataFrame(pipe["stale_deals"]), hide_index=True, width="stretch")
        c2.markdown("**Lead sources by win rate** (where to source new traders/IBs)")
        c2.dataframe(pd.DataFrame(pipe["best_sources"]), hide_index=True, width="stretch")

# --- Execution -------------------------------------------------------------
with T_EXE:
    if "error" in exe:
        st.error(exe["error"])
    else:
        c = st.columns(4)
        c[0].metric("Orders", exe["orders"])
        c[1].metric("Avg slippage (pips)", exe["avg_slippage_pips"])
        c[2].metric("p95 slippage (pips)", exe["p95_slippage_pips"])
        c[3].metric("p95 latency (ms)", exe["p95_latency_ms"])
        fails = pd.DataFrame(exe["failures_by_cause"]).T.reset_index(names="cause")
        st.markdown("**Failed orders by root cause**")
        st.dataframe(fails, hide_index=True, width="stretch")

# --- Markets ---------------------------------------------------------------
with T_MKT:
    tick = st.text_input("Watchlist (Yahoo tickers)", "EURUSD=X,GBPUSD=X,USDJPY=X,XAUUSD=X,^GSPC,BTC-USD")
    if st.button("Analyse regime"):
        res = run("market", "regime", tickers=tick)
        st.dataframe(pd.DataFrame(res).T, width="stretch")
    st.json(mac, expanded=False)

# --- Trade planner ---------------------------------------------------------
with T_PLAN:
    st.caption("Plans only – nothing is sent to a platform. Use a live quote from your terminal. "
               "Personal-account-dealing rules may apply.")
    with st.form("plan"):
        c = st.columns(4)
        tkr = c[0].text_input("Yahoo ticker (for ATR)", "EURUSD=X")
        side = c[1].selectbox("Side", ["buy", "sell"])
        eq = c[2].number_input("Equity", 0.0, value=10000.0)
        sod = c[3].number_input("Start-of-day equity", 0.0, value=10000.0)
        c = st.columns(4)
        bid = c[0].number_input("Bid", 0.0, value=1.0800, format="%.5f")
        ask = c[1].number_input("Ask", 0.0, value=1.0801, format="%.5f")
        pip = c[2].number_input("Pip size", 0.0, value=0.0001, format="%.5f")
        rr = c[3].number_input("Reward:risk", 0.5, value=2.0)
        if st.form_submit_button("Build plan"):
            try:
                plan = A["trading"].plan_trade(tkr, side, eq, sod, bid, ask, pip, reward_risk=rr)
                (st.success if plan["approved"] else st.error)(
                    "APPROVED" if plan["approved"] else "BLOCKED: " + "; ".join(plan["reasons"]))
                st.json(plan)
            except Exception as e:
                st.error(f"{type(e).__name__}: {e}")
    st.caption(f"Limits: {settings.risk_per_trade_pct}% risk/trade · {settings.max_daily_loss_pct}% daily loss · "
               f"{settings.max_open_risk_pct}% open risk · stop {settings.stop_atr_mult}×ATR · "
               f"trail {settings.trail_atr_mult}×ATR · max slippage {settings.max_slippage_pips} pips")

# --- Tasks -----------------------------------------------------------------
with T_TASK:
    t = run("ops", "tasks")
    if "error" in t:
        st.error(t["error"])
    else:
        st.markdown(f"**Overdue ({len(t['overdue'])})**")
        st.dataframe(pd.DataFrame(t["overdue"]), hide_index=True, width="stretch")
        st.markdown(f"**Due this week ({len(t['this_week'])})** · {t['later']} later")
        st.dataframe(pd.DataFrame(t["this_week"]), hide_index=True, width="stretch")

# --- Activity / audit --------------------------------------------------------
with T_LOG:
    rows = audit.recent(100)
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
