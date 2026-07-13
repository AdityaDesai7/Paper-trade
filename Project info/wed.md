# PetroQuant — Daily Work Log

**Previous session:** Friday, 10 July 2026 (see below)  
**This session:** Monday, 13 July 2026

---

## Objective for today (13 July 2026)

Understand whether the trade log and dashboard numbers are trustworthy, explain position-sizing ratios in plain language, fix dashboard visibility on light backgrounds, and tighten backtest risk controls so total exposure cannot silently exceed 100% of equity after losses or pyramiding.

---

## What we delivered today (13 July 2026)

### 1. Dashboard dark theme + trade log visibility fix (`dashboard.py`)
**What we did**  
Investigated broken / washed-out Plotly 6.8 visuals when the browser page background was white. Trade log table text and panel colors had low contrast; download button overlapped the table.

**Fixes applied**
- Separate `COLORS` for `grid`, `border`, `table_text`, `row_alt` (panels no longer same color as grid)
- `template=None` instead of `plotly_dark` (avoids white-page clash)
- Trade log + performance tables: alternating rows, visible borders, brighter text
- `_inject_dark_page_style()` for saved HTML preview (`output/_dashboard_preview.html`)
- Download annotation moved so it no longer covers Panel 11

**Git**
- Branch: `cursor/fix-dashboard-dark-theme-trade-log`
- Commit: `0bd8b58` — pushed to `AdityaDesai7/Paper-trade` on GitHub
- Only `dashboard.py` committed in that push; other local edits remain uncommitted

**Status:** **Kept** — dashboard readable on dark and light browser themes.

---

### 2. Trade log audit — first CSV (`dashboard_hmm_xgb_trade_log (1).csv`)
**What we did**  
Reviewed 422 rows (Aug 2023 → Jul 2026) as accountant + trader.

**What was clean**
- Net P&L math: `net = gross − commission` — **0 errors**
- Win rate, outcomes, date order, side vs signal at open — consistent
- Total net on closed trades: **+$635,338** (53% win rate, PF ~1.32)

**Issues found (behavior, not bad math)**
- **Hidden leverage:** dollar notional fixed at entry while equity moves → effective exposure can exceed 100% after losses
- **Pyramiding:** many same-day scale-ins; 18 days with summed `position_frac` on opens > 100%
- **Stop-loss tail risk:** 21 stops = **−$1.48M** (avg −$70k each); dominated losses
- **Confusing columns:** `gross_pnl_usd` from fills, not raw market prices; partial closes make `signal_close` look wrong
- **35 high-confidence losers** (prob > 0.9 still lost — score ≠ win probability)

**Learning**  
CSV row sums over-count exposure when multiple partial closes share the same `open_date`. Real issue is lifecycle behavior, not corrupted P&L.

**Status:** **Documented** — explained in depth to user in plain language with examples.

---

### 3. Position fraction / exposure analysis
**What we did**  
Quantified how much of the account each trade and each day actually uses.

**Per trade leg (`position_frac`)**
- Median: **~20%** | Meaningful trades (≥5%): **~51%** median | Max single leg: **~85%**

**Live book (the important part)**
- When invested, account often **83–90%** deployed
- **~27% of days** showed >100% if summing overlapping log rows (misleading) OR equity shrank while notional stayed fixed (real hidden leverage)

**Root cause explained**  
`Position_Size` is a daily **target**; `entry_notional` is **sticky** until rebalance. After a loss, `notional ÷ equity` can exceed 100% even when `signed_frac` still says 85%.

**Status:** **Understood** — led directly to item 4.

---

### 4. Risk controls — exposure cap + pyramiding toggle (`config.py` + `dashboard.py`)
**What we did**  
Implemented investor-realistic book limits in the lifecycle backtester.

**`config.py` additions / changes**
| Setting | Value | Purpose |
|---------|-------|---------|
| `MAX_TOTAL_EXPOSURE` | `1.00` | Cap gross notional ÷ **current** equity at 100% |
| `ALLOW_PYRAMIDING` | `True` | When `False`, block same-side scale-ins |
| `REGIME_MULTIPLIERS` | BULL 1.0, CHOPPY 0.9, PANIC 0.75 | Smaller size in volatile regimes |

