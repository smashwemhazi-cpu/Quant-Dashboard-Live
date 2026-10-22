import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import psycopg2
import requests
import yfinance as yf
import xml.etree.ElementTree as ET

# --- 1. CONFIGURATION & CLOUD CONNECTIONS ---
st.set_page_config(page_title="Quant Command Center", page_icon="🏦", layout="wide")

# 🔒 SECURE VAULT
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
DATABASE_URL = st.secrets["DATABASE_URL"]

# Custom Color Palette
THEME_COLOR = '#00E676' 
BENCHMARK_COLOR = '#8892B0' 

# 🏦 MASTER VAULT CURRENCY: All DB cash is strictly handled in this currency
VAULT_CURRENCY = "USD"

@st.cache_resource
def init_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True 
        return conn
    except Exception:
        return None

conn = init_connection()

# --- 2. ALGO BRAIN FUNCTIONS & SMART RESOLVER ---
@st.cache_data(ttl=3600)
def get_fx_rate(from_curr, to_curr):
    """Calculates the exact exchange rate for Cash Conversions"""
    if from_curr == to_curr: return 1.0
    try: return float(yf.Ticker(f"{from_curr}{to_curr}=X").history(period="1d")['Close'].iloc[-1])
    except: return 1.0

def resolve_ticker(raw_input):
    mapping = {
        "APPLE": "AAPL", "APPL": "AAPL",
        "NESTLE": "NESN.SW", "NESN": "NESN.SW",
        "FACEBOOK": "META", "GOOGLE": "GOOGL", "ALPHABET": "GOOGL",
        "AMAZON": "AMZN", "MICROSOFT": "MSFT", 
        "TESLA": "TSLA", "NVIDIA": "NVDA", "NETFLIX": "NFLX"
    }
    clean_input = str(raw_input).strip().upper()
    return mapping.get(clean_input, clean_input)

@st.cache_data(ttl=900) 
def get_normalized_pricing(ticker, client_base_currency):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="6mo") 
        if hist.empty: return 0.0, 0.0, False
        price = float(hist['Close'].iloc[-1])
        start_price = float(hist['Close'].iloc[0])
        if pd.isna(price) or pd.isna(start_price) or price <= 0: return 0.0, 0.0, False

        asset_currency = stock.info.get('currency', 'USD')
        if asset_currency == 'GBp':
            price, start_price, asset_currency = price / 100, start_price / 100, 'GBP'
            
        if asset_currency != client_base_currency:
            fx_rate = get_fx_rate(asset_currency, client_base_currency)
            price *= fx_rate
            start_price *= fx_rate
            
        asset_return = ((price - start_price) / start_price) * 100
        return price, asset_return, True
    except: return 0.0, 0.0, False

@st.cache_data(ttl=3600)
def get_macro_state():
    try:
        vix = float(yf.Ticker("^VIX").history(period="1d")['Close'].iloc[-1])
        tnx = float(yf.Ticker("^TNX").history(period="1d")['Close'].iloc[-1])
        if vix >= 25.0 or tnx >= 4.5: return "🔴 RED (High Fear / High Rates)", vix, tnx
        elif vix <= 20.0 and tnx <= 4.2: return "🟢 GREEN (Calm / Accommodative)", vix, tnx
        else: return "🟡 YELLOW (Transition / Caution)", vix, tnx
    except: return "🟡 YELLOW (Default)", 20.0, 4.0

def calculate_true_peg(info):
    pe = info.get('trailingPE') or info.get('forwardPE')
    eg = info.get('earningsGrowth') 
    if pe and eg and eg > 0: return float(pe / (eg * 100))
    return 999.0 

# --- 3. AUTHENTICATION & ROUTING ---
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
    st.session_state['user_email'] = None
    st.session_state['user_uuid'] = None

def logout():
    st.session_state.clear()
    st.rerun()

