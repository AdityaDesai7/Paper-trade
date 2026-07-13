# ============================================================================
# PETROQUANT — BACKTESTING ENGINE + STRATEGY DASHBOARD
# ============================================================================
# Contains:
#   Backtester        — computes all backtest metrics from strategy signals
#   StrategyDashboard — renders an 11-panel Plotly dashboard + trade log
#
# USAGE:
#   from dashboard import Backtester, StrategyDashboard
#   bt = Backtester()
#   result = bt.run(strategy_df)
#   dash = StrategyDashboard()
#   dash.render(result, strategy)
# ============================================================================

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import json
import os
import base64
import warnings
warnings.filterwarnings('ignore')

import config as cfg


# ═════════════════════════════════════════════════════════════════════════════
# POSITION LIFECYCLE ENGINE
# ═════════════════════════════════════════════════════════════════════════════
@dataclass
class OpenPosition:
    """An open position leg waiting for its matching exit."""
    side: str                   # 'LONG' or 'SHORT'
    signed_frac: float          # signed fraction of equity (+ long, - short)
    entry_price: float          # raw market price at open (pre-slippage, for gross P&L)
    entry_fill: float           # slippage-adjusted entry price (actual execution price)
    entry_notional: float       # dollars deployed at entry (equity × |frac|)
    entry_commission: float     # commission paid on entry leg
    entry_slippage: float       # dollar slippage cost on entry leg
    entry_equity: float         # account equity at entry
    open_timestamp: object      # pd.Timestamp


@dataclass
class ClosedTrade:
    """A fully closed position lifecycle (entry leg + exit leg)."""
    side: str
    fraction: float
    entry_price: float        # raw market price at open (pre-slippage)
    exit_price: float         # raw market price at close (pre-slippage)
    entry_fill: float         # slippage-adjusted fill at open
    exit_fill: float          # slippage-adjusted fill at close
    entry_notional: float
    invested_amount: float
    gross_pnl: float          # P&L at market prices (pre-cost, pre-slippage)
    net_pnl: float            # gross_pnl − commission − slippage
    return_pct: float
    entry_commission: float
    exit_commission: float
    total_commission: float
    entry_slippage: float     # dollar slippage cost on open leg
    exit_slippage: float      # dollar slippage cost on close leg
    open_timestamp: object
    close_timestamp: object
    close_reason: str = 'signal'   # 'signal' | 'stop_loss' | 'max_hold' | 'end_of_data'


def calculate_position_return(
    side: str,
    entry_fill: float,         # slippage-adjusted fill at open
    exit_fill: float,          # slippage-adjusted fill at close
    notional: float,
    entry_commission: float,
    exit_commission: float,
    entry_slippage: float = 0.0,   # informational only — already inside fills
    exit_slippage: float = 0.0,
) -> dict:
    """
    Compute realized P&L for one closed position lifecycle.

    Slippage is embedded in entry_fill / exit_fill (buy higher, sell lower).
    It is NOT deducted again from net_pnl.

    net_pnl = fill_pnl − total_commission
    """
    if side == 'LONG':
        gross_pnl = notional * (exit_fill - entry_fill) / entry_fill
    else:
        gross_pnl = notional * (entry_fill - exit_fill) / entry_fill

    total_commission = entry_commission + exit_commission
    invested_amount  = notional + entry_commission
    net_pnl          = gross_pnl - total_commission
    return_pct       = (net_pnl / invested_amount * 100) if invested_amount > 0 else 0.0

    return {
        'invested_amount'  : invested_amount,
        'gross_pnl'        : gross_pnl,
        'net_pnl'          : net_pnl,
        'return_pct'       : return_pct,
        'entry_slippage'   : entry_slippage,
        'exit_slippage'    : exit_slippage,
        'total_slippage'   : entry_slippage + exit_slippage,
        'total_commission' : total_commission,
    }


def _scaled_slippage_pct(base_slippage_pct: float, position_frac: float) -> float:
    """
    Size-scaled market impact:
        impact = base × (|frac| / REFERENCE_FRAC) ^ EXPONENT

    At the reference fraction the impact equals the flat base rate.
    Square-root scaling (EXPONENT=0.5) is standard for liquid futures.
    Capped at 10× base to prevent unrealistic impact for very large positions.
    """
    ref = cfg.SLIPPAGE_REFERENCE_FRAC
    exp = cfg.SLIPPAGE_IMPACT_EXPONENT
    if ref <= 0 or abs(position_frac) <= 0:
        return base_slippage_pct
    scale = (abs(position_frac) / ref) ** exp
    return min(base_slippage_pct * scale, base_slippage_pct * 10.0)


def _fill_price(side: str, action: str, market_price: float,
                base_slippage_pct: float, position_frac: float = 0.10) -> float:
    """Size-scaled slippage-aware fill price. action = 'OPEN' or 'CLOSE'."""
    slip = _scaled_slippage_pct(base_slippage_pct, position_frac)
    if side == 'LONG':
        return market_price * (1 + slip) if action == 'OPEN' else market_price * (1 - slip)
    return market_price * (1 - slip) if action == 'OPEN' else market_price * (1 + slip)


def _slippage_cost(side: str, action: str, market_price: float,
                   base_slippage_pct: float, notional: float,
                   position_frac: float = 0.10) -> float:
    """Dollar slippage cost (always positive = adverse), size-scaled."""
    if notional <= 0 or market_price <= 0:
        return 0.0
    slip = _scaled_slippage_pct(base_slippage_pct, position_frac)
    return notional * slip


def _close_position(
    pos: OpenPosition,
    market_price: float,
    close_frac: float,
    commission_pct: float,
    slippage_pct: float,
    close_timestamp,
    close_reason: str = 'signal',
) -> tuple:
    """
    Close `close_frac` of an open position (0 < close_frac <= 1).
    Returns (ClosedTrade | None, remaining OpenPosition | None).
    gross_pnl is at RAW MARKET prices; slippage deducted explicitly in net_pnl.
    """
    close_frac = min(max(close_frac, 0.0), 1.0)
    if close_frac <= 1e-12:
        return None, pos

    close_notional   = pos.entry_notional * close_frac
    close_comm_entry = pos.entry_commission * close_frac
    close_slip_entry = pos.entry_slippage * close_frac

    exit_fill  = _fill_price(pos.side, 'CLOSE', market_price, slippage_pct,
                              abs(pos.signed_frac))
    exit_comm  = close_notional * commission_pct
    exit_slip  = _slippage_cost(pos.side, 'CLOSE', market_price, slippage_pct,
                                 close_notional, abs(pos.signed_frac))

    result = calculate_position_return(
        side             = pos.side,
        entry_fill       = pos.entry_fill,
        exit_fill        = exit_fill,
        notional         = close_notional,
        entry_commission = close_comm_entry,
        exit_commission  = exit_comm,
        entry_slippage   = close_slip_entry,
        exit_slippage    = exit_slip,
    )

    closed = ClosedTrade(
        side             = pos.side,
        fraction         = abs(pos.signed_frac) * close_frac,
        entry_price      = pos.entry_price,   # raw market price
        exit_price       = market_price,       # raw market price
        entry_fill       = pos.entry_fill,
        exit_fill        = exit_fill,
        entry_notional   = close_notional,
        invested_amount  = result['invested_amount'],
        gross_pnl        = result['gross_pnl'],
        net_pnl          = result['net_pnl'],
        return_pct       = result['return_pct'],
        entry_commission = close_comm_entry,
        exit_commission  = exit_comm,
        total_commission = result['total_commission'],
        entry_slippage   = close_slip_entry,
        exit_slippage    = exit_slip,
        open_timestamp   = pos.open_timestamp,
        close_timestamp  = close_timestamp,
        close_reason     = close_reason,
    )

    remaining_frac = 1.0 - close_frac
    if remaining_frac <= 1e-12:
        return closed, None

    remaining = OpenPosition(
        side             = pos.side,
        signed_frac      = pos.signed_frac * remaining_frac,
        entry_price      = pos.entry_price,
        entry_fill       = pos.entry_fill,
        entry_notional   = pos.entry_notional * remaining_frac,
        entry_commission = pos.entry_commission * remaining_frac,
        entry_slippage   = pos.entry_slippage * remaining_frac,
        entry_equity     = pos.entry_equity,
        open_timestamp   = pos.open_timestamp,
    )
    return closed, remaining


def _open_position(
    side: str,
    frac: float,
    market_price: float,
    equity: float,
    commission_pct: float,
    slippage_pct: float,
    open_timestamp,
) -> tuple:
    """Open a new position leg. Returns (OpenPosition, total_cash_cost)."""
    notional   = equity * abs(frac)
    entry_fill = _fill_price(side, 'OPEN', market_price, slippage_pct, frac)
    commission = notional * commission_pct
    slip_cost  = _slippage_cost(side, 'OPEN', market_price, slippage_pct, notional, frac)
    signed     = frac if side == 'LONG' else -abs(frac)

    pos = OpenPosition(
        side             = side,
        signed_frac      = signed,
        entry_price      = market_price,   # raw market price for gross P&L
        entry_fill       = entry_fill,     # slippage-adjusted fill
        entry_notional   = notional,
        entry_commission = commission,
        entry_slippage   = slip_cost,
        entry_equity     = equity,
        open_timestamp   = open_timestamp,
    )
    # Commission is a cash deduction; slippage is already in entry_fill.
    return pos, commission


