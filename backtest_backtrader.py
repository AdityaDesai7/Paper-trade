# ============================================================================
# PETROQUANT — BACKTRADER BACKTESTING ENGINE
# ============================================================================
# Proper event-driven backtesting using the Backtrader framework.
#
# WHAT THIS FILE DOES:
#   1. Loads real oil market data from the PetroQuant pipeline
#   2. Runs the HMM + XGBoost strategy to generate signals
#   3. Feeds signals + prices into a proper Backtrader Cerebro engine
#   4. Computes industry-standard metrics (Sharpe, Calmar, Max DD, etc.)
#   5. Saves a matplotlib chart to /output/backtrader_chart.png
#   6. Prints a full, formatted performance tearsheet
#
# USAGE:
#   python backtest_backtrader.py
#
# HOW BACKTRADER DIFFERS FROM THE CUSTOM BACKTESTER:
#   - Backtrader is event-driven: each bar is processed one-by-one
#   - Proper cash management, position tracking, commission simulation
#   - Slippage model included (realistic execution)
#   - Industry-standard framework used by professionals
#   - Built-in analyzers: Sharpe, DrawDown, TradeAnalyzer, etc.
# ============================================================================

import os
import sys
import warnings
import datetime

# Force UTF-8 so box-drawing chars work on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import backtrader as bt
import backtrader.analyzers as btanalyzers
import matplotlib
matplotlib.use('Agg')   # Non-interactive backend — saves to file
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch
import matplotlib.ticker as mtick

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from oil_data_pipeline_new import build_master_df
from strategy import HMMXGBoostStrategy


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
INITIAL_CASH      = 100_000      # Starting portfolio value in USD
COMMISSION        = 0.001        # 0.1% round-trip commission per trade
SLIPPAGE_PERC     = 0.005       # 0.05% slippage on fills
POSITION_SIZE_PCT = 0.95         # Use 95% of available cash per trade
FWD_DAYS          = 5            # Forward-return horizon for strategy signals


# ═════════════════════════════════════════════════════════════════════════════
# STEP 1: PANDAS DATA FEED
# ═════════════════════════════════════════════════════════════════════════════

class SignalPandasData(bt.feeds.PandasData):
    """
    Custom Backtrader data feed that extends the standard OHLCV feed
    with PetroQuant-specific columns: Signal, Probability, Position_Size,
    and Regime (encoded as integer).

    Why custom feed?
    ----------------
    Backtrader's standard PandasData only exposes OHLCV. We need the
    strategy's pre-computed signals available inside the strategy's
    next() method so we can act on them bar-by-bar.
    """
    lines = ('signal', 'probability', 'position_size', 'regime_code',)

    params = (
        ('signal',        -1),   # column index — resolved from df.columns
        ('probability',   -1),
        ('position_size', -1),
        ('regime_code',   -1),
        ('openinterest',  -1),   # not available, suppress
    )


# ═════════════════════════════════════════════════════════════════════════════
# STEP 2: BACKTRADER STRATEGY
# ═════════════════════════════════════════════════════════════════════════════

class PetroQuantBTStrategy(bt.Strategy):
    """
    Backtrader strategy wrapper for PetroQuant signals.

    This strategy does NOT re-run the ML model inside Backtrader.
    Instead it consumes the pre-computed signals from the PetroQuant
    pipeline as a data line — which is the industry standard approach
    for integrating ML signals into an event-driven framework.

    Signal logic:
    -------------
      signal == +1  → BUY  (go long)
      signal == -1  → SELL (go short / exit long)
      signal ==  0  → HOLD (no change)

    Position sizing:
    ----------------
      Uses the pre-computed Position_Size column from strategy.py
      which already incorporates regime-aware scaling:
        BULL regime   = 100% of target allocation
        CHOPPY regime = 50%
        PANIC regime  = 25%

    Commission + Slippage:
    ----------------------
      Applied by Cerebro via broker settings (not hardcoded here).
    """

    params = dict(
        position_size_pct=POSITION_SIZE_PCT,   # max % of portfolio per trade
        verbose=False,                          # print every bar's action
    )

    def __init__(self):
        # Reference the custom signal line
        self.signal        = self.data.signal
        self.probability   = self.data.probability
        self.position_size = self.data.position_size
        self.regime_code   = self.data.regime_code

        # Track trade log for later analysis
        self.trade_log   = []
        self.bar_count   = 0
        self.order       = None   # pending order (prevent duplicate orders)

    def log(self, txt, dt=None):
        if self.params.verbose:
            dt = dt or self.datas[0].datetime.date(0)
            print(f'  [{dt}] {txt}')

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return
        if order.status in [order.Completed]:
            direction = 'BUY ' if order.isbuy() else 'SELL'
            self.log(
                f'{direction} @ {order.executed.price:.2f} | '
                f'Size: {order.executed.size:.2f} | '
                f'Cost: {order.executed.value:.2f} | '
                f'Comm: {order.executed.comm:.2f}'
            )
            self.trade_log.append({
                'date':      self.datas[0].datetime.date(0),
                'type':      direction,
                'price':     order.executed.price,
                'size':      order.executed.size,
                'cost':      order.executed.value,
                'comm':      order.executed.comm,
                'portfolio': self.broker.getvalue(),
            })
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.log(f'Order Canceled/Margin/Rejected: {order.status}')
        self.order = None

    def notify_trade(self, trade):
        if not trade.isclosed:
            return
        self.log(f'TRADE CLOSED | PnL: {trade.pnl:.2f} | Net: {trade.pnlcomm:.2f}')

    def next(self):
        self.bar_count += 1

        # Skip if signal is NaN (warm-up / training period)
        if np.isnan(self.signal[0]):
            return

        # Don't issue new order if one is pending
        if self.order:
            return

        current_signal = int(self.signal[0])
        pos_size_frac  = self.position_size[0]  # regime-aware fraction (-1 to +1)

        # Compute target position value
        portfolio_value = self.broker.getvalue()
        target_value    = portfolio_value * self.params.position_size_pct * abs(pos_size_frac)
        current_price   = self.data.close[0]

        if current_price <= 0:
            return

        target_shares = target_value / current_price

        current_pos = self.getposition().size

        # ── BUY signal ────────────────────────────────────────────────────
        if current_signal == 1:
            if current_pos <= 0:
                # Close any short first
                if current_pos < 0:
                    self.close()
                # Open long
                self.order = self.buy(size=target_shares)
                self.log(f'BUY ORDER | Shares: {target_shares:.2f} | '
                         f'Price: {current_price:.2f} | RegimeFrac: {pos_size_frac:.2f}')

        # ── SELL signal ───────────────────────────────────────────────────
        elif current_signal == -1:
            if current_pos >= 0:
                # Close any long first
                if current_pos > 0:
                    self.close()
                # Open short
                self.order = self.sell(size=target_shares)
                self.log(f'SELL ORDER | Shares: {target_shares:.2f} | '
                         f'Price: {current_price:.2f} | RegimeFrac: {pos_size_frac:.2f}')

        # ── HOLD — do nothing ─────────────────────────────────────────────
        # (signal == 0 means model is not confident enough to act)


