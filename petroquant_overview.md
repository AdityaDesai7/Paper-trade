# PetroQuant — Oil Trading Strategy

> An algorithmic trading system for crude oil (WTI) using machine learning and volatility modelling.
> Built with Python | Updated: March 2026

---

## What Does This Project Do?

PetroQuant is an end-to-end algorithmic trading system that answers one question every trading day:

> **"Should I BUY, SELL, or HOLD crude oil today?"**

It does this by:

1. **Fetching market data** — Oil prices, volatility, inventories, dollar index, speculator positions, and more
2. **Detecting market conditions** — Classifying the market into BULL, CHOPPY, or PANIC regimes
3. **Generating trade signals** — BUY / SELL / HOLD with position sizing based on market conditions
4. **Forecasting future prices** — Price range predictions from 1 day to 6 months ahead
5. **Backtesting** — Validating the strategy on historical data with a detailed performance report
6. **Visualising everything** — Interactive HTML dashboard with 10 panels

---

## System Architecture

```
DATA PIPELINE          STRATEGY               BACKTEST           DASHBOARD
(Fetch & Clean)  -->  (HMM + XGBoost)  -->  (Test on Past)  --> (HTML Report)
                              |
                    VOLATILITY ENGINE
                    (GARCH + ATR + OVX)
```

---

## File Structure

| File | Role |
|------|------|
| `features.py` | Defines which data sources to fetch |
| `oil_data_pipeline_new.py` | Fetches, cleans, and merges all market data |
| `strategy.py` | Core strategy — HMM regime detection + XGBoost signals + Volatility Engine |
| `dashboard.py` | Backtesting engine and interactive HTML dashboard |
| `run_strategy.py` | Entry point — connects everything and runs the full pipeline |

**Supporting files:**
- `.env` — Stores API keys (`FRED_API_KEY`, `EIA_API_KEY`)
- `data/Rigcount_final.csv` — Baker Hughes rig count (local CSV)
- `output/` — Dashboard HTML and cached data files

---

## Data Sources (9 Features)

| Feature | Source | Update Frequency |
|---------|--------|-----------------|
| WTI Crude Price | Yahoo Finance (`CL=F`) | Daily |
| Brent Crude Price | Yahoo Finance (`BZ=F`) | Daily |
| OVX (Oil Volatility Index) | FRED API | Daily |
| USD Index (DXY) | Yahoo Finance (`DX-Y.NYB`) | Daily |
| 3-2-1 Crack Spread | Calculated from Yahoo Finance | Daily |
| Net Speculative Position | CFTC via Socrata API | Weekly |
| US Crude Inventories | EIA API | Weekly |
| Baker Hughes Rig Count | Local CSV | Monthly |
| Strategic Petroleum Reserve | EIA API | Weekly |

All features are merged into a single `master_df` table with one row per trading day. Weekly/monthly data is forward-filled to maintain daily frequency.

---

## Strategy Components

### 1. Feature Engineering

Raw prices are transformed into signals the model can learn from:

- **Log Returns** — Daily and 5-day price changes for WTI and Brent
- **Z-Scores** — How unusual the WTI-Brent spread, crack spread, and speculator positions are
- **Momentum** — 10-day and 20-day price trends
- **Realised Volatility** — 20-day rolling standard deviation of returns
- **Rate-of-Change** — Changes in OVX, USD, inventories, rigs, and SPR over 1–5 days

Result: ~20 clean engineered features per trading day.

---

### 2. HMM Regime Detection

A **Hidden Markov Model (HMM)** classifies each trading day into one of three market regimes:

| Regime | Behaviour | Avg Volatility |
|--------|-----------|---------------|
| 🟢 **BULL** | Calm, trending upward | Low (~18%) |
| 🟡 **CHOPPY** | Sideways, uncertain | Medium (~25%) |
| 🔴 **PANIC** | Large swings, risk-off | High (~42%) |

The HMM is trained on daily returns and realised volatility to detect the hidden market state for each day.

**Example output:**
```
[+] BULL     +0.00080 (+20.2%/yr)   0.1800   950 days (51.9%)
[~] CHOPPY   +0.00010 (+2.5%/yr)    0.2500   468 days (25.6%)
[!] PANIC    -0.00100 (-25.2%/yr)   0.4200   412 days (22.5%)
```

---

### 3. Walk-Forward XGBoost Classifier

An **XGBoost** model predicts whether oil will be higher or lower 5 trading days from now.