def _merge_positions(existing: OpenPosition, add_frac: float,
                     market_price: float, equity: float,
                     commission_pct: float, slippage_pct: float) -> tuple:
    """Scale into an existing position — weighted-average entry price and market price."""
    side = existing.side
    add_notional = equity * abs(add_frac)
    add_fill     = _fill_price(side, 'OPEN', market_price, slippage_pct, add_frac)
    add_comm     = add_notional * commission_pct
    add_slip     = _slippage_cost(side, 'OPEN', market_price, slippage_pct, add_notional, add_frac)

    total_notional = existing.entry_notional + add_notional
    # Weighted-average fill and market prices
    avg_fill = (
        (existing.entry_notional * existing.entry_fill + add_notional * add_fill)
        / total_notional
    )
    avg_market = (
        (existing.entry_notional * existing.entry_price + add_notional * market_price)
        / total_notional
    )
    new_signed = existing.signed_frac + (add_frac if side == 'LONG' else -abs(add_frac))

    merged = OpenPosition(
        side             = side,
        signed_frac      = new_signed,
        entry_price      = avg_market,   # weighted-average raw market price
        entry_fill       = avg_fill,
        entry_notional   = total_notional,
        entry_commission = existing.entry_commission + add_comm,
        entry_slippage   = existing.entry_slippage + add_slip,
        entry_equity     = existing.entry_equity,
        open_timestamp   = existing.open_timestamp,
    )
    return merged, add_comm


def _unrealized_pnl(pos: OpenPosition, market_price: float) -> float:
    """Mark-to-market P&L on an open position (no costs — costs hit on close)."""
    if pos.side == 'LONG':
        return pos.entry_notional * (market_price - pos.entry_fill) / pos.entry_fill
    return pos.entry_notional * (pos.entry_fill - market_price) / pos.entry_fill


def _equity(cash: float, position: Optional[OpenPosition], price: float) -> float:
    """Total account equity = cash + unrealized P&L on open position."""
    if position is None:
        return cash
    return cash + _unrealized_pnl(position, price)


def _rebalance(
    position: Optional[OpenPosition],
    target_frac: float,
    price: float,
    equity: float,
    commission_pct: float,
    slippage_pct: float,
    timestamp,
    closed_trades: List[ClosedTrade],
    close_reason: str = 'signal',
) -> tuple:
    """
    Move from current position to target_frac at `price`.
    Flip = close entire old leg, then open new leg (never a single combined event).
    Returns (position, cash_delta, gross_cash_delta, n_legs).

    cash_delta includes: realized fill P&L on closes minus exit commission;
    commission on open legs.  Slippage is inside fill prices, not deducted again.
    """
    cash_delta       = 0.0
    gross_cash_delta = 0.0
    n_legs           = 0
    current_frac     = position.signed_frac if position else 0.0

    if abs(target_frac - current_frac) < 1e-10:
        return position, cash_delta, gross_cash_delta, n_legs

    # ── FLIP: close 100% of old position, then open new side ─────────────
    if position is not None and target_frac * current_frac < 0:
        closed, position = _close_position(
            position, price, 1.0, commission_pct, slippage_pct, timestamp, close_reason
        )
        if closed:
            closed_trades.append(closed)
            cash_delta       += closed.gross_pnl - closed.exit_commission
            gross_cash_delta += closed.gross_pnl
            n_legs           += 1
        current_frac = 0.0

    # ── REDUCE or FULL CLOSE (same side or going flat) ───────────────────
    if position is not None:
        cur_abs = abs(position.signed_frac)
        if target_frac * position.signed_frac >= 0:
            new_abs = abs(target_frac)
        else:
            new_abs = 0.0

        if new_abs < cur_abs - 1e-10:
            close_frac = 1.0 - (new_abs / cur_abs)
            closed, position = _close_position(
                position, price, close_frac, commission_pct, slippage_pct, timestamp, close_reason
            )
            if closed:
                closed_trades.append(closed)
                cash_delta       += closed.gross_pnl - closed.exit_commission
                gross_cash_delta += closed.gross_pnl
                n_legs           += 1

    # ── INCREASE or NEW OPEN ─────────────────────────────────────────────
    current_frac = position.signed_frac if position else 0.0
    delta        = target_frac - current_frac

    if abs(delta) > 1e-10:
        if position is None:
            side = 'LONG' if target_frac > 0 else 'SHORT'
            position, open_cost = _open_position(
                side, abs(target_frac), price, equity,
                commission_pct, slippage_pct, timestamp,
            )
            cash_delta -= open_cost   # commission only at open
            n_legs     += 1

        elif (delta > 0 and position.side == 'LONG') or \
             (delta < 0 and position.side == 'SHORT'):
            position, open_cost = _merge_positions(
                position, abs(delta), price, equity,
                commission_pct, slippage_pct,
            )
            cash_delta -= open_cost
            n_legs     += 1

    return position, cash_delta, gross_cash_delta, n_legs


def simulate_position_lifecycle(
    prices: pd.Series,
    target_fractions: pd.Series,
    initial_capital: float,
    commission_pct: float,
    slippage_pct: float,
) -> dict:
    """
    Event-driven backtest with proper position lifecycles.

    Rules:
      - A close is always an exit leg tied to its entry, never a new position.
      - A flip (long→short) = close entire old position, then open new one.
      - Partial closes reduce open quantity; scale-ins use weighted average cost.
      - MIN_REBALANCE_FRAC: ignore position changes smaller than this (no micro-trades).
      - STOP_LOSS_PCT: force-close if unrealized loss exceeds this fraction of notional.
      - MAX_HOLD_DAYS: force-close positions held beyond this many calendar days.
      - gross_pnl is at raw MARKET prices; slippage and commission deducted in net_pnl.
    """
    n = len(prices)
    if n == 0:
        return _empty_simulation_result(prices)

    idx            = prices.index
    cash           = float(initial_capital)
    gross_cash     = float(initial_capital)
    position: Optional[OpenPosition] = None
    closed_trades: List[ClosedTrade] = []
    n_transitions    = 0

    net_equity       = np.zeros(n)
    gross_equity_arr = np.zeros(n)
    net_returns      = np.zeros(n)
    gross_returns    = np.zeros(n)
    cost_returns     = np.zeros(n)
    held_fractions   = np.zeros(n)
    position_changes = np.zeros(n)

    prev_net_equity   = initial_capital
    prev_gross_equity = initial_capital

    # Execution control thresholds from config
    min_rebal = cfg.MIN_REBALANCE_FRAC
    stop_loss = cfg.STOP_LOSS_PCT
    max_hold  = cfg.MAX_HOLD_DAYS

    for i in range(n):
        ts          = idx[i]
        price       = float(prices.iloc[i])
        target_frac = float(target_fractions.iloc[i])
        current_frac = position.signed_frac if position else 0.0

        # ── STOP-LOSS check (before signal rebalance) ─────────────────────
        if position is not None:
            unreal = _unrealized_pnl(position, price)
            if position.entry_notional > 0 and (-unreal / position.entry_notional) >= stop_loss:
                equity_now = _equity(cash, position, price)
                position, cash_d, gross_d, n_legs = _rebalance(
                    position, 0.0, price, equity_now,
                    commission_pct, slippage_pct, ts, closed_trades,
                    close_reason='stop_loss',
                )
                cash       += cash_d
                gross_cash += gross_d
                n_transitions += n_legs
                current_frac = 0.0

        # ── MAX HOLD check ────────────────────────────────────────────────
        if position is not None:
            days_held = (pd.Timestamp(ts) - pd.Timestamp(position.open_timestamp)).days
            if days_held >= max_hold:
                equity_now = _equity(cash, position, price)
                position, cash_d, gross_d, n_legs = _rebalance(
                    position, 0.0, price, equity_now,
                    commission_pct, slippage_pct, ts, closed_trades,
                    close_reason='max_hold',
                )
                cash       += cash_d
                gross_cash += gross_d
                n_transitions += n_legs
                current_frac = 0.0

        # ── SIGNAL rebalance (only if change exceeds min threshold) ───────
        current_frac = position.signed_frac if position else 0.0
        if abs(target_frac - current_frac) >= min_rebal:
            position_changes[i] = abs(target_frac - current_frac)
            equity_now = _equity(cash, position, price)
            position, cash_d, gross_d, n_legs = _rebalance(
                position, target_frac, price, equity_now,
                commission_pct, slippage_pct, ts, closed_trades,
                close_reason='signal',
            )
            cash       += cash_d
            gross_cash += gross_d
            n_transitions += n_legs

        net_eq   = _equity(cash, position, price)
        gross_eq = gross_cash + (_unrealized_pnl(position, price) if position else 0.0)

        held_fractions[i]   = position.signed_frac if position else 0.0
        net_equity[i]       = net_eq
        gross_equity_arr[i] = gross_eq

        if i == 0:
            net_returns[i]   = 0.0
            gross_returns[i] = 0.0
            cost_returns[i]  = 0.0
        else:
            net_returns[i]   = (net_eq - prev_net_equity) / prev_net_equity if prev_net_equity > 0 else 0.0
            gross_returns[i] = (gross_eq - prev_gross_equity) / prev_gross_equity if prev_gross_equity > 0 else 0.0
            cost_returns[i]  = gross_returns[i] - net_returns[i]

        prev_net_equity   = net_eq
        prev_gross_equity = gross_eq

    # ── Mark still-open position as end_of_data ──────────────────────────
    if position is not None:
        position = OpenPosition(
            side             = position.side,
            signed_frac      = position.signed_frac,
            entry_price      = position.entry_price,
            entry_fill       = position.entry_fill,
            entry_notional   = position.entry_notional,
            entry_commission = position.entry_commission,
            entry_slippage   = position.entry_slippage,
            entry_equity     = position.entry_equity,
            open_timestamp   = position.open_timestamp,
        )

    return {
        'net_equity'      : pd.Series(net_equity, index=idx),
        'gross_equity'    : pd.Series(gross_equity_arr, index=idx),
        'net_returns'     : pd.Series(net_returns, index=idx),
        'gross_returns'   : pd.Series(gross_returns, index=idx),
        'cost_returns'    : pd.Series(cost_returns, index=idx),
        'held_fractions'  : pd.Series(held_fractions, index=idx),
        'position_changes': pd.Series(position_changes, index=idx),
        'closed_trades'   : closed_trades,
        'n_transitions'   : n_transitions,
        'open_position'   : position,
    }


