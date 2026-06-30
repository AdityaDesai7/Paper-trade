# PetroQuant 

**Quantitative Oil Trading Strategy Platform**

An end-to-end Python system for oil market analysis, featuring a multi-source data pipeline, two ML/RL trading strategies, a walk-forward backtesting engine, and a premium interactive dashboard.

---

##  Project Structure

```
PetroQuant/
│
├── .env                          # API keys (FRED, EIA) — DO NOT COMMIT
│
├── features.py                   # Feature Registry — all 9 oil market data sources
├── oil_data_pipeline.py          # Original monolithic data pipeline
├── oil_data_pipeline_new.py      # Modular pipeline (uses features.py registry)
├── oil_data_pipeline_second.py   # Backup/variant of the modular pipeline
│
├── strategy.py                   # Trading strategies (HMM+XGBoost, TD(0) RL)
├── dashboard.py                  # Backtesting engine + Plotly dashboard renderer
├── run_strategy.py               # Single entry point: Pipeline → Strategy → Backtest → Dashboard
│
├── main.py                       # Health-check & data validation script
├── fun.py                        # Utility: Baker Hughes rig count fetcher
├── rig.py                        # Utility: FRED rig count fetcher
├── daily_tracker.py              # (Placeholder for daily tracking)
│
├── data/                         # Raw/processed data files
│   └── Rigcount_final.csv        # Baker Hughes rig count historical data
│
├── output/                       # Generated dashboards & cached feature CSVs
│   ├── dashboard_hmm_xgb.html    # HMM+XGBoost strategy dashboard
│   ├── dashboard_td0_rl.html     # TD(0) RL strategy dashboard
│   └── master_oil_features_*.csv # Timestamped pipeline output caches
│
└── venv/                         # Python virtual environment
```

---

##  How It Works

The platform follows a **4-stage pipeline** architecture:

```
 ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
 │  1. DATA      │ ──▶ │  2. STRATEGY  │ ──▶ │  3. BACKTEST  │ ──▶ │  4. DASHBOARD │
 │  PIPELINE     │     │  ENGINE       │     │  ENGINE       │     │  RENDERER     │
 └──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
```

### Stage 1: Data Pipeline (`features.py` + `oil_data_pipeline_new.py`)

Fetches, cleans, and merges **9 oil market features** from multiple APIs into a single `master_df` DataFrame:

| # | Feature | Band | Frequency | Source |
|---|---------|------|-----------|--------|
| 1 | **WTI_Close** | Fast | Daily | Yahoo Finance (CL=F) |
| 2 | **Brent_Close** | Fast | Daily | Yahoo Finance (BZ=F) |
| 3 | **OVX** (Oil Volatility Index) | Fast | Daily | FRED API |
| 4 | **USD_Index** (DXY) | Fast | Daily | Yahoo Finance |
| 5 | **Crack_3_2_1** (3-2-1 Crack Spread) | Medium | Daily | Yahoo Finance (computed) |
| 6 | **Net_Speculative_Position** | Medium | Weekly | CFTC Socrata API |
| 7 | **Crude_Stocks_1000bbl** | Medium | Weekly | EIA API v2 |
| 8 | **US_Oil_Rigs** | Medium | Monthly | Baker Hughes CSV |
| 9 | **SPR_Stocks_1000bbl** | Slow | Weekly | EIA API v2 |

**Key design:**
- Features are organized into **3 signal bands** — Fast (daily), Medium (weekly), Slow (monthly)
- The pipeline uses **24-hour caching** to avoid redundant API calls
- Lower-frequency data is forward-filled to align with the daily index
- Adding a new feature requires only writing a `fetch_xxx()` function and appending to the `FEATURES` list

---

### Stage 2: Trading Strategies (`strategy.py`)

All strategies inherit from a common `BaseStrategy` abstract class. Two strategies are implemented:

#### Strategy A: HMM + XGBoost (Regime-Aware)
- **Regime Detection:** 3-state Hidden Markov Model classifies the market into `BULL`, `PANIC`, or `CHOPPY` regimes using return and volatility features
- **Signal Generation:** Walk-forward XGBoost classifier trained on engineered features (momentum, volatility, spreads, regime) to predict N-day forward returns
- **Forecasting:** Multi-horizon return forecasts (1, 7, 15, 30, 60, 90, 180 days) via conditional historical returns per regime
- **Walk-Forward Training:** Retrains every 63 trading days (~1 quarter) with expanding window

