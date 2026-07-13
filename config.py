# =============================================================================
# PETROQUANT — MASTER CONFIGURATION
# =============================================================================
# Single source of truth for EVERY tunable parameter across both tracks:
#
#   TRACK A — Daily / Research
#       strategy.py → HMMXGBoostStrategy
#       run_strategy.py
#       backtest_backtrader.py
#       dashboard.py
#
#   TRACK B — Live Paper Trading
#       paper_trading/model_intraday.py
#       paper_trading/strategy_runner.py
#       paper_trading/order_engine.py
#       paper_trading/portfolio.py
#
# HOW TO USE:
#   Root files  → import config
#   Paper-trade → already handled (paper_trading/config.py re-exports this)
#
# QUICK CHANGE GUIDE:
#   ┌───────────────────────────────────────────────────────────────────────┐
#   │  Change daily strategy horizon   →  DAILY_FWD_DAYS                   │
#   │  Change entry confidence bar     →  BUY_THRESHOLD / SELL_THRESHOLD    │
#   │  Switch to MCX India costs       →  uncomment MCX preset below        │
#   │  Change regime position sizes    →  REGIME_MULTIPLIERS                │
#   │  Change max equity per trade     →  MAX_POSITION_PCT                  │
#   │  Tune XGBoost model              →  Section 14 (XGB_* params)         │
#   │  Change CI width / vol blend     →  Section 15 (VOL_* params)         │
#   │  Add/remove timeframes (live)    →  TIMEFRAME_CONFIGS                 │
#   └───────────────────────────────────────────────────────────────────────┘
# =============================================================================

import os

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 0 — PATHS
# ─────────────────────────────────────────────────────────────────────────────
# All paths derived from this file's location.  Never hardcode absolute paths.

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR        = os.path.join(BASE_DIR, 'output')
DB_PATH           = os.path.join(OUTPUT_DIR, 'paper_trades.db')
LOG_PATH          = os.path.join(OUTPUT_DIR, 'paper_trading.log')
DASHBOARD_PATH    = os.path.join(OUTPUT_DIR, 'paper_dashboard.html')
STATE_BACKUP_DIR  = os.path.join(OUTPUT_DIR, 'state_backups')


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — DATA & TICKERS (oil_data_pipeline_new.py)
# ─────────────────────────────────────────────────────────────────────────────

TICKER_INTRADAY   = "CL=F"       # WTI Crude Oil front-month futures (Yahoo)
TICKER_DAILY      = "CL=F"       # Same ticker used for daily data

# yfinance cache: set False to always pull fresh data
FORCE_DATA_REFRESH = False


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — ACCOUNT
# ─────────────────────────────────────────────────────────────────────────────
# For MCX India paper trading, use INR.  For US-proxy backtests, keep USD.

INITIAL_CAPITAL   = 1_000_000    # Starting paper capital
CURRENCY          = "USD"        # "USD" for backtest | "INR" for MCX India


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — EXECUTION COSTS
# ─────────────────────────────────────────────────────────────────────────────
# Applied per fill (each buy leg and each sell leg separately).
# Round-trip commission ≈ 2 × COMMISSION_PCT × notional.
#
# Slippage is applied to fill prices (buy higher / sell lower) — NOT deducted
# again from net_pnl.  Only commission is subtracted from final P&L.
#
# ┌──────────────────────────────────────────────────────────────────────────┐
# │  PRESET             │ COMMISSION_PCT │ SLIPPAGE_PCT │ Round-trip (~)     │
# │  US Generic (orig)  │ 0.00020 (2bp)  │ 0.00010 (1bp)│  ~0.06%           │
# │  MCX Mini  (10bbl)  │ 0.00045 (4.5bp)│ 0.00020 (2bp)│  ~0.13%  ← India  │
# │  MCX Main (100bbl)  │ 0.00035 (3.5bp)│ 0.00015 (1.5bp│ ~0.10%           │
# │  Conservative       │ 0.00055 (5.5bp)│ 0.00035 (3.5bp│ ~0.18%           │
# └──────────────────────────────────────────────────────────────────────────┘
# MCX breakdown (Mini, per side):
#   Brokerage ₹20 flat + CTT 0.01% (sell) + Exchange 0.0026% + GST 18% on fees + Stamp 0.002%
#   Slippage: typical bid-ask ₹1–₹2/barrel in normal sessions

# ── ACTIVE PRESET — change this line to switch ──────────────────────────────
_COST_PRESET = "MCX_MINI"   # Options: "US_GENERIC" | "MCX_MINI" | "MCX_MAIN" | "CONSERVATIVE"