**`dashboard.py` engine changes**
- `_effective_signed_frac()` — live exposure = `entry_notional / equity`
- `_apply_exposure_cap()` — clip targets; optional no-pyramid
- `_cap_scale_in_delta()` — block scale-ins past book cap
- Daily **de-leverage** when equity drop pushes exposure > 100% (`close_reason = exposure_cap`)
- `_rebalance()` now uses **effective** exposure, not stale `signed_frac`
- `Held_Position` column records live fraction (not entry-time fraction)
- Backtester prints: `per-trade ≤ 85% | book ≤ 100% | pyramiding on/off`

**Verified (synthetic test)**  
30% crash with stop-loss disabled → `exposure_cap` partial close fires; max held ≤ cap.

**Status:** **Implemented locally** — not yet confirmed in a fresh full `run_strategy.py` export (see item 7).

---

### 5. Documentation — ratios and dashboard output
**What we explained**
- **Position sizing chain:** `Position_Size = Signal × Regime_Mult × Confidence_Factor × MAX_POSITION_PCT`
- **Confidence formula:** `0.5 + |Probability − 0.5|` (range 0.5 → 1.0)
- **Performance ratios:** Sharpe, Sortino, win rate, profit factor, etc. — all from **daily net returns** or **closed trade objects**
- **What prints where:** terminal tearsheet (graded box) vs HTML 11 panels (equity, drawdown, heatmap, trade log, etc.)
- **`ALLOW_PYRAMIDING`:** why the line exists — toggle for “keep adding while signal stays BUY/SELL”

**Status:** **Reference for user** — no new code file.

---

### 6. Trade log audit — second CSV (`dashboard_hmm_xgb_trade_log.csv`)
**What we did**  
Re-audited after user exported a new log (418 rows).

**Clean**
- Net P&L math, return %, outcomes, side vs signal — **all pass**
- Total net: **+$630,113** | Win rate: **51.1%** | PF: **1.24**

**Still flagged**
- **21 stop_losses** → **−$1.46M** (same structural tail risk)
- **2 rows** slightly above 85% cap (86.9%) — minor breach
- **`exposure_cap` closes: 0** — this export likely from **before** new cap code ran end-to-end
- Same-day open frac sums > 100% on 18 days (log artifact + pyramiding still on)
- 128 LONG closes while `signal_close = BUY` — partial closes, not necessarily bugs

**Verdict**  
**Not suspicious as accounting** — trustworthy P&L. **Suspicious as risk** — large stops on big SHORTs remain the main problem until a fresh backtest with new caps.

**Status:** **Audited** — user advised to re-run `python run_strategy.py` and compare new CSV for `exposure_cap` rows.

---

### 7. Other topics touched (no code merge)
- Explained **Streamlit `skills` CLI** (official AI instruction packs) vs dashboard “Market Skill” metrics — not the same thing
- Clarified **git remote**: branch pushed to `AdityaDesai7/Paper-trade`; other project files still uncommitted locally

---

## End-of-day position (13 July 2026)

| Area | Status |
|------|--------|
| Dashboard theme + trade log visibility (Plotly 6.8) | **Kept** — committed + pushed on feature branch |
| Trade log accounting trustworthiness | **Confirmed** — math is clean on both CSVs |
| Exposure cap + de-leverage (`MAX_TOTAL_EXPOSURE`) | **Implemented** in `dashboard.py` + `config.py` |
| `ALLOW_PYRAMIDING` toggle | **Added** — default `True` |
| PANIC / CHOPPY regime sizing | **Tuned** — 0.75 / 0.90 |
| Fresh backtest CSV with `exposure_cap` rows | **Pending** — user to re-run `run_strategy.py` |
| Exposure cap + sizing changes committed to git | **Not yet** — only dashboard theme branch pushed |

---

## Key takeaways (13 July 2026)