#### Strategy B: TD(0) Reinforcement Learning
- **Algorithm:** Semi-Gradient TD(0) with Linear Function Approximation
- **State Space:** Continuous features (momentum, volatility, regime) normalized via rolling z-scores
- **Action Space:** Long (+1), Flat (0), Short (-1)
- **Reward:** Differential Sharpe Ratio with drawdown penalties to optimize risk-adjusted returns
- **Exploration:** Softmax (Boltzmann) exploration with decaying temperature
- **Walk-Forward Learning:** Learns and adapts online as new data arrives

---

### Stage 3: Backtesting Engine (`dashboard.py` → `Backtester`)

Simulates strategy performance on out-of-sample data:

- Computes daily strategy returns from signal × market return
- Tracks equity curve, drawdown, and cumulative P&L
- Calculates key metrics:
  - **Total Return** & **Annualized Return**
  - **Sharpe Ratio** & **Calmar Ratio**
  - **Max Drawdown**
  - **Win Rate** & **Profit Factor**
  - **Total Trades** & **Trading Days**

---

### Stage 4: Dashboard (`dashboard.py` → `StrategyDashboard`)

Renders a **premium 10-panel interactive Plotly dashboard** saved as standalone HTML:

- Equity curve with regime shading
- Buy/Sell signal overlay on price chart
- Drawdown chart
- Rolling Sharpe ratio
- Feature importance rankings
- Multi-horizon return forecasts
- Signal distribution analysis
- Performance metrics summary

---

##  Quick Start

### 1. Setup Environment

```bash
# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# Install dependencies
pip install pandas numpy yfinance fredapi requests plotly scikit-learn xgboost hmmlearn python-dotenv
```

### 2. Configure API Keys

Create a `.env` file in the project root:

```env
FRED_API_KEY=your_fred_api_key_here
EIA_API_KEY=your_eia_api_key_here
```

