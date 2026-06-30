# ============================================================================
# PETROQUANT PAPER TRADING — TRADE LOG (SQLite)
# ============================================================================
# Persistent SQLite database for all paper trades, equity snapshots,
# and session summaries. Survives server restarts.
#
# Tables:
#   trades    — every trade execution (BUY/SELL/HOLD) with full fill details
#   snapshots — periodic equity snapshots (logged every minute)
#
# TradeLog:
#   log_trade(...)     — insert a trade record
#   log_snapshot(...)  — insert an equity snapshot
#   get_trade_history()— pd.DataFrame of all trades
#   get_snapshots()    — pd.DataFrame of all snapshots
#   get_summary()      — dict of key stats
#   export_csv(path)   — export trades to CSV file
# ============================================================================

import sqlite3
import pandas as pd
from datetime import datetime
from pathlib import Path
import logging
import os

from . import config as cfg

logger = logging.getLogger(__name__)


class TradeLog:
    """
    SQLite-backed persistent trade log for the paper trading engine.

    The database is stored at config.DB_PATH and persists across restarts.
    All timestamps are stored as UTC ISO-8601 strings.
    """

    def __init__(self, db_path: str = cfg.DB_PATH):
        self.db_path = db_path
        if db_path != ':memory:':
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._mem_conn = None
        else:
            # For in-memory DBs: keep one persistent connection (shared memory)
            self._mem_conn = sqlite3.connect(':memory:', check_same_thread=False)
        self._init_db()
        logger.info(f"[TradeLog] SQLite database: {db_path}")


    # ── Database Setup ────────────────────────────────────────────────────────
    def _init_db(self):
        """Create tables if they don't exist."""
        conn = self._connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT NOT NULL,
                    date            TEXT NOT NULL,
                    action          TEXT NOT NULL,
                    signal          TEXT,
                    ticker          TEXT DEFAULT 'CL=F',
                    interval        TEXT DEFAULT '1m',
                    horizon         INTEGER DEFAULT 5,
                    price           REAL,
                    quantity        REAL,
                    value           REAL,
                    commission      REAL,
                    slippage_est    REAL,
                    regime          TEXT,
                    probability     REAL,
                    position_size   REAL,
                    pnl_realized    REAL DEFAULT 0.0,
                    pnl_unrealized  REAL DEFAULT 0.0,
                    cash_balance    REAL,
                    equity          REAL,
                    cumulative_pnl  REAL DEFAULT 0.0,
                    notes           TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT NOT NULL,
                    date            TEXT NOT NULL,
                    cash            REAL,
                    equity          REAL,
                    unrealized_pnl  REAL,
                    realized_pnl    REAL,
                    total_return_pct REAL,
                    max_drawdown_pct REAL,
                    total_trades    INTEGER,
                    win_rate_pct    REAL,
                    regime          TEXT,
                    signal          TEXT,
                    probability     REAL,
                    wti_price       REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_action ON trades(action)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON snapshots(timestamp)")
            conn.commit()
        finally:
            if self._mem_conn is None:   # only close file-based connections
                conn.close()

    # ── Write ─────────────────────────────────────────────────────────────────
    def log_trade(self,
                  timestamp     : datetime,
                  action        : str,
                  signal        : str,
                  price         : float,
                  quantity      : float,
                  commission    : float,
                  regime        : str,
                  probability   : float,
                  position_size : float,
                  pnl_realized  : float,
                  pnl_unrealized: float,
                  cash_balance  : float,
                  equity        : float,
                  notes         : str = '') -> int:
        """Insert a trade record into the database. Returns the row id."""
        ts_str  = timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp)
        date_str= ts_str[:10]
        value   = price * quantity if price and quantity else 0.0
        slippage_est = value * cfg.SLIPPAGE_PCT

        # Running cumulative P&L
        cum_pnl = self._get_cumulative_pnl() + pnl_realized

        with self._connect() as conn:
            cursor = conn.execute("""
                INSERT INTO trades
                    (timestamp, date, action, signal, ticker, interval, horizon,
                     price, quantity, value, commission, slippage_est,
                     regime, probability, position_size,
                     pnl_realized, pnl_unrealized,
                     cash_balance, equity, cumulative_pnl, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?, ?, ?)
            """, (ts_str, date_str, action, signal, cfg.TICKER_INTRADAY,
                  cfg.CANDLE_INTERVAL, cfg.PREDICT_HORIZON,
                  price, quantity, value, commission, slippage_est,
                  regime, probability, position_size,
                  pnl_realized, pnl_unrealized,
                  cash_balance, equity, cum_pnl, notes))
            return cursor.lastrowid

    def log_snapshot(self,
                     timestamp  : datetime,
                     snapshot   : dict,
                     regime     : str,
                     signal     : str,
                     probability: float) -> None:
        """Insert an equity snapshot (called every minute)."""
        ts_str   = timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp)
        date_str = ts_str[:10]

        with self._connect() as conn:
            conn.execute("""
                INSERT INTO snapshots
                    (timestamp, date, cash, equity, unrealized_pnl, realized_pnl,
                     total_return_pct, max_drawdown_pct, total_trades, win_rate_pct,
                     regime, signal, probability, wti_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ts_str, date_str,
                  snapshot.get('cash', 0),
                  snapshot.get('equity', 0),
                  snapshot.get('unrealized_pnl', 0),
                  snapshot.get('realized_pnl', 0),
                  snapshot.get('total_return_pct', 0),
                  snapshot.get('max_drawdown_pct', 0),
                  snapshot.get('total_trades', 0),
                  snapshot.get('win_rate_pct', 0),
                  regime, signal, probability,
                  snapshot.get('open_position', {}).get('current_price') if snapshot.get('open_position') else None))

    # ── Read ──────────────────────────────────────────────────────────────────
    def get_trade_history(self, limit: int = None, actions_only: bool = False) -> pd.DataFrame:
        """
        Returns trade history as a DataFrame.

        Parameters
        ----------
        limit        : int  — max rows to return (most recent first), None = all
        actions_only : bool — if True, excludes HOLD rows
        """
        query = "SELECT * FROM trades"
        if actions_only:
            query += " WHERE action NOT IN ('HOLD')"
        query += " ORDER BY timestamp DESC"
        if limit:
            query += f" LIMIT {limit}"

        with self._connect() as conn:
            df = pd.read_sql_query(query, conn, parse_dates=['timestamp'])
        return df

    def get_snapshots(self, limit: int = 1000) -> pd.DataFrame:
        """Returns equity snapshots as a DataFrame."""
        query = f"SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT {limit}"
        with self._connect() as conn:
            df = pd.read_sql_query(query, conn, parse_dates=['timestamp'])
        return df.sort_values('timestamp').reset_index(drop=True)

    def get_summary(self) -> dict:
        """Returns key statistics from the trade log."""
        with self._connect() as conn:
            # Trade counts
            counts = conn.execute("""
                SELECT
                    COUNT(*) as total_rows,
                    SUM(CASE WHEN action LIKE 'OPEN%' THEN 1 ELSE 0 END) as total_trades,
                    SUM(CASE WHEN action LIKE 'CLOSE%' AND pnl_realized > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN action LIKE 'CLOSE%' AND pnl_realized <= 0 THEN 1 ELSE 0 END) as losses,
                    SUM(pnl_realized) as total_realized_pnl,
                    SUM(commission) as total_commission,
                    MIN(timestamp) as first_trade,
                    MAX(timestamp) as last_trade
                FROM trades
            """).fetchone()

            # Latest equity
            latest = conn.execute("""
                SELECT equity, total_return_pct, max_drawdown_pct
                FROM snapshots ORDER BY timestamp DESC LIMIT 1
            """).fetchone()

        total_trades = counts[1] or 0
        wins         = counts[2] or 0
        losses       = counts[3] or 0
        win_rate     = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

        return {
            'total_logged_rows'  : counts[0],
            'total_trades'       : total_trades,
            'winning_trades'     : wins,
            'losing_trades'      : losses,
            'win_rate_pct'       : round(win_rate, 1),
            'total_realized_pnl' : round(counts[4] or 0, 2),
            'total_commission'   : round(counts[5] or 0, 2),
            'first_trade'        : counts[6],
            'last_trade'         : counts[7],
            'latest_equity'      : round(latest[0], 2) if latest else cfg.INITIAL_CAPITAL,
            'total_return_pct'   : round(latest[1], 3) if latest else 0.0,
            'max_drawdown_pct'   : round(latest[2], 2) if latest else 0.0,
        }

    def export_csv(self, path: str = None) -> str:
        """Export full trade history to CSV. Returns file path."""
        if path is None:
            path = os.path.join(cfg.OUTPUT_DIR, 'paper_trades_export.csv')
        df = self.get_trade_history()
        df.to_csv(path, index=False)
        logger.info(f"[TradeLog] Exported {len(df)} rows to {path}")
        return path

    def get_today_trades(self) -> pd.DataFrame:
        """Returns trades executed today only."""
        today = datetime.utcnow().strftime('%Y-%m-%d')
        with self._connect() as conn:
            df = pd.read_sql_query(
                "SELECT * FROM trades WHERE date = ? ORDER BY timestamp",
                conn, params=(today,), parse_dates=['timestamp']
            )
        return df

    # ── Internal ──────────────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        """Returns a database connection with WAL mode for concurrency."""
        if self._mem_conn is not None:
            # In-memory: always return the same connection object
            self._mem_conn.row_factory = sqlite3.Row
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")   # write-ahead logging
        conn.row_factory = sqlite3.Row
        return conn

    def _get_cumulative_pnl(self) -> float:
        """Get the last cumulative_pnl value for running total calculation."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT cumulative_pnl FROM trades ORDER BY id DESC LIMIT 1"
                ).fetchone()
                return float(row[0]) if row and row[0] is not None else 0.0
        except Exception:
            return 0.0