_COST_PRESETS = {
    "US_GENERIC"   : {"COMMISSION_PCT": 0.00020, "SLIPPAGE_PCT": 0.00010},
    "MCX_MINI"     : {"COMMISSION_PCT": 0.00045, "SLIPPAGE_PCT": 0.00020},
    "MCX_MAIN"     : {"COMMISSION_PCT": 0.00035, "SLIPPAGE_PCT": 0.00015},
    "CONSERVATIVE" : {"COMMISSION_PCT": 0.00055, "SLIPPAGE_PCT": 0.00035},
}

COMMISSION_PCT    = _COST_PRESETS[_COST_PRESET]["COMMISSION_PCT"]
SLIPPAGE_PCT      = _COST_PRESETS[_COST_PRESET]["SLIPPAGE_PCT"]

# ── Size-scaled market impact model ──────────────────────────────────────────
# fill_slippage = SLIPPAGE_PCT × (|position_frac| / SLIPPAGE_REFERENCE_FRAC)^EXPONENT
# Square-root scaling (exponent=0.5) reflects empirical market impact for liquid futures.
# At SLIPPAGE_REFERENCE_FRAC the impact equals the flat SLIPPAGE_PCT baseline.
SLIPPAGE_IMPACT_EXPONENT  = 0.0    # 0.5 = square-root; 1.0 = linear impact
SLIPPAGE_REFERENCE_FRAC   = 0.10   # 10% of equity → base SLIPPAGE_PCT impact


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — RISK MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

MAX_POSITION_PCT   = 0.85    # Max fraction of equity per trade (85% of equity cap)
MAX_TOTAL_EXPOSURE = 1.00    # Max gross notional / current equity (100% = no leverage)
MAX_DAILY_LOSS_PCT = 0.05    # Circuit breaker: stop trading if daily loss > 5%


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — SIGNAL THRESHOLDS
# ─────────────────────────────────────────────────────────────────────────────
# Model probability thresholds to generate BUY / SELL signals.
# HOLD = probability between SELL_THRESHOLD and BUY_THRESHOLD.
#
# Wider gap (e.g. 0.62 / 0.38) = fewer but higher-conviction trades.
# Tighter gap (e.g. 0.52 / 0.48) = more trades, more noise.

BUY_THRESHOLD     = 0.55     # prob > this → BUY
SELL_THRESHOLD    = 0.45     # prob < this → SELL


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — REGIME-AWARE SIZING
# ─────────────────────────────────────────────────────────────────────────────
# Position size multiplier per HMM market regime.
# Final size = equity × MAX_POSITION_PCT × REGIME_MULT × confidence_factor

REGIME_MULTIPLIERS = {
    'BULL'  : 1.00,   # Calm uptrend — full size
    'CHOPPY': 1.00,   # Sideways/uncertain — slightly reduced
    'PANIC' : 1.00,   # High volatility — smaller size (limits stop-loss tail risk)
}


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7 — DAILY STRATEGY PARAMS  (strategy.py / HMMXGBoostStrategy)
# ─────────────────────────────────────────────────────────────────────────────

DAILY_FWD_DAYS          = 20     # Forward label horizon in days (Target = return over N days)
DAILY_HMM_STATES        = 3     # Number of HMM regimes (BULL / CHOPPY / PANIC)
DAILY_INITIAL_TRAIN_DAYS = 500  # Minimum bars before first walk-forward prediction
DAILY_RETRAIN_EVERY     = 63    # Re-fit XGBoost every N days (~1 quarter)
DAILY_BUY_THRESHOLD     = BUY_THRESHOLD    # Inherits global; override here if needed
DAILY_SELL_THRESHOLD    = SELL_THRESHOLD   # Inherits global; override here if needed
DAILY_FORECAST_HORIZONS = [1, 7, 15, 30]  # Days for multi-horizon forecasts


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8 — BACKTEST PARAMS  (backtest_backtrader.py)
# ─────────────────────────────────────────────────────────────────────────────
# Backtrader applies commission as a fraction per trade (round-trip in BT semantics).
# We derive it from the cost preset for consistency.

