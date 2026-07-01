# PetroQuant -- A Complete Guide to the Oil Trading Strategy System

---

## Table of Contents

1. [What Is This Project About](#1-what-is-this-project-about)
2. [The Big Picture -- How Everything Connects](#2-the-big-picture----how-everything-connects)
3. [The Data Pipeline -- Gathering Intelligence](#3-the-data-pipeline----gathering-intelligence)
4. [The Feature Registry -- What Data We Collect and Why](#4-the-feature-registry----what-data-we-collect-and-why)
5. [Feature Engineering -- Turning Raw Data Into Useful Patterns](#5-feature-engineering----turning-raw-data-into-useful-patterns)
6. [The Strategy Engine](#6-the-strategy-engine)
   - [6.1 HMM Regime Detection -- Reading the Market's Mood](#61-hmm-regime-detection----reading-the-markets-mood)
   - [6.2 XGBoost Walk-Forward Prediction -- The Decision Brain](#62-xgboost-walk-forward-prediction----the-decision-brain)
   - [6.3 Signal Generation and Position Sizing](#63-signal-generation-and-position-sizing)
7. [The Volatility Engine -- Measuring Uncertainty](#7-the-volatility-engine----measuring-uncertainty)
8. [Multi-Horizon Forecasting -- Looking Into the Future](#8-multi-horizon-forecasting----looking-into-the-future)
9. [The Backtesting Engine -- Testing Against History](#9-the-backtesting-engine----testing-against-history)
10. [The Dashboard -- Visualizing Everything](#10-the-dashboard----visualizing-everything)
11. [The Execution Flow -- How It All Runs](#11-the-execution-flow----how-it-all-runs)
12. [Glossary of Key Terms](#12-glossary-of-key-terms)

---

## 1. What Is This Project About

PetroQuant is a quantitative trading strategy system designed to analyze the crude oil market and generate informed trading decisions. In simple terms, it is a software system that:

- Collects real-time and historical data about the oil market from multiple authoritative sources
- Processes that data to identify meaningful patterns that humans might miss
- Uses artificial intelligence models to detect the current "mood" of the market and predict future price movements
- Tests its own predictions against years of historical data to prove whether its approach actually works
- Presents everything in an interactive visual dashboard

The core question this system tries to answer is: **if we combine data about oil prices, volatility, the US dollar, refinery economics, trader positioning, oil storage levels, drilling activity, and government reserves -- can a machine learning model make better-than-random trading decisions?**

The answer, as validated by the backtesting engine, is yes.

---

## 2. The Big Picture -- How Everything Connects

The system follows a strict four-stage pipeline. Each stage feeds into the next, and every piece of data flows in one direction:

```
Stage 1: DATA PIPELINE          Stage 2: STRATEGY ENGINE
+------------------------+      +----------------------------+
| Collect data from      |      | Engineer features from     |
| 9 different sources    | ---> | raw data, detect market    |
| (APIs, files, web)     |      | regimes, predict direction |
+------------------------+      +----------------------------+
                                            |
                                            v
Stage 3: BACKTESTER              Stage 4: DASHBOARD
+------------------------+      +----------------------------+
| Simulate trading with  |      | Render 10-panel            |
| historical signals,    | ---> | interactive visualization  |
| compute 20+ metrics    |      | saved as HTML              |
+------------------------+      +----------------------------+
```

**Why this architecture matters:** Each stage is independent. If you want to test a different data source, you only change Stage 1. If you want to try a new AI model, you only change Stage 2. The backtester and dashboard remain the same regardless. This modular design makes the system easy to extend, debug, and maintain.

---

## 3. The Data Pipeline -- Gathering Intelligence

**File:** `oil_data_pipeline_new.py`

### What It Does

The data pipeline is the foundation of the entire system. Its job is to collect data from nine different sources, align everything to daily dates, fill in gaps where data is missing, and produce a single unified table called `master_df`. Every row in this table represents one calendar day, and every column represents one piece of market information.

### Why It Exists

Oil prices are affected by dozens of factors simultaneously. A human trader might watch oil prices and read the news, but they cannot easily track the US dollar index, oil volatility, refinery margins, speculator positioning, storage tank levels, drilling rig counts, and government reserves -- all at once, every day, for five years. This pipeline does exactly that.

### How It Works, Step by Step

**Step 1: Check the Cache**

Before doing any work, the pipeline checks whether it has a recent copy of the data already saved as a CSV file. If a file exists and is less than 24 hours old, the system loads it directly instead of re-fetching from the internet. This is a practical decision: API calls are rate-limited (most services allow only a certain number of requests per day), so we avoid wasting them.

```
Example: You run the system at 9 AM. The pipeline fetches all data and saves
"master_oil_features_20260310_090000.csv". At 3 PM, you run it again. Instead
of making dozens of API calls, it loads the morning file in under a second.
```

**Step 2: Load API Keys**

The pipeline reads API keys from a `.env` file. These keys are credentials that grant access to data services like FRED (the Federal Reserve Economic Database) and the EIA (Energy Information Administration). This is similar to a password for a website -- without the correct key, the data service will refuse the request.

**Step 3: Fetch Each Feature**

The pipeline iterates over every feature defined in the `FEATURES` registry (more on this in the next section). For each one, it calls the appropriate fetch function, which contacts an API or reads a file. Features are organized into three speed bands:

| Band   | Frequency | Examples                         | Why This Speed |
|--------|-----------|----------------------------------|----------------|
| Fast   | Daily     | Oil prices, volatility, USD      | These change every trading day |
| Medium | Weekly    | Inventory reports, rig counts    | Published on weekly or monthly schedules |
| Slow   | Monthly   | Strategic Petroleum Reserve      | Government data, updated infrequently |

**Step 4: Build the Master Index**

The system identifies "base" features -- WTI Close and Brent Close -- and uses them to define the master date range. If a date has no oil price (weekends, holidays), that row is excluded. This ensures every row in the final table corresponds to a real trading day.

**Step 5: Join Non-Base Features**

Features that arrive at different frequencies (weekly inventory data, monthly rig counts) must be aligned to daily dates. The system handles this through:

- **Resampling:** Converting weekly data to daily by repeating the last known value until a new value arrives.
- **Forward-filling (ffill):** If crude oil inventory data is released every Wednesday, the system carries Wednesday's number through Thursday, Friday, Monday, and Tuesday -- until the next Wednesday. Each feature has a configurable `ffill_limit` that prevents carrying stale data too far (for example, weekly data is forward-filled for a maximum of 7 days; monthly data for 30 days).

```
Example: EIA crude oil inventory report is released on Wednesday, March 5,
showing 440 million barrels. The pipeline fills March 6, 7, 10, 11, 12 with
the same 440 million value. On March 12, a new report shows 438 million.
Now March 12 onwards uses 438 million.
```

**Step 6: Quality Report**

The pipeline prints a coverage report showing, for each column, how many days have valid data and what percentage of the total that represents. A column with 100% coverage has data for every single trading day. A column with 80% coverage is missing data for one in five days. This transparency allows the user to immediately see if a data source has failed or if a feature has poor coverage.

**Step 7: Save the Result**

The final `master_df` is saved as a timestamped CSV file in the `output/` directory. This serves both as a cache for the next run and as an audit trail.

---

## 4. The Feature Registry -- What Data We Collect and Why

**File:** `features.py`

### What It Does

The feature registry is a centralized catalog of every piece of data the system collects. Each feature is defined as a self-contained entry that specifies its name, update frequency, data source, maximum forward-fill duration, and the function used to fetch it. The pipeline reads this registry and automatically handles everything else.

### Why It Is Designed This Way

This design pattern is called a "registry." The advantage is that adding a new data source requires changing only one file (`features.py`) -- you write a fetch function and add a dictionary entry. The pipeline automatically picks it up. No other file needs to change. This makes the system extensible without risk of breaking existing functionality.

### Complete Feature Breakdown

#### 1. WTI Close (West Texas Intermediate Crude Oil Price)

- **Source:** Yahoo Finance (ticker CL=F)
- **Frequency:** Daily
- **What it measures:** The price per barrel at which WTI crude oil futures closed for the day. WTI is the benchmark crude oil grade for the United States.
- **Why it matters:** This is the primary variable the system is trying to predict. Every other feature exists to help explain or forecast movements in this price.
- **Example:** If WTI_Close is $72.50 on Monday and $74.10 on Tuesday, oil rose by $1.60 (approximately 2.2%).

#### 2. Brent Close (Brent Crude Oil Price)

- **Source:** Yahoo Finance (ticker BZ=F)
- **Frequency:** Daily
- **What it measures:** The price per barrel of Brent crude oil, which is the benchmark for international oil markets (produced from fields in the North Sea).
- **Why it matters:** WTI and Brent typically move together, but the spread (difference) between them reveals important information. When Brent trades significantly above WTI, it often indicates that international demand is strong relative to US supply. When the spread narrows, it may signal converging supply and demand conditions globally.
- **Example:** If WTI is $72.50 and Brent is $76.80, the Brent premium is $4.30. If this spread was $2.00 last month, the widening may indicate growing international demand or US supply abundance.

#### 3. OVX (CBOE Crude Oil Volatility Index)

- **Source:** FRED (Federal Reserve) -- series OVXCLS
- **Frequency:** Daily
- **What it measures:** The market's expectation of how much oil prices will swing over the next 30 days. It is calculated from the prices of oil options (financial contracts that give the right to buy or sell oil at a specific price). Higher OVX means the market expects larger price movements. It is often called the "fear gauge" for oil.
- **Why it matters:** When OVX is high (say 45%), the market is uncertain and expects big swings. When OVX is low (say 22%), the market is calm. This information is critical for sizing positions -- you want smaller trades during uncertain times and can afford larger trades when markets are calm.
- **Example:** Before a major OPEC meeting, OVX might rise from 25% to 38% as traders anticipate a big decision. After the meeting, it drops back to 28%. The strategy uses this to reduce position sizes during the uncertainty period.

#### 4. USD Index (US Dollar Index)

- **Source:** Yahoo Finance (ticker DX-Y.NYB)
- **Frequency:** Daily
- **What it measures:** The value of the US dollar relative to a basket of six major foreign currencies (Euro, Yen, British Pound, Canadian Dollar, Swedish Krona, Swiss Franc).
- **Why it matters:** Oil is priced in US dollars globally. When the dollar strengthens, oil becomes more expensive for buyers in other currencies, which tends to reduce demand and push prices down. When the dollar weakens, the reverse happens. This inverse relationship is one of the most well-documented patterns in commodity markets.
- **Example:** If the USD Index rises from 103 to 106 over a month, oil prices often face downward pressure because foreign buyers need to spend more of their local currency to purchase the same barrel.

#### 5. Crack Spread (3-2-1 Crack Spread)

- **Source:** Calculated from Yahoo Finance (tickers RB=F, HO=F, CL=F)
- **Frequency:** Daily
- **What it measures:** The profit margin of oil refineries. The "3-2-1" formula assumes a refinery takes 3 barrels of crude oil and produces 2 barrels of gasoline (RBOB) and 1 barrel of heating oil (HO). The crack spread is: (2 x gasoline price + 1 x heating oil price - 3 x crude oil price) / 3.
- **Why it matters:** When the crack spread is high, refineries are making good money, which means they are motivated to buy more crude oil (driving prices up). When the crack spread is low or negative, refineries may cut production, reducing demand for crude. This feature acts as a real-world demand indicator.
- **Example:** If gasoline is $2.80/gallon, heating oil is $2.60/gallon, and crude is $72/barrel, the 3-2-1 crack spread tells us refinery profitability. A rising crack spread over several weeks signals strengthening demand for crude.

#### 6. Net Speculative Position (CFTC Commitments of Traders)

- **Source:** CFTC Socrata API
- **Frequency:** Weekly (released every Friday for the prior Tuesday)
- **What it measures:** The difference between the number of "long" contracts (bets that oil will rise) and "short" contracts (bets that oil will fall) held by speculative traders. These are hedge funds, commodity trading advisors, and other non-commercial participants.
- **Why it matters:** When speculators are heavily long, it means a lot of money is betting on rising prices. However, extremely lopsided positioning can also signal that a reversal is near -- if everyone has already bought, who is left to push prices higher? Conversely, extreme short positioning often precedes a rally.
- **Example:** If net speculative positions jump from +180,000 contracts to +290,000 contracts over three weeks, speculative sentiment has turned strongly bullish. The strategy watches not just the level, but the rate of change and the z-score (how extreme the current position is compared to recent history).

#### 7. Crude Stocks (US Commercial Crude Oil Inventories)

- **Source:** EIA API (series WCESTUS1)
- **Frequency:** Weekly
- **What it measures:** The total volume of crude oil (in thousands of barrels) stored in commercial facilities across the United States. This does not include the Strategic Petroleum Reserve (that is tracked separately).
- **Why it matters:** Inventories are a direct measure of supply and demand balance. If inventories are rising, it means supply exceeds demand (bearish for prices). If inventories are falling, demand exceeds supply (bullish). The weekly EIA inventory report is one of the most closely watched data releases in energy markets.
- **Example:** If crude stocks fall by 6 million barrels in a single week, it indicates strong demand or reduced imports. Prices typically react positively to such draws.

#### 8. US Oil Rig Count

- **Source:** Baker Hughes Rig Count data (CSV file)
- **Frequency:** Monthly
- **What it measures:** The number of active drilling rigs producing oil in the United States. Each rig represents a well being drilled to extract oil from underground reservoirs.
- **Why it matters:** Rig counts are a leading indicator of future supply. More rigs today means more oil production 3 to 6 months from now, which puts downward pressure on future prices. Fewer rigs means future supply will tighten, supporting prices. The rig count responds to oil prices with a delay -- when prices fall, companies cut rigs; when prices rise, they add rigs.
- **Example:** After oil prices dropped 40% in late 2014, the US rig count fell from over 1,600 to below 400 over the following year. The massive reduction in drilling eventually led to a supply shortage and contributed to the price recovery.

#### 9. Strategic Petroleum Reserve (SPR)

- **Source:** EIA API (series WCSSTUS1)
- **Frequency:** Weekly
- **What it measures:** The volume of crude oil held in the US government's Strategic Petroleum Reserve, stored in salt caverns along the Gulf of Mexico coast. The SPR was created in 1975 after the Arab oil embargo to protect against supply disruptions.
- **Why it matters:** SPR releases flood the market with additional supply, which pushes prices down. SPR replenishment (buying crude to refill the reserve) adds demand, which pushes prices up. In 2022, the US government released a historic 180 million barrels from the SPR to combat high gasoline prices, and later began buying it back at lower prices.
- **Example:** If SPR stocks drop by 5 million barrels in a month, the government is selling crude into the market, adding supply and pressuring prices lower.

---

## 5. Feature Engineering -- Turning Raw Data Into Useful Patterns

**File:** `strategy.py`, method `engineer_features()`

### What It Does

Feature engineering transforms the nine raw data streams into approximately 25 calculated variables that are more informative to a machine learning model. Raw prices alone tell the model "oil is $72 today," but engineered features tell it "oil rose 3% over the last 5 days, volatility is falling, the dollar is weakening, and speculator positioning is at a 2-year extreme."

### Why It Is Necessary

Machine learning models do not understand context the way humans do. A price of $72 means nothing on its own. But a 5-day logarithmic return of +0.03 (meaning a 3% rise over a week) is a pattern the model can learn from. Feature engineering translates raw numbers into the language of patterns.

### Every Engineered Feature Explained

#### Price-Derived Features

| Feature | Calculation | What It Captures |
|---------|-------------|------------------|
| WTI_LogRet_1d | log(today's price / yesterday's price) | Daily momentum -- did oil go up or down today, and by how much? |
| WTI_LogRet_5d | log(today's price / price 5 days ago) | Weekly momentum -- the trend over the past week |
| Brent_LogRet_1d | Same as above, for Brent | Daily momentum of the international benchmark |
| Brent_LogRet_5d | Same as above, for Brent | Weekly momentum of the international benchmark |
| Spread | WTI price minus Brent price | The premium or discount of US oil vs international oil |
| Spread_Zscore | (Spread - 20-day average spread) / 20-day standard deviation | How extreme the current spread is compared to recent history. A z-score of +2 means the spread is two standard deviations above normal. |
| WTI_Mom_10 | Percentage change over 10 days | Medium-term directional pressure |
| WTI_Mom_20 | Percentage change over 20 days | Longer-term directional trend |

**Why logarithmic returns instead of simple percentage changes?** Logarithmic returns have two mathematical properties that make them better for statistical models: they are symmetric (a 50% gain followed by a 50% loss does not return to zero, but log returns handle this correctly) and they are additive (you can add daily log returns to get weekly returns, which simplifies calculations).

#### Volatility-Derived Features

| Feature | Calculation | What It Captures |
|---------|-------------|------------------|
| OVX_Chg_1d | Percentage change in OVX over 1 day | Is fear increasing or decreasing today? |
| OVX_Chg_5d | Percentage change in OVX over 5 days | Is the fear trend rising or falling this week? |
| RealizedVol_20d | Standard deviation of daily log returns over 20 days, annualized | How much has the price actually been swinging recently? This is "realized" (actual) volatility, as opposed to OVX which is "implied" (expected) volatility |

**The relationship between realized and implied volatility** is important: when implied volatility (OVX) is much higher than realized volatility, the market is pricing in more fear than what is actually happening. This often precedes calmer periods. When realized volatility exceeds implied, the market has been surprised by actual moves, which can mean further instability.

#### Macro-Derived Features

| Feature | Calculation | What It Captures |
|---------|-------------|------------------|
| USD_Chg_1d | Percentage change in USD Index over 1 day | Did the dollar strengthen or weaken today? |
| USD_Chg_5d | Percentage change in USD Index over 5 days | Dollar trend over the week |
| USD_ROC_10 | Rate of change over 10 days | Medium-term dollar trend |

#### Positioning-Derived Features

| Feature | Calculation | What It Captures |
|---------|-------------|------------------|
| NSP_Chg_5d | Percentage change in net speculative position over 5 days | Are speculators adding to or cutting their bets? |
| NSP_Zscore | (Current position - 20-day average) / 20-day standard deviation | How extreme is current speculator positioning compared to recent weeks? Extreme readings (above +2 or below -2) often indicate crowded trades. |

#### Supply-Derived Features

| Feature | Calculation | What It Captures |
|---------|-------------|------------------|
| Crude_Chg_5d | Percentage change in crude stocks over 5 days | Are inventories building (bearish) or drawing (bullish)? |
| Rigs_Chg_5d | Percentage change in rig count over 5 days | Is drilling activity increasing or decreasing? |
| SPR_Chg_5d | Percentage change in SPR stocks over 5 days | Is the government releasing or accumulating reserves? |

#### Refinery Economics Features

| Feature | Calculation | What It Captures |
|---------|-------------|------------------|
| Crack_Zscore | (Current crack spread - 20-day average) / 20-day standard deviation | How extreme is refinery profitability right now? |
| Crack_Chg_5d | Percentage change in crack spread over 5 days | Is refinery economics improving or deteriorating? |

### Data Cleaning

After engineering all features, the system performs two cleaning steps:

1. **Remove infinite and null values.** Some calculations can produce infinity (for example, dividing by zero when a price does not change). These are replaced with null and then all rows with any null values are dropped.
2. **Remove zero-variance columns.** If a feature has the same value for every row (variance equals zero), it provides no information and is removed.

---

## 6. The Strategy Engine

**File:** `strategy.py`

The strategy engine is the intelligence layer of the system. It takes the engineered features and makes actual trading decisions. It does this through three interconnected components.

### 6.1 HMM Regime Detection -- Reading the Market's Mood

#### What Is a Hidden Markov Model (HMM)?

An HMM is a statistical model that assumes the thing you are observing (oil price movements) is generated by a system that can be in one of several hidden "states." You cannot directly see which state the system is in, but you can infer it from the patterns in the data.

Think of it this way: imagine you are standing outside a building and can hear sounds from inside, but cannot see in. Some days you hear calm, quiet activity (office day). Other days you hear lively parties (celebration day). And occasionally you hear fire alarms and running footsteps (emergency day). Based only on the sounds, you can estimate which "state" the building is in today. The HMM does the same thing, but with price returns and volatility numbers instead of sounds.

#### How It Works in This System

The HMM is configured with 3 states and is trained on two inputs: daily log returns and 20-day realized volatility. After training, it assigns each historical day to one of three states.

The system then labels these states based on their characteristics:

| Regime | Characteristics | What It Means |
|--------|----------------|---------------|
| BULL | Low volatility, positive average returns | Market is calm and trending upward. This is the ideal environment for larger, more confident trades. |
| CHOPPY | Moderate volatility, returns near zero | Market is uncertain, moving sideways without a clear direction. Trades should be smaller because predictions are less reliable. |
| PANIC | High volatility, negative average returns | Market is in crisis mode with large, unpredictable swings. Think of the 2020 COVID crash or the 2022 energy crisis. Position sizes should be minimal to protect capital. |

#### Why Three States?

Two states (bull/bear) are too coarse -- they miss the important "uncertain" state where the market is moving sideways and trading is risky. Four or more states tend to overfit (the model memorizes noise in the training data rather than learning real patterns) and produce states that are difficult to interpret.

#### Real-World Example

During 2021-2022, the HMM would have detected a regime sequence like:
- Late 2021: BULL (oil rising steadily from $65 to $80)
- February 2022: PANIC (Russia-Ukraine conflict, oil spiking to $130)
- Mid 2022: CHOPPY (prices swinging between $85 and $110 with no clear direction)
- Late 2022: BULL (prices stabilizing around $75-80)

This regime awareness prevents the model from applying the same trading rules in all conditions. A "buy" signal during BULL is acted on with full conviction, while the same signal during PANIC is acted on with only 25% of normal size.

### 6.2 XGBoost Walk-Forward Prediction -- The Decision Brain

#### What Is XGBoost?

XGBoost (Extreme Gradient Boosting) is a machine learning algorithm that builds a series of decision trees, where each new tree learns from the mistakes of the previous ones. A single decision tree is like a flowchart: "Is the 5-day return positive? If yes, is the OVX below 30? If yes, predict UP." XGBoost chains hundreds of these small trees together, and their combined wisdom is much more powerful than any single tree.

In quantitative finance, XGBoost is widely used because it handles mixed data types well (prices, percentages, z-scores), is resistant to overfitting when properly configured, and can capture non-linear relationships (for example, "rising oil prices combined with rising OVX indicates trouble, but rising prices with falling OVX indicates healthy growth").

#### What Is Walk-Forward Validation (and how is it used here)?

This is where PetroQuant distinguishes itself from naive approaches. Many beginner machine learning projects train a model on all historical data and then test it on the same data. This produces misleadingly good results because the model has already "seen" the answers (look-ahead bias).

**Walk-forward validation** works by simulating how a real trader operates: you can only train on the past, and you are always predicting the unknown future. As time moves forward, the model periodically pauses to "re-learn" from the newly revealed data before predicting the next batch.

PetroQuant uses two distinct walk-forward cycles depending on the timeframe:

**1. The Daily Strategy (Backtesting)**
For the daily-resolution model, we use a quarterly (63 trading days) walk-forward cycle:
1. Train the model on the first 500 days of historical data.
2. Use that trained model to predict the next 63 days (one quarter).
3. Roll forward: Train a new model on the first 563 days.
4. Predict the next 63 days.
5. Repeat until all historical data is covered.

**2. The Intraday Strategy (Live Paper Trading)**
For the live 1-minute paper trading engine, the market moves much faster, so the walk-forward cycle is accelerated:
1. The engine fetches the last ~7 days of 1-minute bars (approx. 5,000+ bars).
2. It trains the XGBoost model on this recent window.
3. The model predicts the WTI price direction for the next 5 minutes to take a live trade.
4. **Every 4 hours (240 minutes)**, the system automatically triggers a walk-forward retrain. It grabs the newest 1-minute bars that just occurred and rebuilds the model so it adapts to the intraday momentum of that specific session.

**Why walk-forward?** Markets evolve constantly. The relationship between volatility and oil prices in a 2022 panic is not identical to a calm day in 2024. By continuously retraining as time walks forward, the model adapts to changing market dynamics without ever cheating by looking into the future.

#### The Prediction Target

For each trading day, the model predicts a simple binary question: "Will the WTI price be higher 5 days from now than it is today?" This is classified as UP (1) or DOWN (0).

The choice of 5 days (one trading week) as the prediction horizon balances two concerns: shorter horizons (1 day) are dominated by random noise, making reliable prediction very difficult; longer horizons (30+ days) are influenced by unpredictable events (geopolitical crises, surprise OPEC decisions). Five days captures short-term trends that are influenced by the features we track.

#### Model Configuration

The XGBoost model uses carefully tuned parameters:

| Parameter | Value | Purpose |
|-----------|-------|---------|
| max_depth: 4 | Limits tree complexity | Prevents the model from memorizing random noise |
| n_estimators: 300 | Number of trees | Enough trees for nuanced learning, not so many that training is slow |
| learning_rate: 0.05 | How much each tree contributes | Small steps produce more stable, reliable models |
| subsample: 0.8 | Fraction of data used per tree | Introduces randomness that improves generalization |
| colsample_bytree: 0.8 | Fraction of features used per tree | Forces the model to learn patterns from different feature combinations |
| min_child_weight: 5 | Minimum observations per leaf node | Prevents the model from making decisions based on too few data points |

### 6.3 Signal Generation and Position Sizing

#### From Probability to Action

The XGBoost model does not simply say "buy" or "sell." It outputs a probability between 0 and 1, representing how likely it thinks the price is to be higher in 5 days.

The system converts this probability into a signal using two thresholds:

| Probability | Signal | Meaning |
|------------|--------|---------|
| Above 55% | BUY | Model has sufficient confidence that prices will rise |
| Below 45% | SELL | Model has sufficient confidence that prices will fall |
| Between 45% and 55% | HOLD | Model is not confident enough to take action |

The gap between 45% and 55% is intentional. It creates a "no-trade zone" that prevents the system from taking action when the model is essentially guessing. A model that says "52% chance of going up" barely has an edge, and transaction costs could easily erase any profit.

#### Regime-Aware Position Sizing

Not all trades are equal. A BUY signal during a calm bull market should carry more weight than the same signal during a volatile panic. The system implements this through regime-dependent position sizing:

| Regime | Position Multiplier | Rationale |
|--------|-------------------|-----------|
| BULL | 1.0 (full size) | Market conditions are favorable, volatility is low, predictions are most reliable |
| CHOPPY | 0.5 (half size) | Uncertain conditions make predictions less reliable, so we reduce exposure |
| PANIC | 0.25 (quarter size) | High volatility makes large positions dangerous, capital preservation is the priority |

The system also factors in model confidence. A 70% probability prediction gets a larger position than a 56% prediction, even if both generate a BUY signal. The final position size is:

```
Position Size = Signal Direction x Regime Multiplier x Confidence Factor
```

This means a BUY signal with 70% probability during a BULL regime gets a position of approximately 1.0 x 1.0 x 0.9 = 0.9 (near full size), while a BUY signal with 56% probability during PANIC gets 1.0 x 0.25 x 0.56 = 0.14 (a very small position).

---

## 7. The Volatility Engine -- Measuring Uncertainty

**File:** `strategy.py`, class `VolatilityEngine`

### What It Does

The Volatility Engine estimates how much oil prices might realistically move over a given time period. It produces a "95% confidence interval" -- a price range that the model believes will contain the actual future price 95 times out of 100.

### Why It Uses Three Layers

No single volatility measure is perfect. Each has strengths and weaknesses:

#### Layer 1: GARCH(1,1) -- Statistical Volatility Model

GARCH stands for Generalized Autoregressive Conditional Heteroskedasticity. Behind the complex name is a simple idea: volatility clusters. Big price moves tend to follow big price moves, and calm days tend to follow calm days. GARCH captures this clustering mathematically.

The model has three key parameters:
- **Alpha** (reaction speed): How much today's surprise contributes to tomorrow's volatility estimate
- **Beta** (persistence): How long elevated volatility lasts before decaying
- **Omega** (baseline): The long-run average level of volatility the market reverts to

GARCH is excellent for medium-term forecasts (15 to 60 days) because it captures the mean-reverting nature of volatility -- after a shock, volatility gradually returns to normal.

```
Example: Oil prices drop 8% in one day due to a surprise OPEC decision.
GARCH immediately raises its volatility estimate (alpha kicks in).
Over the following weeks, if no further shocks occur, the volatility
estimate gradually declines back to normal (beta causes decay toward omega).
```

#### Layer 2: ATR (Average True Range) -- Price-Action Volatility

ATR measures the average daily price movement over the past 14 days. It is calculated from actual high-low price ranges (or daily close changes when intraday data is unavailable). ATR is purely observational -- it makes no assumptions about statistical distributions.

ATR excels at very short horizons (1 to 7 days) because it directly reflects how much the price has actually been moving recently. If oil has been swinging $2 per day for the last two weeks, ATR says "expect roughly $2 daily swings."

#### Layer 3: OVX (Implied Volatility) -- The Market's Own Forecast

OVX is derived from the prices of oil options contracts. When traders are willing to pay high premiums for options (insurance against price moves), OVX is high. Implied volatility represents the collective wisdom of all market participants about future uncertainty.

OVX is most valuable for long horizons (60 to 180 days) because it incorporates forward-looking information that statistical models cannot capture: upcoming OPEC meetings, geopolitical tensions, seasonal demand patterns, and other factors that the market is pricing in.

### How the Three Layers Blend

The system uses horizon-dependent weights to combine the three layers:

| Horizon | GARCH Weight | ATR Weight | OVX Weight | Reasoning |
|---------|-------------|-----------|-----------|-----------|
| 1 day | 25% | 50% | 25% | Short-term: ATR dominates because recent price action is the best predictor |
| 7 days | 30% | 40% | 30% | Short-term, but GARCH starts adding value |
| 15 days | 40% | 25% | 35% | Medium-term: GARCH and OVX roughly equal |
| 30 days | 40% | 20% | 40% | Medium-term: statistical and implied views converge |
| 60 days | 30% | 15% | 55% | Longer-term: OVX increasingly important |
| 90 days | 25% | 10% | 65% | Long-term: market's forward view dominates |
| 180 days | 20% | 10% | 70% | Very long-term: only the market's implied view matters |

This blending produces more reliable uncertainty estimates than any single layer alone. The 95% confidence interval is then calculated as:

```
Lower Bound = Current Price x (1 + Expected Return - 1.96 x Composite Volatility)
Upper Bound = Current Price x (1 + Expected Return + 1.96 x Composite Volatility)
```

The factor 1.96 comes from the normal distribution: approximately 95% of outcomes fall within 1.96 standard deviations of the mean.

---

## 8. Multi-Horizon Forecasting -- Looking Into the Future

**File:** `strategy.py`, method `forecast_returns()`

### What It Does

This component generates price forecasts for seven time horizons: 1, 7, 15, 30, 60, 90, and 180 days into the future. For each horizon, it provides:

- **Expected Return:** The model's best guess of the percentage price change
- **Expected Price:** The dollar value corresponding to that return
- **Direction Probability:** How likely the model thinks an upward move is
- **95% Confidence Interval:** The realistic range of prices, powered by the Volatility Engine

### How It Works

For each horizon, the system trains two models:

1. **Direction Classifier (XGBoost Classifier):** Predicts whether the price will be higher or lower at the end of the horizon. It outputs a probability (for example, 62% chance of going up).

2. **Magnitude Regressor (XGBoost Regressor):** Predicts the actual percentage return (for example, +3.2%). This tells us not just direction but how far the price might move.

Both models use the same engineered features but are trained on different targets. The classifier learns from binary labels (up or down), while the regressor learns from continuous return values.

The Volatility Engine then wraps the magnitude prediction in a confidence interval that reflects the realistic range of outcomes.

```
Example output for a 30-day forecast:

Current Price:    $72.50
Expected Return:  +2.8%
Expected Price:   $74.53
95% CI:           $67.20 -- $81.06
Direction:        Bullish (62% probability)
Composite Vol:    GARCH 4.2% / ATR 3.8% / OVX 5.1% -> Blend 4.5%

Interpretation: The model expects oil to be around $74.53 in 30 days,
but acknowledges it could realistically be anywhere between $67.20
and $81.06. The model is moderately confident in an upward move.
```

---

## 9. The Backtesting Engine -- Testing Against History

**File:** `dashboard.py`, class `Backtester`

### What It Does

The backtester answers the most important question in quantitative trading: "If I had actually traded using these signals over the past several years, would I have made money?"

It takes the signals generated by the strategy and simulates trading. Each day, if the signal is BUY, the simulation goes long (buys oil). If SELL, it goes short (bets against oil). If HOLD, it stays flat (does nothing). The backtester then computes over 20 performance metrics to grade the strategy.

### How Returns Are Calculated

The daily strategy return is:

```
Strategy Return = Yesterday's Signal x Today's Price Change
```

The one-day lag is critical. It prevents "look-ahead bias" -- the strategy acts on yesterday's signal, not today's, because in real trading, you would make a decision at the end of one day and execute it at the start of the next.

### Performance Metrics Explained

#### Category 1: The Basics

| Metric | What It Measures | What Is Good |
|--------|-----------------|-------------|
| Total Return | Cumulative profit or loss over the entire period | Higher is better; positive means the strategy made money |
| Buy and Hold Return | What you would have earned by simply buying oil and holding it | This is the benchmark; the strategy must beat this to justify its complexity |
| Alpha | Strategy return minus buy-and-hold return | Positive alpha means the strategy added value beyond passive holding |
| Annualized Return | Average yearly return, compounded | Above 10% annually is strong for a commodity strategy |
| Annualized Volatility | Standard deviation of daily returns, scaled to one year | Lower means smoother returns; below 15% is good |

#### Category 2: Risk-Adjusted Ratios

| Metric | What It Measures | What Is Good |
|--------|-----------------|-------------|
| Sharpe Ratio | Return per unit of total risk (return / volatility) | Above 1.0 is good, above 1.5 is excellent. Below 0.5 suggests the return does not justify the risk. |
| Sortino Ratio | Like Sharpe, but only penalizes downside volatility | More relevant for investors who care about losses but are happy with upside swings. Above 1.5 is good. |
| Information Ratio | Excess return over benchmark per unit of tracking error | Measures how consistently the strategy outperforms. Above 0.5 is good. |
| Treynor Ratio | Return per unit of systematic risk (beta) | Relevant when comparing strategies with different market exposures |
| Omega Ratio | Sum of all gains divided by sum of all losses | Above 1.5 indicates a strong edge |

#### Category 3: Pain and Recovery

| Metric | What It Measures | What Is Good |
|--------|-----------------|-------------|
| Max Drawdown | The largest peak-to-trough decline | Above -20% is acceptable; above -10% is excellent. This is the worst-case pain point. |
| Calmar Ratio | Annual return divided by max drawdown | Above 1.0 means annual gains exceed worst-case losses |
| Sterling Ratio | Annual return divided by the average of the three worst drawdowns | More robust than Calmar because it does not depend on a single worst event |
| Burke Ratio | Penalizes frequent deep drawdowns | Rewards strategies that have shallow, infrequent dips |
| Ulcer Index | Combines both the depth and duration of drawdowns | Lower is better; measures the ongoing "stress" of the strategy |

#### Category 4: Trade Efficiency

| Metric | What It Measures | What Is Good |
|--------|-----------------|-------------|
| Win Rate | Percentage of trades that made money | Above 50% means the strategy is right more often than wrong |
| Profit Factor | Gross profit divided by gross loss | Above 1.5 means winners are significantly larger than losers |
| Expectancy | Expected profit per trade (in basis points) | Positive means each trade is expected to generate profit on average |
| Tail Ratio | 95th percentile return divided by 5th percentile return | Above 1.0 means the best trading days are bigger than the worst |

#### Category 5: Market Skill

| Metric | What It Measures | What Is Good |
|--------|-----------------|-------------|
| Up-Market Capture | How much of the bull-day gains the strategy captures | 100% means full participation in rising markets |
| Down-Market Capture | How much of the bear-day losses the strategy absorbs | Below 50% means the strategy avoids most of the damage on falling days |
| Capture Ratio | Up-market capture divided by down-market capture | Above 1.5 means the strategy captures more upside than downside |

---

## 10. The Dashboard -- Visualizing Everything

**File:** `dashboard.py`, class `StrategyDashboard`

### What It Does

The dashboard renders a 10-panel interactive Plotly visualization that presents every aspect of the strategy's performance in one unified view. It is saved as a standalone HTML file that can be opened in any web browser.

### Panel-by-Panel Breakdown

| Panel | Title | What It Shows |
|-------|-------|--------------|
| 1 | Price + Regime Shading | WTI price history with colored background regions indicating which regime (BULL, CHOPPY, PANIC) the HMM detected for each period |
| 2 | Buy/Sell Signal Overlay | WTI price during the out-of-sample period with green triangles (BUY) and red triangles (SELL) marking each trading signal |
| 3 | Equity Curve: Strategy vs Buy and Hold | Cumulative growth of $1 invested using the strategy versus simply holding oil. This is the most important chart -- it shows whether the strategy outperformed passive holding. |
| 4 | Drawdown (Underwater Plot) | A chart showing how far below its peak the strategy has ever fallen. A drawdown of -5% means the strategy was 5% below its best performance so far. The depth and duration of drawdowns reveal risk characteristics. |
| 5 | Feature Importance (Top 15) | A horizontal bar chart showing which features the XGBoost model relied on most. This reveals which data sources are driving the strategy's decisions. |
| 6 | Accuracy per Regime | Bar chart comparing prediction accuracy across BULL, CHOPPY, and PANIC regimes. Shows whether the model performs better in calm or volatile conditions. |
| 7 | Rolling 60-Day Sharpe Ratio | A line chart showing how the risk-adjusted performance changes over time. Periods above 1.0 are good; periods below 0 mean the strategy was losing money on a risk-adjusted basis. |
| 8 | Monthly Returns Heatmap | A color-coded grid showing profit or loss for each month. Green cells are profitable months, red cells are losing months. This helps identify seasonal patterns. |
| 9 | WTI Price Forecast with 95% CI | Bar chart showing expected price for each forecast horizon (1d to 180d) with error bars representing the 95% confidence interval from the Volatility Engine |
| 10 | Performance Summary Table | A reference table of all key metrics with plain-language descriptions |

---

## 11. The Execution Flow -- How It All Runs

**File:** `run_strategy.py`

### What It Does

This is the single entry point for the entire system. Running `python run_strategy.py` triggers the complete pipeline from data collection to dashboard output.

### The Execution Sequence

```
Step 1:  run_strategy.py calls build_master_df()
         -> oil_data_pipeline_new.py runs
         -> features.py provides the fetch functions
         -> 9 data sources are queried
         -> master_df is assembled (approximately 1,250 rows x 9 columns)

Step 2:  run_strategy.py creates HMMXGBoostStrategy(fwd_days=5)
         -> strategy.run(master_df) is called

Step 2a: strategy.engineer_features(master_df)
         -> 25+ engineered features are computed
         -> Clean DataFrame ready for modeling

Step 2b: strategy.fit_predict(feat_df)
         -> HMM detects 3 regimes (BULL, CHOPPY, PANIC)
         -> Walk-forward XGBoost runs across multiple folds
         -> Signals (BUY/SELL/HOLD) generated
         -> Regime-aware position sizes calculated

Step 2c: strategy.forecast_returns(feat_df)
         -> Volatility Engine initializes (GARCH + ATR + OVX)
         -> 7 horizons forecasted (1d through 180d)
         -> 95% confidence intervals computed for each

Step 3:  Backtester().run(result_df)
         -> Simulates historical trading
         -> Computes 20+ performance metrics
         -> Prints categorized performance report

Step 4:  StrategyDashboard().render(bt_result, strategy)
         -> Generates 10-panel interactive chart
         -> Saved to output/dashboard_hmm_xgb.html
```

### Configuration

The system is configured through the `STRATEGIES` dictionary in `run_strategy.py`. Currently, it runs one strategy: the HMM + XGBoost Regime-Aware Strategy with a 5-day prediction horizon. To run with a different horizon, change the `fwd_days` parameter. To add a new strategy (for instance, a neural-network-based approach), create a new class that inherits from `BaseStrategy`, implement the three required methods, and add it to the dictionary. No other file changes are needed.

---

## 12. Glossary of Key Terms

| Term | Definition |
|------|-----------|
| Alpha | The excess return of a strategy over a benchmark. Positive alpha means the strategy adds value. |
| ATR (Average True Range) | A volatility measure calculated from the average range of daily price movements over a specified period. |
| Backtesting | Testing a trading strategy on historical data to evaluate how it would have performed. |
| Basis Points (bps) | One hundredth of a percentage point. 100 bps = 1%. Used for small return measurements. |
| Beta | A measure of how much a strategy moves relative to the market. Beta of 1.0 means it moves in lockstep. |
| Brent Crude | The international benchmark crude oil grade, produced from North Sea oil fields. |
| Calmar Ratio | Annual return divided by maximum drawdown. Measures return relative to worst-case loss. |
| Confidence Interval (CI) | A range of values that is expected to contain the true outcome with a stated probability (e.g., 95%). |
| COT (Commitments of Traders) | Weekly CFTC report showing the positions of different trader categories in futures markets. |
| Crack Spread | The difference between the price of refined petroleum products and crude oil. Represents refinery profit margins. |
| Drawdown | The decline from a previous peak in cumulative returns. Measures "how much have I lost from my best point?" |
| EIA (Energy Information Administration) | US government agency that collects and publishes energy data. |
| Equity Curve | A line chart showing the cumulative growth of an investment over time. |
| Feature Engineering | The process of transforming raw data into derived variables that better capture patterns for machine learning. |
| Forward Fill (ffill) | A method of filling missing data by carrying the last known value forward until a new value arrives. |
| FRED (Federal Reserve Economic Data) | A database maintained by the Federal Reserve Bank of St. Louis containing economic and financial data. |
| GARCH | A statistical model that captures volatility clustering -- the tendency for volatile periods to persist. |
| HMM (Hidden Markov Model) | A statistical model that infers unobservable "hidden" states from observable data patterns. |
| Log Return | The natural logarithm of the ratio of consecutive prices. Used because they are additive and symmetric. |
| Max Drawdown | The largest peak-to-trough decline observed during a backtest. Represents the worst-case scenario. |
| OOS (Out-of-Sample) | Data that was not used during model training. Performance on OOS data is the true measure of model quality. |
| OPEC | Organization of the Petroleum Exporting Countries. A cartel that coordinates oil production among member nations. |
| OVX | The CBOE Crude Oil Volatility Index. Derived from oil option prices, it represents the market's expected 30-day volatility. |
| Position Sizing | Determining how large a trade should be based on current market conditions and model confidence. |
| Profit Factor | The ratio of gross profits to gross losses. Above 1.0 means the strategy makes more than it loses. |
| Regime | A distinct market state (such as BULL, CHOPPY, or PANIC) characterized by specific return and volatility patterns. |
| Sharpe Ratio | The ratio of average excess return to volatility. The most widely used risk-adjusted performance measure. |
| Sortino Ratio | Like the Sharpe Ratio, but only penalizes downside volatility (losses), not upside volatility (gains). |
| SPR (Strategic Petroleum Reserve) | US government emergency crude oil stockpile stored in salt caverns along the Gulf Coast. |
| Walk-Forward Validation | A backtesting methodology where the model is repeatedly trained on expanding historical windows and tested on subsequent unseen periods. |
| Win Rate | The percentage of trades that produced a positive return. |
| WTI (West Texas Intermediate) | The primary crude oil benchmark for the United States. |
| XGBoost | A gradient boosting machine learning algorithm that builds ensembles of decision trees. |
| Z-Score | A measure of how many standard deviations a value is from its recent average. Extreme values (above 2 or below -2) indicate unusual conditions. |

---

*PetroQuant demonstrates that by combining diverse data sources, rigorous feature engineering, regime-aware machine learning, multi-layered volatility estimation, and disciplined backtesting, it is possible to build a systematic trading strategy that generates meaningful alpha over passive holding -- while transparently measuring and managing risk at every stage.*
