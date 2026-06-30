#  Feature Engineering — Complete Deep Dive Guide

> **For:** Faculty presentation on PetroQuant Oil Trading Strategy  
> **Level:** Beginner-friendly, from macro concepts to micro implementation  
> **Project:** PetroQuant — HMM + XGBoost Oil Price Trading Strategy

---

## Table of Contents

1. [What is Feature Engineering?](#1-what-is-feature-engineering)
2. [Why is it Important?](#2-why-is-it-important)
3. [Types of Feature Engineering (Macro → Micro)](#3-types-of-feature-engineering)
4. [Step-by-Step with Easy Examples](#4-step-by-step-with-easy-examples)
5. [Feature Engineering in Our PetroQuant Project](#5-feature-engineering-in-our-petroquant-project)
6. [The Logic Behind Our Choices](#6-the-logic-behind-our-choices)
7. [Research Papers & References](#7-research-papers--references)
8. [Summary for Faculty Presentation](#8-summary-for-faculty-presentation)

---

## 1. What is Feature Engineering?

### Simple Analogy 

Imagine you're a cricket coach trying to decide if a batsman will score a century tomorrow. You don't just look at his name — you look at:
- How many runs he scored in the **last 5 matches** (recent form)
- Is the pitch **spin-friendly or pace-friendly** (conditions)
- Has he played **against this bowler before** (history)
- Is it a **day match or night match** (environment)

> **Feature Engineering = Converting raw information into useful "clues" that help an AI/ML model make better predictions.**

### Formal Definition

Feature engineering is the process of **transforming raw data into meaningful input variables (features)** that improve a machine learning model's ability to learn patterns and make accurate predictions.

```
Raw Data → [Feature Engineering] → Meaningful Features → [ML Model] → Prediction
```

### Another Simple Example

**Raw Data:** Today's temperature is 35°C

**Engineered Features from this one number:**
| Feature | Value | What it tells the model |
|---------|-------|------------------------|
| `Temperature` | 35°C | The raw value |
| `Temp_Change_from_Yesterday` | +5°C | It's getting hotter |
| `Temp_vs_Monthly_Average` | +8°C above avg | It's unusually hot |
| `Is_Hot_Day` | Yes (>30°C) | Binary category |
| `Season` | Summer | Time context |

**One raw number → 5 features!** This is feature engineering.

---

## 2. Why is it Important?

> **"Better features = Better model. Period."**  
> — A golden rule in ML

| Without Feature Engineering | With Feature Engineering |
|---|---|
| Feed raw price: ₹72.50 | Feed: price went up 3% in 5 days |
| Model sees a number | Model sees a **pattern** |
| Low accuracy | Higher accuracy |
| Model can't learn context | Model understands market conditions |

### Real-World Impact

In our PetroQuant project:
- Raw features: **9 columns** (price, volatility, etc.)
- After engineering: **22+ features**
- Result: XGBoost accuracy improved from ~50% (coin flip) to **56-59%** out-of-sample
- In trading, even 55% accuracy with proper risk management creates massive profits!

---

## 3. Types of Feature Engineering (Macro → Micro)

Let's go from the **biggest concepts** down to the **smallest details**.

###  MACRO Level — "What Kind of Data Do We Need?"

This is the **strategic** decision — what data sources to collect.

| Category | Example in General ML | Example in Our Project |
|----------|----------------------|----------------------|
| **Target Variable** | What are we predicting? | Will WTI oil price go UP or DOWN in 5 days? |
| **Domain Knowledge** | What factors affect the target? | Supply, demand, macroeconomics, sentiment |
| **Data Collection** | Where to get the data? | Yahoo Finance, FRED, EIA, CFTC APIs |

**Our Macro Decisions:**
```
Oil Price Direction ← depends on:
  ├── Price itself (WTI, Brent)         ← Yahoo Finance
  ├── Market fear (OVX volatility)      ← FRED API
  ├── US Dollar strength (DXY)          ← Yahoo Finance
  ├── Refinery demand (Crack Spread)    ← Yahoo Finance
  ├── Trader sentiment (COT positions)  ← CFTC API
  ├── Supply levels (Crude Stocks)      ← EIA API
  ├── Production capacity (Rig Count)   ← Baker Hughes CSV
  └── Government reserves (SPR)         ← EIA API
```

###  MESO Level — "What Transformations Do We Apply?"

This is the **tactical** level — deciding what math to apply to raw data.

| Transformation Type | What it Does | Example |
|---------------------|-------------|---------|
| **Returns / Changes** | How much did it change? | Price today ÷ Price yesterday − 1 |
| **Log Returns** | Mathematically better returns | ln(Price today ÷ Price yesterday) |
| **Rolling Statistics** | Average/std over a window | 20-day average of returns |
| **Z-Score** | How unusual is today vs history? | (Today − Average) ÷ Std Dev |
| **Momentum** | Speed and direction of change | 10-day percentage change |
| **Categorization** | Convert numbers to labels | Regime: BULL / CHOPPY / PANIC |

### 🔬 MICRO Level — "The Exact Formula and Parameters"

This is the **implementation** level — specific window sizes, normalization methods, etc.

| Decision | Options | Our Choice | Why? |
|----------|---------|------------|------|
| Return window | 1d, 5d, 10d, 20d | 1d AND 5d | Capture both daily noise and weekly trend |
| Rolling window for Z-Score | 10, 20, 50, 100 | 20 days | ~1 trading month, balances responsiveness vs stability |
| Log vs Simple returns | log or simple | Log returns | Additive property, works better for financial data |
| Number of HMM states | 2, 3, 4, 5 | 3 states | Financial literature says 3 regimes cover most markets |
| Forward-fill limit | 0, 7, 14, 30 | Varies by frequency | Weekly data → 7 days fill, Monthly → 30 days |

---

## 4. Step-by-Step with Easy Examples

Let's say oil price for the last 5 days was:

| Day | WTI Price |
|-----|-----------|
| Mon | $70.00 |
| Tue | $71.50 |
| Wed | $70.80 |
| Thu | $72.00 |
| Fri | $73.50 |

### Step 1: Log Return (How much did it change?)

```
Log Return = ln(Today Price / Yesterday Price)

Tuesday:  ln(71.50 / 70.00) = +0.0213 = +2.13%  ← price went UP
Wednesday: ln(70.80 / 71.50) = -0.0099 = -0.99%  ← price went DOWN
Thursday:  ln(72.00 / 70.80) = +0.0168 = +1.68%  ← price went UP
Friday:    ln(73.50 / 72.00) = +0.0207 = +2.07%  ← price went UP
```

**Why log returns?**
- They are **additive** (you can add daily returns to get weekly)
- They are **symmetric** (+10% and -10% are truly opposite)
- They work better for ML models

In our code (`strategy.py`, line 320):
```python
feat['WTI_LogRet_1d'] = np.log(feat['WTI_Close'] / feat['WTI_Close'].shift(1))
```

### Step 2: 5-Day Return (weekly trend)

```
5-Day Return = ln(Friday Price / Monday Price)
             = ln(73.50 / 70.00) = +0.0488 = +4.88%
```

This tells the model: "Over the whole week, oil went up ~5%"

In our code (line 321):
```python
feat['WTI_LogRet_5d'] = np.log(feat['WTI_Close'] / feat['WTI_Close'].shift(5))
```

### Step 3: Spread (price gap between WTI and Brent)

```
If WTI = $73.50 and Brent = $77.00
Spread = 73.50 - 77.00 = -$3.50

This is normal — Brent usually costs more than WTI.
```

In our code (line 324):
```python
feat['Spread'] = feat['WTI_Close'] - feat['Brent_Close']
```

### Step 4: Z-Score (How unusual is today?)

```
Z-Score = (Today's Spread - Average Spread over 20 days) / Std Dev of Spread

If average spread = -$4.00, std dev = $0.80:
Z-Score = (-3.50 - (-4.00)) / 0.80 = +0.625

Meaning: The spread is 0.625 standard deviations ABOVE normal
→ WTI is relatively expensive vs Brent today
```

| Z-Score | Meaning |
|---------|---------|
| 0 | Perfectly normal |
| +1 to +2 | Somewhat unusual (WTI relatively expensive) |
| > +2 | Very unusual — may revert back |
| -1 to -2 | WTI relatively cheap |
| < -2 | Extremely cheap — may bounce up |

In our code (lines 325-327):
```python
spread_mu  = feat['Spread'].rolling(20).mean()
spread_sig = feat['Spread'].rolling(20).std()
feat['Spread_Zscore'] = (feat['Spread'] - spread_mu) / (spread_sig + 1e-8)
```

### Step 5: Momentum (Speed of price movement)

```
10-Day Momentum = (Today's Price - Price 10 days ago) / Price 10 days ago

If WTI today = $73.50, WTI 10 days ago = $69.00:
Momentum = (73.50 - 69.00) / 69.00 = +0.0652 = +6.52%

→ Strong upward momentum!
```

In our code (line 328):
```python
feat['WTI_Mom_10'] = feat['WTI_Close'].pct_change(10)
```

### Step 6: Realized Volatility (How wild is the market?)

```
Realized Vol = Standard Deviation of daily returns × √252

(252 = trading days in a year → this "annualizes" the volatility)

If std of daily returns over 20 days = 0.015:
Realized Vol = 0.015 × √252 = 0.015 × 15.87 = 0.238 = 23.8% per year

→ Market is moderately volatile
```

| Realized Vol | Market Condition |
|-------------|-----------------|
| < 15% | Calm, low risk |
| 15-30% | Normal |
| 30-50% | High volatility, risky |
| > 50% | Extreme (crisis mode) |

In our code (line 335):
```python
feat['RealizedVol_20d'] = feat['WTI_LogRet_1d'].rolling(20).std() * np.sqrt(252)
```

---

## 5. Feature Engineering in Our PetroQuant Project

### Phase 1: Raw Data Collection (`features.py`)

We collect **9 raw market variables** from 5 different data sources:

| # | Raw Feature | Source | Frequency | Why We Need It |
|---|------------|--------|-----------|----------------|
| 1 | `WTI_Close` | Yahoo Finance (CL=F) | Daily | **THE price we're predicting** |
| 2 | `Brent_Close` | Yahoo Finance (BZ=F) | Daily | Global oil benchmark — spread reveals arbitrage |
| 3 | `OVX` | FRED (OVXCLS) | Daily | Market's fear gauge for oil — like VIX but for crude |
| 4 | `USD_Index` | Yahoo Finance (DX-Y.NYB) | Daily | Dollar strength — oil is priced in USD, inverse relationship |
| 5 | `Crack_3_2_1` | Yahoo Finance (RB=F, HO=F) | Daily | Refinery profit margin — measures refining demand |
| 6 | `Net_Speculative_Position` | CFTC Socrata API | Weekly | Big traders' bets on oil — sentiment indicator |
| 7 | `Crude_Stocks_1000bbl` | EIA API (WCESTUS1) | Weekly | US oil inventory — supply indicator |
| 8 | `US_Oil_Rigs` | Baker Hughes CSV | Monthly | Active drilling rigs — future supply indicator |
| 9 | `SPR_Stocks_1000bbl` | EIA API (WCSSTUS1) | Weekly | Government oil reserves — policy indicator |

### Phase 2: Data Pipeline Consolidation (`oil_data_pipeline_new.py`)

The pipeline handles a critical challenge: **different frequencies**.

```
 WTI Price:    Mon Tue Wed Thu Fri Mon Tue Wed ...  (DAILY)
 Crude Stocks: Wed ─── ─── ─── ─── Wed ─── ─── ... (WEEKLY)  
 Rig Count:    1st ─── ─── ─── ─── ─── ─── ─── ... (MONTHLY)
```

**Solution: Multi-Band Architecture with Forward-Fill**

| Band | Features | Fill Strategy |
|------|----------|--------------|
| **FAST** (Daily) | WTI, Brent, OVX, USD | No extra fill needed |
| **MEDIUM** (Weekly) | Crack Spread, COT, Crude Stocks, Rigs | ffill up to 7 days |
| **SLOW** (Monthly) | SPR, Rig Count | ffill up to 30 days |

```
Weekly data with 7-day forward fill:
  Day:     Wed   Thu   Fri   Sat   Sun   Mon   Tue   Wed
  Raw:     100   NaN   NaN   NaN   NaN   NaN   NaN   105
  Filled:  100   100   100   100   100   100   100   105
                 ↑ filled from Wed's value for up to 7 days
```

### Phase 3: Feature Engineering (`strategy.py` → `engineer_features()`)

This is where the **magic** happens. We transform 9 raw columns into **22+ meaningful features**:

#### Category 1: Price-Derived Features (8 features)

| Feature | Formula | What it Captures |
|---------|---------|-----------------|
| `WTI_LogRet_1d` | ln(WTI_t / WTI_{t-1}) | Yesterday's price change (momentum) |
| `WTI_LogRet_5d` | ln(WTI_t / WTI_{t-5}) | Weekly trend direction |
| `Brent_LogRet_1d` | ln(Brent_t / Brent_{t-1}) | Global oil benchmark change |
| `Brent_LogRet_5d` | ln(Brent_t / Brent_{t-5}) | Global weekly trend |
| `Spread` | WTI − Brent | Pricing gap (usually negative) |
| `Spread_Zscore` | (Spread − μ₂₀) / σ₂₀ | How unusual is today's gap? |
| `WTI_Mom_10` | (WTI_t − WTI_{t-10}) / WTI_{t-10} | 10-day momentum |
| `WTI_Mom_20` | (WTI_t − WTI_{t-20}) / WTI_{t-20} | 20-day momentum |

#### Category 2: Volatility-Derived Features (3 features)

| Feature | Formula | What it Captures |
|---------|---------|-----------------|
| `OVX_Chg_1d` | (OVX_t − OVX_{t-1}) / OVX_{t-1} | Is fear increasing TODAY? |
| `OVX_Chg_5d` | (OVX_t − OVX_{t-5}) / OVX_{t-5} | Is fear increasing THIS WEEK? |
| `RealizedVol_20d` | std(returns, 20d) × √252 | Actual price volatility |

#### Category 3: Macro-Derived Features (3 features)

| Feature | Formula | What it Captures |
|---------|---------|-----------------|
| `USD_Chg_1d` | (DXY_t − DXY_{t-1}) / DXY_{t-1} | Dollar strength change today |
| `USD_Chg_5d` | (DXY_t − DXY_{t-5}) / DXY_{t-5} | Dollar trend this week |
| `USD_ROC_10` | (DXY_t − DXY_{t-10}) / DXY_{t-10} | Dollar momentum |

#### Category 4: Positioning-Derived Features (2 features)

| Feature | Formula | What it Captures |
|---------|---------|-----------------|
| `NSP_Chg_5d` | % change in net spec. position | Are big traders changing bets? |
| `NSP_Zscore` | (NSP − μ₂₀) / σ₂₀ | Is positioning extreme vs recent history? |

#### Category 5: Supply-Derived Features (3 features)

| Feature | Formula | What it Captures |
|---------|---------|-----------------|
| `Crude_Chg_5d` | % change in crude stocks | Is supply increasing or decreasing? |
| `Rigs_Chg_5d` | % change in active rigs | Is future production expanding? |
| `SPR_Chg_5d` | % change in SPR stocks | Is government releasing/storing oil? |

#### Category 6: Refining-Derived Features (2 features)

| Feature | Formula | What it Captures |
|---------|---------|-----------------|
| `Crack_Zscore` | (Crack − μ₂₀) / σ₂₀ | Is refining margin unusually high/low? |
| `Crack_Chg_5d` | % change in crack spread | Is refinery demand trending? |

### Phase 4: Regime Detection (HMM — Hidden Feature)

The HMM creates an **invisible feature** — the market's "mood":

```
           [WTI_LogRet_1d]  ──┐
                               ├── HMM(3 states) ──→ Regime: BULL / CHOPPY / PANIC
          [RealizedVol_20d] ──┘
```

| Regime | Characteristics | Trading Impact |
|--------|----------------|----------------|
| **BULL**  | Low volatility, positive returns | Full position size (100%) |
| **CHOPPY**  | Medium volatility, uncertain direction | Half position size (50%) |
| **PANIC**  | High volatility, negative returns | Quarter position size (25%) |

This is itself a **feature engineering technique** — we're creating a categorical feature from continuous data using an unsupervised model!

---

## 6. The Logic Behind Our Choices

### Why These Specific Features?

Our feature choices are backed by financial theory and academic research:

| Feature | Financial Theory | Logic |
|---------|-----------------|-------|
| **WTI-Brent Spread** | Law of One Price | Same commodity, different markets → divergences are informative and mean-reverting |
| **Log Returns** | Geometric Brownian Motion | Standard in quantitative finance; additive, symmetric, normally distributed |
| **Z-Scores** | Mean Reversion | Extreme deviations from mean tend to revert → predictive signal |
| **OVX** | Volatility-Return Relationship | High fear → oversold markets → potential reversals |
| **USD Index** | Dollar Denomination Effect | Oil priced in USD → strong dollar = cheaper oil (inverse correlation) |
| **COT Net Speculative** | Sentiment / Crowding | When everyone is betting one way, the market often reverses |
| **Crack Spread** | Derived Demand | Refineries buy crude → high crack spread = strong demand |
| **Crude Stocks** | Supply-Demand Balance | Rising stocks = oversupply = bearish; falling stocks = bullish |
| **Momentum** | Trend Following | Financial markets exhibit short-term momentum (prices trend) |
| **HMM Regimes** | Regime Switching | Markets behave differently in bull/bear/crisis — one model can't fit all |

### Why Walk-Forward Validation?

```
Standard ML:     [Train on shuffled data] → [Test on shuffled data]
                  WRONG for time-series! (future data leaks into training)

Walk-Forward:    [Train: 2021-2022] → [Test: 2023 Q1]
                 [Train: 2021-2023 Q1] → [Test: 2023 Q2]
                 [Train: 2021-2023 Q2] → [Test: 2023 Q3]
                  CORRECT! Model only ever sees past data
```

---

## 7. Research Papers & References

### Core Feature Engineering & ML in Finance

| # | Paper / Resource | What it Covers | Link |
|---|-----------------|---------------|------|
| 1 | **"Machine Learning for Financial Market Prediction"** — Henrique et al., 2019 | Comprehensive survey of ML in financial prediction, covers feature engineering approaches | [Link](https://doi.org/10.1016/j.eswa.2019.01.012) |
| 2 | **"Feature Engineering for Machine Learning"** — Zheng & Casari, O'Reilly 2018 | Foundational book on feature engineering principles | [O'Reilly](https://www.oreilly.com/library/view/feature-engineering-for/9781491953235/) |

### Log Returns & Financial Time Series

| # | Paper / Resource | What it Covers | Link |
|---|-----------------|---------------|------|
| 3 | **"The Econometrics of Financial Markets"** — Campbell, Lo & MacKinlay, 1997 | Why log returns are used, statistical properties of financial returns | [Princeton Press](https://press.princeton.edu/books/hardcover/9780691043012/the-econometrics-of-financial-markets) |
| 4 | **"Stocks, Bonds, Bills, and Inflation"** — Ibbotson, 2011 | Empirical evidence on financial return distributions and momentum | [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2055431) |

### HMM Regime Detection in Financial Markets

| # | Paper / Resource | What it Covers | Link |
|---|-----------------|---------------|------|
| 5 | **"A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle"** — Hamilton, 1989 | Foundational paper on regime-switching models in economics | [JSTOR](https://doi.org/10.2307/1912559) |
| 6 | **"Regime Changes and Financial Markets"** — Ang & Timmermann, 2012 | How HMMs model bull/bear/crisis regimes in financial markets | [Annual Reviews](https://doi.org/10.1146/annurev-financial-110311-101808) |
| 7 | **"Hidden Markov Models in Finance"** — Mamon & Elliott, 2007 | Complete textbook on applying HMMs to financial data | [Springer](https://link.springer.com/book/10.1007/0-387-71163-5) |

### XGBoost & Tree-Based Models

| # | Paper / Resource | What it Covers | Link |
|---|-----------------|---------------|------|
| 8 | **"XGBoost: A Scalable Tree Boosting System"** — Chen & Guestrin, 2016 | The original XGBoost paper — algorithm we use for prediction | [arXiv](https://arxiv.org/abs/1603.02754) |
| 9 | **"Gradient Boosting for Financial Applications"** — Natekin & Knoll, 2013 | Why gradient boosting works well for financial feature engineering | [Frontiers](https://doi.org/10.3389/fnbot.2013.00021) |

### Oil Market Specific Features

| # | Paper / Resource | What it Covers | Link |
|---|-----------------|---------------|------|
| 10 | **"Forecasting Crude Oil Prices"** — Baumeister & Kilian, 2015 | Which economic indicators predict oil prices (inventories, rigs, etc.) | [NBER](https://www.nber.org/papers/w18724) |
| 11 | **"The Role of Inventories and Speculative Trading in the Global Market for Crude Oil"** — Kilian & Murphy, 2014 | Why crude stocks and speculative positions matter for oil prices | [Journal of Applied Econometrics](https://doi.org/10.1002/jae.2322) |
| 12 | **"Oil Price Volatility: The Role of OVX"** — Maghyereh et al., 2016 | Research on OVX as a predictor of oil price movements | [Energy Economics](https://doi.org/10.1016/j.eneco.2016.05.020) |
| 13 | **"The Effect of the US Dollar on Oil Prices"** — Zhang et al., 2008 | Academic evidence on USD-Oil inverse relationship we capture with DXY | [Energy Policy](https://doi.org/10.1016/j.enpol.2008.06.006) |

### GARCH Volatility Modeling

| # | Paper / Resource | What it Covers | Link |
|---|-----------------|---------------|------|
| 14 | **"Generalized Autoregressive Conditional Heteroskedasticity"** — Bollerslev, 1986 | Original GARCH paper — the volatility model we use in VolatilityEngine | [Journal of Econometrics](https://doi.org/10.1016/0304-4076(86)90063-1) |
| 15 | **"GARCH Models for Oil Price Forecasting"** — Wei et al., 2010 | GARCH specifically applied to crude oil volatility | [Energy Economics](https://doi.org/10.1016/j.eneco.2010.04.010) |

### Walk-Forward Validation

| # | Paper / Resource | What it Covers | Link |
|---|-----------------|---------------|------|
| 16 | **"Advances in Financial Machine Learning"** — de Prado, 2018 | Walk-forward validation, anti-look-ahead bias in financial ML | [Wiley](https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086) |

---

## 8. Summary for Faculty Presentation

### Your Feature Engineering Journey — Quick Script

> "In our PetroQuant project, we did feature engineering in **4 phases**:"

**Phase 1 — Data Collection (Macro Level):**
"We identified 9 raw data sources covering price, volatility, macroeconomics, sentiment, and supply — based on financial theory and research papers by Baumeister & Kilian (2015) and Zhang et al. (2008)."

**Phase 2 — Data Pipeline (Meso Level):**
"We built a multi-band pipeline that handles daily, weekly, and monthly data with intelligent forward-filling. This is important because our features come at different frequencies."

**Phase 3 — Feature Transformation (Micro Level):**
"We engineered 22+ derived features using:
- **Log returns** (1d and 5d) for both WTI and Brent
- **Z-Scores** for spread, crack spread, and trader positioning — capturing mean-reversion signals
- **Momentum** indicators at 10d and 20d horizons
- **Rate of change** for USD, volatility, stocks, and rigs
- **Realized volatility** (20d annualized)

These choices are backed by Campbell, Lo & MacKinlay (1997) for returns and Hamilton (1989) for regime-switching."

**Phase 4 — Regime Detection (Advanced Feature):**
"We used a Hidden Markov Model to create an unsupervised categorical feature — market regime (BULL/CHOPPY/PANIC). This is itself feature engineering — converting continuous returns and volatility into discrete market states, following Ang & Timmermann (2012)."

**Validation:**
"We used walk-forward validation (de Prado, 2018) to ensure no future data leaks into training, achieving 56-59% out-of-sample accuracy."

### The Big Picture Diagram

```
                        ┌─────────────────────────────────────────┐
                        │          RAW DATA SOURCES               │
                        │  (Yahoo, FRED, EIA, CFTC, Baker Hughes) │
                        └────────────────┬────────────────────────┘
                                         │
                                    [features.py]
                                  9 raw variables
                                         │
                                 [oil_data_pipeline_new.py]
                              Multi-band consolidation
                              Forward-fill alignment
                                         │
                                    [strategy.py]
                            ┌────────────┼────────────┐
                            │            │            │
                     Price Features  Vol Features  Macro Features
                     (8 features)   (3 features)  (3 features)
                            │            │            │
                     Supply Features  Position Features  Crack Features
                     (3 features)     (2 features)       (2 features)
                            │            │            │
                            └────────────┼────────────┘
                                         │
                                    22+ Engineered
                                      Features
                                         │
                            ┌────────────┼────────────┐
                            │                         │
                     HMM Regime Detection      XGBoost Prediction
                     (BULL/CHOPPY/PANIC)      (Walk-Forward, 5d)
                            │                         │
                            └────────────┬────────────┘
                                         │
                              Trading Signals + CI
                            (BUY/SELL/HOLD + 95% CI)
```

---

> **Remember:** Feature engineering is not just math — it's **domain knowledge encoded as data transformations**. The reason our project works is because we understood *what drives oil prices* (economics, geopolitics, sentiment) and translated that understanding into features the XGBoost model could learn from.