def _empty_simulation_result(prices):
    return {
        'net_equity'      : pd.Series(dtype=float),
        'gross_equity'    : pd.Series(dtype=float),
        'net_returns'     : pd.Series(dtype=float),
        'gross_returns'   : pd.Series(dtype=float),
        'cost_returns'    : pd.Series(dtype=float),
        'held_fractions'  : pd.Series(dtype=float),
        'position_changes': pd.Series(dtype=float),
        'closed_trades'   : [],
        'n_transitions'   : 0,
        'open_position'   : None,
    }


def _lookup_oos_row(oos: pd.DataFrame, ts) -> pd.Series:
    """Fetch OOS row for a timestamp; nearest match if exact date missing."""
    if oos is None or len(oos) == 0:
        return pd.Series(dtype=float)
    ts = pd.Timestamp(ts)
    if ts in oos.index:
        return oos.loc[ts]
    pos = oos.index.get_indexer([ts], method='nearest')
    if pos[0] < 0:
        return pd.Series(dtype=float)
    return oos.iloc[pos[0]]


def build_trade_log_df(
    closed_trades: List[ClosedTrade],
    oos_df: pd.DataFrame,
    open_position: Optional[OpenPosition] = None,
) -> pd.DataFrame:
    """
    Convert closed position lifecycles into an investor-ready trade log.
    Enriches each row with signal, probability, and regime from the OOS DataFrame.
    """
    rows = []
    for i, t in enumerate(closed_trades, start=1):
        open_row  = _lookup_oos_row(oos_df, t.open_timestamp)
        close_row = _lookup_oos_row(oos_df, t.close_timestamp)
        open_ts   = pd.Timestamp(t.open_timestamp)
        close_ts  = pd.Timestamp(t.close_timestamp)
        hold_days = max((close_ts - open_ts).days, 0)
        slip_total = t.entry_slippage + t.exit_slippage

        total_cost = t.total_commission   # slippage already in fills; not part of net deduction
        rows.append({
            'trade_id'         : i,
            'status'           : 'CLOSED',
            'close_reason'     : getattr(t, 'close_reason', 'signal'),
            'side'             : t.side,
            'open_date'        : open_ts.strftime('%Y-%m-%d'),
            'close_date'       : close_ts.strftime('%Y-%m-%d'),
            'hold_days'        : hold_days,
            'entry_market'     : round(t.entry_price, 4),
            'exit_market'      : round(t.exit_price, 4),
            'entry_fill'       : round(t.entry_fill, 4),
            'exit_fill'        : round(t.exit_fill, 4),
            'position_frac'    : round(t.fraction, 4),
            'notional_usd'     : round(t.entry_notional, 2),
            'invested_usd'     : round(t.invested_amount, 2),
            'gross_pnl_usd'    : round(t.gross_pnl, 2),
            'commission_usd'   : round(t.total_commission, 2),
            'slippage_usd'     : round(slip_total, 2),
            'total_cost_usd'   : round(total_cost, 2),
            'net_pnl_usd'      : round(t.net_pnl, 2),
            'pnl'              : round(t.net_pnl, 2),
            'return_pct'       : round(t.return_pct, 2),
            'outcome'          : 'WIN' if t.net_pnl > 0 else 'LOSS' if t.net_pnl < 0 else 'FLAT',
            'signal_open'      : open_row.get('Signal_Label', open_row.get('Signal', '')),
            'probability_open' : round(float(open_row['Probability']), 4) if 'Probability' in open_row and pd.notna(open_row.get('Probability')) else '',
            'regime_open'      : open_row.get('Regime', ''),
            'signal_close'     : close_row.get('Signal_Label', close_row.get('Signal', '')),
            'probability_close': round(float(close_row['Probability']), 4) if 'Probability' in close_row and pd.notna(close_row.get('Probability')) else '',
            'regime_close'     : close_row.get('Regime', ''),
        })

    if open_position is not None:
        open_row = _lookup_oos_row(oos_df, open_position.open_timestamp)
        open_ts  = pd.Timestamp(open_position.open_timestamp)
        last_row = _lookup_oos_row(oos_df, oos_df.index[-1]) if len(oos_df) else pd.Series()
        mark_price = float(last_row.get('WTI_Close', open_position.entry_fill))
        unrealized = _unrealized_pnl(open_position, mark_price)
        if open_position.entry_fill > 0:
            if open_position.side == 'LONG':
                ret_pct = (mark_price - open_position.entry_fill) / open_position.entry_fill * 100
            else:
                ret_pct = (open_position.entry_fill - mark_price) / open_position.entry_fill * 100
        else:
            ret_pct = 0.0
        entry_market = getattr(open_position, 'entry_price', open_position.entry_fill)
        rows.append({
            'trade_id'         : len(rows) + 1,
            'status'           : 'OPEN',
            'close_reason'     : 'end_of_data',
            'side'             : open_position.side,
            'open_date'        : open_ts.strftime('%Y-%m-%d'),
            'close_date'       : '—',
            'hold_days'        : max((pd.Timestamp(oos_df.index[-1]) - open_ts).days, 0) if len(oos_df) else 0,
            'entry_market'     : round(entry_market, 4),
            'exit_market'      : round(mark_price, 4),
            'entry_fill'       : round(open_position.entry_fill, 4),
            'exit_fill'        : '—',
            'position_frac'    : round(abs(open_position.signed_frac), 4),
            'notional_usd'     : round(open_position.entry_notional, 2),
            'invested_usd'     : round(open_position.entry_notional + open_position.entry_commission, 2),
            'gross_pnl_usd'    : round(unrealized, 2),
            'commission_usd'   : round(open_position.entry_commission, 2),
            'slippage_usd'     : round(open_position.entry_slippage, 2),
            'total_cost_usd'   : round(open_position.entry_commission, 2),
            'net_pnl_usd'      : round(unrealized - open_position.entry_commission, 2),
            'pnl'              : round(unrealized - open_position.entry_commission, 2),
            'return_pct'       : round(ret_pct, 2),
            'outcome'          : 'OPEN',
            'signal_open'      : open_row.get('Signal_Label', open_row.get('Signal', '')),
            'probability_open' : round(float(open_row['Probability']), 4) if 'Probability' in open_row and pd.notna(open_row.get('Probability')) else '',
            'regime_open'      : open_row.get('Regime', ''),
            'signal_close'     : '',
            'probability_close': '',
            'regime_close'     : '',
        })

    if not rows:
        return pd.DataFrame(columns=[
            'trade_id', 'status', 'close_reason', 'side', 'open_date', 'close_date',
            'hold_days', 'entry_market', 'exit_market', 'entry_fill', 'exit_fill',
            'position_frac', 'notional_usd', 'invested_usd', 'gross_pnl_usd',
            'commission_usd', 'slippage_usd', 'total_cost_usd', 'net_pnl_usd', 'pnl',
            'return_pct', 'outcome',
            'signal_open', 'probability_open', 'regime_open',
            'signal_close', 'probability_close', 'regime_close',
        ])
    return pd.DataFrame(rows)


# ═════════════════════════════════════════════════════════════════════════════
# BACKTEST RESULT CONTAINER
# ═════════════════════════════════════════════════════════════════════════════
@dataclass
class BacktestResult:
    """Container for all backtesting outputs."""
    oos_df: pd.DataFrame              # Out-of-sample DataFrame with returns
    total_return: float = 0.0
    bnh_return: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    calmar_ratio: float = 0.0
    total_trades: int = 0
    trading_days: int = 0
    annual_return: float = 0.0
    annual_vol: float = 0.0
    # Advanced ratios
    sortino: float = 0.0
    information_ratio: float = 0.0
    treynor_ratio: float = 0.0
    sterling_ratio: float = 0.0
    burke_ratio: float = 0.0
    expectancy: float = 0.0
    omega_ratio: float = 0.0
    tail_ratio: float = 0.0
    up_capture: float = 0.0
    down_capture: float = 0.0
    capture_ratio: float = 0.0
    ulcer_index: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    # Cost metadata (filled by Backtester when costs are applied)
    gross_total_return: float = 0.0
    cost_drag: float = 0.0
    n_transitions: int = 0
    commission_pct: float = 0.0
    slippage_pct: float = 0.0
    closed_trades: List = field(default_factory=list)
    n_closed_trades: int = 0
    trade_log_df: Optional[pd.DataFrame] = None
    open_position: Optional[object] = None


