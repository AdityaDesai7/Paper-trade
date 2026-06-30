# Volatility Engine Deep Dive — GARCH, ATR & OVX

## Table of Contents

1. [Why Volatility Matters](#1-why-volatility-matters)
2. [Layer 1: GARCH(1,1) — Conditional Volatility](#2-layer-1-garch11--conditional-volatility)
3. [Layer 2: ATR(14) — Average True Range](#3-layer-2-atr14--average-true-range)
4. [Layer 3: OVX — Oil Volatility Index](#4-layer-3-ovx--oil-volatility-index)
5. [How We Blend Them — Composite CI](#5-how-we-blend-them--composite-ci)
6. [Implementation Walkthrough](#6-implementation-walkthrough)
7. [References & Papers](#7-references--papers)

---

## 1. Why Volatility Matters

Imagine you predict that oil will be $72 next week. That prediction alone is incomplete — you also need to know **how confident** you should be. Will it be $72 plus or minus $1? Or plus or minus $10?

**Volatility** measures how wildly a price swings. Higher volatility = wider range of possible outcomes = more risk.

Our project doesn't just predict a price target — it wraps every forecast in a **95% Confidence Interval (CI)**, meaning: *"We are 95% confident the price will land somewhere between $X and $Y."*

To build that CI, we use **three independent volatility layers**, each capturing a different aspect of market uncertainty:

| Layer | What It Measures | Analogy |
|---|---|---|
| GARCH(1,1) | How volatility *clusters and mean-reverts* over time | A weather model that knows storms come in clusters |
| ATR(14) | How much the price *actually moves* day-to-day | A speedometer reading actual road bumpiness |
| OVX | What the *market collectively expects* volatility to be | Checking the weather forecast made by all meteorologists combined |

---

## 2. Layer 1: GARCH(1,1) — Conditional Volatility

### What is GARCH?

**GARCH** stands for **Generalized Autoregressive Conditional Heteroskedasticity**. Let's break that down into simple English:

- **Heteroskedasticity**: Volatility changes over time (it's not constant)
- **Conditional**: Today's volatility depends on what happened yesterday
- **Autoregressive**: Volatility feeds back into itself — high vol today → likely high vol tomorrow
- **Generalized**: It's an improved version of the original ARCH model

### The Core Insight: Volatility Clusters

Look at any oil price chart and you'll notice something: **calm periods and stormy periods tend to stick together**. After a big price swing (like COVID crash in 2020), the market stays volatile for weeks. After a calm period, it tends to stay calm.

GARCH models this mathematically.

### The GARCH(1,1) Formula

The model predicts tomorrow's variance (volatility squared) using three components:

```
σ²(tomorrow) = ω + α × ε²(today) + β × σ²(today)
```

Where:
- **ω (omega)** = baseline volatility — the "floor" that volatility never drops below
- **α (alpha)** = shock sensitivity — how much today's surprise moves tomorrow's volatility
- **ε²(today)** = today's squared return (the "shock" or surprise)
- **β (beta)** = persistence — how much of today's volatility carries over to tomorrow
- **σ²(today)** = today's conditional variance

### Example: Walking Through a GARCH Calculation

Suppose our GARCH model has learned these parameters from oil price history:
- ω = 0.01
- α = 0.05
- β = 0.90

And today:
- Oil price dropped 3% (a large shock): ε(today) = -0.03, so **ε² = 0.0009**
- Current conditional volatility: σ²(today) = 0.0004 (about 2% daily vol)

Tomorrow's predicted variance:
```
σ²(tomorrow) = 0.01 + 0.05 × 0.0009 + 0.90 × 0.0004
             = 0.01 + 0.000045 + 0.00036
             = 0.010405
```

The volatility jumped up because of the large shock (the α × ε² term), but the high β (0.90) also carries forward a lot of today's existing volatility.

### Key Property: Mean Reversion

GARCH has a "long-run variance" that it always gravitates back toward:

```
σ²(long-run) = ω / (1 - α - β)
```

With our example:
```
σ²(long-run) = 0.01 / (1 - 0.05 - 0.90) = 0.01 / 0.05 = 0.20
```

This means after any shock, volatility will always drift back to this long-run level. Big shocks push it up temporarily, but it always "cools down."

The **persistence** (α + β = 0.95 in our example) tells you how slowly it cools down:
- Close to 1.0 = very slow cooling (shocks linger for weeks)
- Close to 0.5 = fast cooling (shocks die out in days)

Oil markets typically have persistence around 0.93-0.97, meaning volatility shocks are quite persistent.

### Multi-Step Forecasting

For predicting risk over longer horizons (30 days, 90 days), GARCH uses a formula that accounts for mean-reversion:

```
σ²(h-steps) = h × σ²(long-run) + persistence × [(1 - persistence^h) / (1 - persistence)] × (σ²(today) - σ²(long-run))
```

This captures the idea that:
- **Short term**: If today is volatile, next week will likely be volatile too
- **Long term**: Over 6 months, volatility will have partially reverted to average

This is much more realistic than simply scaling by √(h) like simpler models do.

### Why GARCH Is Good for Oil

- Oil prices exhibit strong **volatility clustering** (OPEC decisions, geopolitical events create extended turbulent periods)
- The mean-reversion property prevents forecasts from becoming unreasonably extreme at longer horizons
- It captures the asymmetry where negative shocks (crashes) tend to increase vol more than positive ones

---

## 3. Layer 2: ATR(14) — Average True Range

### What is ATR?

**ATR** stands for **Average True Range**. It's a simpler, more intuitive volatility measure invented by J. Welles Wilder in 1978. While GARCH is a statistical model, ATR is a **price-action indicator** — it directly measures how much the price *actually moves* each day.

### The True Range Concept

The "True Range" of a single day is the *largest* of these three values:

```
True Range = MAX of:
  1. Today's High - Today's Low            (intraday range)
  2. |Today's High  - Yesterday's Close|   (overnight gap up)
  3. |Today's Low   - Yesterday's Close|   (overnight gap down)
```

### Why "True" Range?

Regular range (High - Low) misses **overnight gaps**. Imagine:
- Yesterday's close: $70
- Today opens at $73 (gap up on news), trades between $73-$74

Regular range = $74 - $73 = **$1** (looks calm)
True Range = $74 - $70 = **$4** (captures the real movement)

> In our project, since we only have daily close prices (no High/Low data), we approximate True Range using `|Close(today) - Close(yesterday)|`.

### ATR(14) Calculation

ATR smooths the True Range over 14 days using an **Exponential Moving Average (EMA)**:

```
ATR(14) = EMA of True Range over 14 days
```

EMA gives more weight to recent days, so ATR reacts quickly to changing market conditions.

### Example: ATR Interpretation

Suppose:
- Current WTI price: **$70.00**
- ATR(14): **$1.40**

This means oil has been moving about **$1.40 per day** on average recently.

As a percentage: $1.40 / $70.00 = **2.0% of price** — this is the daily "noise floor."

### How We Scale ATR to Longer Horizons

For multi-day forecasts, we scale ATR using:

```
ATR_horizon_vol = (ATR / Price) × √(horizon) × decay_factor
```

The **decay factor** (0.9^(horizon/5)) prevents ATR from exploding at long horizons. Without it:
- 1-day vol: 2.0% (reasonable)
- 180-day vol: 2.0% × √180 = 26.8% (too high — mean reversion should reduce this)

With decay:
- 1-day vol: 2.0% × 1.0 × 0.98 = **1.96%**
- 7-day vol: 2.0% × 2.65 × 0.87 = **4.60%**
- 30-day vol: 2.0% × 5.48 × 0.52 = **5.70%**
- 180-day vol: 2.0% × 13.4 × 0.0003 = **0.8%**

This tapering effect is intentional — ATR is most useful for **short-term** risk estimation. For long horizons, GARCH and OVX take over.

### Why ATR Is Good for Oil

- **Gap-sensitive**: Oil futures often gap up/down on overnight OPEC news, inventory reports, or geopolitical events. ATR captures these gaps
- **Non-parametric**: Unlike GARCH, ATR doesn't assume any statistical distribution — it's pure price action
- **Responsive**: The 14-day EMA adapts quickly to regime changes (e.g., a sudden jump in daily ranges after a pipeline attack)

---

## 4. Layer 3: OVX — Oil Volatility Index

### What is OVX?

**OVX** is the **CBOE Crude Oil Volatility Index**, often called the "Oil VIX." Just as VIX measures the stock market's expected volatility, OVX measures the oil market's expected volatility.

It is calculated by the **Chicago Board Options Exchange (CBOE)** using real-time prices of WTI crude oil options (specifically, options on the USO ETF).

### How OVX Works — The Options Connection

Options are financial contracts that give you the *right* (but not the obligation) to buy or sell oil at a specific price. The price of an option depends heavily on expected volatility:

- If traders expect oil to swing wildly → options become expensive → OVX goes **UP**
- If traders expect oil to stay calm → options are cheap → OVX goes **DOWN**

OVX essentially **extracts the market's consensus expectation** of how volatile oil will be over the next 30 days.

### Example: Reading OVX

| OVX Value | What It Means | Context |
|---|---|---|
| 20-25 | Low volatility expected | Calm markets, stable supply/demand |
| 30-40 | Moderate volatility | Some uncertainty (OPEC meetings, mild tensions) |
| 50-60 | High volatility | Significant events (sanctions, wars, demand shocks) |
| 80+ | Extreme fear | Crisis levels (COVID crash in 2020 hit ~325!) |

A typical OVX reading might be **30**, meaning the options market expects WTI crude oil to move about **30% annualized** over the next month.

### Converting OVX to a Horizon-Specific Volatility

OVX is expressed as an **annualized percentage** (based on 252 trading days). To convert it to a specific horizon:

```
σ_iv(horizon) = (OVX / 100) × √(horizon / 252)
```

Example with OVX = 30:

| Horizon | Calculation | Expected Vol |
|---|---|---|
| 1 day | 0.30 × √(1/252) = 0.30 × 0.063 | **1.9%** |
| 7 days | 0.30 × √(7/252) = 0.30 × 0.167 | **5.0%** |
| 30 days | 0.30 × √(30/252) = 0.30 × 0.345 | **10.4%** |
| 90 days | 0.30 × √(90/252) = 0.30 × 0.598 | **17.9%** |
| 180 days | 0.30 × √(180/252) = 0.30 × 0.845 | **25.4%** |

### Why OVX Is Uniquely Valuable

1. **Forward-Looking**: GARCH and ATR look at *past* prices. OVX looks at what the market *expects to happen* in the future. If traders know a major OPEC decision is coming next week, they buy options for protection, pushing OVX up — even if recent prices have been calm.

2. **Aggregated Intelligence**: OVX reflects the collective view of thousands of professional traders, hedge funds, and oil companies — all of whom have money on the line. It is the market's "wisdom of the crowd."

3. **Asymmetric Information**: Institutional traders often have better information about supply disruptions, geopolitical risks, and demand shifts than any single model. OVX bakes all of this in.

### Where We Get OVX Data

In our project, OVX is fetched from the **FRED API** (Federal Reserve Economic Data) using the series code `OVXCLS`:

```python
# From features.py
fred = Fred(api_key=fred_key)
data = fred.get_series('OVXCLS', observation_start=start, observation_end=end)
```

---

## 5. How We Blend Them — Composite CI

### The Problem: Each Layer Has Strengths and Weaknesses

| Layer | Best At | Weak At |
|---|---|---|
| GARCH | Medium-term clustering & mean-reversion | Misses sudden regime changes |
| ATR | Short-term actual price action | Explodes at long horizons |
| OVX | Incorporating forward-looking market intel | Not always available; can be noisy |

### The Solution: Horizon-Dependent Blending

We assign **different weights** to each layer depending on the forecast horizon:

| Horizon | GARCH Weight | ATR Weight | OVX (IV) Weight | Rationale |
|---|---|---|---|---|
| **1 day** | 25% | **50%** | 25% | Short-term: price action (ATR) dominates |
| **7 days** | 30% | **40%** | 30% | ATR still important, GARCH gaining |
| **15 days** | **40%** | 25% | 35% | GARCH clustering kicks in |
| **30 days** | **40%** | 20% | 40% | GARCH + IV roughly equal |
| **60 days** | 30% | 15% | **55%** | Market's forward view (IV) starts dominating |
| **90 days** | 25% | 10% | **65%** | IV heavily weighted for medium-long term |
| **180 days** | 20% | 10% | **70%** | Long-term: IV (market consensus) rules |

### Why This Weighting Makes Sense

- **1-day forecast**: What happened today in price action (ATR) is the best predictor of tomorrow's range. The market's 30-day implied vol (OVX) is less relevant for tomorrow.
- **180-day forecast**: Day-to-day price action is meaningless for a 6-month outlook. The market's collective forward view (OVX) and GARCH's mean-reversion model are far more informative.

### Building the 95% CI

Once we have the composite volatility, the confidence interval is:

```
Price_Target = Current_Price × (1 + Expected_Return)
CI_Lower     = Current_Price × (1 + Expected_Return - 1.96 × Composite_Vol)
CI_Upper     = Current_Price × (1 + Expected_Return + 1.96 × Composite_Vol)
```

The **1.96** comes from the normal distribution — 95% of outcomes fall within 1.96 standard deviations of the mean.

### Full Example: 30-Day Forecast

Given:
- Current WTI Price: **$70.00**
- XGBoost expected return (30d): **+2.3%**
- GARCH 30d vol: **8.5%**
- ATR 30d vol: **5.7%**
- OVX 30d vol: **10.4%**

Step 1: Composite vol (using 30d weights: 40/20/40):
```
Composite = 0.40 × 8.5% + 0.20 × 5.7% + 0.40 × 10.4%
          = 3.4% + 1.14% + 4.16%
          = 8.7%
```

Step 2: Build CI:
```
Price Target = $70.00 × (1 + 0.023) = $71.61
CI Lower     = $70.00 × (1 + 0.023 - 1.96 × 0.087) = $70.00 × 0.852 = $59.67
CI Upper     = $70.00 × (1 + 0.023 + 1.96 × 0.087) = $70.00 × 1.194 = $83.55
```

**Result**: We expect WTI at $71.61 in 30 days, with 95% confidence it will be between **$59.67 and $83.55**.

### OVX Fallback

If OVX data is unavailable, the system redistributes the IV weight proportionally to GARCH and ATR:

```python
# From strategy.py, lines 186-188
if vol_iv is None:
    total = w_g + w_a
    w_g, w_a, w_i = w_g / total, w_a / total, 0.0
```

For 30d weights (0.40, 0.20, 0.40) without OVX → new weights become (0.67, 0.33, 0.00).

---

## 6. Implementation Walkthrough

Here's how the code in `strategy.py` implements each layer:

### GARCH Implementation (Lines 77-127)

```python
# Fit GARCH(1,1) on last 252 daily log-returns
from arch import arch_model

log_ret = np.log(prices / prices.shift(1)).dropna()
recent = log_ret.tail(252) * 100  # arch library expects percent returns

model = arch_model(recent, vol='Garch', p=1, q=1, mean='Zero', rescale=False)
res = model.fit(disp='off')

# Extract learned parameters
omega = res.params.get('omega')    # baseline variance
alpha = res.params.get('alpha[1]') # shock sensitivity
beta  = res.params.get('beta[1]')  # persistence
```

Key choices:
- `mean='Zero'`: Assumes zero mean return (standard for short-term vol modeling)
- `p=1, q=1`: One lag of the shock, one lag of the variance (the classic specification)
- `lookback=252`: Uses the last year of trading data to fit

### ATR Implementation (Lines 130-149)

```python
# Daily absolute price change as a proxy for True Range
daily_abs_change = (close - close.shift(1)).abs()

# 14-day Exponential Moving Average
atr_14 = daily_abs_change.ewm(span=14, adjust=False).mean().iloc[-1]
```

### OVX Implementation (Lines 152-166)

```python
# Simply reads the latest available OVX value
ovx_latest = float(ovx_series.dropna().iloc[-1])

# Scales to horizon using square-root-of-time rule
iv_vol = (ovx_latest / 100.0) * np.sqrt(horizon / 252.0)
```

### Composite Blending (Lines 169-208)

```python
# Get horizon-specific weights
w_g, w_a, w_i = BLEND_WEIGHTS.get(horizon, (0.33, 0.33, 0.34))

# Blend
vol_composite = w_g * vol_garch + w_a * vol_atr + w_i * vol_iv

# Build CI
ci_lower = current_price * (1 + expected_return - 1.96 * vol_composite)
ci_upper = current_price * (1 + expected_return + 1.96 * vol_composite)
```

---

## 7. References & Papers

### GARCH
- **Original Paper**: Bollerslev, T. (1986). *"Generalized Autoregressive Conditional Heteroskedasticity."* Journal of Econometrics, 31(3), 307-327. [DOI:10.1016/0304-4076(86)90063-1](https://doi.org/10.1016/0304-4076(86)90063-1)
- **ARCH Foundation**: Engle, R. F. (1982). *"Autoregressive Conditional Heteroscedasticity with Estimates of the Variance of United Kingdom Inflation."* Econometrica, 50(4), 987-1007.
- **Python Library**: [`arch`](https://arch.readthedocs.io/en/latest/) by Kevin Sheppard

### ATR
- **Original Source**: Wilder, J. W. (1978). *"New Concepts in Technical Trading Systems."* Trend Research. (Chapter on Average True Range)
- **Modern Application**: Aronson, D. (2006). *"Evidence-Based Technical Analysis."* Wiley.

### OVX
- **CBOE OVX Whitepaper**: [CBOE OVX Methodology](https://www.cboe.com/tradable_products/vix/ovx/) — Official calculation methodology
- **Academic Study**: Haugom, E. et al. (2014). *"Forecasting volatility of the U.S. oil market."* Journal of Banking & Finance, 47, 1-14.

### Composite Volatility / Blending
- **Implied vs Realized**: Poon, S. & Granger, C. (2003). *"Forecasting Volatility in Financial Markets: A Review."* Journal of Economic Literature, 41(2), 478-539.
- **Combining Forecasts**: Timmermann, A. (2006). *"Forecast Combinations."* Handbook of Economic Forecasting, 1, 135-196.
