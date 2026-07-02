# ============================================================================
# PETROQUANT PAPER TRADING — STRATEGY RUNNER (per-timeframe engine)
# ============================================================================
# Each StrategyRunner is a fully self-contained trading engine for ONE
# timeframe. It owns its own:
#   - Data fetch interval (5m bars / 15m bars / 1h bars / 1d bars)
#   - XGBoost model (trained only on its own TF's data)
#   - Portfolio (independent equity, P&L, positions)
#   - TradeLog (separate SQLite file: paper_trades_5m.db, etc.)
#   - Loop thread (runs at its own interval — 300s / 900s / 3600s / 86400s)
#
# Multiple StrategyRunners run simultaneously inside MultiStrategyEngine.
# They never share state — no global config mutation, no shared portfolio.
#
# StrategyRunner:
#   start()       — begin the loop thread
#   stop()        — gracefully stop (wakes sleep immediately)
#   run_once()    — one cycle (for testing)
#   reset()       — wipe DB + restart portfolio
#   get_status()  — dict for web server / dashboard
# ============================================================================

import threading
import time
import logging
import os
from datetime import datetime

from . import config as cfg
from .price_feed        import fetch_candles, is_market_open, get_market_status
from .features_intraday import build_features, build_target
from .model_intraday    import IntradaySignalModel
from .daily_regime      import DailyRegimeDetector
from .portfolio         import Portfolio
from .order_engine      import OrderEngine
from .trade_log         import TradeLog

logger = logging.getLogger(__name__)

_INTERVAL_TO_MINUTES = {
    '1m': 1, '5m': 5, '15m': 15, '30m': 30,
    '1h': 60, '2h': 120, '4h': 240, '1d': 1440,
}