# ═════════════════════════════════════════════════════════════════════════════
# BACKTESTER
# ═════════════════════════════════════════════════════════════════════════════
class Backtester:
    """Compute backtest metrics from strategy signals."""

    def run(self, strategy_df, price_col='WTI_Close',
            commission_pct=None, slippage_pct=None):
        """
        Run backtest on strategy output DataFrame.

        Parameters
        ----------
        strategy_df    : pd.DataFrame with 'Signal', price_col columns
        price_col      : str, column name for the asset price
        commission_pct : one-way commission fraction (default from config.py)
        slippage_pct   : one-way slippage fraction  (default from config.py)

        All performance metrics are computed on NET returns (after costs).
        Gross return is stored separately in BacktestResult.gross_total_return.
        """
        commission = commission_pct if commission_pct is not None else cfg.COMMISSION_PCT
        slippage   = slippage_pct   if slippage_pct   is not None else cfg.SLIPPAGE_PCT
        initial_capital = cfg.INITIAL_CAPITAL
        print("\n  -- Backtesting (position-lifecycle engine) ---------------------------")
        print("     (Each trade = entry leg + exit leg; flips = close then open)")

        # Filter to out-of-sample period (where we have signals)
        has_signal = strategy_df['Probability'].notna() if 'Probability' in strategy_df.columns \
            else strategy_df['Signal'] != 0
        oos = strategy_df[has_signal].copy()

        if len(oos) == 0:
            print("    [WARN] No OOS data to backtest")
            return BacktestResult(oos_df=oos)

        # Target exposure series (fraction of equity, signed)
        position_series = (
            oos['Position_Size']
            if 'Position_Size' in oos.columns
            else oos['Signal'].astype(float)
        )

        # Hard cap: strategy.py should already enforce MAX_POSITION_PCT,
        # but clip here as a safety net against config changes between runs.
        position_series = position_series.clip(-cfg.MAX_POSITION_PCT, cfg.MAX_POSITION_PCT)

        # ── Position-lifecycle simulation ────────────────────────────────
        sim = simulate_position_lifecycle(
            prices           = oos[price_col],
            target_fractions = position_series,
            initial_capital  = initial_capital,
            commission_pct   = commission,
            slippage_pct     = slippage,
        )

        closed_trades = sim['closed_trades']
        open_pos      = sim['open_position']

        # ── Build OOS DataFrame (dashboard-compatible columns) ───────────
        oos['Daily_Return']            = oos[price_col].pct_change().fillna(0)
        oos['Strategy_Return']         = sim['gross_returns'].values
        oos['Net_Strategy_Return']     = sim['net_returns'].values
        oos['Cost_Return']             = sim['cost_returns'].values
        oos['Position_Change']         = sim['position_changes'].values
        oos['Held_Position']           = sim['held_fractions'].values
        oos['Strategy_Cumulative']     = sim['gross_equity'].values / initial_capital
        oos['Net_Strategy_Cumulative'] = sim['net_equity'].values / initial_capital
        oos['BnH_Return']              = oos['Daily_Return']
        oos['BnH_Cumulative']          = (1 + oos['BnH_Return']).cumprod()

        # ── Drawdown (on NET equity) ───────────────────────────────────
        cum_max          = oos['Net_Strategy_Cumulative'].cummax()
        oos['Drawdown']  = (oos['Net_Strategy_Cumulative'] - cum_max) / cum_max
        gross_cum_max         = oos['Strategy_Cumulative'].cummax()
        oos['Gross_Drawdown'] = (oos['Strategy_Cumulative'] - gross_cum_max) / gross_cum_max

        # ── Rolling Sharpe (on net returns) ──────────────────────────────
        oos['Rolling_Sharpe'] = (
            oos['Net_Strategy_Return'].rolling(60).mean() /
            (oos['Net_Strategy_Return'].rolling(60).std() + 1e-8)
        ) * np.sqrt(252)

        oos['YearMonth'] = oos.index.to_period('M')

        if open_pos is not None:
            print(f"    [NOTE] {len(closed_trades)} closed trades; "
                  f"1 position still open at end (excluded from win-rate stats)")

        trade_log_df = build_trade_log_df(closed_trades, oos, open_pos)
        if len(trade_log_df) > 0:
            n_wins = (trade_log_df['outcome'] == 'WIN').sum()
            n_loss = (trade_log_df['outcome'] == 'LOSS').sum()
            print(f"    [OK] Trade log: {len(trade_log_df)} rows "
                  f"({n_wins} wins, {n_loss} losses, "
                  f"{(trade_log_df['status'] == 'OPEN').sum()} still open)")

        # ═════════════════════════════════════════════════════════════
        # CORE METRICS — net equity curve + closed-trade stats
        # ═════════════════════════════════════════════════════════════
        strat_ret        = oos['Net_Strategy_Return']
        bnh_daily        = oos['BnH_Return']
        gross_total_ret  = oos['Strategy_Cumulative'].iloc[-1] - 1
        total_ret        = oos['Net_Strategy_Cumulative'].iloc[-1] - 1
        bnh_ret          = oos['BnH_Cumulative'].iloc[-1] - 1
        n_transitions    = int(sim['n_transitions'])
        cost_drag        = gross_total_ret - total_ret

        # Trade-level stats from closed position lifecycles (investor-grade)
        n_closed = len(closed_trades)
        if n_closed > 0:
            trade_pnls   = [t.net_pnl for t in closed_trades]
            trade_rets   = [t.return_pct / 100.0 for t in closed_trades]
            wins         = [p for p in trade_pnls if p > 0]
            losses       = [p for p in trade_pnls if p <= 0]
            win_rate     = len(wins) / n_closed
            gross_p      = sum(wins)
            gross_l      = abs(sum(losses))
            pf           = gross_p / gross_l if gross_l > 0 else np.inf
            avg_win      = np.mean([t.return_pct for t in closed_trades if t.net_pnl > 0]) / 100 if wins else 0.0
            avg_loss     = abs(np.mean([t.return_pct for t in closed_trades if t.net_pnl <= 0])) / 100 if losses else 0.0
            loss_rate    = 1 - win_rate
            expectancy   = (win_rate * avg_win) - (loss_rate * avg_loss)
            total_trades = n_closed
        else:
            win_rate = pf = avg_win = avg_loss = expectancy = 0.0
            total_trades = 0
        n_years     = len(oos) / 252
        sharpe      = (strat_ret.mean() / (strat_ret.std() + 1e-8)) * np.sqrt(252)
        max_dd      = oos['Drawdown'].min()
        annual_ret  = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1
        annual_vol  = strat_ret.std() * np.sqrt(252)
        calmar      = annual_ret / abs(max_dd) if max_dd != 0 else np.inf

        # ═════════════════════════════════════════════════════════════
        # ADVANCED RATIOS
        # ═════════════════════════════════════════════════════════════

        # 1. Sortino Ratio -- only penalizes downside volatility
        downside = strat_ret[strat_ret < 0]
        downside_std = downside.std() if len(downside) > 0 else 1e-8
        sortino = (strat_ret.mean() / (downside_std + 1e-8)) * np.sqrt(252)

        # 2. Information Ratio -- skill vs benchmark
        excess = strat_ret - bnh_daily
        tracking_error = excess.std() + 1e-8
        information_ratio = (excess.mean() / tracking_error) * np.sqrt(252)

        # 3. Treynor Ratio -- return per unit of systematic risk
        cov_matrix = np.cov(strat_ret.dropna().values, bnh_daily.dropna().values)
        beta = cov_matrix[0, 1] / (cov_matrix[1, 1] + 1e-8)
        treynor = annual_ret / (beta + 1e-8) if beta != 0 else np.inf

        # 4. Sterling Ratio -- return / average of top 3 drawdowns
        dd_series = oos['Drawdown']
        dd_periods = []
        in_dd = False
        current_dd = 0
        for dd_val in dd_series:
            if dd_val < 0:
                in_dd = True
                current_dd = min(current_dd, dd_val)
            elif in_dd:
                dd_periods.append(current_dd)
                current_dd = 0
                in_dd = False
        if in_dd:
            dd_periods.append(current_dd)
        top3_dd = sorted(dd_periods)[:3] if len(dd_periods) >= 3 else dd_periods
        avg_top3 = abs(np.mean(top3_dd)) if top3_dd else 1e-8
        sterling = annual_ret / avg_top3 if avg_top3 > 0 else np.inf

        # 5. Burke Ratio -- return / sqrt(sum of squared drawdowns)
        squared_dd_sum = sum(d ** 2 for d in dd_periods) if dd_periods else 1e-8
        burke = annual_ret / (np.sqrt(squared_dd_sum) + 1e-8)

        # 6. Expectancy — already computed from closed-trade lifecycles above

        # 7. Omega Ratio — probability-weighted gains vs losses at threshold=0
        gains = strat_ret[strat_ret > 0].sum()
        losses = abs(strat_ret[strat_ret < 0].sum())
        omega = (gains / losses) if losses > 0 else np.inf

        # 8. Tail Ratio -- are big wins bigger than big losses?
        p95 = strat_ret.quantile(0.95)
        p05 = abs(strat_ret.quantile(0.05))
        tail = p95 / p05 if p05 > 0 else np.inf

        # 9. Up/Down Capture Ratios
        up_days = bnh_daily > 0
        dn_days = bnh_daily < 0
        up_capture = (strat_ret[up_days].mean() / bnh_daily[up_days].mean()) if up_days.sum() > 0 else 0
        down_capture = (strat_ret[dn_days].mean() / bnh_daily[dn_days].mean()) if dn_days.sum() > 0 else 0
        capture_ratio = up_capture / down_capture if down_capture != 0 else np.inf

        # 10. Ulcer Index -- depth & duration of drawdowns
        ulcer = np.sqrt((dd_series ** 2).mean()) * 100

        result = BacktestResult(
            oos_df=oos,
            total_return=total_ret,
            bnh_return=bnh_ret,
            sharpe=sharpe,
            max_drawdown=max_dd,
            win_rate=win_rate,
            profit_factor=pf,
            calmar_ratio=calmar,
            total_trades=total_trades,
            trading_days=len(strat_ret),
            annual_return=annual_ret,
            annual_vol=annual_vol,
            sortino=sortino,
            information_ratio=information_ratio,
            treynor_ratio=treynor,
            sterling_ratio=sterling,
            burke_ratio=burke,
            expectancy=expectancy,
            omega_ratio=omega,
            tail_ratio=tail,
            up_capture=up_capture,
            down_capture=down_capture,
            capture_ratio=capture_ratio,
            ulcer_index=ulcer,
            avg_win=avg_win,
            avg_loss=avg_loss,
            gross_total_return=gross_total_ret,
            cost_drag=cost_drag,
            n_transitions=n_transitions,
            commission_pct=commission,
            slippage_pct=slippage,
            closed_trades=closed_trades,
            n_closed_trades=n_closed,
            trade_log_df=trade_log_df,
            open_position=open_pos,
        )

        # ═════════════════════════════════════════════════════════════
        # PRETTY PRINT — CATEGORIZED PERFORMANCE REPORT
        # ═════════════════════════════════════════════════════════════
        def _grade(val, thresholds, reverse=False):
            """Return a letter grade based on thresholds: (excellent, good, fair)."""
            a, b, c = thresholds
            if reverse:
                if val <= a: return '[A+]'
                if val <= b: return '[B ]'
                if val <= c: return '[C ]'
                return '[D-]'
            else:
                if val >= a: return '[A+]'
                if val >= b: return '[B ]'
                if val >= c: return '[C ]'
                return '[D-]'

        W = 88  # total box width
        HR = '-' * W  # horizontal rule
        print()
        print(f"  +{HR}+")
        print(f"  |{'PETROQUANT -- STRATEGY PERFORMANCE REPORT':^{W}}|")
        print(f"  |{HR}|")
        print(f"  |{'(How well did the strategy perform if we had traded it in the past?)':^{W}}|")
        print(f"  +{HR}+")

        # ---- THE BASICS ----
        print(f"  |{'':^{W}}|")
        print(f"  |  >> THE BASICS (Return & Activity){'':>{W-37}}|")
        print(f"  |{HR}|")
        _p = lambda label, val, desc, grade='': print(
            f"  |  {label:<28} {val:>12}   {grade:<6} {desc:<{W-52}}|"
        )
        _p('Total Strategy Return', f'{total_ret*100:+.2f}%',
           'Total profit if you followed every signal', _grade(total_ret*100, (50, 20, 0)))
        _p('Buy & Hold Return', f'{bnh_ret*100:+.2f}%',
           'What you\'d earn just holding oil (the benchmark)', '')
        _p('Alpha (Strategy - B&H)', f'{(total_ret-bnh_ret)*100:+.2f}%',
           'Extra return the strategy generated over passive holding',
           _grade((total_ret-bnh_ret)*100, (30, 10, 0)))
        _p('Annualized Return', f'{annual_ret*100:+.2f}%',
           'Average yearly return, compounded', _grade(annual_ret*100, (20, 10, 0)))
        _p('Annualized Volatility', f'{annual_vol*100:.2f}%',
           'How much the returns swing per year (lower = smoother)', _grade(annual_vol*100, (15, 25, 35), reverse=True))
        _p('Trading Days', f'{len(strat_ret):,}',
           'Total calendar days in the backtest', '')
        _p('Closed Round-Trips', f'{total_trades:,}',
           'Completed position lifecycles (entry + exit pairs)', '')

        # ---- RISK-ADJUSTED ----
        print(f"  |{'':^{W}}|")
        print(f"  |  >> RISK-ADJUSTED RATIOS (Return normalised by risk taken){'':>{W-59}}|")
        print(f"  |{HR}|")
        _p('Sharpe Ratio', f'{sharpe:.3f}',
           'Return / total volatility (>= 1.0 is good)', _grade(sharpe, (1.5, 1.0, 0.5)))
        _p('Sortino Ratio', f'{sortino:.3f}',
           'Like Sharpe, but only counts DOWNSIDE swings (>= 1.5 ideal)', _grade(sortino, (2.0, 1.0, 0.5)))
        _p('Information Ratio', f'{information_ratio:.3f}',
           'Your skill vs benchmark -- excess return / tracking error', _grade(information_ratio, (1.0, 0.5, 0.0)))
        _p('Treynor Ratio', f'{treynor:.3f}',
           'Return / market risk (beta) -- rewards low-beta strategies', _grade(treynor, (0.3, 0.15, 0.0)))
        _p('Omega Ratio', f'{omega:.3f}',
           'Total gains / total losses (>= 1.5 = strong edge)', _grade(omega, (1.5, 1.2, 1.0)))

        # ---- PAIN & RECOVERY ----
        print(f"  |{'':^{W}}|")
        print(f"  |  >> PAIN & RECOVERY (Drawdown-based -- 'can I stomach this?'){'':>{W-62}}|")
        print(f"  |{HR}|")
        _p('Max Drawdown', f'{max_dd*100:.2f}%',
           'Worst peak-to-trough drop (>=-20% is acceptable)', _grade(abs(max_dd*100), (10, 20, 30), reverse=True))
        _p('Calmar Ratio', f'{calmar:.3f}',
           'Annual return / max drawdown (>= 1.0 = good recovery)', _grade(calmar, (2.0, 1.0, 0.5)))
        _p('Sterling Ratio', f'{sterling:.3f}',
           'Annual return / avg of top 3 drawdowns (more stable)', _grade(sterling, (2.0, 1.0, 0.5)))
        _p('Burke Ratio', f'{burke:.3f}',
           'Penalises frequent deep drops more heavily', _grade(burke, (1.0, 0.5, 0.2)))
        _p('Ulcer Index', f'{ulcer:.2f}',
           'Depth + duration of drawdowns (lower = less stress)', _grade(ulcer, (5, 10, 20), reverse=True))

        # ---- TRADE EFFICIENCY ----
        print(f"  |{'':^{W}}|")
        print(f"  |  >> TRADE EFFICIENCY (The math of each individual trade){'':>{W-58}}|")
        print(f"  |{HR}|")
        _p('Win Rate', f'{win_rate*100:.1f}%',
           'Percentage of trades that made money (>= 50% needed)',
           _grade(win_rate*100, (55, 50, 45)))
        _p('Profit Factor', f'{pf:.3f}',
           'Gross profit / gross loss (>= 1.5 = robust edge)', _grade(pf, (1.5, 1.2, 1.0)))
        _p('Expectancy', f'{expectancy*10000:.2f} bps',
           'Expected profit per trade in basis points (> 0 = profitable)', _grade(expectancy*10000, (5, 2, 0)))
        _p('Avg Win', f'{avg_win*10000:.2f} bps',
           'Average gain on winning trades (in basis points)', '')
        _p('Avg Loss', f'{avg_loss*10000:.2f} bps',
           'Average loss on losing trades (in basis points)', '')
        _p('Tail Ratio', f'{tail:.3f}',
           'Big wins / big losses (>= 1.0 = your best days > worst)', _grade(tail, (1.2, 1.0, 0.8)))

        # ---- MARKET SKILL ----
        print(f"  |{'':^{W}}|")
        print(f"  |  >> MARKET SKILL (How well do you capture gains & dodge losses?){'':>{W-65}}|")
        print(f"  |{HR}|")
        _p('Up-Market Capture', f'{up_capture*100:.1f}%',
           'How much of bull-day gains you captured (>= 100% = full)', _grade(up_capture*100, (100, 70, 50)))
        _p('Down-Market Capture', f'{down_capture*100:.1f}%',
           'How much of bear-day pain you absorbed (<= 50% = great)', _grade(down_capture*100, (30, 50, 80), reverse=True))
        _p('Capture Ratio', f'{capture_ratio:.3f}',
           'Up / Down capture (>= 1.5 = skilful market timing)', _grade(capture_ratio, (2.0, 1.5, 1.0)))

        # ---- TRANSACTION COST IMPACT ----
        print(f"  |{'':^{W}}|")
        print(f"  |  >> TRANSACTION COST IMPACT (Real friction vs idealised backtest){'':>{W-70}}|")
        print(f"  |{HR}|")
        roundtrip_bps = (commission + slippage) * 2 * 10000
        _p('Cost Preset',     cfg.get_active_costs()['preset'],
           f'Active cost model from config.py', '')
        _p('Commission (1-way)', f'{commission*10000:.1f} bps',
           'Per-leg commission cost on every entry or exit', '')
        _p('Slippage (1-way)',   f'{slippage*10000:.1f} bps',
           'Market impact / bid-ask spread cost per leg', '')
        _p('Round-trip Cost',    f'{roundtrip_bps:.1f} bps',
           'Total in+out cost per complete trade (commission × 2 + slippage × 2)', '')
        _p('Position Transitions', f'{n_transitions:,}',
           'Days where you entered, exited, or flipped position', '')
        _p('Gross Total Return',  f'{gross_total_ret*100:+.2f}%',
           'Return BEFORE applying any trading costs (inflated / unrealistic)', '')
        _p('Net Total Return',    f'{total_ret*100:+.2f}%',
           'Return AFTER commission + slippage (what you actually earn)', '')
        _p('Cost Drag',           f'{cost_drag*100:.2f}%',
           'Total % return lost to transaction friction over backtest', '')

        print(f"  |{'':^{W}}|")
        print(f"  +{HR}+")

        # Legend
        print(f"\n  * Grades: [A+] = Excellent | [B ] = Good | [C ] = Fair | [D-] = Needs Work")
        print(f"    bps = basis points (100 bps = 1%). Example: 5 bps per trade means $5 profit per $10,000 traded.")
        print(f"  * All ratios above (Sharpe, Sortino, Calmar, etc.) are computed on NET returns.")

        return result


