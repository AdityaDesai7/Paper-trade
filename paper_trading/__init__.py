# ============================================================================
# PETROQUANT — PAPER TRADING MODULE
# ============================================================================
# Intraday paper trading engine for WTI Crude Oil (CL=F)
# Uses 1-minute candles to predict 5-minute price direction via XGBoost.
# Hybrid approach: daily HMM regime (macro filter) + intraday XGBoost signal.
#
# Folder structure:
#   config.py            — all settings (capital, slippage, thresholds)
#   price_feed.py        — yfinance 1-min WTI candle fetcher
#   features_intraday.py — feature engineering on 1-min OHLCV bars
#   model_intraday.py    — rolling-window XGBoost classifier
#   daily_regime.py      — daily HMM regime detection (macro context)
#   portfolio.py         — cash, positions, P&L tracking
#   order_engine.py      — trade execution with slippage & commission
#   trade_log.py         — SQLite persistent trade log
#   dashboard_live.py    — Plotly HTML dashboard generator
#   web_server.py        — Flask web server (live dashboard + /status + /trades)
#   run_paper_trader.py  — main entry point
# ============================================================================

__version__ = "1.0.0"
__author__  = "PetroQuant"
