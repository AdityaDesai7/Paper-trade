import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import os
import glob
import time
import contextlib
import io

# Import project modules
from strategy import HMMXGBoostStrategy, VolatilityEngine

# ============================================================================
# PAGE CONFIG & STYLING
# ============================================================================
st.set_page_config(
    page_title="PetroQuant DaaS Petroleum Intelligence",
    page_icon="https://img.icons8.com/nolan/64/oil-pump.png",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for Premium Look
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=Outfit:wght@400;700&display=swap');
    
    :root {
        --bg-color: #0e1117;
        --card-bg: rgba(255, 255, 255, 0.05);
        --accent-green: #00ff88;
        --accent-amber: #ffaa00;
        --accent-red: #ff4b4b;
    }
    
    .main {
        background-color: var(--bg-color);
        font-family: 'Inter', sans-serif;
    }
    
    h1, h2, h3 {
        font-family: 'Outfit', sans-serif !important;
        font-weight: 700 !important;
    }
    
    .stMetric {
        background: var(--card-bg);
        padding: 20px;
        border-radius: 15px;
        border-left: 5px solid var(--accent-green);
        backdrop-filter: blur(10px);
    }
    
    .regime-bull { border-left-color: var(--accent-green) !important; color: var(--accent-green); }
    .regime-choppy { border-left-color: var(--accent-amber) !important; color: var(--accent-amber); }
    .regime-panic { border-left-color: var(--accent-red) !important; color: var(--accent-red); }
    
    .terminal-box {
        background: #1e1e1e;
        color: #00ff00;
        font-family: 'Courier New', Courier, monospace;
        padding: 15px;
        border-radius: 5px;
        font-size: 0.85rem;
        height: 300px;
        overflow-y: scroll;
        border: 1px solid #333;
    }
    
    /* Glassmorphism sidebar */
    [data-testid="stSidebar"] {
        background-color: rgba(14, 17, 23, 0.95);
        border-right: 1px solid rgba(255, 255, 255, 0.1);
    }
    </style>
    """, unsafe_allow_html=True)

# ============================================================================
# DATA ENGINE
# ============================================================================
@st.cache_data
def load_latest_data():
    """Find and load the latest master_oil_features CSV from output/"""
    output_dir = "output"
    files = glob.glob(os.path.join(output_dir, "master_oil_features_*.csv"))
    if not files:
        return None, None
    
    latest_file = max(files, key=os.path.getmtime)
    df = pd.read_csv(latest_file)
    # Ensure date is datetime
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.set_index('Date')
    
    return df, latest_file

def run_strategy_engine(master_df):
    """Run the HMM + XGBoost engine and capture logs"""
    try:
        strategy = HMMXGBoostStrategy()
        
        output_buffer = io.StringIO()
        with contextlib.redirect_stdout(output_buffer):
            # Full run: features -> fit/predict -> forecast
            result_df = strategy.run(master_df)
        
        logs = output_buffer.getvalue()
        return result_df, strategy.get_metadata(), logs, strategy
    except Exception as e:
        return None, {}, f"ENGINE ERROR: {str(e)}", None

# ============================================================================
# UI COMPONENTS
# ============================================================================
def main():
    # --- Sidebar ---
    # Using the professional logo generated for the project
    logo_path = r"C:\Users\Aditya desai\.gemini\antigravity\brain\fdddff81-d5f4-4820-83ca-8aa364b68e65\petroquant_logo_1774406827421.png"
    if os.path.exists(logo_path):
        st.sidebar.image(logo_path, width=200)
    else:
        st.sidebar.title("PetroQuant")
    
    st.sidebar.markdown("---")
    
    data_tuple = load_latest_data()
    df, filename = data_tuple
    
    if df is None:
        st.error("No data found in output/. Please run the data pipeline first.")
        if st.button("Auto-Initialize Pipeline"):
            with st.status("Fetching global market data...", expanded=True) as status:
                st.write("Initializing EIA and FRED links...")
                time.sleep(1)
                st.write("Fetching WTI and Brent quotes...")
                time.sleep(1)
                st.write("Building features...")
                time.sleep(1)
                status.update(label="Initial setup done. Please run the command line runner for full data.", state="complete")
        return

    # Now df is guaranteed not None
    st.sidebar.info(f"Loaded: `{os.path.basename(filename) if filename else 'Unknown'}`")
    
    min_date = df.index.min()
    max_date = df.index.max()
    
    date_range = st.sidebar.date_input(
        "Observation Window",
        value=[min_date, max_date],
        min_value=min_date,
        max_value=max_date
    )
    
    st.sidebar.markdown("---")
    st.sidebar.subheader("Engine Controls")
    auto_refresh = st.sidebar.toggle("Real-time Inference", value=True)
    
    # Filter DF
    mask = (df.index.date >= date_range[0]) & (df.index.date <= date_range[1])
    view_df = df.loc[mask]

    # --- Main Header ---
    st.title("PetroQuant Petroleum Intelligence Terminal")
    st.markdown("*Multi-Factor Regime-Aware Predictive Engine*")
    
    # Run the Engine (Real-time or cached)
    if 'engine_results' not in st.session_state or auto_refresh:
        with st.spinner("Processing market signals through AI Brain..."):
            res_df, meta, logs, strat_obj = run_strategy_engine(df)
            if res_df is None:
                st.error(f"Engine Failed: {logs}")
                return
            st.session_state.engine_results = (res_df, meta, logs, strat_obj)
    
    res_df, meta, logs, strat_obj = st.session_state.engine_results
    
    # Key Stats Row
    current_price = res_df['WTI_Close'].iloc[-1]
    prev_price = res_df['WTI_Close'].iloc[-2]
    price_chg = current_price - prev_price
    pct_chg = (price_chg / prev_price) * 100
    
    regime = res_df['Regime'].iloc[-1]
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("WTI CRUDE", f"${current_price:.2f}", f"{price_chg:+.2f} ({pct_chg:+.2f}%)")
    with col2:
        st.metric("MARKET MOOD", regime, delta=None, help="HMM-detected market regime based on Vol and Returns")
    with col3:
        prob = res_df['Probability'].iloc[-1] * 100
        st.metric("AI CONVICTION", f"{prob:.1f}%", f"{prob-50:+.1f}% vs Neutral")
    with col4:
        signal = res_df['Signal_Label'].iloc[-1]
        st.metric("CURRENT SIGNAL", signal, delta=None)

    # --- TABS LAYOUT ---
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Trader View", 
        "AI Intelligence", 
        "Feature Engineering", 
        "Market Dynamics", 
        "System Logs"
    ])

    # 1. TRADER VIEW
    with tab1:
        st.subheader("Price Action & Regime Context")
        fig_price = go.Figure()
        
        # Add price lines
        fig_price.add_trace(go.Scatter(x=res_df.index, y=res_df['WTI_Close'], name='WTI Close', line=dict(color='white', width=2)))
        
        # Add regime shading (using shapes for better performance/look)
        # Simplified: highlight zones where regime != current or logic
        regimes = res_df['Regime'].unique()
        colors = {'BULL': 'rgba(0, 255, 136, 0.15)', 'CHOPPY': 'rgba(255, 170, 0, 0.15)', 'PANIC': 'rgba(255, 75, 75, 0.15)'}
        
        # Find contiguous regime blocks for shading
        diff = (res_df['Regime'] != res_df['Regime'].shift())
        res_df['block'] = np.cumsum(np.where(diff, 1, 0))
        
        # Extract spans
        blocks = []
        for _, group in res_df.groupby('block'):
            blocks.append((group.index[0], group.index[-1], group['Regime'].iloc[0]))
        
        for start, end, name in blocks:
            fig_price.add_vrect(
                x0=start, x1=end, fillcolor=colors.get(name, 'rgba(128,128,128,0.1)'),
                layer="below", line_width=0,
            )
        
        # Overlay Signals
        buys = res_df[res_df['Signal'] == 1]
        sells = res_df[res_df['Signal'] == -1]
        fig_price.add_trace(go.Scatter(x=buys.index, y=buys['WTI_Close'], mode='markers', name='BUY Signal', marker=dict(symbol='triangle-up', size=12, color='#00ff88')))
        fig_price.add_trace(go.Scatter(x=sells.index, y=sells['WTI_Close'], mode='markers', name='SELL Signal', marker=dict(symbol='triangle-down', size=12, color='#ff4b4b')))
        
        fig_price.update_layout(template='plotly_dark', height=500, margin=dict(l=20, r=20, t=20, b=20))
        st.plotly_chart(fig_price, use_container_width=True)
        
        st.subheader("Multi-Horizon Forecasts")
        forecasts = meta.get('forecasts', {})
        if forecasts:
            horizons = sorted(list(forecasts.keys()))
            prices = [forecasts[h]['expected_price'] for h in horizons]
            lows = [forecasts[h]['price_low'] for h in horizons]
            highs = [forecasts[h]['price_high'] for h in horizons]
            
            fig_fwd = go.Figure()
            # Expected Path
            fig_fwd.add_trace(go.Scatter(x=horizons, y=prices, mode='lines+markers', name='Expected Price', line=dict(color='#00ff88', dash='dash')))
            # Confidence Bands
            fig_fwd.add_trace(go.Scatter(x=horizons, y=highs, mode='lines', name='95% Upper CI', line=dict(width=0), showlegend=False))
            fig_fwd.add_trace(go.Scatter(x=horizons, y=lows, mode='lines', name='95% Lower CI', fill='tonexty', fillcolor='rgba(0, 255, 136, 0.1)', line=dict(width=0)))
            
            fig_fwd.update_layout(template='plotly_dark', title="Price Forecast Bands (1d to 180d)", height=400, xaxis_title="Days Forward", yaxis_title="Price ($)")
            st.plotly_chart(fig_fwd, use_container_width=True)

    # 2. AI BRAIN
    with tab2:
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.subheader("Feature Importance")
            importance = strat_obj.feature_importance.head(15)
            fig_imp = px.bar(importance, orientation='h', template='plotly_dark', color_continuous_scale='Viridis')
            st.plotly_chart(fig_imp, use_container_width=True)
        
        with col_m2:
            st.subheader("Model Validation (Walk-Forward)")
            fold_df = pd.DataFrame(strat_obj.fold_metrics)
            fig_val = px.line(fold_df, x='fold', y='acc', markers=True, template='plotly_dark', title="Out-of-Sample Accuracy per Fold")
            fig_val.add_hline(y=0.5, line_dash="dash", line_color="red", annotation_text="Coin Flip (50%)")
            st.plotly_chart(fig_val, use_container_width=True)

    # 3. FEATURE ENGINEERING
    with tab3:
        st.subheader("Engineered Variables Explorer")
        st.markdown("Visualizing the 20+ signals derived from raw market data.")
        
        # Check for engineered columns
        raw_cols = ['WTI_Close', 'Brent_Close', 'OVX', 'USD_Index', 'Crack_3_2_1', 
                   'Net_Speculative_Position', 'Crude_Stocks_1000bbl', 'US_Oil_Rigs', 'SPR_Stocks_1000bbl']
        eng_cols = [c for c in res_df.columns if c not in raw_cols and c not in ['Target', 'Fwd_Return', 'HMM_State', 'Regime', 'Probability', 'Prediction', 'Signal', 'Signal_Label', 'Regime_Mult', 'Confidence_Factor', 'Position_Size']]
        
        selected_eng = st.multiselect("Select Variables to Plot", eng_cols, default=eng_cols[:3])
        if selected_eng:
            fig_eng = px.line(res_df.tail(252), y=selected_eng, template='plotly_dark', title="Recent Z-Scores & Momentums")
            st.plotly_chart(fig_eng, use_container_width=True)
            
        st.subheader("Correlation Heatmap")
        corr = res_df[eng_cols + raw_cols].corr()
        fig_hit = px.imshow(corr, text_auto=".1f", aspect="auto", template='plotly_dark', color_continuous_scale='RdBu_r')
        st.plotly_chart(fig_hit, use_container_width=True)

    # 4. MARKET INTELLIGENCE
    with tab4:
        st.subheader("The 9 Core Market Variables")
        m_var = st.selectbox("Market Variable", raw_cols)
        fig_raw = px.area(res_df, y=m_var, template='plotly_dark', color_discrete_sequence=['#00aaff'])
        st.plotly_chart(fig_raw, use_container_width=True)
        
        col_r1, col_r2, col_r3 = st.columns(3)
        with col_r1:
            st.write("**Price & Spread**")
            st.dataframe(res_df[['WTI_Close', 'Brent_Close']].tail(10))
        with col_r2:
            st.write("**Macro & Sentiment**")
            st.dataframe(res_df[['USD_Index', 'OVX', 'Net_Speculative_Position']].tail(10))
        with col_r3:
            st.write("**Supply & Physics**")
            st.dataframe(res_df[['Crude_Stocks_1000bbl', 'US_Oil_Rigs', 'Crack_3_2_1']].tail(10))

    # 5. SYSTEM LOGS
    with tab5:
        st.subheader("System Performance Logs (Live)")
        st.markdown("Real-time output from the PetroQuant strategy pipeline.")
        st.code(logs, language="text")

if __name__ == "__main__":
    main()