# ═════════════════════════════════════════════════════════════════════════════
# STRATEGY DASHBOARD — 10-PANEL PLOTLY
# ═════════════════════════════════════════════════════════════════════════════

# Color palette — locked dark theme (high contrast for tables + charts)
COLORS = {
    'bg':         '#0f172a',
    'panel':      '#1e293b',
    'row_alt':    '#162032',
    'grid':       '#334155',
    'border':     '#475569',
    'text':       '#94a3b8',
    'table_text': '#f8fafc',
    'cyan':       '#00d4ff',
    'green':      '#34d399',
    'red':        '#ff6b6b',
    'amber':      '#fbbf24',
    'purple':     '#a78bfa',
    'pink':       '#f472b6',
    'white':      '#f8fafc',
}


def _table_row_fills(n_rows: int) -> List[str]:
    """Alternating dark row backgrounds for readable Plotly tables."""
    return [COLORS['bg'] if i % 2 == 0 else COLORS['row_alt'] for i in range(n_rows)]


def _table_fill_matrix(n_rows: int, n_cols: int) -> List[List[str]]:
    row_fills = _table_row_fills(n_rows)
    return [row_fills] * n_cols

REGIME_COLORS = {
    'BULL':   'rgba(52,211,153,0.15)',
    'PANIC':  'rgba(255,107,107,0.15)',
    'CHOPPY': 'rgba(251,191,36,0.15)',
}
REGIME_LINE = {
    'BULL':   '#34d399',
    'PANIC':  '#ff6b6b',
    'CHOPPY': '#fbbf24',
}