BT_COMMISSION     = COMMISSION_PCT       # Backtrader applies this per order leg; buy+sell = correct round-trip
BT_SLIPPAGE_PERC  = SLIPPAGE_PCT         # Slippage per fill
BT_POSITION_PCT   = 0.85                 # Max portfolio fraction used per trade (backtest_backtrader.py only)
# NOTE: BT_POSITION_PCT applies ONLY to backtest_backtrader.py (Track A, Backtrader engine).
#       The dashboard lifecycle backtester (dashboard.py) enforces MAX_POSITION_PCT (Section 4).
#       If this log came from run_strategy.py → dashboard.py, the binding cap is MAX_POSITION_PCT.


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8.5 — LIFECYCLE BACKTESTER EXECUTION CONTROLS  (dashboard.py)
# ─────────────────────────────────────────────────────────────────────────────
# These parameters govern the event-driven position lifecycle simulation.

# Minimum change in position fraction to trigger a rebalance/trade.
# Prevents hundreds of micro-lot events from cluttering the trade log when
# Position_Size drifts by tiny amounts between consecutive signals.
MIN_REBALANCE_FRAC = 0.005   # Ignore changes < 0.5% of equity (no trade logged)

# Block scale-ins (pyramiding). When False, only reductions / flips are allowed on an
# existing position; new size must come from a flat start.
ALLOW_PYRAMIDING   = True

# Unrealized-loss stop-loss: close a position if mark-to-market loss exceeds
# this fraction of entry notional.
STOP_LOSS_PCT = 0.05         # 5% loss on notional triggers forced exit

# Maximum holding period (days).  Force-close at next bar if exceeded.
# Tied to DAILY_FWD_DAYS: holding beyond the forecast horizon undermines signal validity.
MAX_HOLD_DAYS = 25           # ≥ DAILY_FWD_DAYS=15 to allow some slack


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9 — INTRADAY MODEL DEFAULTS  (model_intraday.py)
# ─────────────────────────────────────────────────────────────────────────────
# These are overridden per-timeframe by TIMEFRAME_CONFIGS below.
# They serve as fallback defaults if a StrategyRunner is constructed directly.

CANDLE_INTERVAL    = "5m"
BARS_TO_FETCH      = 30
BARS_FOR_TRAINING  = 2000
PREDICT_HORIZON    = 3
LOOP_INTERVAL_SECS = 300
MIN_TRAIN_BARS     = 150
RETRAIN_EVERY_MINS = 480
RETRAIN_AFTER_TRADE = True    # Retrain immediately after any completed trade


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 10 — TIMEFRAME CONFIGURATIONS  (strategy_runner.py / engine.py)
# ─────────────────────────────────────────────────────────────────────────────
# Each timeframe runs an independent StrategyRunner with its own model,
# portfolio, and database.  Fastest supported: 5m.

