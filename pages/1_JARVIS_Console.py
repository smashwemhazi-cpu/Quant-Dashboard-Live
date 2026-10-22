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

tabs = st.tabs(["💬 Ask JARVIS", "👥 Retention", "📈 Pipeline", "⚙️ Execution", "🌍 Markets",
                "🎯 Trade planner", "🗂 Tasks", "📜 Activity"])

# --- Chat ------------------------------------------------------------------
with tabs[0]:
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

# --- Retention -------------------------------------------------------------
with tabs[1]:
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
with tabs[2]:
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
with tabs[3]:
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
with tabs[4]:
    tick = st.text_input("Watchlist (Yahoo tickers)", "EURUSD=X,GBPUSD=X,USDJPY=X,XAUUSD=X,^GSPC,BTC-USD")
    if st.button("Analyse regime"):
        res = run("market", "regime", tickers=tick)
        st.dataframe(pd.DataFrame(res).T, width="stretch")
    st.json(mac, expanded=False)

# --- Trade planner ---------------------------------------------------------
with tabs[5]:
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
with tabs[6]:
    t = run("ops", "tasks")
    if "error" in t:
        st.error(t["error"])
    else:
        st.markdown(f"**Overdue ({len(t['overdue'])})**")
        st.dataframe(pd.DataFrame(t["overdue"]), hide_index=True, width="stretch")
        st.markdown(f"**Due this week ({len(t['this_week'])})** · {t['later']} later")
        st.dataframe(pd.DataFrame(t["this_week"]), hide_index=True, width="stretch")

# --- Activity / audit --------------------------------------------------------
with tabs[7]:
    rows = audit.recent(100)
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