# ═════════════════════════════════════════════════════════════════════════════
# STEP 3: CUSTOM ANALYZERS
# ═════════════════════════════════════════════════════════════════════════════

class PortfolioValueObserver(bt.Observer):
    """Observer that records portfolio value on every bar for equity curve."""
    lines = ('value',)
    plotinfo = dict(plot=True, subplot=True)

    def next(self):
        self.lines.value[0] = self._owner.broker.getvalue()


# ═════════════════════════════════════════════════════════════════════════════
# STEP 4: DATA PREPARATION
# ═════════════════════════════════════════════════════════════════════════════

def prepare_backtrader_feed(strategy_df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert the PetroQuant strategy output DataFrame into the format
    required by Backtrader's PandasData feed.

    Backtrader requires:
        - DatetimeIndex (timezone-naive)
        - Columns: open, high, low, close, volume
        - Our extras: signal, probability, position_size, regime_code

    Since PetroQuant uses daily close prices (no OHLC), we synthesize
    reasonable OHLH values from the close to satisfy Backtrader's feed.
    """
    df = strategy_df.copy()

    # Ensure datetime index
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    # Strip timezone if present (Backtrader requirement)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    close = df['WTI_Close']

    # Synthesize OHLV from daily close (standard practice when only close available)
    bt_df = pd.DataFrame(index=df.index)
    bt_df['open']   = close.shift(1).fillna(close)   # yesterday's close as open
    bt_df['high']   = close * 1.005                  # +0.5% intraday range proxy
    bt_df['low']    = close * 0.995                  # -0.5% intraday range proxy
    bt_df['close']  = close
    bt_df['volume'] = 1_000_000                       # constant placeholder

    # Strategy signals
    bt_df['signal']        = df['Signal'].fillna(0).astype(float)
    bt_df['probability']   = df['Probability'].fillna(0.5).astype(float)
    bt_df['position_size'] = df['Position_Size'].fillna(0.0).astype(float)

    # Encode regime as integer (for the data feed line)
    regime_map = {'BULL': 1, 'CHOPPY': 0, 'PANIC': -1}
    if 'Regime' in df.columns:
        bt_df['regime_code'] = df['Regime'].map(regime_map).fillna(0).astype(float)
    else:
        bt_df['regime_code'] = 0.0

    # Drop rows with NaN close
    bt_df = bt_df.dropna(subset=['close'])

    print(f"    [OK] Backtrader feed prepared: {len(bt_df)} bars")
    print(f"         Range: {bt_df.index.min().date()} → {bt_df.index.max().date()}")
    print(f"         WTI Close range: ${bt_df['close'].min():.2f} – ${bt_df['close'].max():.2f}")

    return bt_df


# ═════════════════════════════════════════════════════════════════════════════
# STEP 5: CEREBRO RUNNER
# ═════════════════════════════════════════════════════════════════════════════

def run_cerebro(bt_df: pd.DataFrame) -> dict:
    """
    Configure and run Backtrader's Cerebro engine.

    Returns
    -------
    dict with cerebro instance, strategy results, and analyzer outputs.
    """
    print("\n  -- Configuring Backtrader Cerebro Engine --")

    cerebro = bt.Cerebro()

    # ── Data Feed ────────────────────────────────────────────────────────
    data_feed = SignalPandasData(
        dataname=bt_df,
        datetime=None,     # use index
        open='open',
        high='high',
        low='low',
        close='close',
        volume='volume',
        signal='signal',
        probability='probability',
        position_size='position_size',
        regime_code='regime_code',
    )
    cerebro.adddata(data_feed, name='WTI_Crude')

    # ── Strategy ─────────────────────────────────────────────────────────
    cerebro.addstrategy(
        PetroQuantBTStrategy,
        position_size_pct=POSITION_SIZE_PCT,
        verbose=False,
    )

    # ── Broker Settings ──────────────────────────────────────────────────
    cerebro.broker.setcash(INITIAL_CASH)
    cerebro.broker.setcommission(commission=COMMISSION)
    cerebro.broker.set_slippage_perc(perc=SLIPPAGE_PERC)

    # ── Analyzers ────────────────────────────────────────────────────────
    # These are Backtrader's built-in analyzers — industry standard
    cerebro.addanalyzer(btanalyzers.SharpeRatio,
                        _name='sharpe',
                        timeframe=bt.TimeFrame.Days,
                        annualize=True,
                        riskfreerate=0.04)      # 4% risk-free (current T-bill)

    cerebro.addanalyzer(btanalyzers.DrawDown,
                        _name='drawdown')

    cerebro.addanalyzer(btanalyzers.TradeAnalyzer,
                        _name='trades')

    cerebro.addanalyzer(btanalyzers.Returns,
                        _name='returns',
                        timeframe=bt.TimeFrame.Days,
                        tann=252)

    cerebro.addanalyzer(btanalyzers.AnnualReturn,
                        _name='annual_return')

    cerebro.addanalyzer(btanalyzers.Calmar,
                        _name='calmar')

    cerebro.addanalyzer(btanalyzers.SQN,
                        _name='sqn')

    cerebro.addanalyzer(btanalyzers.TimeReturn,
                        _name='time_return',
                        timeframe=bt.TimeFrame.Days)

    # ── Observers ────────────────────────────────────────────────────────
    cerebro.addobserver(PortfolioValueObserver)

    # ── Run ──────────────────────────────────────────────────────────────
    print(f"    Initial Cash: ${INITIAL_CASH:,.0f}")
    print(f"    Commission:   {COMMISSION*100:.2f}% per trade")
    print(f"    Slippage:     {SLIPPAGE_PERC*100:.3f}% per fill")
    print(f"    Position:     {POSITION_SIZE_PCT*100:.0f}% of portfolio (max)")
    print(f"\n    Running Cerebro...")

    strats = cerebro.run()
    strat  = strats[0]

    final_value  = cerebro.broker.getvalue()
    total_return = (final_value - INITIAL_CASH) / INITIAL_CASH

    print(f"    [OK] Cerebro complete.")
    print(f"    Final Portfolio Value: ${final_value:,.2f}")
    print(f"    Total Return:          {total_return*100:+.2f}%")

    return {
        'cerebro': cerebro,
        'strat':   strat,
        'final_value': final_value,
        'total_return': total_return,
        'bt_df': bt_df,
    }


# ═════════════════════════════════════════════════════════════════════════════
# STEP 6: METRICS EXTRACTION
# ═════════════════════════════════════════════════════════════════════════════

def extract_metrics(run_result: dict, oos_df: pd.DataFrame) -> dict:
    """
    Extract and compute all performance metrics from Backtrader analyzers
    plus supplementary metrics computed from the OOS DataFrame.
    """
    strat = run_result['strat']
    final_value  = run_result['final_value']
    total_return = run_result['total_return']

    # ── Backtrader Analyzer Outputs ───────────────────────────────────────
    sharpe_analysis  = strat.analyzers.sharpe.get_analysis()
    dd_analysis      = strat.analyzers.drawdown.get_analysis()
    trade_analysis   = strat.analyzers.trades.get_analysis()
    returns_analysis = strat.analyzers.returns.get_analysis()
    annual_analysis  = strat.analyzers.annual_return.get_analysis()
    calmar_analysis  = strat.analyzers.calmar.get_analysis()
    sqn_analysis     = strat.analyzers.sqn.get_analysis()
    time_return      = strat.analyzers.time_return.get_analysis()

    sharpe       = sharpe_analysis.get('sharperatio', None) or 0.0
    max_dd       = dd_analysis.get('max', {}).get('drawdown', 0.0) / 100  # convert % to decimal
    max_dd_len   = dd_analysis.get('max', {}).get('len', 0)
    avg_dd       = dd_analysis.get('average', {}).get('drawdown', 0.0) / 100
    ann_return   = returns_analysis.get('rnorm', 0.0)
    calmar       = calmar_analysis.get('calmar', None) or 0.0
    sqn          = sqn_analysis.get('sqn', 0.0) or 0.0

    # ── Trade statistics ─────────────────────────────────────────────────
    total_trades  = trade_analysis.get('total', {}).get('total', 0)
    won_trades    = trade_analysis.get('won', {}).get('total', 0)
    lost_trades   = trade_analysis.get('lost', {}).get('total', 0)
    win_rate      = won_trades / total_trades if total_trades > 0 else 0.0
    avg_win       = trade_analysis.get('won', {}).get('pnl', {}).get('average', 0.0) or 0.0
    avg_loss      = abs(trade_analysis.get('lost', {}).get('pnl', {}).get('average', 0.0) or 0.0)
    gross_profit  = trade_analysis.get('won', {}).get('pnl', {}).get('total', 0.0) or 0.0
    gross_loss    = abs(trade_analysis.get('lost', {}).get('pnl', {}).get('total', 0.0) or 0.0)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    expectancy    = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

    # ── Daily return series for supplementary metrics ─────────────────────
    if time_return:
        daily_ret = pd.Series(time_return).sort_index()
    else:
        daily_ret = pd.Series(dtype=float)

    # Sortino ratio (downside deviation)
    if len(daily_ret) > 10:
        downside = daily_ret[daily_ret < 0]
        downside_std = downside.std() if len(downside) > 0 else 1e-8
        sortino = (daily_ret.mean() / (downside_std + 1e-8)) * np.sqrt(252)
    else:
        sortino = 0.0

    # Buy & Hold return
    bnh_ret = (oos_df['WTI_Close'].iloc[-1] / oos_df['WTI_Close'].iloc[0]) - 1

    # Annual volatility
    annual_vol = daily_ret.std() * np.sqrt(252) if len(daily_ret) > 10 else 0.0

    # Omega ratio
    if len(daily_ret) > 10:
        gains  = daily_ret[daily_ret > 0].sum()
        losses = abs(daily_ret[daily_ret < 0].sum())
        omega  = gains / losses if losses > 0 else float('inf')
    else:
        omega = 0.0

    # Tail ratio
    if len(daily_ret) > 30:
        p95 = daily_ret.quantile(0.95)
        p05 = abs(daily_ret.quantile(0.05))
        tail_ratio = p95 / p05 if p05 > 0 else float('inf')
    else:
        tail_ratio = 0.0

    # SQN grading
    sqn_grade = (
        'WORLD CLASS' if sqn > 7 else
        'EXCELLENT'   if sqn > 5 else
        'VERY GOOD'   if sqn > 3 else
        'GOOD'        if sqn > 2 else
        'AVERAGE'     if sqn > 1 else
        'BAD'
    )

    # Annual returns dict
    annual_rets = {str(yr): ret * 100 for yr, ret in annual_analysis.items()}

    return {
        # Core
        'initial_cash':   INITIAL_CASH,
        'final_value':    final_value,
        'total_return':   total_return,
        'bnh_return':     bnh_ret,
        'alpha':          total_return - bnh_ret,
        'annual_return':  ann_return,
        'annual_vol':     annual_vol,
        # Risk-adjusted
        'sharpe':         sharpe,
        'sortino':        sortino,
        'calmar':         calmar,
        'omega':          omega,
        'tail_ratio':     tail_ratio,
        'sqn':            sqn,
        'sqn_grade':      sqn_grade,
        # Drawdown
        'max_drawdown':   -abs(max_dd),
        'max_dd_len':     max_dd_len,
        'avg_drawdown':   -abs(avg_dd),
        # Trades
        'total_trades':   total_trades,
        'won_trades':     won_trades,
        'lost_trades':    lost_trades,
        'win_rate':       win_rate,
        'profit_factor':  profit_factor,
        'expectancy':     expectancy,
        'avg_win':        avg_win,
        'avg_loss':       avg_loss,
        'gross_profit':   gross_profit,
        'gross_loss':     gross_loss,
        # Annual
        'annual_rets':    annual_rets,
        # Series
        'daily_ret':      daily_ret,
    }


# ═════════════════════════════════════════════════════════════════════════════
# STEP 7: EQUITY CURVE + DRAWDOWN SERIES (from OOS DataFrame)
# ═════════════════════════════════════════════════════════════════════════════

def build_equity_curves(oos_df: pd.DataFrame) -> dict:
    """
    Build equity curve and drawdown series from the OOS signal DataFrame.
    This gives us daily granularity for plotting.
    """
    df = oos_df.copy()

    df['Daily_Return']       = df['WTI_Close'].pct_change()
    df['Strategy_Return']    = df['Signal'].shift(1) * df['Daily_Return']
    df['Strategy_Return']    = df['Strategy_Return'].fillna(0)
    df['BnH_Return']         = df['Daily_Return'].fillna(0)
    df['Strat_Cumulative']   = (1 + df['Strategy_Return']).cumprod() * INITIAL_CASH
    df['BnH_Cumulative']     = (1 + df['BnH_Return']).cumprod() * INITIAL_CASH

    # Drawdown
    cum_max = df['Strat_Cumulative'].cummax()
    df['Drawdown_Pct'] = (df['Strat_Cumulative'] - cum_max) / cum_max * 100

    # Rolling Sharpe
    df['Rolling_Sharpe'] = (
        df['Strategy_Return'].rolling(60).mean() /
        (df['Strategy_Return'].rolling(60).std() + 1e-8)
    ) * np.sqrt(252)

    return df


# ═════════════════════════════════════════════════════════════════════════════
# STEP 8: RICH MATPLOTLIB DASHBOARD
# ═════════════════════════════════════════════════════════════════════════════

def plot_dashboard(metrics: dict, equity_df: pd.DataFrame,
                   oos_df: pd.DataFrame, output_path: str):
    """
    Generate a premium 6-panel matplotlib dashboard and save to PNG.

    Panels:
    -------
    1. WTI Price + Regime Shading + Buy/Sell Markers
    2. Equity Curve (Strategy vs Buy & Hold) with drawdown fill
    3. Drawdown (Underwater) chart
    4. Rolling 60-Day Sharpe Ratio
    5. Annual Returns Bar Chart
    6. Performance Metrics Summary Table
    """
    print(f"\n  -- Generating Backtrader Dashboard --")

    # ── Style ────────────────────────────────────────────────────────────
    plt.rcParams.update({
        'figure.facecolor':  '#0f172a',
        'axes.facecolor':    '#1e293b',
        'axes.edgecolor':    '#334155',
        'axes.labelcolor':   '#94a3b8',
        'xtick.color':       '#64748b',
        'ytick.color':       '#64748b',
        'text.color':        '#e2e8f0',
        'grid.color':        '#1e293b',
        'grid.linestyle':    '--',
        'grid.alpha':        0.5,
        'font.family':       'DejaVu Sans',
        'font.size':         9,
    })

    C_CYAN   = '#00d4ff'
    C_GREEN  = '#34d399'
    C_RED    = '#ff6b6b'
    C_AMBER  = '#fbbf24'
    C_PURPLE = '#a78bfa'
    C_WHITE  = '#e2e8f0'
    C_MUTED  = '#64748b'
    C_BULL   = '#34d399'
    C_PANIC  = '#ff6b6b'
    C_CHOPPY = '#fbbf24'

    fig = plt.figure(figsize=(20, 26), facecolor='#0f172a')
    gs  = gridspec.GridSpec(4, 2, figure=fig,
                             hspace=0.38, wspace=0.15,
                             left=0.06, right=0.97,
                             top=0.94, bottom=0.04)

    # ── Main Title ───────────────────────────────────────────────────────
    sr  = metrics['sharpe']
    tr  = metrics['total_return'] * 100
    mdd = metrics['max_drawdown'] * 100
    wr  = metrics['win_rate'] * 100
    title = (
        f"PetroQuant — Backtrader Backtesting Report\n"
        f"Sharpe: {sr:.2f}  |  Total Return: {tr:+.1f}%  |  "
        f"Max Drawdown: {mdd:.1f}%  |  Win Rate: {wr:.0f}%  |  "
        f"SQN: {metrics['sqn']:.2f} ({metrics['sqn_grade']})"
    )
    fig.suptitle(title, fontsize=14, color=C_WHITE, fontweight='bold', y=0.97)

    dates = equity_df.index

    # ─────────────────────────────────────────────────────────────────────
    # PANEL 1: WTI Price + Regime Shading + Signals
    # ─────────────────────────────────────────────────────────────────────
    ax1 = fig.add_subplot(gs[0, :])   # spans full width
    ax1.set_title('(1) WTI Crude Oil Price | Regime Shading | Buy/Sell Signals',
                  color=C_WHITE, fontsize=11, pad=8)

    # Price line
    ax1.plot(dates, equity_df['WTI_Close'], color=C_CYAN, linewidth=1.2, label='WTI Close', zorder=3)

    # Regime shading
    if 'Regime' in equity_df.columns:
        regime_colors_map = {'BULL': C_BULL, 'PANIC': C_PANIC, 'CHOPPY': C_CHOPPY}
        for regime, color in regime_colors_map.items():
            mask = equity_df['Regime'] == regime
            if mask.any():
                ax1.fill_between(dates, equity_df['WTI_Close'].min() * 0.98,
                                 equity_df['WTI_Close'].max() * 1.02,
                                 where=mask, alpha=0.08, color=color, label=f'{regime} regime')

    # Buy/Sell markers
    buys  = equity_df[equity_df['Signal'] == 1]
    sells = equity_df[equity_df['Signal'] == -1]
    if len(buys) > 0:
        ax1.scatter(buys.index, buys['WTI_Close'],
                    marker='^', color=C_GREEN, s=30, zorder=5,
                    label=f'BUY ({len(buys)})', alpha=0.8)
    if len(sells) > 0:
        ax1.scatter(sells.index, sells['WTI_Close'],
                    marker='v', color=C_RED, s=30, zorder=5,
                    label=f'SELL ({len(sells)})', alpha=0.8)

    ax1.set_ylabel('WTI Price (USD)', color=C_MUTED, fontsize=9)
    ax1.legend(loc='upper left', fontsize=8, framealpha=0.3,
               labelcolor=C_WHITE, facecolor='#0f172a')
    ax1.yaxis.set_major_formatter(mtick.FormatStrFormatter('$%.0f'))
    ax1.grid(True, alpha=0.2)

    # ─────────────────────────────────────────────────────────────────────
    # PANEL 2: Equity Curve
    # ─────────────────────────────────────────────────────────────────────
    ax2 = fig.add_subplot(gs[1, 0])
    ax2.set_title('(2) Equity Curve — Strategy vs Buy & Hold',
                  color=C_WHITE, fontsize=11, pad=8)

    strat_curve = equity_df['Strat_Cumulative']
    bnh_curve   = equity_df['BnH_Cumulative']

    ax2.plot(dates, strat_curve, color=C_CYAN, linewidth=2, label='PetroQuant Strategy')
    ax2.plot(dates, bnh_curve,   color=C_MUTED, linewidth=1.5, linestyle='--', label='Buy & Hold')
    ax2.axhline(INITIAL_CASH, color='white', linewidth=0.5, alpha=0.3, linestyle=':')

    # Shade outperformance / underperformance
    ax2.fill_between(dates, strat_curve, bnh_curve,
                     where=(strat_curve >= bnh_curve),
                     alpha=0.15, color=C_GREEN, label='Outperform')
    ax2.fill_between(dates, strat_curve, bnh_curve,
                     where=(strat_curve < bnh_curve),
                     alpha=0.15, color=C_RED, label='Underperform')

    ax2.set_ylabel('Portfolio Value (USD)', color=C_MUTED, fontsize=9)
    ax2.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f'${x:,.0f}'))
    ax2.legend(loc='upper left', fontsize=8, framealpha=0.3,
               labelcolor=C_WHITE, facecolor='#0f172a')
    ax2.grid(True, alpha=0.2)

    # ─────────────────────────────────────────────────────────────────────
    # PANEL 3: Drawdown
    # ─────────────────────────────────────────────────────────────────────
    ax3 = fig.add_subplot(gs[1, 1])
    ax3.set_title('(3) Drawdown — Underwater Chart', color=C_WHITE, fontsize=11, pad=8)

    dd = equity_df['Drawdown_Pct']
    ax3.fill_between(dates, dd, 0, alpha=0.4, color=C_RED)
    ax3.plot(dates, dd, color=C_RED, linewidth=1, alpha=0.8)
    ax3.axhline(0, color=C_MUTED, linewidth=0.5, linestyle='--')
    ax3.axhline(metrics['max_drawdown'] * 100, color=C_AMBER, linewidth=1,
                linestyle=':', alpha=0.7,
                label=f"Max DD: {metrics['max_drawdown']*100:.1f}%")

    ax3.set_ylabel('Drawdown (%)', color=C_MUTED, fontsize=9)
    ax3.yaxis.set_major_formatter(mtick.PercentFormatter())
    ax3.legend(fontsize=8, framealpha=0.3, labelcolor=C_WHITE, facecolor='#0f172a')
    ax3.grid(True, alpha=0.2)

    # ─────────────────────────────────────────────────────────────────────
    # PANEL 4: Rolling 60-Day Sharpe
    # ─────────────────────────────────────────────────────────────────────
    ax4 = fig.add_subplot(gs[2, 0])
    ax4.set_title('(4) Rolling 60-Day Sharpe Ratio', color=C_WHITE, fontsize=11, pad=8)

    rs = equity_df['Rolling_Sharpe'].dropna()
    ax4.plot(rs.index, rs.values, color=C_PURPLE, linewidth=1.5)
    ax4.axhline(0,   color=C_MUTED, linewidth=0.5, linestyle='--', alpha=0.5)
    ax4.axhline(1.0, color=C_GREEN, linewidth=1,   linestyle=':', alpha=0.6,
                label='Sharpe = 1.0 (good)')
    ax4.axhline(-1.0, color=C_RED, linewidth=1,   linestyle=':', alpha=0.6,
                label='Sharpe = -1.0')
    ax4.fill_between(rs.index, rs.values, 0,
                     where=(rs.values >= 0), alpha=0.15, color=C_GREEN)
    ax4.fill_between(rs.index, rs.values, 0,
                     where=(rs.values < 0),  alpha=0.15, color=C_RED)

    ax4.set_ylabel('Rolling Sharpe (60D)', color=C_MUTED, fontsize=9)
    ax4.legend(fontsize=8, framealpha=0.3, labelcolor=C_WHITE, facecolor='#0f172a')
    ax4.grid(True, alpha=0.2)

    # ─────────────────────────────────────────────────────────────────────
    # PANEL 5: Annual Returns
    # ─────────────────────────────────────────────────────────────────────
    ax5 = fig.add_subplot(gs[2, 1])
    ax5.set_title('(5) Annual Returns — Strategy', color=C_WHITE, fontsize=11, pad=8)

    annual = metrics['annual_rets']
    if annual:
        years  = sorted(annual.keys())
        values = [annual[y] for y in years]
        colors = [C_GREEN if v >= 0 else C_RED for v in values]
        bars   = ax5.bar(years, values, color=colors, alpha=0.85, edgecolor='#334155')
        ax5.axhline(0, color=C_MUTED, linewidth=0.8, linestyle='--')

        # Labels on bars
        for bar, val in zip(bars, values):
            ax5.text(bar.get_x() + bar.get_width() / 2,
                     bar.get_height() + (0.3 if val >= 0 else -1.0),
                     f'{val:+.1f}%', ha='center', va='bottom',
                     fontsize=8, color=C_WHITE)
    else:
        ax5.text(0.5, 0.5, 'No annual data', ha='center', va='center',
                 transform=ax5.transAxes, color=C_MUTED)

    ax5.set_ylabel('Annual Return (%)', color=C_MUTED, fontsize=9)
    ax5.yaxis.set_major_formatter(mtick.PercentFormatter())
    ax5.grid(True, alpha=0.2, axis='y')
    ax5.tick_params(axis='x', rotation=45)

    # ─────────────────────────────────────────────────────────────────────
    # PANEL 6: Performance Metrics Table
    # ─────────────────────────────────────────────────────────────────────
    ax6 = fig.add_subplot(gs[3, :])   # full width
    ax6.set_facecolor('#0f172a')
    ax6.axis('off')
    ax6.set_title('(6) Backtrader Performance Tearsheet',
                  color=C_WHITE, fontsize=11, pad=8)

    def _grade(val, good, ok, reverse=False):
        if reverse:
            return '★★★' if val <= good else '★★' if val <= ok else '★'
        return '★★★' if val >= good else '★★' if val >= ok else '★'

    m = metrics
    rows = [
        # Category, Metric, Value, Grade, Description
        ('RETURNS',  'Total Return',       f"{m['total_return']*100:+.2f}%",
         _grade(m['total_return']*100, 50, 20),
         'Total profit/loss from strategy'),

        ('RETURNS',  'Buy & Hold Return',  f"{m['bnh_return']*100:+.2f}%",
         '',
         'WTI passive holding benchmark'),

        ('RETURNS',  'Alpha (vs B&H)',     f"{m['alpha']*100:+.2f}%",
         _grade(m['alpha']*100, 20, 5),
         'Excess return above passive holding'),

        ('RETURNS',  'Annual Return',      f"{m['annual_return']*100:+.2f}%",
         _grade(m['annual_return']*100, 20, 10),
         'Compound annual growth rate'),

        ('RETURNS',  'Annual Volatility',  f"{m['annual_vol']*100:.2f}%",
         _grade(m['annual_vol']*100, 15, 25, reverse=True),
         'Annualized standard deviation of returns'),

        ('RISK',     'Sharpe Ratio',       f"{m['sharpe']:.3f}",
         _grade(m['sharpe'], 1.5, 1.0),
         'Return per unit risk (>1.0 is good)'),

        ('RISK',     'Sortino Ratio',      f"{m['sortino']:.3f}",
         _grade(m['sortino'], 2.0, 1.0),
         'Return per unit downside risk only'),

        ('RISK',     'Calmar Ratio',       f"{m['calmar']:.3f}",
         _grade(m['calmar'], 2.0, 1.0),
         'Annual return / max drawdown'),

        ('RISK',     'Omega Ratio',        f"{m['omega']:.3f}",
         _grade(m['omega'], 1.5, 1.2),
         'Weighted gains / weighted losses (>1.5 strong)'),

        ('RISK',     'SQN Score',          f"{m['sqn']:.2f} ({m['sqn_grade']})",
         _grade(m['sqn'], 3.0, 2.0),
         'System Quality Number: >3=Very Good, >5=Excellent'),

        ('DRAWDOWN', 'Max Drawdown',       f"{m['max_drawdown']*100:.2f}%",
         _grade(abs(m['max_drawdown']*100), 10, 20, reverse=True),
         'Worst peak-to-trough portfolio decline'),

        ('DRAWDOWN', 'Max DD Duration',    f"{m['max_dd_len']} bars",
         '',
         'Number of bars spent in worst drawdown'),

        ('DRAWDOWN', 'Avg Drawdown',       f"{m['avg_drawdown']*100:.2f}%",
         _grade(abs(m['avg_drawdown']*100), 3, 8, reverse=True),
         'Average depth of all drawdowns'),

        ('TRADES',   'Total Trades',       f"{m['total_trades']}",
         '',
         'Number of completed round-trip trades'),

        ('TRADES',   'Win Rate',           f"{m['win_rate']*100:.1f}%",
         _grade(m['win_rate']*100, 55, 50),
         '% of trades that were profitable'),

        ('TRADES',   'Profit Factor',      f"{m['profit_factor']:.3f}",
         _grade(m['profit_factor'], 1.5, 1.2),
         'Gross profit / gross loss (>1.5 robust)'),

        ('TRADES',   'Expectancy',         f"${m['expectancy']:+.2f}",
         _grade(m['expectancy'], 50, 10),
         'Expected dollar profit per trade'),

        ('TRADES',   'Avg Win',            f"${m['avg_win']:+.2f}",
         '',
         'Average profit on winning trades'),

        ('TRADES',   'Avg Loss',           f"-${m['avg_loss']:.2f}",
         '',
         'Average loss on losing trades'),

        ('TRADES',   'Tail Ratio',         f"{m['tail_ratio']:.3f}",
         _grade(m['tail_ratio'], 1.2, 1.0),
         'P95 return / P05 loss (>1 = big wins > big losses)'),
    ]

    # Layout table in 2 columns of 10 rows each
    col_items = 10
    n_rows = col_items
    cell_h = 0.045
    cell_w = 0.5
    col_offsets = [0.0, 0.5]

    cat_colors = {
        'RETURNS':  '#1e3a5f',
        'RISK':     '#2d1e3a',
        'DRAWDOWN': '#3a1e1e',
        'TRADES':   '#1e3a2d',
    }
    grade_color_map = {'★★★': C_GREEN, '★★': C_AMBER, '★': C_RED, '': C_MUTED}
    prev_cat = ['', '']

    for i, row in enumerate(rows):
        col = i // col_items
        r   = i %  col_items
        x_off = col_offsets[col]
        cat, name, val, grade, desc = row

        y_top = 0.96 - r * cell_h

        # Category header if changed
        if cat != prev_cat[col]:
            rect = FancyBboxPatch(
                (x_off + 0.005, y_top),
                cell_w - 0.01, cell_h * 0.85,
                boxstyle='round,pad=0.002',
                transform=ax6.transAxes,
                facecolor=cat_colors.get(cat, '#1e293b'),
                edgecolor='#334155', linewidth=0.5,
                zorder=1,
            )
            ax6.add_patch(rect)
            ax6.text(x_off + 0.01, y_top + cell_h * 0.3,
                     f'▶ {cat}',
                     transform=ax6.transAxes,
                     fontsize=8.5, fontweight='bold',
                     color=C_WHITE, zorder=2)
            prev_cat[col] = cat
            y_top -= cell_h * 0.95

        # Row background
        bg_color = '#1a2535' if r % 2 == 0 else '#1e293b'
        rect2 = FancyBboxPatch(
            (x_off + 0.005, y_top - cell_h * 0.1),
            cell_w - 0.01, cell_h * 0.85,
            boxstyle='round,pad=0.002',
            transform=ax6.transAxes,
            facecolor=bg_color, edgecolor='#2d3f55', linewidth=0.3,
            zorder=1,
        )
        ax6.add_patch(rect2)

        # Metric name
        ax6.text(x_off + 0.015, y_top + cell_h * 0.22,
                 name, transform=ax6.transAxes,
                 fontsize=8.5, color=C_WHITE, zorder=2)

        # Value with color coding
        val_num = None
        try:
            cleaned = val.replace('%', '').replace('+', '').replace('$', '').replace(',', '').split()[0]
            val_num = float(cleaned)
        except Exception:
            pass

        if val_num is not None:
            if 'Drawdown' in name or 'Loss' in name or 'Avg Loss' in name:
                val_color = C_RED
            elif val_num > 0:
                val_color = C_GREEN
            elif val_num < 0:
                val_color = C_RED
            else:
                val_color = C_MUTED
        else:
            val_color = C_CYAN

        ax6.text(x_off + 0.21, y_top + cell_h * 0.22,
                 val, transform=ax6.transAxes,
                 fontsize=8.5, color=val_color, fontweight='bold', zorder=2)

        # Grade stars
        if grade:
            ax6.text(x_off + 0.30, y_top + cell_h * 0.22,
                     grade, transform=ax6.transAxes,
                     fontsize=8, color=grade_color_map.get(grade, C_MUTED), zorder=2)

        # Description
        ax6.text(x_off + 0.345, y_top + cell_h * 0.22,
                 desc, transform=ax6.transAxes,
                 fontsize=7.5, color=C_MUTED, zorder=2)

    ax6.set_xlim(0, 1)
    ax6.set_ylim(0, 1)

    # Footer
    footer = (
        f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"Engine: Backtrader v1.9.78 | "
        f"Commission: {COMMISSION*100:.2f}% | "
        f"Slippage: {SLIPPAGE_PERC*100:.3f}% | "
        f"Initial Capital: ${INITIAL_CASH:,} | "
        f"Strategy: HMM + XGBoost Regime-Aware"
    )
    fig.text(0.5, 0.01, footer, ha='center', va='bottom',
             fontsize=7.5, color=C_MUTED, style='italic')

    # ── Save ─────────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight',
                facecolor='#0f172a', edgecolor='none')
    plt.close()
    print(f"    [OK] Dashboard saved: {output_path}")


# ═════════════════════════════════════════════════════════════════════════════
# STEP 9: PRETTY-PRINT TEARSHEET
# ═════════════════════════════════════════════════════════════════════════════

def print_tearsheet(metrics: dict):
    """Print a detailed, formatted tearsheet to stdout."""

    def _grade(val, thresholds, reverse=False):
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

    W  = 96
    HR = '─' * W
    m  = metrics

    print()
    print(f"  ╔{'═'*W}╗")
    print(f"  ║{'PETROQUANT — BACKTRADER PERFORMANCE TEARSHEET':^{W}}║")
    print(f"  ║{'Framework: Backtrader 1.9.78 | Event-Driven Simulation':^{W}}║")
    print(f"  ╠{'═'*W}╣")

    def row(label, val, grade, desc):
        print(f"  ║  {label:<28} {val:>14}   {grade:<6} {desc:<{W-54}}║")

    # ── RETURNS ──
    print(f"  ║{'':^{W}}║")
    print(f"  ║  ► RETURNS & ACTIVITY{'':<{W-23}}║")
    print(f"  ╠{HR}╣")
    row('Total Strategy Return',  f"{m['total_return']*100:+.2f}%",
        _grade(m['total_return']*100, (50, 20, 0)),
        'Total profit from strategy start to end')
    row('Buy & Hold Return',      f"{m['bnh_return']*100:+.2f}%",
        '',
        'Passive WTI holding — the benchmark to beat')
    row('Alpha (Strategy−B&H)',   f"{m['alpha']*100:+.2f}%",
        _grade(m['alpha']*100, (20, 5, 0)),
        'Extra profit generated over passive holding')
    row('Annualized Return',      f"{m['annual_return']*100:+.2f}%",
        _grade(m['annual_return']*100, (20, 10, 0)),
        'Average yearly return (CAGR), compounded')
    row('Annualized Volatility',  f"{m['annual_vol']*100:.2f}%",
        _grade(m['annual_vol']*100, (15, 25, 35), reverse=True),
        'Standard deviation of returns × √252')
    row('Total Trades',           f"{m['total_trades']}",
        '',
        'Completed round-trips (buy+sell pairs)')
    row('Initial Capital',        f"${m['initial_cash']:,.0f}",
        '',
        f"Commission: {COMMISSION*100:.2f}% | Slippage: {SLIPPAGE_PERC*100:.3f}%")
    row('Final Portfolio Value',  f"${m['final_value']:,.2f}",
        '',
        'After all commissions and slippage costs')

    # ── RISK-ADJUSTED ──
    print(f"  ║{'':^{W}}║")
    print(f"  ║  ► RISK-ADJUSTED RATIOS{'':<{W-25}}║")
    print(f"  ╠{HR}╣")
    row('Sharpe Ratio',           f"{m['sharpe']:.4f}",
        _grade(m['sharpe'], (1.5, 1.0, 0.5)),
        'Return / total vol (4% risk-free rate, annualized)')
    row('Sortino Ratio',          f"{m['sortino']:.4f}",
        _grade(m['sortino'], (2.0, 1.5, 0.5)),
        'Like Sharpe but only penalizes DOWNSIDE volatility')
    row('Calmar Ratio',           f"{m['calmar']:.4f}",
        _grade(m['calmar'], (2.0, 1.0, 0.5)),
        'Annual return / max drawdown (>1.0 = good recovery)')
    row('Omega Ratio',            f"{m['omega']:.4f}",
        _grade(m['omega'], (1.5, 1.2, 1.0)),
        'Prob-weighted gains / losses at threshold=0')
    row('Tail Ratio',             f"{m['tail_ratio']:.4f}",
        _grade(m['tail_ratio'], (1.2, 1.0, 0.8)),
        'P95 daily gain / P05 daily loss (>1 = fat right tail)')
    row('SQN Score',              f"{m['sqn']:.3f} — {m['sqn_grade']}",
        _grade(m['sqn'], (3.0, 2.0, 1.0)),
        '>5=Excellent >3=Very Good >2=Good >1=Average')

    # ── DRAWDOWN ──
    print(f"  ║{'':^{W}}║")
    print(f"  ║  ► DRAWDOWN & PAIN{'':<{W-20}}║")
    print(f"  ╠{HR}╣")
    row('Max Drawdown',           f"{m['max_drawdown']*100:.2f}%",
        _grade(abs(m['max_drawdown']*100), (10, 20, 30), reverse=True),
        'Worst peak-to-trough decline (lower is better)')
    row('Max DD Duration',        f"{m['max_dd_len']} trading bars",
        '',
        'How long the strategy stayed in its worst drawdown')
    row('Average Drawdown',       f"{m['avg_drawdown']*100:.2f}%",
        _grade(abs(m['avg_drawdown']*100), (3, 8, 15), reverse=True),
        'Typical drawdown depth across all underwater periods')

    # ── TRADES ──
    print(f"  ║{'':^{W}}║")
    print(f"  ║  ► TRADE EFFICIENCY{'':<{W-21}}║")
    print(f"  ╠{HR}╣")
    row('Win Rate',               f"{m['win_rate']*100:.1f}%",
        _grade(m['win_rate']*100, (55, 50, 45)),
        '% of trades closed at profit')
    row('Profit Factor',          f"{m['profit_factor']:.4f}",
        _grade(m['profit_factor'], (1.5, 1.2, 1.0)),
        'Gross profit / gross loss (>1.5 = robust edge)')
    row('Expectancy',             f"${m['expectancy']:+.2f} per trade",
        _grade(m['expectancy'], (50, 10, 0)),
        'Expected dollar P&L per trade after commission')
    row('Average Win',            f"${m['avg_win']:+.2f}",
        '',
        'Average profit on winning trades')
    row('Average Loss',           f"-${m['avg_loss']:.2f}",
        '',
        'Average loss on losing trades')
    row('Gross Profit',           f"${m['gross_profit']:+.2f}",
        '',
        'Total profit from all winning trades')
    row('Gross Loss',             f"-${m['gross_loss']:.2f}",
        '',
        'Total loss from all losing trades')

    print(f"  ║{'':^{W}}║")
    print(f"  ╚{'═'*W}╝")

    # Annual returns
    if metrics['annual_rets']:
        print(f"\n  ► ANNUAL RETURNS\n  {'─'*60}")
        for yr, ret in sorted(metrics['annual_rets'].items()):
            bar_len = int(abs(ret) / 2)
            bar = ('█' * bar_len) if ret >= 0 else ('▓' * bar_len)
            sign = '+' if ret >= 0 else ''
            print(f"    {yr}  {sign}{ret:5.1f}%  {'[' + bar + ']'}")

    print(f"\n  * Grades: [A+]=Excellent [B ]=Good [C ]=Fair [D-]=Needs Work")
    print(f"    Backtrader: event-driven, bar-by-bar simulation with real broker logic.")
    print(f"    Commission: {COMMISSION*100:.2f}% per trade | Slippage: {SLIPPAGE_PERC*100:.3f}% per fill")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    print()
    print("=" * 72)
    print("  PETROQUANT — BACKTRADER BACKTESTING ENGINE")
    print("=" * 72)
    print("  Framework: Backtrader 1.9.78 (event-driven, bar-by-bar simulation)")
    print("  Strategy:  HMM Regime Detection + Walk-Forward XGBoost")
    print()

    # ── Step 1: Load Data ─────────────────────────────────────────────────
    print("  [1/6] Loading market data from pipeline...")
    master_df = build_master_df(force_refresh=False)
    if master_df is None or len(master_df) == 0:
        print("  [FAIL] No data. Run oil_data_pipeline_new.py first.")
        return
    print(f"  [OK]  {len(master_df)} rows | "
          f"{master_df.index.min().date()} → {master_df.index.max().date()}")

    # ── Step 2: Run PetroQuant Strategy ──────────────────────────────────
    print("\n  [2/6] Running HMM + XGBoost strategy pipeline...")
    strategy = HMMXGBoostStrategy(fwd_days=FWD_DAYS)
    result_df = strategy.run(master_df)
    print(f"  [OK]  Strategy complete. {len(result_df)} rows with signals.")

    # ── Step 3: Prepare Backtrader Feed ──────────────────────────────────
    print("\n  [3/6] Preparing Backtrader data feed...")
    bt_df = prepare_backtrader_feed(result_df)

    # ── Step 4: Run Cerebro ───────────────────────────────────────────────
    print("\n  [4/6] Running Backtrader Cerebro...")
    run_result = run_cerebro(bt_df)

    # ── Step 5: Extract Metrics ───────────────────────────────────────────
    print("\n  [5/6] Extracting performance metrics...")
    # Use OOS period (where signals exist) for supplementary calcs
    oos_mask = result_df['Probability'].notna()
    oos_df   = result_df[oos_mask].copy()
    equity_df = build_equity_curves(oos_df)
    metrics   = extract_metrics(run_result, oos_df)

    # ── Step 6: Output ────────────────────────────────────────────────────
    print("\n  [6/6] Generating outputs...")

    # Save chart
    output_dir  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
    chart_path  = os.path.join(output_dir, 'backtrader_dashboard.png')
    plot_dashboard(metrics, equity_df, oos_df, chart_path)

    # Print tearsheet
    print_tearsheet(metrics)

    # Summary
    print()
    print("=" * 72)
    print("  [COMPLETE] BACKTRADER BACKTEST DONE")
    print(f"  Chart saved to: output/backtrader_dashboard.png")
    print("=" * 72)
    print()


if __name__ == '__main__':
    main()