- **FRED API Key:** Get one free at [https://fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html)
- **EIA API Key:** Get one free at [https://www.eia.gov/opendata/register.php](https://www.eia.gov/opendata/register.php)

### 3. Run the Full Pipeline

```bash
# Run all strategies end-to-end (data → strategy → backtest → dashboard)
python run_strategy.py
```

This will:
1. Build `master_df` from all 9 API sources (or use cached data)
2. Run both HMM+XGBoost and TD(0) RL strategies
3. Backtest each strategy
4. Save interactive HTML dashboards to `output/`

### 4. View Results

Open the generated dashboards in your browser:
- `output/dashboard_hmm_xgb.html` — HMM + XGBoost results
- `output/dashboard_td0_rl.html` — TD(0) RL results

---

##  Other Scripts

| Script | Purpose |
|--------|---------|
| `main.py` | Run a **data health check** — validates staleness, coverage, and volatility pulse |
| `fun.py` | Standalone Baker Hughes rig count fetcher (Excel download) |
| `rig.py` | Standalone FRED-based rig count fetcher (API) |

---

##  Data Flow Diagram

```
Yahoo Finance ──┐
  (CL=F, BZ=F,  │
   RB=F, HO=F,  │     ┌─────────────────┐     ┌───────────────┐
   DX-Y.NYB)    ├────▶│                 │     │               │
                 │     │  features.py    │     │  strategy.py  │
FRED API ────────┤     │  (9 fetchers)   │────▶│               │
  (OVX)          │     │       ↓         │     │  HMM+XGBoost  │
                 │     │  oil_data_      │     │  TD(0) RL     │
CFTC Socrata ────┤     │  pipeline_new.py│     │       ↓       │     ┌────────────┐
  (COT data)     │     │  (merge & clean)│     │  Signals +    │────▶│ dashboard  │
                 │     │       ↓         │     │  Forecasts    │     │ .py        │
EIA API v2 ──────┤     │  master_df      │     └───────────────┘     │            │
  (Stocks, SPR)  │     │  (daily index)  │                           │ Backtester │
                 │     └─────────────────┘                           │ Dashboard  │
Baker Hughes ────┘                                                   │     ↓      │
  (Rig Count CSV)                                                    │ HTML files │
                                                                     └────────────┘
```

---

##  Tech Stack

- **Python 3.10+**
- **pandas / NumPy** — Data manipulation
- **yfinance** — Yahoo Finance market data
- **fredapi** — FRED economic data
- **requests** — API calls (EIA, CFTC)
- **scikit-learn** — Preprocessing & metrics
- **XGBoost** — Gradient boosting classifier
- **hmmlearn** — Hidden Markov Model
- **Plotly** — Interactive dashboards
- **python-dotenv** — Environment variable management

---



##  Trading Scenario: Geopolitical Volatility Arbitrage

**Project:** PetroQuant Analytics | **Asset Class:** Energy Derivatives | **Date Range:** March 2–25, 2026

---

### 1. The Strategy Objective
To capture alpha by tracking the **Cash-Futures Basis**. Since the Micro WTI (`$MCLK26`) is a derivative, its price is anchored to the physical WTI (`$CF=L`). We monitor the "Spot" for supply-side shocks and trade the "Micro" for leveraged gains with a smaller capital footprint (1/10th of a standard contract).

### 2. The Setup: "The Two-Screen View"
A professional trader monitors two distinct data feeds:

| Indicator | Symbol | Role | Key Metric Observed |
| :--- | :--- | :--- | :--- |
| **Underlying (Spot)** | `$CF=L` | The Signal | Real-time physical supply at Cushing, OK. |
| **Derivative (Micro)** | `$MCLK26` | The Execution | May 2026 Expiry. 100 Barrels per contract. |

---

### 3. Step-by-Step Trading Journey (March 2026)

#### **Phase 1: Observation & Signal Generation (March 2–9)**
* **Context:** Escalation in the Strait of Hormuz. US-Iran tensions hit a peak.
* **Observation:** The Spot price (`$CF=L`) gaps up from **$74.56** to **$113.41**.
* **The Signal:** The derivative (`$MCLK26`) lags slightly behind the spot move. We see a "**Backwardation**" where the spot is higher than the May futures, indicating an immediate physical shortage.
* **Trader Action:** Bullish Bias confirmed.

#### **Phase 2: Technical Entry (March 10–12)**
* **The Trigger:** Spot (`$CF=L`) pulls back to **$82.10** after a "Trump Tweet" hinting at a 5-day strike postponement.
* **Indicator:** RSI on the 1-hour chart of `$MCLK26` hits 30 (oversold).
* **Execution:** * **Buy Order:** 10 Lots of `$MCLK26` @ **$86.07**.
    * **Notional Value:** $86.07 \times 100 \times 10 = \$86,070$
    * **Margin Required:** Roughly **$7,500** (using 10x leverage).

#### **Phase 3: The "Black Swan" Hedge (March 13–19)**
* **Risk:** What if the 5-day peace window leads to a massive price crash?
* **Hedging Action:** The trader spends 2% of the trade's profit to buy **Put Options** on Oil (USO).
* **Scenario:** If the conflict ends suddenly, the Puts will print money, offsetting the loss on the `$MCLK26` long position.

#### **Phase 4: Managing the "Gray Swan" (March 20–23)**
* **Event:** Confirmed strikes on Iranian infrastructure. `$CF=L` spikes back toward **$98.32**.
* **The Trap:** On March 23, the market crashes **10%** in one session due to a liquidity flush (long liquidations).
* **Trader Action:** Does not panic. The Stop-Loss is set at **$84.50** (just below the recent support). The trade survives the flush.

#### **Phase 5: Exit & Profit Realization (March 24–25 - Current)**
* **Status:** `$MCLK26` recovers to **$91.60**.
* **Exit Signal:** Price reaches a major psychological resistance level (**$92.00**). The news cycle is saturated; "the risk is priced in."
* **Closing Order:** Sell 10 Lots of `$MCLK26` @ **$91.60**.

---

### 4. Performance Summary (Simulation Results)

| Metric | Calculation | Result |
| :--- | :--- | :--- |
| **Entry Price** | March 11 Low | **$86.07** |
| **Exit Price** | March 25 Current | **$91.60** |
| **Point Gain** | $91.60 - $86.07 | **+5.53 Points** |
| **Gross Profit** | $5.53 \times 100 \times 10$ | **$5,530** |
| **Return on Margin** | $\$5,530 / \$7,500$ | **~73.7% ROI** |

---

### 5. Key Takeaways for PetroQuant Analytics
* **Spot Tracking is King:** You cannot trade `$MCLK26` in a vacuum. The movements in `$CF=L` act as the "Front-Run" indicator.
* **Gamma Risk is Real:** During the March 23 drop, the "Delta" of the portfolio shifted rapidly. Active traders must adjust their hedge ratios during such high-volatility sessions.
* **Micro Advantage:** Using `$MCL` instead of standard `$CL` allowed the trader to scale in with 10 lots rather than being forced into 1 large contract, allowing for "partial exits" as the price hit **$90**.


#  The Buffett Options Strategy  
### *Thinking Like “The Oracle of Omaha”*

Welcome to the world of **Warren Buffett**.

For most people, options = risky gambling.  
For Buffett, options = **insurance business**.

---

## 1️ The Core Concept: *You Are the Insurance Agent*

- Buying a **put option** = buying insurance  
  - You pay a **premium**
  - You get protection if the stock falls  

- Selling a **put option** = being the insurance company  
  - You **collect premium**
  - You promise to buy stock if price falls  

###  Buffett Rule
> Never sell a put on a company you wouldn’t happily own for 10 years.

---

## 2️ Example: 1993 Coca-Cola (KO) Trade

- Coca-Cola price ≈ **$40**
- Buffett wanted to buy at **$35**

Instead of waiting, he **sold put options**

---

###  Trade Details

- Contracts sold: **50,000**
- Shares per contract: **100**
- Total shares: **5,000,000**

#### Strike Price
`$35`

#### Premium Collected
`$1.50 per share`

####  Total Cash Received
5,000,000 × 1.50 = 7,500,000

➡ **Buffett received $7.5M upfront**

---

###  Scenario A: Stock stays above $35

- Example: KO = $39  
- Options expire worthless  

#### Result:
- Buffett keeps **$7.5M**

#### Return:
1.50 / 35 ≈ 4.3%

 *He got paid just for waiting*

---

###  Scenario B: Stock drops to $30

- Market price = **$30**
- Buffett must buy at **$35**

#### Effective Price:
35 - 1.50 = 33.50

#### Result:
- Bought at **$33.50**
- Wanted to buy anyway

 *He gets a discount on a great company*

---

## 3️ The 2008 Index Puts: *“The Big Float”*

During the financial crisis:

Buffett sold puts on **entire stock indices** (e.g., S&P 500)

---

###  Strategy

- Expiry: **15–20 years**
- Premium collected: **$4.9 Billion**

---

###  The Power of Float

- Buffett received **$4.9B upfront**
- Invested it elsewhere

---

###  Key Insight

- Options were **European style**
- Could only be exercised at expiry

#### His Bet:
> Over 20 years, markets will be higher

 He was right

---

## 4️ Selling Puts vs Limit Orders

| Feature | Limit Order | Selling a Put |
|--------|------------|--------------|
| If price hits $35 | Buy at $35 | Buy at $35 + keep premium |
| If price stays at $40 | Nothing happens | Keep premium (profit) |
| Risk | Price may crash | Same risk |
| Upfront | No money | You get paid |

---

##  Beginner Warning (VERY IMPORTANT)

Buffett can do this because:
- He has **billions in cash**
- He can always buy the stock

---

###  Danger: Naked Puts

If you:
- Sell puts  
- BUT don’t have money to buy shares  

Then:
- You use leverage (margin)
- A crash can **wipe you out**

> You can lose more money than you started with

---

##  Final Insight

> Selling puts = getting paid to buy stocks you already want

But only if:
- You have cash  
- You choose strong companies  
- You understand the risk  


##  License


This project is for educational and research purposes only. Not financial advice.
