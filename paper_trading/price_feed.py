# ============================================================================
# PETROQUANT PAPER TRADING — PRICE FEED
# ============================================================================
# Fetches live WTI 1-minute OHLCV candles from Yahoo Finance (yfinance).
# yfinance provides 1-min data for the last 7 calendar days (free tier).
#
# Key functions:
#   fetch_1min_candles()  — returns DataFrame of recent 1-min OHLCV bars
#   fetch_latest_price()  — returns single latest close price (float)
#   is_market_open()      — True if WTI futures market is currently active
# ============================================================================

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import logging
import time

from . import config as cfg

logger = logging.getLogger(__name__)

# Eastern time zone (WTI market hours reference)
ET = pytz.timezone("US/Eastern")
UTC = pytz.utc


# ─────────────────────────────────────────────────────────────────────────────
def fetch_1min_candles(ticker: str = cfg.TICKER_INTRADAY,
                       days: int = cfg.BARS_TO_FETCH,
                       retries: int = 3) -> pd.DataFrame:
    """
    Fetch 1-minute OHLCV candles from yfinance.

    Parameters
    ----------
    ticker : str   — Yahoo Finance ticker (default: 'CL=F' = WTI Crude Futures)
    days   : int   — How many days of history (max 7 for free 1-min data)
    retries: int   — Number of retry attempts on failure

    Returns
    -------
    pd.DataFrame with columns: Open, High, Low, Close, Volume
                 DatetimeIndex in UTC, no timezone info (tz-naive)
    """
    for attempt in range(retries):
        try:
            ticker_obj = yf.Ticker(ticker)
            df = ticker_obj.history(
                period=f"{days}d",
                interval='1m',
                auto_adjust=True,
                prepost=True,      # Include pre/post market (WTI is nearly 24/7)
            )

            if df is None or df.empty:
                logger.warning(f"[PriceFeed] Empty response from yfinance (attempt {attempt+1})")
                time.sleep(2 ** attempt)
                continue

            # ── Normalise index ──────────────────────────────────────────────
            df.index = pd.to_datetime(df.index)
            if df.index.tz is not None:
                df.index = df.index.tz_convert(UTC).tz_localize(None)

            # ── Remove duplicates & sort ─────────────────────────────────────
            df = df[~df.index.duplicated(keep='last')].sort_index()

            # ── Keep only OHLCV ──────────────────────────────────────────────
            ohlcv_cols = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]
            df = df[ohlcv_cols].copy()

            # ── Drop zero/null rows ──────────────────────────────────────────
            df = df.dropna(subset=['Close'])
            df = df[df['Close'] > 0]

            # ── Drop maintenance-hour gaps (5PM-6PM ET) ──────────────────────
            df = _drop_maintenance_hour(df)

            logger.info(f"[PriceFeed] Fetched {len(df)} 1-min bars "
                        f"({df.index[0]} -> {df.index[-1]})")
            return df

        except Exception as e:
            logger.error(f"[PriceFeed] Error on attempt {attempt+1}: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)

    logger.error("[PriceFeed] All retry attempts failed. Returning empty DataFrame.")
    return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])


# ─────────────────────────────────────────────────────────────────────────────
def fetch_latest_price(ticker: str = cfg.TICKER_INTRADAY) -> float | None:
    """
    Fetch just the latest close price for marking positions to market.

    Returns
    -------
    float — latest close price, or None on failure
    """
    try:
        ticker_obj = yf.Ticker(ticker)
        info = ticker_obj.fast_info
        price = getattr(info, 'last_price', None)

        # Fallback: fetch last 2 1-min bars and take the last close
        if price is None or price == 0:
            df = fetch_1min_candles(days=1)
            if not df.empty:
                price = float(df['Close'].iloc[-1])

        return float(price) if price else None
    except Exception as e:
        logger.error(f"[PriceFeed] fetch_latest_price error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
def is_market_open(ticker: str = cfg.TICKER_INTRADAY) -> bool:
    """
    Check if WTI futures market is currently active.

    WTI (CL=F) trades nearly 24/7:
      Sun 6:00 PM ET → Fri 5:00 PM ET
      Daily maintenance: 5:00 PM – 6:00 PM ET

    Returns
    -------
    bool — True if market is open and not in maintenance window
    """
    now_et = datetime.now(ET)
    weekday = now_et.weekday()   # 0=Mon, 6=Sun
    hour    = now_et.hour
    minute  = now_et.minute

    # Market is closed Saturday (all day) and Sunday before 6PM ET
    if weekday == 5:  # Saturday
        return False
    if weekday == 6 and hour < 18:  # Sunday before 6PM
        return False
    if weekday == 4 and hour >= 17:  # Friday after 5PM
        return False

    # Maintenance window: 5:00 PM – 6:00 PM ET daily
    if hour == 17:  # 5:XX PM ET
        return False

    return True


# ─────────────────────────────────────────────────────────────────────────────
def get_market_status() -> dict:
    """
    Returns a dict describing current market status and session timing.
    """
    now_et  = datetime.now(ET)
    is_open = is_market_open()

    return {
        'is_open'    : is_open,
        'status'     : 'OPEN' if is_open else 'CLOSED',
        'current_et' : now_et.strftime('%Y-%m-%d %H:%M:%S ET'),
        'weekday'    : now_et.strftime('%A'),
        'note'       : 'WTI maintenance 5-6 PM ET' if now_et.hour == 17 else
                       ('Weekend' if now_et.weekday() >= 5 else 'Normal session'),
    }


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _drop_maintenance_hour(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove rows that fall in the WTI maintenance window (5:00-6:00 PM ET).
    This prevents flat/zero-volume bars from polluting the feature matrix.
    """
    if df.empty:
        return df

    # Convert UTC index to ET for filtering
    df_et_index = df.index.tz_localize(UTC).tz_convert(ET)
    mask = df_et_index.hour != 17   # Drop rows where ET hour == 17 (5PM hour)
    return df[mask]
