# PetroQuant — Complete Guide for AI/ML Engineers

> **Who this is for:** You know ML (XGBoost, HMM, features, backtesting). You are new to **deploying** Python apps (Render, Railway, Flask, git push → live server). This doc explains **everything** in one place.

---

## 1. What Is This Project?

**PetroQuant** is a quantitative oil (WTI crude) trading platform. It has **two separate systems** in one repo:

| System | Purpose | When you use it |
|--------|---------|-----------------|
| **Research / Backtest** | Train strategies on historical data, measure Sharpe/drawdown | Offline analysis, strategy development |
| **Live Paper Trading** | Run the strategy on **real-time** prices with fake money | 24/7 demo trading, dashboard, Render deployment |

**Asset:** WTI Crude Oil futures (`CL=F` on Yahoo Finance)  
**Starting capital (paper):** $1,000,000 (fake money — no real broker)

---

## 2. Repo Map — What Lives Where

```
Trading Strategy/
│
├── paper_trading/          ← LIVE SYSTEM (this is what runs on Render)
│   ├── run_paper_trader.py ← START HERE — main entry point
│   ├── engine.py           ← Orchestrates the trading loop
│   ├── config.py           ← All settings + timeframe configs
│   ├── price_feed.py       ← Fetches OHLCV from yfinance
│   ├── features_intraday.py← Builds ML features per bar
│   ├── model_intraday.py   ← XGBoost classifier (BUY/SELL/HOLD)
│   ├── daily_regime.py     ← HMM macro filter (BULL/CHOPPY/PANIC)
│   ├── order_engine.py     ← Signal → trade action logic
│   ├── portfolio.py        ← Cash, positions, P&L
│   ├── trade_log.py        ← SQLite persistence (trades + snapshots)
│   ├── dashboard_live.py   ← Plotly HTML dashboard
│   └── web_server.py       ← Flask API + serves dashboard
│
├── strategy.py             ← RESEARCH: HMM + XGBoost daily strategy
├── backtest_backtrader.py  ← RESEARCH: Backtrader event-driven backtest
├── oil_data_pipeline_new.py← RESEARCH: Multi-source data pipeline
├── features.py             ← RESEARCH: 9 oil market data sources
├── run_strategy.py         ← RESEARCH: One command for full backtest pipeline
│
├── render.yaml             ← Render.com deploy config
├── railway.toml            ← Railway.app deploy config (alternative)
├── Procfile                ← Heroku-style start command
├── requirements.txt        ← Python deps for cloud deploy
└── output/                 ← Generated at runtime (DB, logs, HTML dashboard)
    ├── paper_trades.db     ← All trades persisted here
    ├── paper_trading.log   ← Engine logs
    └── paper_dashboard.html← Live dashboard file
```

**Rule of thumb:** If it is under `paper_trading/`, it is the **live** system. Everything else is **research/backtest**.

---

## 3. GitHub Repos (You Have Two)