class StrategyRunner:
    """
    Self-contained paper trading engine for a single timeframe.

    Fetches only its own TF's candles, trains its own XGBoost model on that
    data, and manages an independent portfolio. Multiple StrategyRunners can
    run in parallel without any shared mutable state.

    Parameters
    ----------
    timeframe : str — '5m', '15m', '1h', or '1d'
    """

    def __init__(self, timeframe: str):
        if timeframe not in cfg.TIMEFRAME_CONFIGS:
            raise ValueError(
                f"Unknown timeframe '{timeframe}'. "
                f"Valid: {list(cfg.TIMEFRAME_CONFIGS.keys())}"
            )

        self.timeframe = timeframe
        tf = cfg.TIMEFRAME_CONFIGS[timeframe]

        # ── TF-specific params (immutable after init — no global mutations) ──
        self._candle_interval = tf['interval']           # '5m', '15m', '1h', '1d'
        self._bars_to_fetch   = tf['bars_to_fetch_days'] # days of history to fetch
        self._loop_interval   = tf['loop_secs']          # seconds between cycles
        self._horizon         = tf['predict_horizon']    # bars ahead to predict
        self._min_train_bars  = tf['min_train_bars']
        self._retrain_mins    = tf['retrain_mins']
        self._tf_minutes      = _INTERVAL_TO_MINUTES.get(tf['interval'], 1)
        self._label           = tf['label']              # display label e.g. '5 Min'

        # ── Per-TF SQLite database ───────────────────────────────────────────
        db_path = os.path.join(cfg.OUTPUT_DIR, f'paper_trades_{timeframe}.db')

        # ── Per-TF JSON backup directory ──────────────────────────────────────
        self._backup_dir = os.path.join(cfg.STATE_BACKUP_DIR, timeframe)

        # ── Independent components — never shared with other runners ─────────
        self.trade_log    = TradeLog(db_path=db_path)
        self.portfolio    = Portfolio(initial_capital=cfg.INITIAL_CAPITAL, backup_dir=self._backup_dir)
        self.model        = IntradaySignalModel(
            horizon            = self._horizon,
            min_train_bars     = self._min_train_bars,
            retrain_every_mins = self._retrain_mins,
        )
        self.order_engine = OrderEngine(self.portfolio, self.trade_log, self.model)
        self.regime_det = DailyRegimeDetector()

        # ── Live state (read by web server / dashboard) ──────────────────────
        self.last_price          = None
        self.last_signal         = 'HOLD'
        self.last_prob           = 0.5
        self.current_regime      = 'UNKNOWN'
        self.current_regime_mult = 1.0
        self._last_df            = None   # latest OHLCV DataFrame

        # ── Threading ────────────────────────────────────────────────────────
        self._running    = False
        self._thread     = None
        self._stop_event = threading.Event()
        self._start_time = None

        # ── Restore state from DB, then JSON fallback ────────────────────────
        restored = self.portfolio.restore_from_db(self.trade_log)
        if restored:
            logger.info(f"[{timeframe}] Portfolio restored from DB")
        else:
            # DB was empty — try JSON backup (survives ephemeral filesystem wipes)
            json_restored = self.portfolio.restore_from_json(self._backup_dir)
            if json_restored:
                logger.info(f"[{timeframe}] Portfolio restored from JSON backup")
            else:
                logger.info(f"[{timeframe}] Fresh start — no prior trade history")

        logger.info(
            f"[{timeframe}] StrategyRunner ready — "
            f"interval={self._candle_interval}, loop={self._loop_interval}s, "
            f"horizon={self._horizon}bars"
        )

    # ── Public API ────────────────────────────────────────────────────────────
    def start(self):
        """Start the trading loop in a background daemon thread."""
        if self._running:
            logger.warning(f"[{self.timeframe}] Already running.")
            return
        self._stop_event.clear()
        self._running    = True
        self._start_time = datetime.utcnow()
        self._thread = threading.Thread(
            target=self._loop,
            name=f'Loop-{self.timeframe}',
            daemon=True,
        )
        self._thread.start()
        logger.info(f"[{self.timeframe}] Loop started (every {self._loop_interval}s).")

    def stop(self):
        """
        Gracefully stop the trading loop.
        Sets _stop_event so the sleep wakes immediately instead of waiting
        up to loop_interval seconds (which can be 86400s on 1d).
        """
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=30)
            if self._thread.is_alive():
                logger.warning(
                    f"[{self.timeframe}] Thread did not exit in 30s — continuing"
                )
        logger.info(f"[{self.timeframe}] Loop stopped.")

    def run_once(self) -> dict:
        """Execute exactly one trading cycle (for testing / manual runs)."""
        return self._cycle()

    def uptime_seconds(self) -> float:
        if self._start_time is None:
            return 0.0
        return (datetime.utcnow() - self._start_time).total_seconds()

    def reset(self):
        """Wipe all trades and snapshots; restart portfolio at initial capital."""
        with self.trade_log._get_conn() as conn:
            conn.execute("DELETE FROM trades")
            conn.execute("DELETE FROM snapshots")
        self.portfolio    = Portfolio(initial_capital=cfg.INITIAL_CAPITAL, backup_dir=self._backup_dir)
        self.model        = IntradaySignalModel(
            horizon            = self._horizon,
            min_train_bars     = self._min_train_bars,
            retrain_every_mins = self._retrain_mins,
        )
        self.order_engine = OrderEngine(self.portfolio, self.trade_log, self.model)

        # Also clear JSON backup so reset is truly clean
        import shutil
        if os.path.exists(self._backup_dir):
            shutil.rmtree(self._backup_dir, ignore_errors=True)
            logger.info(f"[{self.timeframe}] Cleared JSON backup at {self._backup_dir}")

        logger.warning(
            f"[{self.timeframe}] Strategy RESET — trades cleared, "
            f"portfolio restarted at ${cfg.INITIAL_CAPITAL:,.0f}"
        )

    def get_status(self) -> dict:
        """Returns full current status as a dict for the API / dashboard."""
        snap    = self.portfolio.get_snapshot(self.last_price)
        summary = self.trade_log.get_summary()
        return {
            'timeframe'    : self.timeframe,
            'label'        : self._label,
            'loop_secs'    : self._loop_interval,
            'horizon'      : self._horizon,
            'interval'     : self._candle_interval,
            'running'      : self._running,
            'uptime_secs'  : self.uptime_seconds(),
            'current_price': self.last_price,
            'last_signal'  : self.last_signal,
            'last_prob'    : self.last_prob,
            'regime'       : self.current_regime,
            'regime_mult'  : self.current_regime_mult,
            'portfolio'    : snap,
            'summary'      : summary,
            'model'        : self.model.get_model_status(timeframe=self.timeframe),
        }

    # ── Trading Loop ──────────────────────────────────────────────────────────
    def _loop(self):
        logger.info(
            f"[{self.timeframe}] Loop running — every {self._loop_interval}s "
            f"({self._candle_interval} candles, horizon={self._horizon}bars)"
        )
        while self._running:
            cycle_start = time.time()
            try:
                self._cycle()
            except Exception as e:
                logger.error(
                    f"[{self.timeframe}] Unhandled error in cycle: {e}",
                    exc_info=True
                )
            elapsed   = time.time() - cycle_start
            sleep_for = max(0, self._loop_interval - elapsed)
            logger.debug(
                f"[{self.timeframe}] Cycle took {elapsed:.1f}s — "
                f"sleeping {sleep_for:.1f}s"
            )
            # event.wait() instead of time.sleep() so stop() wakes us instantly
            self._stop_event.wait(timeout=sleep_for)

    # ── Single Cycle ──────────────────────────────────────────────────────────
    def _cycle(self) -> dict:
        """
        One complete trading cycle:
          fetch → features → (re)train → signal → execute → log
        Uses only this TF's candle interval and horizon — completely isolated
        from other StrategyRunner instances.
        """
        now = datetime.utcnow()
        logger.info(
            f"[{self.timeframe}] ---- Cycle @ "
            f"{now.strftime('%Y-%m-%d %H:%M:%S UTC')} ----"
        )

        # Step 1: Check market hours
        market_status = get_market_status()
        if not market_status['is_open']:
            logger.info(
                f"[{self.timeframe}] Market CLOSED "
                f"({market_status['note']}) — skipping"
            )
            return {'status': 'market_closed', 'reason': market_status['note']}

        # Step 2: Fetch THIS timeframe's candles only
        logger.info(
            f"[{self.timeframe}] Fetching {self._candle_interval} WTI candles "
            f"({self._bars_to_fetch} days of history)..."
        )
        df_raw = fetch_candles(
            interval = self._candle_interval,
            days     = self._bars_to_fetch,
        )
        if df_raw.empty:
            logger.warning(f"[{self.timeframe}] No data from yfinance — skipping")
            return {'status': 'no_data'}

        self._last_df   = df_raw
        self.last_price = float(df_raw['Close'].iloc[-1])
        logger.info(
            f"[{self.timeframe}] Fetched {len(df_raw)} bars | "
            f"WTI: ${self.last_price:.4f}"
        )

        # Step 3: Get daily regime (shared HMM, cached per day)
        regime, regime_mult = self.regime_det.get_regime()
        self.current_regime      = regime
        self.current_regime_mult = regime_mult
        logger.info(f"[{self.timeframe}] Regime: {regime} (mult: {regime_mult})")

        # Step 4: Build features using THIS TF's minute resolution
        feat_df = build_features(
            df_raw,
            regime            = regime,
            timeframe_minutes = self._tf_minutes,
        )
        if feat_df.empty:
            logger.warning(f"[{self.timeframe}] Feature build failed — skipping")
            return {'status': 'feature_error'}

        # Step 5: Train or retrain model (on THIS TF's features and horizon)
        if self.model.should_retrain():
            logger.info(f"[{self.timeframe}] Retraining XGBoost model...")
            labeled_df   = build_target(feat_df, horizon=self._horizon)
            train_result = self.model.train(labeled_df)
            logger.info(f"[{self.timeframe}] Retrain: {train_result.get('status')} | "
                        f"val_acc={train_result.get('val_accuracy', 'N/A')}")

        # Step 6: Generate signal from the latest bar
        signal, prob = self.model.predict_latest(feat_df)
        self.last_signal = signal
        self.last_prob   = prob
        logger.info(
            f"[{self.timeframe}] Signal: {signal} | Prob(UP): {prob:.2%}"
        )

        # Step 7: Execute order via order engine
        result = self.order_engine.execute(
            signal      = signal,
            probability = prob,
            price       = self.last_price,
            regime      = regime,
            regime_mult = regime_mult,
            bar_time    = now,
        )
        logger.info(
            f"[{self.timeframe}] Execution: {result.get('action')} | "
            f"equity=${result.get('equity', self.portfolio.cash):,.2f}"
        )

        # Step 8: Log portfolio snapshot
        snap = self.portfolio.get_snapshot(self.last_price)
        pos_label = 'FLAT'
        if snap.get('open_position'):
            pos_label = snap['open_position'].get('side', 'OPEN').upper()
        logger.info(
            f"[{self.timeframe}] Portfolio | equity=${snap['equity']:,.2f} | "
            f"return={snap['total_return_pct']:+.3f}% | "
            f"position={pos_label} | trades={snap['total_trades']}"
        )

        return {
            'status'     : 'ok',
            'signal'     : signal,
            'probability': prob,
            'price'      : self.last_price,
            'regime'     : regime,
            'action'     : result.get('action'),
            'equity'     : snap['equity'],
            'return_pct' : snap['total_return_pct'],
            'timeframe'  : self.timeframe,
        }