1. **Trade logs are honest** — net P&L, fees, and win/loss labels add up; headline +$630k is believable.
2. **The real enemy is tail risk** — a handful of large stop-losses on oversized SHORT legs erase most signal profits.
3. **Hidden leverage was the insight** — sticky notional + falling equity made effective exposure > 100% without looking wrong in `position_frac`.
4. **Fix is in the engine** — `MAX_TOTAL_EXPOSURE`, effective exposure rebalance, and optional `ALLOW_PYRAMIDING = False`.
5. **Next validation step** — re-run strategy, export new trade log, confirm `exposure_cap` closes and lower max deployed %.

---

## Files changed today (13 July 2026)

| File | Change summary |
|------|----------------|
| `dashboard.py` | Dark theme fix; exposure cap engine; effective exposure rebalance; `Held_Position` = live fraction |
| `config.py` | `MAX_TOTAL_EXPOSURE`, `ALLOW_PYRAMIDING`, `REGIME_MULTIPLIERS` (CHOPPY 0.9, PANIC 0.75) |
| `Project info/wed.md` | This session log |
| Git branch `cursor/fix-dashboard-dark-theme-trade-log` | Theme fix committed + pushed |

---

# Previous session — Friday, 10 July 2026

---

## Objective for today (10 July 2026)

Make `config.py` the true single source of truth for research and paper trading, fix the `MAX_POSITION_PCT` wiring bug, and replace the vectorized backtester's flawed P&L logic with investor-grade position-lifecycle accounting in `dashboard.py`.

---

## What we delivered today (10 July 2026)

### 1. Diagnosed `MAX_POSITION_PCT` bug
**What we did**  
Traced the full pipeline: `config.py` → `run_strategy.py` → `strategy.py` → `dashboard.py` → `paper_trading/`.

**Root cause**  
`MAX_POSITION_PCT` was defined in `config.py` but only consumed by `paper_trading/portfolio.py`. The research path (`run_strategy.py` → `dashboard.py`) never read it. `strategy.py` also hardcoded its own regime multipliers (`CHOPPY=0.5`, `PANIC=0.25`) that disagreed with config (`0.6`, `0.4`).

**Learning**  
Changing a config value has zero effect unless every execution path that should use it actually imports and applies it.

**Status:** **Fixed** (see items 2–4).

---

### 2. Centralized configuration (`config.py`)
**What we did**
- Fixed misleading comment on `MAX_POSITION_PCT` (said 25%, value was 85%)
- Added **Section 13** — `CONFIDENCE_FLOOR`, `CONFIDENCE_SCALE` (position sizing formula)
- Added **Section 14** — all `XGB_*` hyperparameters + `FORECAST_TRAIN_SPLIT`
- Added **Section 15** — `VOL_LOOKBACK_DAYS`, `CI_Z_SCORE`, `ATR_DECAY_*`, `VOL_BLEND_WEIGHTS`
- Updated `get_daily_strategy_params()` to pass `max_position_pct`
- Expanded QUICK CHANGE GUIDE at top of file

**How it helps**  
One file to edit for strategy horizon, thresholds, position cap, XGBoost tuning, volatility CI width, and regime sizing — no more hunting across `strategy.py`, `run_strategy.py`, and `backtest_backtrader.py`.

**Status:** **Kept** — production value.

---

### 3. Wired `strategy.py` to `config.py`
**What we did**
- `import config as cfg`
- `HMMXGBoostStrategy` accepts `max_position_pct` (from config via `get_daily_strategy_params()`)
- `Position_Size = Signal × Regime_Mult × Confidence × MAX_POSITION_PCT` (was missing the last factor)
- `REGIME_MULTIPLIERS` read from config (replaced hardcoded `0.5` / `0.25`)
- Confidence factor uses `CONFIDENCE_FLOOR` / `CONFIDENCE_SCALE`
- Walk-forward and forecast XGBoost use `cfg.XGB_*` params
- `VolatilityEngine` uses `cfg.VOL_BLEND_WEIGHTS`, `CI_Z_SCORE`, `ATR_DECAY_*`, `VOL_LOOKBACK_DAYS`

**How it helps**  
Research output (`run_strategy.py` → HTML dashboard) now reflects config changes immediately. Regime sizing is consistent between research and paper trading.