TIMEFRAME_CONFIGS = {
    "5m": {
        "interval"          : "5m",
        "bars_to_fetch_days": 30,
        "loop_secs"         : 300,
        "predict_horizon"   : 3,       # 3 × 5m = 15 min ahead
        "min_train_bars"    : 150,
        "retrain_mins"      : 480,     # retrain every 8 hours
        "bars_for_training" : 2000,
        "label"             : "5 Min",
        "description"       : "5 Minutes — trade every 5 min, predict 15 min ahead",
    },
    "15m": {
        "interval"          : "15m",
        "bars_to_fetch_days": 60,
        "loop_secs"         : 900,
        "predict_horizon"   : 2,       # 2 × 15m = 30 min ahead
        "min_train_bars"    : 100,
        "retrain_mins"      : 720,     # retrain every 12 hours
        "bars_for_training" : 1000,
        "label"             : "15 Min",
        "description"       : "15 Minutes — trade every 15 min, predict 30 min ahead",
    },
    "1h": {
        "interval"          : "1h",
        "bars_to_fetch_days": 180,
        "loop_secs"         : 3600,
        "predict_horizon"   : 2,       # 2 × 1h = 2 hours ahead
        "min_train_bars"    : 80,
        "retrain_mins"      : 1440,    # retrain once per day
        "bars_for_training" : 500,
        "label"             : "1 Hour",
        "description"       : "1 Hour — trade every hour, predict 2 hours ahead",
    },
    "1d": {
        "interval"          : "1d",
        "bars_to_fetch_days": 730,
        "loop_secs"         : 86400,
        "predict_horizon"   : 1,       # predict next-day direction
        "min_train_bars"    : 80,
        "retrain_mins"      : 10080,   # retrain weekly
        "bars_for_training" : 400,
        "label"             : "1 Day",
        "description"       : "1 Day — trade once daily, predict tomorrow's direction",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 11 — WTI MARKET HOURS  (price_feed.py)
# ─────────────────────────────────────────────────────────────────────────────

MAINTENANCE_START = "17:00"    # CME maintenance window start (UTC−5)
MAINTENANCE_END   = "18:00"    # CME maintenance window end


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 12 — WEB SERVER  (web_server.py)
# ─────────────────────────────────────────────────────────────────────────────

WEB_PORT          = int(os.environ.get('PORT', 8080))
DASHBOARD_REFRESH = 60         # seconds between live dashboard auto-refresh


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 13 — POSITION SIZING FORMULA  (strategy.py)
# ─────────────────────────────────────────────────────────────────────────────
# strategy.py computes a signed Position_Size column that the lifecycle backtester
# (dashboard.py) uses directly as the target equity fraction.
#
# Formula (strategy.py line ~546):
#   Position_Size = Signal × Regime_Mult × confidence_factor × MAX_POSITION_PCT
#
# Components:
#   Signal            ∈ {-1, 0, +1}  (SELL / HOLD / BUY from XGBoost probability)
#   Regime_Mult       ∈ REGIME_MULTIPLIERS  (0.90 – 1.00)
#   confidence_factor = CONFIDENCE_FLOOR + CONFIDENCE_SCALE × (|prob − 0.5| × 2)
#                     = 0.5 + |prob − 0.5|   (with current defaults)
#                     ∈ [0.5, 1.0]
#   MAX_POSITION_PCT  = hard equity cap (currently 0.85)
#
# Analytical range (non-zero signals):
#   Minimum: Signal=1 × CHOPPY(0.90) × CF_min(0.5) × MAX(0.85) ≈ 0.38
#   Maximum: Signal=1 × BULL(1.00)   × CF_max(1.0) × MAX(0.85) = 0.85
#
# Why does the trade log show fractions far below 0.38?
#   The lifecycle engine logs EVERY rebalancing event (even tiny ones) as a
#   ClosedTrade.  The `position_frac` field = abs(signed_frac) × close_frac,
#   where close_frac is the fraction of the existing position being closed.
#   A 0.5% rebalance on a 0.6 position → position_frac ≈ 0.003.
#   MIN_REBALANCE_FRAC (Section 8.5) suppresses these micro-events.
#
# Why do values exceed MAX_POSITION_PCT?
#   They should not.  A hard clip is applied in Backtester.run().
#   If you observe cap breaches, check that you haven't temporarily raised
#   MAX_POSITION_PCT above the value used when the log was generated.

CONFIDENCE_FLOOR  = 0.5   # Minimum conviction multiplier (at threshold-edge signals)
CONFIDENCE_SCALE  = 0.5   # Rate at which conviction grows with probability distance from 0.5


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 14 — XGBOOST MODEL HYPERPARAMETERS  (strategy.py)
# ─────────────────────────────────────────────────────────────────────────────
# Two XGBoost models are trained per run:
#   1. Main walk-forward classifier (XGB_N_ESTIMATORS — more data, more trees)
#   2. Per-horizon forecast model   (XGB_FORECAST_ESTIMATORS — less data, fewer trees)
#
# Increase XGB_MAX_DEPTH for more complex patterns (risk: overfitting).
# Decrease XGB_LEARNING_RATE + increase XGB_N_ESTIMATORS for more stable training.

XGB_MAX_DEPTH            = 5      # Tree depth (4–6 typical for financial time series)
XGB_N_ESTIMATORS         = 500    # Trees for main walk-forward classifier
XGB_FORECAST_ESTIMATORS  = 200    # Trees for per-horizon forecast models
XGB_LEARNING_RATE        = 0.05   # Step size — lower = more robust but slower
XGB_SUBSAMPLE            = 0.8    # Row sampling per tree (prevents overfitting)
XGB_COLSAMPLE            = 0.8    # Feature sampling per tree (prevents overfitting)
XGB_MIN_CHILD_WEIGHT     = 5      # Min samples in leaf (higher = more conservative)
FORECAST_TRAIN_SPLIT     = 0.80   # Fraction of horizon data used for training (rest = held out)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 15 — VOLATILITY ENGINE PARAMS  (strategy.py / VolatilityEngine)
# ─────────────────────────────────────────────────────────────────────────────
# Controls GARCH fitting, ATR decay, CI width, and GARCH/ATR/OVX blend weights.
#
# CI_Z_SCORE:
#   1.00 = ~68% CI  (tighter bands, more actionable entry/exit levels)
#   1.645 = 90% CI
#   1.96 = 95% CI   (wider, more conservative)
#
# ATR scaling: atr_frac × sqrt(h) × ATR_DECAY_FACTOR^(h / ATR_DECAY_WINDOW)
#   ATR_DECAY_FACTOR < 1 prevents volatility explosion at long horizons.

VOL_LOOKBACK_DAYS  = 252   # GARCH fitting window (1 trading year)
CI_Z_SCORE         = 1.96  # CI multiplier (1.00 = ~68%, 1.96 = 95%)
ATR_DECAY_FACTOR   = 0.9   # Decay base: lower = ATR fades faster at long horizons
ATR_DECAY_WINDOW   = 5.0   # Horizon divisor in decay exponent

# Horizon-dependent vol blend weights: {horizon_days: (w_garch, w_atr, w_iv)}
# Short horizons → heavier ATR (price-action), long horizons → heavier OVX (IV)
VOL_BLEND_WEIGHTS = {
    1:   (0.25, 0.50, 0.25),
    7:   (0.30, 0.40, 0.30),
    15:  (0.40, 0.25, 0.35),
    30:  (0.40, 0.20, 0.40),
    60:  (0.30, 0.15, 0.55),
    90:  (0.25, 0.10, 0.65),
    180: (0.20, 0.10, 0.70),
}


# ─────────────────────────────────────────────────────────────────────────────
# ACTIVE TIMEFRAME TRACKER  (mutated at runtime by apply_timeframe)
# ─────────────────────────────────────────────────────────────────────────────

ACTIVE_TIMEFRAME = "5m"


# ─────────────────────────────────────────────────────────────────────────────
# RUNTIME HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def apply_timeframe(tf: str) -> None:
    """
    Switch the active trading timeframe at runtime.
    Updates all global defaults to match the chosen timeframe.
    Raises ValueError for unknown timeframes.
    """
    if tf not in TIMEFRAME_CONFIGS:
        raise ValueError(
            f"Unknown timeframe '{tf}'. Valid: {list(TIMEFRAME_CONFIGS.keys())}"
        )
    global ACTIVE_TIMEFRAME, CANDLE_INTERVAL, BARS_TO_FETCH, BARS_FOR_TRAINING
    global PREDICT_HORIZON, LOOP_INTERVAL_SECS, MIN_TRAIN_BARS, RETRAIN_EVERY_MINS

    c = TIMEFRAME_CONFIGS[tf]
    ACTIVE_TIMEFRAME    = tf
    CANDLE_INTERVAL     = c["interval"]
    BARS_TO_FETCH       = c["bars_to_fetch_days"]
    BARS_FOR_TRAINING   = c["bars_for_training"]
    PREDICT_HORIZON     = c["predict_horizon"]
    LOOP_INTERVAL_SECS  = c["loop_secs"]
    MIN_TRAIN_BARS      = c["min_train_bars"]
    RETRAIN_EVERY_MINS  = c["retrain_mins"]


def get_timeframe_label(tf: str = None) -> str:
    """Return the short display label for a timeframe (e.g. '5 Min')."""
    tf = tf or ACTIVE_TIMEFRAME
    return TIMEFRAME_CONFIGS.get(tf, {}).get("label", tf)


def get_timeframe_description(tf: str = None) -> str:
    """Return the full description for a timeframe."""
    tf = tf or ACTIVE_TIMEFRAME
    return TIMEFRAME_CONFIGS.get(tf, {}).get("description", tf)


def get_active_costs() -> dict:
    """Return currently active cost preset as a dict for logging/display."""
    return {
        "preset"        : _COST_PRESET,
        "commission_pct": COMMISSION_PCT,
        "slippage_pct"  : SLIPPAGE_PCT,
        "roundtrip_pct" : round(2 * (COMMISSION_PCT + SLIPPAGE_PCT), 6),
    }


def get_daily_strategy_params() -> dict:
    """Return HMMXGBoostStrategy constructor kwargs from this config."""
    return {
        "fwd_days"          : DAILY_FWD_DAYS,
        "hmm_states"        : DAILY_HMM_STATES,
        "initial_train_days": DAILY_INITIAL_TRAIN_DAYS,
        "retrain_every"     : DAILY_RETRAIN_EVERY,
        "buy_threshold"     : DAILY_BUY_THRESHOLD,
        "sell_threshold"    : DAILY_SELL_THRESHOLD,
        "forecast_horizons" : list(DAILY_FORECAST_HORIZONS),
        "max_position_pct"  : MAX_POSITION_PCT,
    }
