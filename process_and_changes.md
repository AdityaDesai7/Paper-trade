# PetroQuant Paper Trading: Complete Process & Development Summary

This document serves as a complete log of all the improvements, bug fixes, and feature additions made to the PetroQuant Paper Trading Engine during our recent development sessions. 

---

## 1. Initial QA Audit & Bug Fixes
We conducted a comprehensive QA audit of the existing codebase and identified/fixed 15 bugs across various severity levels to make the system production-ready.

**Critical Fixes:**
- **Price Chart Rendering:** Fixed the Plotly chart rendering bug where the price line wasn't appearing alongside the signal markers. This was caused by mismatched timezone awareness (tz-aware vs tz-naive) between `yfinance` data and SQLite timestamps.
- **State Recovery (Cash Balance):** Fixed a bug where restarting the engine after a crash would inflate the cash balance by including lost unrealized P&L. `Portfolio.restore_from_db()` now strictly calculates `cash = initial_capital + realized_pnl`.
- **Dashboard State Sync:** Ensured that calling `engine.reset_account()` updates the live dashboard's reference to the new Portfolio instance so the UI doesn't remain stale.
- **yfinance 1.2.0 Compatibility:** Migrated the data fetcher from `ticker.history()` (which broke by returning MultiIndex columns in yfinance 1.x) to a highly stable `yf.download()` implementation with automatic MultiIndex flattening.

**Medium & Minor Fixes:**
- **Timezone Maintenance Drop:** Fixed `_drop_maintenance_hour()` crashing on tz-aware indices.
- **Dynamic Training Windows:** Stopped hardcoding `min_train_bars = 500`. The model now dynamically adjusts the minimum required bars based on the active timeframe so it can actually train on 1h and 1d candles.
- **Cold-Start Gracefulness:** Handled `None` current prices on engine startup gracefully so the dashboard says "Connecting..." instead of crashing or showing "$0.00".
- **Loss Limit Snapshots:** Fixed a broken snapshot chain when the daily loss limit triggered a circuit breaker.

---

## 2. Multi-Timeframe Architecture (Phase 1)
We successfully scaled the trading engine from a hardcoded 1-minute loop to a dynamic multi-timeframe system.

- **Centralized Configuration:** Created a `TIMEFRAME_CONFIGS` dictionary in `config.py` and an `apply_timeframe(tf)` helper. This allows every module (price feed, features, model, portfolio) to instantly adapt to a new bar size.
- **Runtime Switching:** Added a `switch_timeframe()` method in `engine.py` that gracefully halts the current trading loop, reconfigures the global state, clears the model, and restarts on the new timeframe *without* killing the python process.
- **Web API:** Created a `POST /set-timeframe` endpoint in `web_server.py`.
- **CLI Support:** Updated `run_paper_trader.py` to accept `--timeframe` arguments (e.g., `python paper_trading/run_paper_trader.py --timeframe 15m`).

---

## 3. UI Overhaul & 1-Minute Removal (Phase 2)
The user noted that 1-minute trading is too noisy for realistic signals, and requested its complete removal along with a massive UI upgrade for the timeframe switcher.

- **1-Minute Removed:** Completely stripped out the 1-minute timeframe. The **5-Minute (5m)** timeframe is now the absolute minimum and the system's new default.
- **Dashboard UI Redesign:** Replaced the tiny HTML select dropdown with large, premium **Pill Buttons** (`5 Min`, `15 Min`, `1 Hour`, `1 Day`) fixed at the top of the dashboard.
- **Interactive Feedback:** 
  - The currently active timeframe button glows with a blue accent.
  - Clicking a new timeframe triggers a dark loading overlay with a spinner while the backend switches over.
  - The chart titles dynamically update (e.g., "WTI 5 Min Price + Signals").
  - Added helpful descriptive text (e.g., *"5 Minutes — trade every 5 min, predict 15 min ahead"*).

---

## 4. Testing & Deployment
- **Smoke Testing:** Wrote a comprehensive `smoke_test.py` script that verified `config`, `price_feed`, `model_intraday`, `portfolio`, `order_engine`, and `dashboard_live` modules end-to-end to ensure zero crashes.
- **Version Control:** Committed all changes across 10 files and successfully pushed them to the `origin main` branch of the GitHub repository (`aditya-heliuswork/Paper-Trading`).

---

### Final Timeframe Configurations

| Timeframe | Engine Loop | ML Prediction Horizon | Minimum Bars to Train |
|-----------|-------------|------------------------|-----------------------|
| **5 Min** | 300 seconds | 3 bars (15 minutes) | 150 bars |
| **15 Min**| 900 seconds | 2 bars (30 minutes) | 100 bars |
| **1 Hour**| 1 hour | 2 bars (2 hours) | 80 bars |
| **1 Day** | 24 hours | 1 bar (next day) | 80 bars |

*All code is successfully pushed to production. To see changes live on Render, a manual redeployment may be required in the Render.com dashboard if Auto-Deploy is disabled or delayed.*