**Status:** **Kept**.

---

### 4. Wired `backtest_backtrader.py` to `config.py`
**What we did**  
Replaced five hardcoded constants with config references:

| Was hardcoded | Now from config |
|---------------|-----------------|
| `INITIAL_CASH = 100_000` | `cfg.INITIAL_CAPITAL` ($1M) |
| `COMMISSION = 0.001` | `cfg.COMMISSION_PCT` |
| `SLIPPAGE_PERC = 0.005` | `cfg.SLIPPAGE_PCT` |
| `POSITION_SIZE_PCT = 0.95` | `cfg.BT_POSITION_PCT` |
| `FWD_DAYS = 5` | `cfg.DAILY_FWD_DAYS` |

**How it helps**  
Backtrader validation runs use the same capital, costs, and horizon as the rest of the system.

**Status:** **Kept**.

---

### 5. Position-lifecycle backtester (`dashboard.py`) — investor-grade fix
**What we did**  
Replaced the vectorized shortcut (`position.shift(1) × daily_return`) with a full **Position Lifecycle Engine**:

| Component | Role |
|-----------|------|
| `calculate_position_return()` | Per-trade P&L: invested amount, gross/net P&L, return %, slippage, commission |
| `OpenPosition` / `ClosedTrade` | Entry leg + exit leg tracked as one lifecycle |
| `simulate_position_lifecycle()` | Event-driven simulation over full backtest |
| `_rebalance()` | Open, scale-in, partial close, full close; **flip = close then open** |

**Rules (aligned with `paper_trading/order_engine.py`)**
- Slippage-aware fill prices on every leg
- Commission on **both** entry and exit
- Partial closes reduce size; scale-ins use weighted-average entry
- Still-open position at end → mark-to-market only; **excluded from win-rate**

**Metrics now computed correctly**

| Metric | Before (buggy) | After (correct) |
|--------|----------------|-----------------|
| Win rate | % of positive **days** | % of **closed round-trip trades** |
| Profit factor | Daily gain/loss sums | Winning trades / losing trades ($) |
| Total trades | Days with non-zero return | Completed entry+exit lifecycles |
| Flips | One continuous exposure change | Close old + open new (2 commission legs) |
| Costs | `abs(Δposition) × cost_per_side` approximation | Actual per-fill commission + slippage in prices |

**How it helps**  
Dashboard numbers are defensible in front of investors. Returns are more conservative but honest. Research backtest now matches the same lifecycle logic as live paper trading.

**Status:** **Kept** — required for investor-facing dashboard.

---

### 6. Documentation and before/after reference
**What we did**  
- Full pipeline diagram (Track A research vs Track B paper trading)
- Before/after table mapping old hardcoded `strategy.py` values → new `config.py` variables
- Audit of position-lifecycle bug pattern vs `portfolio.py` (paper trading was already correct)

**Status:** **Reference for team** — see conversation / this log.

---

## End-of-day position (10 July 2026)

| Area | Status |
|------|--------|
| Single source of truth (`config.py` Sections 0–15) | **Kept** |
| `strategy.py` reads all sizing / XGB / vol params from config | **Kept** |
| `get_daily_strategy_params()` passes `max_position_pct` | **Kept** |
| `backtest_backtrader.py` reads capital + costs from config | **Kept** |
| Position-lifecycle engine in `dashboard.py` | **Kept** |
| `paper_trading/config.py` shim (re-export only) | **Unchanged** — already correct |

---

## Key takeaways (10 July 2026)

1. **Config is now real** — Edit `config.py` only; `run_strategy.py` output changes for position cap, regime multipliers, XGBoost, and volatility settings.
2. **Research ≠ paper trading gap closed** — Same regime multipliers and position-cap formula; backtester uses lifecycle logic like `order_engine.py`.
3. **Investor dashboard is trustworthy** — Win rate, profit factor, and total trades are trade-level, not day-level fiction.
4. **Expect lower headline returns** — Lifecycle + full costs + `MAX_POSITION_PCT` cap produce more conservative numbers; that is correct.
5. **Next tuning lever** — Adjust `MAX_POSITION_PCT`, `REGIME_MULTIPLIERS`, or `BUY_THRESHOLD` / `SELL_THRESHOLD` in `config.py` and re-run `python run_strategy.py` to see impact without touching code.

