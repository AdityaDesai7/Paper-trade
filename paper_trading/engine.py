# ============================================================================
# PETROQUANT PAPER TRADING — MULTI-STRATEGY ENGINE
# ============================================================================
# Manages 4 independent StrategyRunner instances, one per timeframe.
# All strategies run simultaneously — each with its own:
#   - Data fetch (5m / 15m / 1h / 1d candles from yfinance)
#   - XGBoost model (trained ONLY on its own TF's data)
#   - Portfolio (independent equity, P&L, open positions)
#   - SQLite database (paper_trades_5m.db, paper_trades_15m.db, etc.)
#   - Loop thread (runs at its own cadence: 300s / 900s / 3600s / 86400s)
#
# No global config mutations. No shared portfolios. Each TF is isolated.
#
# MultiStrategyEngine:
#   start_all()           — start all 4 trading loops
#   stop_all()            — gracefully stop all loops
#   get_strategy(tf)      — returns a specific StrategyRunner
#   get_all_status()      — dict of all TF statuses for the API
#   reset_strategy(tf)    — reset one strategy's trades + portfolio
#   reset_all()           — reset all strategies
#   is_market_open()      — True if WTI futures are currently trading
#   uptime_seconds()      — how long the engine has been running
# ============================================================================

import logging
import sys
import os
from datetime import datetime
from pathlib import Path

# ── Setup logging before other imports ───────────────────────────────────────
def _setup_logging(log_path: str):
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-7s | %(name)s | %(message)s',
        handlers=[
            logging.FileHandler(log_path, encoding='utf-8'),
            logging.StreamHandler(sys.stdout),
        ]
    )

from . import config as cfg

_setup_logging(cfg.LOG_PATH)
logger = logging.getLogger(__name__)

from .price_feed     import is_market_open
from .strategy_runner import StrategyRunner


class MultiStrategyEngine:
    """
    Coordinates multiple independent StrategyRunner instances.

    Each StrategyRunner owns its own data, model, portfolio, and DB.
    They all run simultaneously in separate daemon threads.

    Enabled timeframes: 5m, 15m, 1h, 1d
    """

    ENABLED_TIMEFRAMES = ['5m', '15m', '1h', '1d']

    def __init__(self):
        logger.info("=" * 60)
        logger.info("  PetroQuant Multi-Strategy Paper Trading Engine")
        logger.info(f"  Strategies : {', '.join(self.ENABLED_TIMEFRAMES)}")
        logger.info(f"  Capital    : ${cfg.INITIAL_CAPITAL:,.0f} per strategy")
        logger.info(f"  Ticker     : {cfg.TICKER_INTRADAY}")
        logger.info(f"  DB dir     : {cfg.OUTPUT_DIR}")
        logger.info("=" * 60)

        self._start_time = None
        self.strategies: dict[str, StrategyRunner] = {}

        for tf in self.ENABLED_TIMEFRAMES:
            self.strategies[tf] = StrategyRunner(tf)

        logger.info("[MultiEngine] All strategies initialized.")

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start_all(self):
        """Start all strategy loops simultaneously."""
        self._start_time = datetime.utcnow()
        for runner in self.strategies.values():
            runner.start()
        logger.info("[MultiEngine] All trading loops started.")

    def stop_all(self):
        """Gracefully stop all strategy loops."""
        for runner in self.strategies.values():
            runner.stop()
        logger.info("[MultiEngine] All trading loops stopped.")

    # ── Strategy Access ───────────────────────────────────────────────────────
    def get_strategy(self, tf: str) -> StrategyRunner:
        """Returns the StrategyRunner for a given timeframe, or None."""
        return self.strategies.get(tf)

    def get_all_status(self) -> dict:
        """Returns status dict for all strategies (keyed by timeframe)."""
        return {
            tf: runner.get_status()
            for tf, runner in self.strategies.items()
        }

    def get_combined_price(self) -> float | None:
        """Returns the latest known price from any running strategy."""
        for runner in self.strategies.values():
            if runner.last_price is not None:
                return runner.last_price
        return None

    def get_current_regime(self) -> str:
        """Returns regime from the first strategy that has one."""
        for runner in self.strategies.values():
            if runner.current_regime != 'UNKNOWN':
                return runner.current_regime
        return 'UNKNOWN'

    # ── Reset ─────────────────────────────────────────────────────────────────
    def reset_strategy(self, tf: str):
        """Reset a single strategy's trades and portfolio."""
        runner = self.strategies.get(tf)
        if runner is None:
            raise ValueError(f"Unknown timeframe '{tf}'")
        runner.reset()

    def reset_all(self):
        """Reset all strategies."""
        for runner in self.strategies.values():
            runner.reset()
        logger.warning("[MultiEngine] ALL strategies reset.")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def is_market_open(self) -> bool:
        return is_market_open()

    def uptime_seconds(self) -> float:
        if self._start_time is None:
            return 0.0
        return (datetime.utcnow() - self._start_time).total_seconds()