**Target variable:**
```
1 (UP)   if WTI price in 5 days > today's price
0 (DOWN) if WTI price in 5 days < today's price
```

**Walk-forward training** is used to avoid data leakage — the model is only ever tested on data it has never seen during training. It retrains every 63 trading days (~1 quarter).

**Signal generation:**
```
Probability > 55%  -->  BUY
Probability < 45%  -->  SELL
45% to 55%         -->  HOLD  (not enough conviction to trade)
```

**Regime-aware position sizing** scales down exposure in riskier markets:
```
BULL   regime = 1.00x position (full size)
CHOPPY regime = 0.50x position (half size)
PANIC  regime = 0.25x position (quarter size)
```

---

### 4. Volatility Engine — Price Forecasting

The volatility engine generates **95% confidence intervals** for WTI prices across 7 time horizons (1d, 7d, 15d, 30d, 60d, 90d, 180d).

It uses a **composite of three volatility measures**:

- **GARCH(1,1)** — Captures volatility clustering over medium horizons
- **ATR(14)** — Average True Range, responsive to recent price action
- **OVX (Implied Vol)** — The options market's forward-looking volatility estimate

**Sample output:**
```
Horizon   Price Range (95% CI)     Expected    Return
  1d      $56.85 -- $59.07         $57.97      +0.03%
  7d      $53.80 -- $61.60         $57.61      -0.58%
 15d      $51.13 -- $67.10         $59.12      +0.97%
 30d      $49.50 -- $68.10         $59.68      +1.97%
```

---

## Backtesting Results

Backtested over ~2.9 years (732 trading days) of out-of-sample data.

### Summary

| Metric | Strategy | Buy & Hold |
|--------|----------|-----------|
| Total Return | **+148.39%** | -24.44% |
| Annualised Return | **+36.78%** | — |
| Alpha (vs benchmark) | **+172.84%** | — |

### Risk Metrics

| Metric | Value |
|--------|-------|
| Sharpe Ratio | 1.193 |
| Sortino Ratio | 1.687 |
| Max Drawdown | -27.95% |
| Calmar Ratio | 1.316 |
| Win Rate | 55.7% |
| Up-Market Capture | 35.2% |
| Down-Market Capture | 16.3% |

---

## Dashboard — 10-Panel Interactive Report

The dashboard is saved as `output/dashboard_hmm_xgb.html` and can be opened in any browser.

| Panel | Contents |
|-------|----------|
| 1 | WTI price history with BULL/CHOPPY/PANIC regime shading |
| 2 | Buy/Sell signal overlay on price chart |
| 3 | Strategy equity curve vs buy-and-hold benchmark |
| 4 | Drawdown chart (peak-to-trough losses over time) |
| 5 | XGBoost feature importance |
| 6 | Model accuracy by market regime |
| 7 | Rolling Sharpe ratio over time |
| 8 | Monthly returns heatmap |
| 9 | Multi-horizon price forecast with 95% confidence bands |
| 10 | Full performance metrics table |

---

## How to Run

```bash
# 1. Add API keys to .env
FRED_API_KEY=your_key_here
EIA_API_KEY=your_key_here

# 2. Run the strategy
python run_strategy.py

# 3. Open the dashboard
output/dashboard_hmm_xgb.html
```

**Dependencies:** `pandas`, `numpy`, `yfinance`, `arch`, `hmmlearn`, `xgboost`, `plotly`, `requests`, `python-dotenv`

---

## Key Terms

| Term | Meaning |
|------|---------|
| **WTI** | West Texas Intermediate — US crude oil benchmark price |
| **Regime** | Market condition: BULL (calm), CHOPPY (uncertain), PANIC (volatile) |
| **HMM** | Hidden Markov Model — detects hidden market states |
| **XGBoost** | Gradient boosting algorithm used for price direction prediction |
| **GARCH** | Model for estimating time-varying volatility |
| **ATR** | Average True Range — measures typical daily price movement |
| **OVX** | Oil Volatility Index — options market's fear gauge for crude oil |
| **Walk-Forward** | Training method where the model is always tested on unseen future data |
| **Sharpe Ratio** | Return divided by volatility — measures risk-adjusted performance |
| **Max Drawdown** | Largest peak-to-trough loss during the backtest period |
| **Alpha** | Strategy return minus the benchmark (buy-and-hold) return |
| **Crack Spread** | Refinery profit margin from crude oil to refined products |
| **COT / NSP** | CFTC Commitment of Traders — net speculator positioning in oil futures |