| Repo | Account | Used for |
|------|---------|----------|
| [AdityaDesai7/Paper-trade](https://github.com/AdityaDesai7/Paper-trade) | AdityaDesai7 | **Render deployment** (production) |
| [aditya-heliuswork/Paper-Trading](https://github.com/aditya-heliuswork/Paper-Trading) | aditya-heliuswork | Backup / team copy |

**Git remotes on your machine:**
- `origin` → aditya-heliuswork/Paper-Trading
- `production` → AdityaDesai7/Paper-trade

When you push to `production main`, Render auto-deploys (if connected).

---

## 4. How Live Paper Trading Works (End-to-End)

Think of it as a **loop** that runs every N seconds (depends on timeframe):

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     PAPER TRADING LOOP (every bar)                     │
└─────────────────────────────────────────────────────────────────────────┘

  1. PRICE FEED          fetch_candles() from yfinance (5m/15m/1h/1d)
         │
         ▼
  2. MARKET CHECK        is_market_open()? Skip if weekend/maintenance
         │
         ▼
  3. DAILY REGIME        HMM on daily WTI → BULL / CHOPPY / PANIC
         │                (cached once per day — scales position size)
         ▼
  4. FEATURES            EMA, RSI, VWAP, ATR, Bollinger, volume, streak...
         │
         ▼
  5. MODEL               XGBoost: "Will price go UP in N bars?"
         │                Output: probability 0–1
         ▼
  6. SIGNAL              prob > 52% → BUY
                         prob < 48% → SELL
                         else       → HOLD
         │
         ▼
  7. ORDER ENGINE        Translate signal + current position → action
         │
         ▼
  8. PORTFOLIO           Execute trade (fake money), update cash/P&L
         │
         ▼
  9. TRADE LOG           Save to SQLite (paper_trades.db)
         │
         ▼
  10. DASHBOARD          Regenerate HTML chart for browser
         │
         ▼
  SLEEP until next bar (e.g. 300 sec for 5m timeframe)
```

---

## 5. ML Logic (What the Model Actually Does)

### 5.1 Target (what we predict)

For **5m timeframe** (default):
- **Question:** Will `Close` be higher **3 bars later** (15 minutes)?
- **Target:** `1` = yes (UP), `0` = no (DOWN)

Built in `features_intraday.py` → `build_target()`.

### 5.2 Features (inputs to XGBoost)

All **backward-looking** (no future leakage). Examples:

| Category | Features |
|----------|----------|
| Momentum | Ret_1m, Ret_5m, Ret_15m, Ret_30m |
| Trend | EMA crosses, price vs EMA |
| Oscillators | RSI_7, RSI_14 |
| Volatility | ATR, Bollinger position/width |
| Volume | Vol_ratio, Vol_sign |
| Macro | Regime_BULL, Regime_CHOPPY, Regime_PANIC (from HMM) |

### 5.3 Model (`model_intraday.py`)

- **Algorithm:** XGBoost binary classifier
- **Training:** Rolling window on last N bars; 80/20 time-ordered split
- **Retrain:** Every 8 hours (5m config) — `should_retrain()` checks elapsed time
- **Output:** `predict_proba()` → probability price goes UP

### 5.4 Daily Regime (`daily_regime.py`)

Separate **slow** model (not intraday):
- Fetches **daily** WTI history (1 year)
- Fits **3-state Gaussian HMM** on log returns + realized vol
- Labels states: **BULL** (low vol, positive drift), **CHOPPY**, **PANIC** (high vol)
- Used only to **scale position size**, not direction:

| Regime | Position multiplier |
|--------|---------------------|
| BULL   | 100% |
| CHOPPY | 60%  |
| PANIC  | 40%  |

---

## 6. Trading Logic (Order Engine + Portfolio)

### 6.1 Signal → Action (correct production logic)

| Current position | Signal | Action |
|------------------|--------|--------|
| None (flat) | BUY | Open LONG |
| None (flat) | SELL | Open SHORT |
| LONG | SELL | **Close LONG only** → go FLAT |
| SHORT | BUY | **Close SHORT only** → go FLAT |
| LONG | BUY | HOLD (already long) |
| SHORT | SELL | HOLD (already short) |
| Any | HOLD | Do nothing |

**Important:** We do **NOT** flip LONG→SHORT in one bar. Opposite signal = close first, re-enter next bar if signal persists. This avoids doubling risk on noisy signals.

### 6.2 Position sizing

```
trade_value = equity × 15% × regime_mult × confidence_mult
units       = trade_value / current_price
```

- Max 15% of equity per trade (`MAX_POSITION_PCT`)
- Confidence scales size when model is more confident (far from 50% prob)

### 6.3 Risk controls

- **Commission:** 2 bps per side
- **Slippage:** 1 bp simulated on fills
- **Daily loss limit:** Stop trading if daily loss > 5% of equity

### 6.4 Persistence (`trade_log.py` + `portfolio.py`)

- Every trade → SQLite table `trades`
- Every minute → equity snapshot in table `snapshots`
- On **server restart**, `portfolio.restore_from_db()` reloads cash/equity from DB
- Open positions at crash time are **abandoned** (logged as warning)

---

## 7. Timeframes (Multi-Timeframe System)

1-minute trading was **removed** (too noisy). Minimum is **5m**.

| Timeframe | Loop every | Predicts ahead | Retrain every |
|-----------|------------|----------------|---------------|
| **5m** (default) | 5 min | 15 min (3 bars) | 8 hours |
| **15m** | 15 min | 30 min (2 bars) | 12 hours |
| **1h** | 1 hour | 2 hours | 24 hours |
| **1d** | 24 hours | next day | 7 days |

Switch at runtime:
- **Dashboard:** Pill buttons (5 Min / 15 Min / 1 Hour / 1 Day)
- **API:** `POST /set-timeframe` with body `{"timeframe": "15m"}`
- **CLI:** `python paper_trading/run_paper_trader.py --timeframe 15m`

Config lives in `config.py` → `TIMEFRAME_CONFIGS` + `apply_timeframe()`.

---

## 8. Web Server & API (`web_server.py`)

Flask runs **in the same process** as the trading engine (background thread for trading, main thread for HTTP).

| Endpoint | Method | What it does |
|----------|--------|--------------|
| `/` or `/dashboard` | GET | Live Plotly HTML dashboard |
| `/status` | GET | JSON: equity, position, signal, regime, timeframe |
| `/health` | GET | `{"status":"ok"}` — for uptime monitors |
| `/trades` | GET | Download full trade log as CSV |
| `/trades/json` | GET | Last 100 trades as JSON |
| `/set-timeframe` | POST | Switch timeframe (`{"timeframe":"15m"}`) |
| `/reset` | GET | Confirmation page |
| `/reset` | POST | Wipe all trades (requires `confirm=yes`) |

**Port:** Render sets `PORT` env var → app listens on that port (see `config.WEB_PORT`).

---

## 9. Deployment Platforms Explained (Beginner-Friendly)

### What is "deployment"?

Locally you run: `python paper_trading/run_paper_trader.py`  
On **Render/Railway**, a remote Linux server does the same thing **24/7** so your paper trader keeps running when your laptop is off.

**Flow:**
```
You edit code → git commit → git push to GitHub
                                    │
                                    ▼
                         Render watches GitHub repo
                                    │
                                    ▼
                         Render runs: pip install -r requirements.txt
                                    │
                                    ▼
                         Render runs: python paper_trading/run_paper_trader.py
                                    │
                                    ▼
                         You get a public URL like https://xxx.onrender.com
```

### Render.com (your main platform)

File: **`render.yaml`**

```yaml
services:
  - type: web
    name: petroquant-paper-trader
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: python paper_trading/run_paper_trader.py
    autoDeploy: true   # push to GitHub → auto redeploy
```

**What Render does:**
1. **Build** — installs Python packages from `requirements.txt`
2. **Start** — runs your start command (same as local)
3. **Expose** — gives you HTTPS URL on port from `$PORT`

**Free tier caveats:**
- Service **sleeps** after ~15 min no traffic (use a cron ping or upgrade)
- **Disk is ephemeral** — `output/paper_trades.db` may reset on redeploy unless you add Render Persistent Disk or external DB

### Railway.app (alternative)

File: **`railway.toml`** — same start command, different host.

### Procfile (Heroku-style)

```
web: python paper_trading/run_paper_trader.py
```

Some platforms read this instead of `render.yaml`.

### Which file matters where?

| Platform | Config file | Start command source |
|----------|-------------|----------------------|
| Render | `render.yaml` | `startCommand` in yaml |
| Railway | `railway.toml` | `startCommand` in toml |
| Heroku | `Procfile` | `web:` line |

All three run the **same Python script**.

---

## 10. How to Run Locally

### Setup (one time)

```powershell
cd "C:\Users\adity\OneDrive\Desktop\Trading Strategy"

# Create venv with Python 3.12 (hmmlearn needs 3.12, not 3.14)
uv venv --python 3.12
.\.venv\Scripts\activate

pip install -r requirements.txt
```

> **Windows note:** If `python` is blocked by Application Control, use:
> `.\run.ps1 paper_trading/run_paper_trader.py`

### Run modes

```powershell
# Full engine + web dashboard (default 5m)
python paper_trading/run_paper_trader.py

# Different timeframe
python paper_trading/run_paper_trader.py --timeframe 15m

# One cycle only (debug)
python paper_trading/run_paper_trader.py --once

# Check account from terminal
python paper_trading/run_paper_trader.py --status

# Inspect SQLite DB
python check_db.py
```

Open browser: **http://localhost:8080/** (or port in `$PORT`).

---

## 11. How to Deploy to Render (Step by Step)

1. **Push code** to GitHub repo connected to Render:
   ```powershell
   git add .
   git commit -m "your message"
   git push production main
   ```
   (`production` remote = AdityaDesai7/Paper-trade)

2. **Render Dashboard** → [dashboard.render.com](https://dashboard.render.com)
   - Open service **petroquant-paper-trader**
   - Tab **Events** — watch build log
   - Tab **Logs** — live engine output

3. **Verify live:**
   - `https://YOUR-SERVICE.onrender.com/health` → `{"status":"ok"}`
   - `https://YOUR-SERVICE.onrender.com/status` → equity, signal, regime
   - `https://YOUR-SERVICE.onrender.com/` → dashboard

4. **If deploy fails**, check Logs for:
   - Missing package → add to `requirements.txt`
   - Port error → app must use `os.environ.get('PORT')` (already in `config.py`)
   - hmmlearn build fail → Render uses Python 3.x; may need `runtime.txt` with `python-3.12`

---

## 12. Research / Backtest System (Separate from Live)

Used for **historical** strategy validation — not deployed to Render.

| File | Role |
|------|------|
| `oil_data_pipeline_new.py` | Pull WTI, Brent, OVX, COT, EIA stocks, rig counts → `master_df` |
| `strategy.py` | HMM regime + XGBoost walk-forward (daily bars) |
| `backtest_backtrader.py` | Event-driven backtest with Backtrader (Sharpe, Calmar, etc.) |
| `run_strategy.py` | One command: pipeline → strategy → backtest → HTML dashboard |
| `dashboard.py` | Plotly backtest dashboards |

**Live paper trading reuses similar ideas** (HMM regime + XGBoost) but on **intraday bars** with a simpler rolling retrain (not full walk-forward).

---

## 13. Data Flow Diagram (ML Engineer View)

```
                    ┌──────────────┐
                    │  yfinance    │
                    │  CL=F OHLCV  │
                    └──────┬───────┘
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
     ┌────────────────┐       ┌────────────────┐
     │ Daily bars     │       │ Intraday bars  │
     │ (regime HMM)   │       │ (5m/15m/1h/1d) │
     └────────┬───────┘       └────────┬───────┘
              │                        │
              │ BULL/CHOPPY/PANIC      │ features_intraday
              │ (position mult)        │
              └──────────┬─────────────┘
                         ▼
                ┌─────────────────┐
                │ XGBoost         │
                │ P(up) ∈ [0,1]   │
                └────────┬────────┘
                         │
                         ▼
                ┌─────────────────┐
                │ BUY/SELL/HOLD   │
                └────────┬────────┘
                         │
                         ▼
                ┌─────────────────┐
                │ OrderEngine     │
                │ Portfolio       │
                └────────┬────────┘
                         │
                         ▼
                ┌─────────────────┐
                │ SQLite + Flask  │
                │ Dashboard       │
                └─────────────────┘
```

---

## 14. Key Config Knobs (`paper_trading/config.py`)

| Setting | Default | Meaning |
|---------|---------|---------|
| `INITIAL_CAPITAL` | 1,000,000 | Paper account size |
| `BUY_THRESHOLD` | 0.52 | Prob above → BUY |
| `SELL_THRESHOLD` | 0.48 | Prob below → SELL |
| `MAX_POSITION_PCT` | 0.15 | Max 15% equity per trade |
| `MAX_DAILY_LOSS_PCT` | 0.05 | Circuit breaker at -5% day |
| `ACTIVE_TIMEFRAME` | 5m | Current bar size |

---

## 15. Common Questions

**Q: Is this real money?**  
No. Paper trading = simulated fills, fake $1M account.

**Q: Why two GitHub repos?**  
Different accounts/orgs. Render is wired to `AdityaDesai7/Paper-trade`.

**Q: Why did trades disappear after redeploy?**  
Render free tier disk resets. Trades live in `output/paper_trades.db` on local disk. Use Persistent Disk or export CSV via `/trades`.

**Q: What's the difference vs `backtest_backtrader.py`?**  
Backtest = historical simulation with Backtrader. Paper trading = live loop + web UI + SQLite log.

**Q: Can I change the model?**  
Yes — edit `model_intraday.py` (XGBoost params) or swap algorithm. Keep `predict_latest()` returning `(signal, prob)`.

**Q: 1m timeframe?**  
Removed intentionally. Use 5m minimum.

---

## 16. Quick Reference Commands

```powershell
# Local run
python paper_trading/run_paper_trader.py --timeframe 5m

# Push to Render production repo
git push production main

# Check DB locally
python check_db.py

# Git remotes
git remote -v
# origin     → aditya-heliuswork/Paper-Trading
# production → AdityaDesai7/Paper-trade
```

---

## 17. Related Docs in This Repo

| File | Content |
|------|---------|
| `process_and_changes.md` | Changelog of recent bug fixes & features |
| `petroquant_project_explanation.md` | Deep dive on research pipeline |
| `README.md` | Original project overview |
| `Project Guide.md` | General project guide |

---

*Last updated: July 2026 — reflects multi-timeframe system, close-only order logic, DB restore on restart, and Render deployment via AdityaDesai7/Paper-trade.*