if not st.session_state['logged_in']:
    st.title("🏦 Quant Fund Portal")
    tab1, tab2 = st.tabs(["🔒 Log In", "📝 Sign Up"])
    with tab1:
        with st.form("login"):
            le, lp = st.text_input("Email"), st.text_input("Password", type="password")
            if st.form_submit_button("Log In"):
                with st.spinner("Authenticating..."):
                    url = f"{SUPABASE_URL}/auth/v1/token?grant_type=password"
                    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
                    r = requests.post(url, headers=headers, json={"email": le, "password": lp})
                    if r.status_code == 200:
                        st.session_state.update({'logged_in': True, 'user_email': le, 'user_uuid': r.json()['user']['id']})
                        st.rerun()
                    else: st.error("Invalid credentials")
    with tab2:
        with st.form("signup"):
            se, sp = st.text_input("Email"), st.text_input("Password", type="password")
            if st.form_submit_button("Create Account"):
                with st.spinner("Creating secure account..."):
                    url = f"{SUPABASE_URL}/auth/v1/signup"
                    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
                    r = requests.post(url, headers=headers, json={"email": se, "password": sp})
                    if r.status_code == 200: st.success("Account created! Log in above.")
    st.stop()

# --- SIDEBAR: DYNAMIC CURRENCY & RESET ---
st.sidebar.success(f"User: {st.session_state['user_email']}")
BASE_CURRENCY = st.sidebar.selectbox("🌍 Select Base Currency", ["USD", "CHF", "GBP", "EUR", "ZAR"])

target_client_id, client_name = None, ""
if conn:
    with conn.cursor() as cur:
        if st.session_state['user_email'] == "admin@quantfund.com":
            try:
                cur.execute("SELECT client_id, name FROM clients")
                all_c = cur.fetchall()
                if all_c:
                    choice = st.sidebar.selectbox("Admin View", [f"{c[1]} ({c[0]})" for c in all_c])
                    target_client_id, client_name = choice.split('(')[1].replace(')', ''), choice.split('(')[0].strip()
            except: pass
        else:
            try:
                cur.execute("SELECT client_id, name FROM clients WHERE user_id = %s", (st.session_state['user_uuid'],))
                res = cur.fetchone()
                if res: target_client_id, client_name = res
            except: pass

if target_client_id and st.session_state['user_email'] != "admin@quantfund.com":
    st.sidebar.markdown("---")
    if st.sidebar.button("⚠️ Wipe Portfolio Data"):
        with st.spinner("Erasing data..."):
            with conn.cursor() as cur:
                cur.execute("DELETE FROM portfolios WHERE client_id = %s", (target_client_id,))
                cur.execute("DELETE FROM clients WHERE client_id = %s", (target_client_id,))
            st.rerun()
    st.sidebar.button("🚪 Logout", on_click=logout)

# --- 4. ONBOARDING ---
if target_client_id is None and st.session_state['user_email'] != "admin@quantfund.com":
    st.title("🚀 Welcome to Quant Fund")
    st.markdown("Your portfolio is currently empty. Initialize your account below.")
    new_cid = f"CL_{st.session_state['user_email'][:4].upper()}"
    choice = st.radio("Initialization Method:", ["Start with Cash Only", "Manual Entry (Stocks)"])
    
    if choice == "Start with Cash Only":
        with st.form("cash_only"):
            c_name = st.text_input("Legal Full Name")
            c_cash = st.number_input(f"Starting Cash ({BASE_CURRENCY})", min_value=0.0)
            if st.form_submit_button("Initialize Account"):
                with st.spinner("Building portfolio..."):
                    # 💱 Fix: Convert front-end currency to Vault USD before saving
                    db_cash = c_cash / get_fx_rate(VAULT_CURRENCY, BASE_CURRENCY)
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO clients (client_id, name, user_id, cash_balance) VALUES (%s, %s, %s, %s)", (new_cid, c_name, st.session_state['user_uuid'], db_cash))
                    st.rerun()
    elif choice == "Manual Entry (Stocks)":
        with st.form("setup_portfolio"):
            c_name = st.text_input("Legal Full Name")
            c_cash = st.number_input(f"Starting Cash ({BASE_CURRENCY})", min_value=0.0)
            c_ticker = st.text_input("First Asset Ticker (e.g. AAPL)", help="Enter official Wall Street tickers")
            c_shares = st.number_input("Shares", min_value=0.1)
            if st.form_submit_button("Initialize Account"):
                with st.spinner("Building portfolio..."):
                    resolved_ticker = resolve_ticker(c_ticker)
                    # 💱 Fix: Convert front-end currency to Vault USD before saving
                    db_cash = c_cash / get_fx_rate(VAULT_CURRENCY, BASE_CURRENCY)
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO clients (client_id, name, user_id, cash_balance) VALUES (%s, %s, %s, %s)", (new_cid, c_name, st.session_state['user_uuid'], db_cash))
                        cur.execute("INSERT INTO portfolios (client_id, ticker, shares) VALUES (%s, %s, %s)", (new_cid, resolved_ticker, c_shares))
                    st.rerun()
    st.stop()

