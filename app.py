import streamlit as st
import time
import requests
import pandas as pd
import numpy as np
import pyotp  # NEW: For Auto TOTP Generation
from smartapi import SmartConnect
from datetime import datetime, timedelta

# ==========================================
# 1. PAGE CONFIGURATION (Strictly Untouched)
# ==========================================
st.set_page_config(
    page_title="Quantum Algo Trader",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for Neon Look
st.markdown("""
    <style>
    .metric-card {
        background-color: #0e1117;
        border: 1px solid #303030;
        border-radius: 10px;
        padding: 20px;
        text-align: center;
    }
    .stSuccess {
        background-color: rgba(0, 255, 0, 0.1);
        border: 1px solid #00ff00;
    }
    .stError {
        background-color: rgba(255, 0, 0, 0.1);
        border: 1px solid #ff0000;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("🚀 QUANTUM ALGO TRADER V2.0")
st.markdown("### [ AI-Powered Nifty Breakout Scanner ]")

# ==========================================
# 2. UPDATED SIDEBAR LOGIN (From 1.py)
# ==========================================
with st.sidebar:
    st.header("🔐 Login (Manual)")
    st.caption("Enter Angel One Details Here")
    
    # 1. User Inputs (Secrets की जगह अब Manual Input)
    api_key = st.text_input("API Key", type="password")
    client_id = st.text_input("Client ID")
    password = st.text_input("Password", type="password")
    totp_secret = st.text_input("TOTP Secret Key", type="password", help="Enter the long code provided by Angel One for Authenticator")

    connect_btn = st.button("🔌 CONNECT SYSTEM")

    # Session State Initialization
    if 'angel_api' not in st.session_state:
        st.session_state['angel_api'] = None

    if connect_btn:
        if not api_key or not client_id or not password or not totp_secret:
            st.error("❌ All fields are required!")
        else:
            try:
                # --- LOGIN LOGIC (Updated to match 1.py) ---
                smartApi = SmartConnect(api_key=api_key)
                
                # Auto Generate TOTP (6 Digit Code)
                try:
                    totp_obj = pyotp.TOTP(totp_secret).now()
                except:
                    st.error("Invalid TOTP Secret Key format!")
                    st.stop()
                
                # Generate Session
                data = smartApi.generateSession(client_id, password, totp_obj)
                
                if data['status']:
                    st.session_state['angel_api'] = smartApi
                    st.success("✅ Connected Successfully!")
                    st.rerun() # Refresh page to start scanner
                else:
                    st.error(f"❌ Login Failed: {data['message']}")
            
            except Exception as e:
                st.error(f"❌ Connection Error: {e}")

# ==========================================
# 3. HELPER FUNCTIONS (Strictly Untouched)
# ==========================================
@st.cache_resource
def get_master_data():
    try:
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        response = requests.get(url)
        master_df = pd.DataFrame(response.json())
        master_df['expiry'] = pd.to_datetime(master_df['expiry'], errors='coerce')
        master_df['strike'] = pd.to_numeric(master_df['strike'], errors='coerce')
        return master_df
    except:
        return None

def get_nifty_future_token(master_df):
    if master_df is None: return None, None
    today = pd.to_datetime(datetime.now().date())
    futures = master_df[
        (master_df['exch_seg'] == 'NFO') & (master_df['name'] == 'NIFTY') & 
        (master_df['instrumenttype'] == 'FUTIDX') & (master_df['expiry'] >= today)
    ].sort_values('expiry')
    if futures.empty: return None, None
    return futures.iloc[0]['token'], futures.iloc[0]['symbol']

def get_high_delta_option(master_df, spot_price, signal_type):
    if master_df is None: return None, None, 0
    atm_strike = round(spot_price / 50) * 50
    if signal_type == "BUY_CALL":
        selected_strike = atm_strike - 100
        opt_type = "CE"
    else:
        selected_strike = atm_strike + 100
        opt_type = "PE"
        
    target_val = selected_strike * 100
    today = pd.to_datetime(datetime.now().date())
    
    options = master_df[
        (master_df['exch_seg'] == 'NFO') & (master_df['name'] == 'NIFTY') &
        (master_df['instrumenttype'] == 'OPTIDX') & (master_df['expiry'] >= today) &
        (abs(master_df['strike'] - target_val) < 1.0) & 
        (master_df['symbol'].str.endswith(opt_type))
    ].sort_values('expiry')
    
    if options.empty: return None, None, selected_strike
    return options.iloc[0]['token'], options.iloc[0]['symbol'], selected_strike

def calculate_indicators(df):
    df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    df['resistance_level'] = df['high'].rolling(window=20).max().shift(1)
    df['support_level'] = df['low'].rolling(window=20).min().shift(1)
    return df

def calculate_trade_setup(entry_price, candle_low, candle_high, signal_type):
    DELTA = 0.6
    if signal_type == "BUY_CALL":
        nifty_sl = candle_low
        risk = entry_price - candle_low
        target = entry_price + (risk * 2)
    else:
        nifty_sl = candle_high
        risk = candle_high - entry_price
        target = entry_price - (risk * 2)
        
    return {
        "nifty_sl": nifty_sl,
        "nifty_target": target,
        "opt_risk": risk * DELTA,
        "opt_reward": (risk * 2) * DELTA
    }

# ==========================================
# 4. MAIN APP LOGIC (Scanning Loop)
# ==========================================

# Check if logged in
if st.session_state['angel_api'] is None:
    st.info("👋 Please enter your API Credentials in the Sidebar and click CONNECT.")
else:
    obj = st.session_state['angel_api']
    
    # Load Master Data Once
    if 'master_df' not in st.session_state:
        with st.spinner("Downloading Scrip Master..."):
            st.session_state['master_df'] = get_master_data()
    
    master_df = st.session_state['master_df']
    
    if master_df is not None:
        NIFTY_TOKEN, NIFTY_SYMBOL = get_nifty_future_token(master_df)
        
        if not NIFTY_TOKEN:
            st.error("Failed to get Nifty Token")
        else:
            st.success(f"✅ System Armed: Scanning {NIFTY_SYMBOL}")
            
            # Start Scanning Button
            start_scan = st.toggle("🚀 RUN LIVE SCANNER", value=True)
            
            placeholder = st.empty()
            
            if start_scan:
                # Main Loop logic for Streamlit (using st_autorefresh manually via loop inside placeholder)
                # Note: In Streamlit Cloud, while True loops can be tricky, 
                # but for simplicity we use basic loop with sleep.
                
                try:
                    # Fetch Data
                    to_date = datetime.now()
                    from_date = to_date - timedelta(days=5)
                    historicParam = {
                        "exchange": "NFO", "symboltoken": NIFTY_TOKEN, "interval": "FIVE_MINUTE",
                        "fromdate": from_date.strftime("%Y-%m-%d %H:%M"), "todate": to_date.strftime("%Y-%m-%d %H:%M")
                    }
                    cdata = obj.getCandleData(historicParam)
                    
                    if cdata['data']:
                        df = pd.DataFrame(cdata['data'], columns=["timestamp", "open", "high", "low", "close", "volume"])
                        for col in ['open', 'high', 'low', 'close', 'volume']: df[col] = df[col].astype(float)
                        
                        df = calculate_indicators(df)
                        latest = df.iloc[-1]
                        
                        # --- DASHBOARD METRICS ---
                        col1, col2, col3, col4 = st.columns(4)
                        col1.metric("Nifty Price", f"₹{latest['close']}", delta=round(latest['close'] - latest['open'], 1))
                        
                        rsi_val = round(latest['rsi'], 2)
                        col2.metric("RSI (14)", rsi_val)
                        
                        col3.metric("EMA (20)", round(latest['ema_20'], 2))
                        col4.metric("Volume", int(latest['volume']))

                        # --- LOGIC ---
                        df['avg_vol'] = df['volume'].rolling(20).mean()
                        is_high_vol = latest['volume'] > latest['avg_vol']
                        breakout_up = latest['close'] > latest['resistance_level']
                        breakout_down = latest['close'] < latest['support_level']
                        trend_up = latest['close'] > latest['ema_20']
                        trend_down = latest['close'] < latest['ema_20']
                        momentum_strong = latest['rsi'] > 50

                        # --- SIGNALS ---
                        if breakout_up and trend_up and momentum_strong and is_high_vol:
                            setup = calculate_trade_setup(latest['close'], latest['low'], latest['high'], "BUY_CALL")
                            token, symbol, strike = get_high_delta_option(master_df, latest['close'], "BUY_CALL")
                            
                            st.success(f"🔥🔥 BUY CALL DETECTED | {symbol}")
                            st.dataframe(pd.DataFrame([{
                                "Signal": "BUY CE",
                                "Option": symbol,
                                "Strike": strike,
                                "Index SL": setup['nifty_sl'],
                                "Index Target": setup['nifty_target'],
                                "Opt SL (Pts)": round(setup['opt_risk'], 1),
                                "Opt Tgt (Pts)": round(setup['opt_reward'], 1)
                            }]))
                            st.balloons()

                        elif breakout_down and trend_down and is_high_vol:
                            setup = calculate_trade_setup(latest['close'], latest['low'], latest['high'], "BUY_PUT")
                            token, symbol, strike = get_high_delta_option(master_df, latest['close'], "BUY_PUT")
                            
                            st.error(f"🔻 SELL PUT DETECTED | {symbol}")
                            st.dataframe(pd.DataFrame([{
                                "Signal": "BUY PE",
                                "Option": symbol,
                                "Strike": strike,
                                "Index SL": setup['nifty_sl'],
                                "Index Target": setup['nifty_target'],
                                "Opt SL (Pts)": round(setup['opt_risk'], 1),
                                "Opt Tgt (Pts)": round(setup['opt_reward'], 1)
                            }]))
                        
                        else:
                            st.info(f"📡 Scanning... No Signal yet. (Last Update: {datetime.now().strftime('%H:%M:%S')})")
                            
                    else:
                        st.warning("⚠️ Data fetch error or Market Closed.")

                except Exception as e:
                    st.error(f"Runtime Error: {e}")
                
                # Auto-refresh logic (Simple sleep for Streamlit Loop)
                time.sleep(10)
                st.rerun()
            
    else:
        st.error("Could not load Master Data.")