---

## Files changed today (10 July 2026)

| File | Change summary |
|------|----------------|
| `config.py` | Sections 13–15; `get_daily_strategy_params()` + comment fixes |
| `strategy.py` | Full config wiring; `Position_Size` includes `MAX_POSITION_PCT` |
| `backtest_backtrader.py` | 5 constants → `cfg.*` |
| `dashboard.py` | Position Lifecycle Engine; `Backtester.run()` rewrite |
| `run_strategy.py` | No code change (already called `get_daily_strategy_params()`) |
| `paper_trading/config.py` | No change (re-export shim) |

---

# Previous session — Wednesday, 8 July 2026

---

## Objective for the day

Strengthen the research pipeline so reported performance reflects more realistic market conditions, diagnose data/horizon issues, and test whether richer data or trade discipline improves live-usable results — while recording what worked, what failed, and what we learned.

---

## What we delivered

### 1. Diagnostic: prediction horizon gap
**What we did**  
Investigated why model forecasts stopped around mid-March 2026 while the master feature CSV extended into July 2026.

**Outcome**  
Root cause identified: incomplete / stale `US_Oil_Rigs` coverage combined with a hard `dropna()` in feature engineering, which dropped all subsequent usable rows.

**Learning**  
Pipeline “end date” and strategy “usable end date” are not the same. Feature completeness — not only API refresh — governs how far signals can run.

---

### 2. Configuration visibility (`config.py`)
**What we did**  
Mapped how presets and thresholds flow into `run_strategy.py`, the vectorized backtester, and Backtrader.

**Outcome**  
Clarified why some settings (especially cost presets) previously appeared to have “no effect”: the vectorized research path was not applying commission or slippage.

**Learning**  
A parameter only exists if every critical path consumes it. Central config alone is not enough.

---

### 3. Transaction-cost accounting in the research backtester
**What we did**  
Added commission and slippage to the vectorized backtester in `dashboard.py`, reported gross vs net returns, cost drag, and cost metadata in the terminal report and dashboard.

**Also corrected**
- Backtrader commission double-counting (`BT_COMMISSION` now equals one-way commission per leg, not ×2)
- Alignment of PnL with `Position_Size` (regime × confidence), not only raw `Signal`

**Outcome**  
Research reports now show idealized (gross) and friction-aware (net) views under the active cost preset (e.g. `CONSERVATIVE`).

**Learning**  
A large share of previously reported performance was inflated by zero-friction assumptions. Transparency on costs improved decision quality even when headline returns looked worse.

**Status:** **Kept** — production value.

---

### 4. Vectorized vs Backtrader decision
**What we did**  
Compared roles of the fast vectorized engine and the event-driven Backtrader engine for a daily close-to-close ML strategy.

**Outcome**  
Recommended keeping both: vectorized for iteration and dashboards; Backtrader for cash/order realism and secondary validation.

**Learning**  
“More realistic engine” does not automatically mean “better model.” For this architecture, signal quality, sizing, and costs matter more than switching frameworks alone.

**Status:** **Guidance kept**; no exclusive switch.

---

### 5. OHLCV enrichment experiment
**What we did**  
Designed and implemented a fuller OHLC path: fetch Open/High/Low/Volume, richer volatility (True Range / range-based estimators), HMM inputs, and real OHLC in Backtrader where available.

**Outcome**  
**Rolled back after review.** Gross performance fell materially versus the historical close-only baseline (user reading: from a high historical level into roughly mid–30% gross / single-digit net under that experiment). Attribution was messy because several effects stacked.

**Why it failed (for this stage)**
- Many features added without a controlled A/B against the close-only stack
- Information set and volatility structure changed in one shot
- Combined with stricter sizing / cost reporting, so impacts were hard to separate