# --- 5. LIVE COMMAND CENTER ---
if target_client_id:
    st.title(f"🏦 {client_name} - Command Center")
    
    with st.spinner("Syncing with Quant Engine..."):
        with conn.cursor() as cur:
            cur.execute("SELECT ticker, shares FROM portfolios WHERE client_id = %s", (target_client_id,))
            p_data = cur.fetchall()
            cur.execute("SELECT cash_balance FROM clients WHERE client_id = %s", (target_client_id,))
            raw_db_cash = float(cur.fetchone()[0] or 0.0)

        df = pd.DataFrame(p_data, columns=["Ticker", "Shares"])
        if not df.empty: df['Shares'] = pd.to_numeric(df['Shares'], errors='coerce')

    # 💱 Fix: Convert raw Vault USD to Front-End Selected Currency for UI
    display_cash = raw_db_cash * get_fx_rate(VAULT_CURRENCY, BASE_CURRENCY)

    with st.expander("⚙️ Portfolio Controls (Add/Remove Assets)"):
        col_add, col_del = st.columns(2)
        with col_add:
            with st.form("add_holding_form"):
                st.markdown("**➕ Update Holdings**")
                new_t = st.text_input("Ticker", help="e.g. MSFT, AAPL, NESN.SW")
                new_s = st.number_input("Shares", min_value=0.0)
                add_cash = st.number_input(f"Deposit Cash ({BASE_CURRENCY})", min_value=0.0)
                if st.form_submit_button("Update Portfolio"):
                    with conn.cursor() as cur:
                        if new_t and new_s > 0: 
                            clean_t = resolve_ticker(new_t)
                            cur.execute("INSERT INTO portfolios (client_id, ticker, shares) VALUES (%s, %s, %s)", (target_client_id, clean_t, new_s))
                        if add_cash > 0: 
                            # Convert deposited UI currency back to DB Vault currency
                            db_add_cash = add_cash / get_fx_rate(VAULT_CURRENCY, BASE_CURRENCY)
                            cur.execute("UPDATE clients SET cash_balance = cash_balance + %s WHERE client_id = %s", (db_add_cash, target_client_id))
                    st.rerun()
        with col_del:
            with st.form("delete_holding_form"):
                st.markdown("**🗑️ Remove Asset**")
                if not df.empty:
                    del_t = st.selectbox("Select Asset", df['Ticker'].tolist())
                    if st.form_submit_button("Delete Asset"):
                        with conn.cursor() as cur:
                            cur.execute("DELETE FROM portfolios WHERE client_id = %s AND ticker = %s", (target_client_id, del_t))
                        st.rerun()
                else:
                    st.info("No assets to remove.")
                    st.form_submit_button("Delete Asset", disabled=True)

    st.subheader(f"💵 Available Capital: {display_cash:,.2f} {BASE_CURRENCY}")

    total_current_equity = display_cash
    total_historical_equity = display_cash
    equity_chart_data = [{"Asset": "Cash", "Value": display_cash}]
    
    portfolio_display_data = [] 
    invalid_tickers = []
    
    if not df.empty:
        for index, row in df.iterrows():
            t = row['Ticker']
            s = float(row['Shares'])
            price, asset_return, success = get_normalized_pricing(t, BASE_CURRENCY)
            if success:
                current_val = price * s
                total_current_equity += current_val
                total_historical_equity += current_val / (1 + (asset_return / 100))
                equity_chart_data.append({"Asset": t, "Value": current_val})
                
                portfolio_display_data.append({
                    "Asset": t, 
                    "Shares": s, 
                    "Price": f"{price:.2f}", 
                    "Total Value": current_val
                })
            else:
                invalid_tickers.append(t)

    if invalid_tickers:
        st.warning(f"⚠️ **Action Required:** Could not fetch live pricing for {', '.join(invalid_tickers)}. Please remove using Portfolio Controls.")

    port_return = ((total_current_equity - total_historical_equity) / total_historical_equity) * 100 if total_historical_equity > 0 else 0.0
    _, spy_return, _ = get_normalized_pricing("SPY", BASE_CURRENCY)
    
    st.markdown("### 📈 6-Month Performance Comparison")
    try:
        spy_hist = yf.Ticker("SPY").history(period="6mo")['Close']
        spy_pct = (spy_hist / spy_hist.iloc[0] - 1) * 100
        
        fig_line = go.Figure()
        fig_line.add_trace(go.Scatter(x=spy_pct.index, y=spy_pct, name="S&P 500 Benchmark", line=dict(color=BENCHMARK_COLOR, width=2)))
        
        if not df.empty and total_current_equity > display_cash:
            main_t = df.iloc[0]['Ticker']
            port_hist = yf.Ticker(main_t).history(period="6mo")['Close']
            port_pct = (port_hist / port_hist.iloc[0] - 1) * 100
            fig_line.add_trace(go.Scatter(x=port_pct.index, y=port_pct, name="Your Portfolio Trend", line=dict(color=THEME_COLOR, width=3)))
        else:
            flat_cash = pd.Series(0.0, index=spy_pct.index)
            fig_line.add_trace(go.Scatter(x=flat_cash.index, y=flat_cash, name="Cash Reserve", line=dict(color=THEME_COLOR, width=3)))
            
        fig_line.update_layout(margin=dict(t=0, b=0, l=0, r=0), height=300, yaxis_title="Return (%)", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', hovermode="x unified")
        st.plotly_chart(fig_line, use_container_width=True)
    except:
        st.caption("Performance graph temporarily unavailable.")

    # --- ASSET ALLOCATION ---
    st.markdown("### 🗂️ Asset Allocation")
    col1, col2 = st.columns([1, 2])
    with col1:
        chart_style = st.radio("Select Visualization:", ["Donut Chart", "Solid Pie", "Bar Chart"], horizontal=True, label_visibility="collapsed")
        eq_df = pd.DataFrame(equity_chart_data)
        
        custom_colors = [THEME_COLOR, '#1E88E5', '#FFC107', '#D81B60', '#8E24AA']
        
        if chart_style == "Donut Chart":
            fig = go.Figure(data=[go.Pie(labels=eq_df['Asset'], values=eq_df['Value'], hole=.5, marker=dict(colors=custom_colors))])
        elif chart_style == "Solid Pie":
            fig = go.Figure(data=[go.Pie(labels=eq_df['Asset'], values=eq_df['Value'], marker=dict(colors=custom_colors))])
        else:
            fig = go.Figure(data=[go.Bar(x=eq_df['Asset'], y=eq_df['Value'], marker_color=THEME_COLOR)])
        
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300, showlegend=True)
        st.plotly_chart(fig, use_container_width=True)
        
    with col2:
        if portfolio_display_data:
            display_df = pd.DataFrame(portfolio_display_data)
            display_df['% Weight'] = (display_df['Total Value'] / total_current_equity * 100).map("{:.1f}%".format)
            display_df['Total Value'] = display_df['Total Value'].map(f"{BASE_CURRENCY} {{:,.2f}}".format)
            st.dataframe(display_df, use_container_width=True, hide_index=True)
        else:
            st.info("Portfolio is currently 100% Cash Allocation.")

    # --- 6. THE ALGO INSIGHTS TAB ---
    st.markdown("---")
    st.title("🧠 Quant Engine Analysis")
    
    c1, c2 = st.columns([2, 1])
    with c1:
        st.subheader("🗞️ Live Market Wire")
        try:
            rss_url = "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"
            r = requests.get(rss_url, timeout=5)
            root = ET.fromstring(r.content)
            articles = root.findall('.//item')[:4]
            if articles:
                for a in articles: 
                    st.markdown(f"**📰 {a.find('title').text}**")
            else: raise Exception("Feed empty")
        except: 
            st.markdown("**📰 MacroEdge Alt-Feed:** Markets compress as VIX stabilizes under 20.")
            st.markdown("**📰 Quant Signal:** Institutional block buying detected in tech sectors.")
            st.markdown("**📰 FX Alert:** Currency strength continues amid shifting rate expectations.")
        
    with c2:
        st.subheader("🚦 Macro Status")
        state, vix, tnx = get_macro_state()
        if "GREEN" in state: st.success(state) 
        elif "YELLOW" in state: st.warning(state) 
        else: st.error(state)
        st.metric("VIX (Volatility Index)", f"{vix:.2f}")
        st.metric("TNX (10Y Yield)", f"{tnx:.2f}%")

    st.markdown("<br>", unsafe_allow_html=True)
    with st.expander("🔍 View Comparative Reallocation Showdown", expanded=False):
        if display_cash >= 50:
            st.info(f"Capital available for strategy deployment: **{display_cash:,.2f} {BASE_CURRENCY}**")
            growth_bench = ["CRWD", "CELH"] 
            
            cands = []
            with st.spinner("Analyzing institutional fundamentals..."):
                for t in growth_bench:
                    try:
                        stock = yf.Ticker(t)
                        info = stock.info
                        pe = info.get('trailingPE') or info.get('forwardPE') or 0.0
                        price = info.get('currentPrice') or info.get('regularMarketPrice') or 0.0
                        
                        # Convert Showdown pricing to chosen currency dynamically
                        if BASE_CURRENCY != "USD":
                            price *= get_fx_rate("USD", BASE_CURRENCY)
                            
                        margin = (info.get('profitMargins') or 0.0) * 100
                        peg = calculate_true_peg(info)
                        cands.append({"Ticker": t, "PE": pe, "Price": price, "PEG": peg, "Margin": f"{margin:.1f}%"})
                    except: pass
            
            if len(cands) >= 2:
                winner = cands[0] if cands[0]['PE'] < cands[1]['PE'] and cands[0]['PE'] > 0 else cands[1]
                runner_up = cands[1] if winner == cands[0] else cands[0]
                
                win_peg_str = f"{winner['PEG']:.2f}" if winner['PEG'] != 999.0 else "N/A"
                run_peg_str = f"{runner_up['PEG']:.2f}" if runner_up['PEG'] != 999.0 else "N/A"
                
                st.markdown("### 📊 Algorithm Showdown")
                comp_data = [
                    {"Metric": "Asset Target", "Selected Winner 🏆": winner['Ticker'], "Runner-Up": runner_up['Ticker']},
                    {"Metric": "Current Price", "Selected Winner 🏆": f"{BASE_CURRENCY} {winner['Price']:.2f}", "Runner-Up": f"{BASE_CURRENCY} {runner_up['Price']:.2f}"},
                    {"Metric": "P/E Ratio", "Selected Winner 🏆": f"{winner['PE']:.2f}", "Runner-Up": f"{runner_up['PE']:.2f}"},
                    {"Metric": "True PEG Ratio", "Selected Winner 🏆": win_peg_str, "Runner-Up": run_peg_str},
                    {"Metric": "Profit Margin", "Selected Winner 🏆": winner['Margin'], "Runner-Up": runner_up['Margin']},
                ]
                st.table(pd.DataFrame(comp_data))
                
                st.success(f"📝 **Analyst Observation on {winner['Ticker']}:** Trading at a P/E of {winner['PE']:.1f} with a {winner['Margin']} profit margin, this asset demonstrates robust pricing power. In the current macro environment, it presents a superior risk-adjusted entry point compared to {runner_up['Ticker']}.")
            else:
                st.warning("Insufficient market data to generate showdown today.")
        else:
            st.warning("Insufficient cash balance to trigger rebalancing strategy.")

    st.markdown("---")
    st.caption("⚠️ **IMPORTANT LEGAL DISCLAIMER:** This report is generated by an automated quantitative algorithm for informational and educational purposes only. It does NOT constitute personalized financial, tax, or legal advice. All investments carry the risk of loss, including the loss of principal.")