class StrategyDashboard:
    """Renders an 11-panel Plotly dashboard with full trade log for any strategy."""

    def render(self, backtest_result, strategy, full_df=None):
        """
        Build and display the dashboard.

        Parameters
        ----------
        backtest_result : BacktestResult from Backtester
        strategy        : BaseStrategy instance (for metadata, importance, etc.)
        full_df         : optional full DataFrame (for regime shading over full history)
        """
        oos = backtest_result.oos_df
        meta = strategy.get_metadata()
        has_regimes = meta.get('has_regimes', False)
        forecasts = meta.get('forecasts', {})

        # Use full_df for regime plot if available, otherwise oos
        price_df = full_df if full_df is not None else oos

        fig = make_subplots(
            rows=6, cols=2,
            subplot_titles=(
                '(1) Price + Regime Shading',
                '(2) Buy/Sell Signal Overlay',
                '(3) Equity Curve: Strategy vs Buy & Hold',
                '(4) Drawdown (Underwater Plot)',
                '(5) Feature Importance (Top 15)',
                '(6) Accuracy per Regime',
                '(7) Rolling 60-Day Sharpe Ratio',
                '(8) Monthly Returns Heatmap',
                '(9) WTI Price Forecast with 95% Volatility CI',
                '(10) Performance Summary',
                '(11) Complete Trade Log — download CSV below',
                None,
            ),
            vertical_spacing=0.04,
            horizontal_spacing=0.08,
            row_heights=[0.14, 0.12, 0.12, 0.12, 0.12, 0.38],
            specs=[
                [{"type": "xy"}, {"type": "xy"}],
                [{"type": "xy"}, {"type": "xy"}],
                [{"type": "xy"}, {"type": "xy"}],
                [{"type": "xy"}, {"type": "xy"}],
                [{"type": "xy", "secondary_y": True}, {"type": "table"}],
                [{"type": "table", "colspan": 2}, None],
            ]
        )

        # ── Panel 1: Price + Regime Shading ──────────────────────────────
        fig.add_trace(go.Scatter(
            x=price_df.index, y=price_df['WTI_Close'], mode='lines',
            name='WTI Close', line=dict(color=COLORS['cyan'], width=1.2),
        ), row=1, col=1)

        if has_regimes and 'Regime' in price_df.columns:
            y_max = price_df['WTI_Close'].max() * 1.05
            for rname, rcolor in REGIME_COLORS.items():
                rmask = price_df['Regime'] == rname
                if rmask.any():
                    y_vals = np.where(rmask, y_max, np.nan)
                    fig.add_trace(go.Scatter(
                        x=price_df.index, y=y_vals,
                        fill='tozeroy', fillcolor=rcolor,
                        line=dict(width=0), mode='none',
                        name=f'{rname} regime', hoverinfo='skip',
                        connectgaps=False
                    ), row=1, col=1)

        # ── Panel 2: Signal Overlay ──────────────────────────────────────
        fig.add_trace(go.Scatter(
            x=oos.index, y=oos['WTI_Close'], mode='lines',
            name='WTI (OOS)', line=dict(color=COLORS['text'], width=1),
            showlegend=False,
        ), row=1, col=2)

        buys = oos[oos['Signal'] == 1]
        if len(buys) > 0:
            fig.add_trace(go.Scatter(
                x=buys.index, y=buys['WTI_Close'], mode='markers',
                name='BUY', marker=dict(symbol='triangle-up', size=7, color=COLORS['green']),
            ), row=1, col=2)

        sells = oos[oos['Signal'] == -1]
        if len(sells) > 0:
            fig.add_trace(go.Scatter(
                x=sells.index, y=sells['WTI_Close'], mode='markers',
                name='SELL', marker=dict(symbol='triangle-down', size=7, color=COLORS['red']),
            ), row=1, col=2)

        # ── Panel 3: Equity Curve ────────────────────────────────────────
        # Show gross (idealized) as a faint dashed line so you can see the cost gap
        if 'Strategy_Cumulative' in oos.columns:
            fig.add_trace(go.Scatter(
                x=oos.index, y=oos['Strategy_Cumulative'], mode='lines',
                name='Gross (no costs)', line=dict(color=COLORS['cyan'], width=1.2, dash='dot'),
                opacity=0.45,
            ), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=oos.index, y=oos['Net_Strategy_Cumulative'], mode='lines',
            name=f'{strategy.name} (after costs)', line=dict(color=COLORS['cyan'], width=2.2),
        ), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=oos.index, y=oos['BnH_Cumulative'], mode='lines',
            name='Buy & Hold', line=dict(color=COLORS['text'], width=1.5, dash='dash'),
        ), row=2, col=1)
        fig.add_hline(y=1.0, line_dash='dot', line_color=COLORS['border'], row=2, col=1)

        # ── Panel 4: Drawdown (net — the real underwater line) ───────────
        if 'Gross_Drawdown' in oos.columns:
            fig.add_trace(go.Scatter(
                x=oos.index, y=oos['Gross_Drawdown'] * 100, mode='lines',
                line=dict(color=COLORS['red'], width=1, dash='dot'),
                opacity=0.35, name='Gross DD', showlegend=False,
            ), row=2, col=2)
        fig.add_trace(go.Scatter(
            x=oos.index, y=oos['Drawdown'] * 100, mode='lines',
            fill='tozeroy', fillcolor='rgba(255,107,107,0.2)',
            line=dict(color=COLORS['red'], width=1.5),
            name='Net Drawdown', showlegend=False,
        ), row=2, col=2)
        fig.update_yaxes(title_text='Drawdown % (net)', row=2, col=2)

        # ── Panel 5: Feature Importance ──────────────────────────────────
        if strategy.feature_importance is not None:
            top15 = strategy.feature_importance.head(15)
            fig.add_trace(go.Bar(
                y=top15.index[::-1], x=top15.values[::-1], orientation='h',
                marker_color=COLORS['amber'], showlegend=False,
            ), row=3, col=1)

        # ── Panel 6: Accuracy per Regime ─────────────────────────────────
        if has_regimes and 'Regime' in oos.columns and 'Prediction' in oos.columns:
            oos_valid = oos[oos['Prediction'].notna()]
            if len(oos_valid) > 0 and 'Target' in oos_valid.columns:
                from sklearn.metrics import accuracy_score
                regime_acc = oos_valid.groupby('Regime').apply(
                    lambda g: accuracy_score(g['Target'], g['Prediction'])
                    if len(g) > 0 else 0, include_groups=False
                )
                regime_n = oos_valid['Regime'].value_counts()
                fig.add_trace(go.Bar(
                    x=regime_acc.index, y=regime_acc.values,
                    marker_color=[REGIME_LINE.get(r, '#fff') for r in regime_acc.index],
                    text=[f"{v:.1%}<br>n={regime_n.get(r, 0)}"
                          for r, v in regime_acc.items()],
                    textposition='auto', showlegend=False,
                ), row=3, col=2)
                fig.add_hline(y=0.5, line_dash='dash',
                              line_color='rgba(255,107,107,0.5)',
                              annotation_text='50% (random)', row=3, col=2)

        # ── Panel 7: Rolling Sharpe ──────────────────────────────────────
        fig.add_trace(go.Scatter(
            x=oos.index, y=oos['Rolling_Sharpe'], mode='lines',
            line=dict(color=COLORS['purple'], width=1.5), showlegend=False,
        ), row=4, col=1)
        fig.add_hline(y=0, line_dash='dash', line_color=COLORS['border'], row=4, col=1)
        fig.add_hline(y=1.0, line_dash='dot', line_color=COLORS['green'],
                      annotation_text='Sharpe=1.0', row=4, col=1)

        # ── Panel 8: Monthly Returns Heatmap (net returns) ───────────────
        monthly = oos['Net_Strategy_Return'].resample('ME').sum() * 100
        if len(monthly) > 0:
            monthly_df = pd.DataFrame({
                'Year': monthly.index.year,
                'Month': monthly.index.month,
                'Return': monthly.values
            })
            pivot = monthly_df.pivot_table(values='Return', index='Year',
                                           columns='Month', aggfunc='sum')
            month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                           'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
            pivot.columns = [month_names[m - 1] for m in pivot.columns]

            fig.add_trace(go.Heatmap(
                z=pivot.values,
                x=pivot.columns.tolist(),
                y=[str(y) for y in pivot.index.tolist()],
                colorscale=[
                    [0.0, COLORS['red']],
                    [0.5, COLORS['bg']],
                    [1.0, COLORS['green']],
                ],
                zmid=0,
                text=np.round(pivot.values, 1),
                texttemplate='%{text:.1f}%',
                textfont=dict(size=10),
                showscale=False,
                hovertemplate='%{y} %{x}: %{z:.2f}%<extra></extra>',
            ), row=4, col=2)

        # ── Panel 9: WTI Price Forecast with 95% Volatility CI ──────────────
        if forecasts:
            horizons = sorted(forecasts.keys())
            exp_prices = [forecasts[h]['expected_price'] for h in horizons]
            exp_rets   = [forecasts[h]['expected_return'] * 100 for h in horizons]
            prob_ups   = [forecasts[h]['prob_up'] for h in horizons]
            current_price = forecasts[horizons[0]].get('current_price', 0)
            labels     = [f"{h}d" for h in horizons]

            # CI bounds
            price_lows  = [forecasts[h].get('price_low', exp_prices[i]) for i, h in enumerate(horizons)]
            price_highs = [forecasts[h].get('price_high', exp_prices[i]) for i, h in enumerate(horizons)]

            # Volatility info for hover
            vol_composites = [forecasts[h].get('vol_composite', 0) for h in horizons]
            vol_garchs = [forecasts[h].get('vol_garch', 0) for h in horizons]
            vol_atrs = [forecasts[h].get('vol_atr', 0) for h in horizons]
            vol_ivs = [forecasts[h].get('vol_iv', 0) or 0 for h in horizons]

            # CI width relative to price determines bar color
            ci_widths = [(price_highs[i] - price_lows[i]) / current_price if current_price > 0 else 0
                         for i in range(len(horizons))]
            bar_colors = []
            for i, w in enumerate(ci_widths):
                if prob_ups[i] > 0.55:
                    bar_colors.append(COLORS['green'])
                elif prob_ups[i] < 0.45:
                    bar_colors.append(COLORS['red'])
                else:
                    bar_colors.append(COLORS['amber'])

            # Error bars for CI
            error_low  = [exp_prices[i] - price_lows[i] for i in range(len(horizons))]
            error_high = [price_highs[i] - exp_prices[i] for i in range(len(horizons))]

            fig.add_trace(go.Bar(
                x=labels, y=exp_prices,
                marker_color=bar_colors,
                error_y=dict(
                    type='data',
                    symmetric=False,
                    array=error_high,
                    arrayminus=error_low,
                    color=COLORS['white'],
                    thickness=1.5,
                    width=6,
                ),
                text=[f"${p:.2f}" for p in exp_prices],
                textposition='outside', showlegend=False,
                name='Expected Price',
                hovertemplate=(
                    '<b>%{x} Forecast</b><br>'
                    'Expected: $%{y:.2f}<br>'
                    'Range: $%{customdata[0]:.2f} - $%{customdata[1]:.2f}<br>'
                    'Return: %{customdata[2]:+.2f}%<br>'
                    'P(Up): %{customdata[3]:.0%}<br>'
                    'Vol: GARCH %{customdata[4]:.1f}% / ATR %{customdata[5]:.1f}% / '
                    'IV %{customdata[6]:.1f}% -> Composite %{customdata[7]:.1f}%'
                    '<extra></extra>'
                ),
                customdata=list(zip(
                    price_lows, price_highs, exp_rets, prob_ups,
                    [v*100 for v in vol_garchs], [v*100 for v in vol_atrs],
                    [v*100 for v in vol_ivs], [v*100 for v in vol_composites]
                )),
            ), row=5, col=1)

            # Composite vol line on secondary axis (overlaid)
            fig.add_trace(go.Scatter(
                x=labels, y=[v*100 for v in vol_composites],
                mode='lines+markers',
                name='Composite Vol %',
                line=dict(color=COLORS['purple'], width=2, dash='dot'),
                marker=dict(size=6, color=COLORS['purple']),
                showlegend=False,
                hovertemplate='%{x}: %{y:.1f}% composite vol<extra></extra>',
            ), row=5, col=1, secondary_y=True)

            # Current price reference line
            if current_price > 0:
                fig.add_hline(
                    y=current_price, line_dash='dash',
                    line_color=COLORS['amber'],
                    annotation_text=f'Current: ${current_price:.2f}',
                    annotation_font_color=COLORS['amber'],
                    row=5, col=1
                )

            fig.update_yaxes(title_text='WTI Price ($) / Composite Vol (%)', row=5, col=1)


        # ── Panel 10: Performance Summary Table ──────────────────────
        r = backtest_result
        metrics_header = ['Metric', 'Value', 'What It Means']
        metric_rows = [
            ('Total Return',       f"{r.total_return*100:+.2f}%",   'Overall profit/loss'),
            ('Buy & Hold Rtn',     f"{r.bnh_return*100:+.2f}%",     'Passive holding benchmark'),
            ('Sharpe Ratio',       f"{r.sharpe:.3f}",               'Return per unit of risk'),
            ('Sortino Ratio',      f"{r.sortino:.3f}",              'Downside-risk adjusted'),
            ('Information Ratio',  f"{r.information_ratio:.3f}",    'Skill vs benchmark'),
            ('Omega Ratio',        f"{r.omega_ratio:.3f}",          'Total gains / losses'),
            ('Max Drawdown',       f"{r.max_drawdown*100:.2f}%",    'Worst drop from peak'),
            ('Calmar Ratio',       f"{r.calmar_ratio:.3f}",         'Return / max drawdown'),
            ('Sterling Ratio',     f"{r.sterling_ratio:.3f}",       'Return / avg drawdowns'),
            ('Ulcer Index',        f"{r.ulcer_index:.2f}",          'Drawdown stress score'),
            ('Win Rate',           f"{r.win_rate*100:.1f}%",        '% of profitable trades'),
            ('Profit Factor',      f"{r.profit_factor:.3f}",        'Gross gain / gross loss'),
            ('Expectancy',         f"{r.expectancy*10000:.2f} bps", 'Avg profit per trade'),
            ('Tail Ratio',         f"{r.tail_ratio:.3f}",           'Big wins vs big losses'),
            ('Capture Ratio',      f"{r.capture_ratio:.3f}",        'Up capture / down capture'),
            ('Trading Days',       f"{r.trading_days}",             'Days in backtest'),
            ('Total Trades',       f"{r.total_trades}",             'Closed round-trip positions'),
        ]
        names = [m[0] for m in metric_rows]
        vals  = [m[1] for m in metric_rows]
        descs = [m[2] for m in metric_rows]

        # Color-code values
        value_colors = []
        for i, val_str in enumerate(vals):
            try:
                cleaned = val_str.replace('%', '').replace('+', '').replace(',', '').split()[0]
                num = float(cleaned)
                if 'Drawdown' in names[i] or 'Ulcer' in names[i]:
                    value_colors.append(COLORS['red'])
                elif num > 0:
                    value_colors.append(COLORS['green'])
                elif num < 0:
                    value_colors.append(COLORS['red'])
                else:
                    value_colors.append(COLORS['text'])
            except (ValueError, IndexError):
                value_colors.append(COLORS['text'])

        fig.add_trace(go.Table(
            header=dict(
                values=metrics_header,
                fill_color=COLORS['panel'],
                font=dict(color=COLORS['table_text'], size=12),
                align='left',
                line_color=COLORS['border'],
            ),
            cells=dict(
                values=[names, vals, descs],
                fill_color=_table_fill_matrix(len(names), 3),
                font=dict(
                    color=[
                        [COLORS['table_text']] * len(names),
                        value_colors,
                        [COLORS['text']] * len(names),
                    ],
                    size=11,
                ),
                align='left',
                line_color=COLORS['border'],
                height=24,
            ),
        ), row=5, col=2)

        # ── Panel 11: Complete Trade Log ─────────────────────────────────
        trade_log_df = backtest_result.trade_log_df
        if trade_log_df is None or len(trade_log_df) == 0:
            trade_log_df = build_trade_log_df(
                backtest_result.closed_trades or [],
                oos,
                getattr(backtest_result, 'open_position', None),
            )

        if trade_log_df is not None and len(trade_log_df) > 0:
            col_labels = {
                'trade_id': '#', 'status': 'Status', 'side': 'Side',
                'open_date': 'Open', 'close_date': 'Close', 'hold_days': 'Days',
                'entry_market': 'Entry Mkt', 'exit_market': 'Exit Mkt',
                'entry_fill': 'Entry Fill', 'exit_fill': 'Exit Fill',
                'position_frac': 'Size Frac', 'notional_usd': 'Notional $',
                'invested_usd': 'Invested $', 'gross_pnl_usd': 'Gross P&L',
                'commission_usd': 'Commission', 'slippage_usd': 'Slippage',
                'net_pnl_usd': 'Net P&L', 'return_pct': 'Return %',
                'outcome': 'Result', 'signal_open': 'Signal In',
                'probability_open': 'Prob In', 'regime_open': 'Regime In',
                'signal_close': 'Signal Out', 'probability_close': 'Prob Out',
                'regime_close': 'Regime Out',
            }
            display_cols = [c for c in col_labels if c in trade_log_df.columns]
            headers = [col_labels[c] for c in display_cols]
            cell_values = [trade_log_df[c].astype(str).tolist() for c in display_cols]

            # Colour Net P&L column green/red
            pnl_colors = []
            for c in display_cols:
                if c == 'net_pnl_usd':
                    pnl_colors.append([
                        COLORS['green'] if _pnl_val > 0 else COLORS['red'] if _pnl_val < 0 else COLORS['text']
                        for _pnl_val in trade_log_df[c]
                    ])
                elif c == 'outcome':
                    pnl_colors.append([
                        COLORS['green'] if v == 'WIN' else COLORS['red'] if v == 'LOSS'
                        else COLORS['amber'] if v == 'OPEN' else COLORS['text']
                        for v in trade_log_df[c]
                    ])
                else:
                    pnl_colors.append([COLORS['table_text']] * len(trade_log_df))

            n_trades = len(trade_log_df)
            fig.add_trace(go.Table(
                header=dict(
                    values=headers,
                    fill_color=COLORS['panel'],
                    font=dict(color=COLORS['table_text'], size=11),
                    align='left',
                    line_color=COLORS['border'],
                ),
                cells=dict(
                    values=cell_values,
                    fill_color=_table_fill_matrix(n_trades, len(display_cols)),
                    font=dict(color=pnl_colors, size=10),
                    align='left',
                    line_color=COLORS['border'],
                    height=24,
                ),
            ), row=6, col=1)
        else:
            fig.add_trace(go.Table(
                header=dict(values=['Trade Log'], fill_color=COLORS['panel'],
                            font=dict(color=COLORS['table_text'], size=12),
                            line_color=COLORS['border']),
                cells=dict(values=[['No closed trades in this backtest period.']],
                           fill_color=[[COLORS['bg']]],
                           font=dict(color=[COLORS['text']], size=11),
                           line_color=COLORS['border']),
            ), row=6, col=1)

        # Download link overlaid at bottom of chart (next to trade log panel)
        if trade_log_df is not None and len(trade_log_df) > 0:
            self._add_trade_log_download_annotation(fig, trade_log_df)

        # ── Layout ───────────────────────────────────────────────────────
        fig.update_layout(
            title=dict(
                text=(f"<b>PETROQUANT -- {strategy.name} Dashboard</b>"
                      f"<br><span style='font-size:12px;color:{COLORS['text']}'>"
                      f"Sharpe: {r.sharpe:.2f} | Return: {r.total_return*100:+.1f}% | "
                      f"MaxDD: {r.max_drawdown*100:.1f}% | "
                      f"Win Rate: {r.win_rate*100:.0f}%</span>"),
                font=dict(size=18, color=COLORS['table_text']),
            ),
            template=None,
            height=2800,
            width=1500,
            paper_bgcolor=COLORS['bg'],
            plot_bgcolor=COLORS['bg'],
            font=dict(color=COLORS['text']),
            hovermode='x unified',
            legend=dict(
                orientation='h', y=-0.01, x=0.5, xanchor='center',
                bgcolor='rgba(15,23,42,0.9)', bordercolor=COLORS['border'],
                font=dict(color=COLORS['table_text']),
            ),
            margin=dict(b=80),
        )
        fig.update_xaxes(
            gridcolor=COLORS['grid'], zeroline=False,
            tickfont=dict(color=COLORS['text']),
            title_font=dict(color=COLORS['table_text']),
        )
        fig.update_yaxes(
            gridcolor=COLORS['grid'], zeroline=False,
            tickfont=dict(color=COLORS['text']),
            title_font=dict(color=COLORS['table_text']),
        )

        # Update subplot title fonts — bright on dark background
        for annotation in fig['layout']['annotations']:
            annotation['font'] = dict(size=13, color=COLORS['table_text'])

        self._show_dashboard(fig)
        print(f"\n  [OK] Dashboard rendered for: {strategy.name}")
        return fig

    def _show_dashboard(self, fig) -> None:
        """Open dashboard in browser with locked dark page background."""
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
        os.makedirs(output_dir, exist_ok=True)
        preview_path = os.path.join(output_dir, '_dashboard_preview.html')
        fig.write_html(preview_path, include_plotlyjs='cdn')
        self._inject_dark_page_style(preview_path)
        import webbrowser
        webbrowser.open(preview_path)
        print(f"  [OK] Dashboard preview: {preview_path}")

    @staticmethod
    def _inject_dark_page_style(html_path: str) -> None:
        """Force dark page chrome so tables/charts don't sit on a white browser background."""
        dark_style = (
            '<style id="pq-dark-page">'
            f'html,body{{background-color:{COLORS["bg"]}!important;color-scheme:dark;margin:0;padding:0;}}'
            '</style>'
        )
        with open(html_path, 'r', encoding='utf-8') as f:
            html = f.read()
        if 'pq-dark-page' not in html and '<head>' in html:
            html = html.replace('<head>', '<head>' + dark_style, 1)
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html)

    @staticmethod
    def _add_trade_log_download_annotation(fig, trade_log_df: pd.DataFrame) -> None:
        """Place a clickable CSV download link inside the chart, above the trade log table."""
        csv_text = trade_log_df.to_csv(index=False)
        b64      = base64.b64encode(csv_text.encode('utf-8')).decode('ascii')
        href     = f"data:text/csv;base64,{b64}"
        n_trades = len(trade_log_df)

        fig.add_annotation(
            text=(
                f'<a href="{href}" download="petroquant_trade_log.csv" '
                f'style="color:#0f172a;background:#facc15;padding:10px 22px;'
                f'border-radius:8px;font-size:14px;font-weight:700;text-decoration:none;'
                f'font-family:Segoe UI,system-ui,sans-serif;">'
                f'&#11015; Download Trade Log (CSV) — {n_trades} trades</a>'
            ),
            xref='paper', yref='paper',
            x=0.5, y=0.32,
            xanchor='center', yanchor='bottom',
            showarrow=False,
            align='center',
        )

    def save_html(self, fig, filepath, trade_log_df=None):
        """
        Export dashboard to standalone HTML with an embedded CSV download button.
        Also writes a sibling *_trade_log.csv file to the output folder.
        """
        fig.write_html(filepath, include_plotlyjs=True)
        self._inject_dark_page_style(filepath)

        csv_filename = os.path.basename(filepath).replace('.html', '_trade_log.csv')
        n_trades     = 0
        csv_content  = ''

        if trade_log_df is not None and len(trade_log_df) > 0:
            n_trades  = len(trade_log_df)
            csv_path  = filepath.replace('.html', '_trade_log.csv')
            trade_log_df.to_csv(csv_path, index=False)
            csv_content = trade_log_df.to_csv(index=False)
            print(f"  [OK] Trade log CSV: {csv_path} ({n_trades} trades)")

        self._inject_trade_log_download(filepath, csv_content, csv_filename, n_trades)
        print(f"  [OK] Saved: {filepath}")
        if n_trades > 0:
            print(f"  [OK] Download: floating bar at bottom of page + link above trade log table")

    @staticmethod
    def _strip_trade_log_download(html: str) -> str:
        """Remove any prior trade-log download injection (idempotent re-save)."""
        import re
        html = re.sub(
            r'<style>\s*#pq-trade-log-download\s*\{[^}]*\}\s*</style>\s*',
            '', html, flags=re.DOTALL,
        )
        html = re.sub(
            r'<div id="pq-trade-log-download"[^>]*>.*?</div>\s*',
            '', html, flags=re.DOTALL,
        )
        html = re.sub(
            r'<script>\s*const PQ_TRADE_LOG_CSV.*?function pqDownloadTradeLog\(\).*?\}\s*</script>\s*',
            '', html, flags=re.DOTALL,
        )
        return html

    @staticmethod
    def _inject_trade_log_download(html_path: str, csv_content: str,
                                   csv_filename: str, n_trades: int) -> None:
        """Inject a fixed download bar that stays visible at the bottom of the viewport."""
        btn_disabled = 'disabled' if n_trades == 0 else ''
        btn_opacity  = '0.45' if n_trades == 0 else '1.0'
        btn_cursor   = 'not-allowed' if n_trades == 0 else 'pointer'
        trade_label  = f'{n_trades} trades ready to export' if n_trades else 'No trades in this backtest'

        download_block = f"""
<style>
  #pq-trade-log-download {{
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
    z-index: 2147483647; max-width: 96vw;
    background: #1e293b; border: 2px solid #facc15; border-radius: 10px;
    padding: 12px 24px; display: flex; align-items: center; justify-content: center; gap: 14px;
    font-family: Segoe UI, system-ui, sans-serif;
    box-shadow: 0 8px 32px rgba(0,0,0,0.55);
  }}
</style>
<div id="pq-trade-log-download">
  <span style="color:#e2e8f0; font-size:13px; font-weight:600;">Trade Log</span>
  <button id="pq-csv-btn" onclick="pqDownloadTradeLog()" {btn_disabled} style="
      background:#facc15; color:#0f172a; border:none; border-radius:8px;
      padding:10px 22px; font-size:14px; font-weight:700; cursor:{btn_cursor};
      opacity:{btn_opacity};">
    &#11015; Download CSV ({n_trades} trades)
  </button>
</div>
<script>
const PQ_TRADE_LOG_CSV  = {json.dumps(csv_content)};
const PQ_TRADE_LOG_NAME = {json.dumps(csv_filename)};
function pqDownloadTradeLog() {{
  if (!PQ_TRADE_LOG_CSV) {{ alert('No trades in this backtest to download.'); return; }}
  const blob = new Blob([PQ_TRADE_LOG_CSV], {{type: 'text/csv;charset=utf-8;'}});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = PQ_TRADE_LOG_NAME;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(link.href);
}}
</script>
"""
        with open(html_path, 'r', encoding='utf-8') as f:
            html = f.read()

        html = StrategyDashboard._strip_trade_log_download(html)

        # Style in <head>; bar + script before </body>
        if '<head>' in html:
            html = html.replace('<head>', '<head>' + download_block.split('<div id')[0], 1)
        anchor = '</body>'
        if anchor in html:
            bar_only = '<div id' + download_block.split('<div id', 1)[1]
            html = html.replace(anchor, bar_only + anchor, 1)
        else:
            html += download_block

        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)