**Learning**  
Richer data is not automatically better. OHLCV should be reintroduced in small, measurable steps (e.g. ATR only → gap features → HMM inputs), each with a clear before/after metric set — not as a single redesign.

**Status:** **Reverted**.

---

### 6. Anti-churn / trade-discipline filters (first attempt)
**What we did**  
Tried higher conviction thresholds, signal persistence, and a minimum holding period together to cut cost drag from near-daily position flips.

**Outcome**  
**Rolled back immediately.** The filter stack produced **zero trades** — too restrictive for the current probability distribution.

**Why it failed**  
Persistence + widened bands + minimum hold compounded and blocked almost all actionable crossings around 0.5.

**Learning**  
Churn is real (~700 transitions per ~700 days in recent reports), but discipline must be tuned gently and validated. Aggressive multi-filter stacks that maximize cost savings can erase the entire book.

**Status:** **Reverted**.

---

### 7. Holding-policy experiment (second attempt)
**What we did**  
Implemented a single portfolio-layer rule: hold ~`DAILY_FWD_DAYS` (5), early exit only on a hard Prob flip past `0.5 ± FLIP_EDGE`, and freeze position size while direction is unchanged.

**Outcome**  
**Rolled back** after review — results worse than the baseline churning book for the user’s criteria.

**Learning**  
Aligning hold length with the label horizon is conceptually sound, but hardcoded min-hold + flip-to-flat can destroy path-dependent alpha when the edge comes from frequent directional updates. Turnover reduction is not free.

**Status:** **Reverted**.

---

### 8. Post-rollback repair
**What we did**  
Restored core strategy/pipeline files after experimental reverts and fixed a constructor mismatch (`forecast_horizons`) so `run_strategy.py` runs cleanly with `config.py`.

**Outcome**  
Strategy runner operational again. After OHLCV/filter undos, a representative cost-aware report looked on the order of **~+55% gross / ~+34% net** under `CONSERVATIVE` costs (exact figures vary by run/date window).

---

## End-of-day clarification: ~146% vs ~53% gross

| Number | Meaning |
|--------|---------|
| **~+86% (older style)** | Often full **`Signal` (±1)** compound return, frequently **without** costs in the old report |
| **~+53% gross (current)** | **`Position_Size`** = Signal × regime × confidence — smaller average exposure |
| **~+34% net (current)** | Sized book **after** commission + slippage |

**Which is nearer real world?**  
Sized book + costs (`Position_Size` → gross → net). The ~86% figure is useful for “does the signal point the right way?” but not for “what would I keep after realistic risk and friction?”

---

## End-of-day position

| Area | Status |
|------|--------|
| Cost-aware research reporting (`dashboard.py`) | **Kept** |
| `BT_COMMISSION` fix; sized PnL in vectorized metrics | **Kept** |
| Dual backtest architecture guidance | **Kept** |
| OHLCV redesign | **Reverted** — revisit later, staged |
| Anti-churn filter stack | **Reverted** |
| Min-hold / flip holding policy | **Reverted** |
| Open risk | High daily turnover → large cost drag under conservative costs |

---

## Key takeaways

1. **Measurement matured** — We can now distinguish idealized edge from net of costs and from full-size vs sized books.  
2. **Experiments were decisive** — Hypotheses were shipped, measured, and rolled back without ego when they failed the bar.  
3. **Main remaining tension** — Not “no alpha”; recent sized gross still looks positive. Bind is **over-trading under realistic costs** vs filters that kill the edge.  
4. **Next design rule** — One controlled change at a time, with **gross (Signal)**, **gross (Position_Size)**, **net**, and **transitions** published side by side before any permanent merge.

---

## Representative metrics discussed today (illustrative)

| Regime | Approx. print |
|--------|----------------|
| After OHLCV + costs (then rolled back) | ~+36% gross / ~+6% net, high transitions |
| Baseline after OHLCV undo (sized + costs) | ~+55% gross / ~+26% net, ~711 transitions |
| Older full-Signal style (research memory) | ~+146% (not apples-to-apples with sized + costs) |

---

*Document intent: status update for stakeholders — progress, honest failure analysis, and learning for the next iteration.*
