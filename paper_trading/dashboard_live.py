# ============================================================================
# PETROQUANT PAPER TRADING — LIVE DASHBOARD (multi-strategy v2.0)
# ============================================================================
# Shows all 4 strategies simultaneously:
#   - Strategy cards: equity, return, signal, position, win rate (per TF)
#   - Combined equity curves chart: all 4 TFs on one Plotly chart
#   - Tab-based detail view: metrics + trade table per TF
#   - Header: WTI price, regime, uptime, time
#
# No /set-timeframe button — all TFs run in parallel.
# ============================================================================

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
import logging
import os

from . import config as cfg

logger = logging.getLogger(__name__)

# ── Color Palette ─────────────────────────────────────────────────────────────
COLORS = {
    'bg'           : '#0d1117',
    'bg_panel'     : '#161b22',
    'bg_card'      : '#1c2128',
    'accent_green' : '#2ea043',
    'accent_red'   : '#f85149',
    'accent_blue'  : '#388bfd',
    'accent_gold'  : '#d29922',
    'accent_purple': '#a5a5f5',
    'text_primary' : '#e6edf3',
    'text_muted'   : '#8b949e',
    'grid'         : '#21262d',
}

# Per-TF color scheme for the combined equity chart
TF_COLORS = {
    '5m' : '#388bfd',   # blue
    '15m': '#2ea043',   # green
    '1h' : '#d29922',   # gold
    '1d' : '#a5a5f5',   # purple
}


class LiveDashboard:
    """
    Multi-strategy live dashboard for PetroQuant.

    Receives all strategies' status from MultiStrategyEngine and renders
    a single HTML page showing all 4 TF strategies simultaneously.
    """

    def __init__(self, engine=None):
        """
        Parameters
        ----------
        engine : MultiStrategyEngine — reference for querying live data
        """
        self.engine = engine

    # ── Main Render ───────────────────────────────────────────────────────────
    def render(self, all_strategies: dict = None,
               current_price: float = None,
               regime: str = 'UNKNOWN') -> str:
        """
        Build and return the full dashboard HTML.

        Parameters
        ----------
        all_strategies : dict  — result of MultiStrategyEngine.get_all_status()
        current_price  : float — latest WTI price (for header display)
        regime         : str   — current HMM regime (BULL/CHOPPY/PANIC)
        """
        if all_strategies is None:
            all_strategies = (
                self.engine.get_all_status() if self.engine else {}
            )

        # Pull snapshots + trades from each runner's DB for the chart
        all_snapshots: dict[str, pd.DataFrame] = {}
        all_trades:    dict[str, pd.DataFrame] = {}
        if self.engine:
            for tf, runner in self.engine.strategies.items():
                all_snapshots[tf] = runner.trade_log.get_snapshots(limit=2000)
                all_trades[tf]    = runner.trade_log.get_trade_history(limit=200)

        # Build the combined equity chart
        equity_chart_html = self._build_combined_equity_chart(
            all_snapshots, all_strategies
        )

        # Build per-TF tab content (metrics + trade table)
        tabs_html = self._build_strategy_tabs(all_strategies, all_trades)

        # Build strategy overview cards (4 across the top)
        cards_html = self._build_strategy_cards(all_strategies, current_price)

        return self._wrap_html(
            equity_chart_html = equity_chart_html,
            tabs_html         = tabs_html,
            cards_html        = cards_html,
            all_strategies    = all_strategies,
            regime            = regime,
            current_price     = current_price,
        )

    def save(self, html: str, path: str = cfg.DASHBOARD_PATH) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
        logger.info(f"[Dashboard] Saved to {path}")
        return path

    # ── Combined Equity Chart ─────────────────────────────────────────────────
    def _build_combined_equity_chart(self,
                                     all_snapshots: dict,
                                     all_strategies: dict) -> str:
        fig = go.Figure()

        has_data = False
        for tf, snaps in all_snapshots.items():
            if snaps.empty or 'equity' not in snaps.columns:
                continue
            has_data = True
            label = all_strategies.get(tf, {}).get('label', tf)
            color = TF_COLORS.get(tf, '#8b949e')
            fig.add_trace(go.Scatter(
                x    = snaps['timestamp'],
                y    = snaps['equity'],
                name = f'{label} ({tf})',
                line = dict(color=color, width=2),
                mode = 'lines',
            ))

        if not has_data:
            fig.add_annotation(
                text="No equity data yet — strategies are warming up...",
                xref="paper", yref="paper",
                x=0.5, y=0.5, showarrow=False,
                font=dict(size=14, color=COLORS['text_muted']),
            )

        # $1M reference line
        fig.add_hline(
            y=cfg.INITIAL_CAPITAL,
            line=dict(color='#30363d', width=1, dash='dash'),
            annotation_text='Initial Capital',
            annotation_font_color=COLORS['text_muted'],
        )

        fig.update_layout(
            title=dict(
                text='All Strategies — Portfolio Equity Curves',
                font=dict(size=13, color=COLORS['text_primary']),
                x=0.01,
            ),
            paper_bgcolor=COLORS['bg'],
            plot_bgcolor=COLORS['bg_panel'],
            font=dict(family='Inter, system-ui, sans-serif',
                      size=11, color=COLORS['text_primary']),
            height=340,
            margin=dict(l=60, r=30, t=40, b=40),
            legend=dict(
                bgcolor='rgba(0,0,0,0)',
                font=dict(size=11),
                orientation='h',
                yanchor='bottom', y=1.02,
                xanchor='left',   x=0,
            ),
            xaxis=dict(gridcolor=COLORS['grid'], zerolinecolor=COLORS['grid']),
            yaxis=dict(
                gridcolor=COLORS['grid'],
                zerolinecolor=COLORS['grid'],
                tickformat='$,.0f',
            ),
        )
        return fig.to_html(full_html=False, include_plotlyjs='cdn')

    # ── Strategy Overview Cards ───────────────────────────────────────────────
    def _build_strategy_cards(self, all_strategies: dict,
                               current_price: float = None) -> str:
        cards = []
        for tf, st in all_strategies.items():
            port    = st.get('portfolio', {})
            equity  = port.get('equity', cfg.INITIAL_CAPITAL)
            ret_pct = port.get('total_return_pct', 0.0)
            trades  = port.get('total_trades', 0)
            win_rt  = port.get('win_rate_pct', 0.0)
            pos     = port.get('open_position')
            signal  = st.get('last_signal', 'HOLD')
            prob    = st.get('last_prob', 0.5)
            running = st.get('running', False)
            label   = st.get('label', tf)
            color   = TF_COLORS.get(tf, '#8b949e')

            ret_color  = COLORS['accent_green'] if ret_pct >= 0 else COLORS['accent_red']
            sig_color  = (COLORS['accent_green'] if signal == 'BUY'
                          else COLORS['accent_red'] if signal == 'SELL'
                          else COLORS['text_muted'])
            run_dot    = f'<span style="color:{"#2ea043" if running else "#f85149"};font-size:8px">●</span>'
            pos_text   = ('FLAT' if not pos
                          else f'<span style="color:{"#2ea043" if pos.get("side")=="LONG" else "#f85149"}">'
                               f'{pos.get("side","?")}</span>')

            cards.append(f"""
  <div class="strategy-card" onclick="switchTab('{tf}')"
       style="border-left:3px solid {color};cursor:pointer"
       id="card-{tf}">
    <div class="sc-header">
      {run_dot}
      <span class="sc-tf" style="color:{color}">{label}</span>
      <span class="sc-badge">{tf}</span>
    </div>
    <div class="sc-equity">${equity:,.2f}</div>
    <div class="sc-return" style="color:{ret_color}">{ret_pct:+.3f}%</div>
    <div class="sc-row">
      <span class="sc-kv"><span class="k">Signal</span>
        <span style="color:{sig_color};font-weight:600">{signal}</span>
        <span class="k">({prob:.0%})</span></span>
      <span class="sc-kv"><span class="k">Pos</span>{pos_text}</span>
    </div>
    <div class="sc-row">
      <span class="sc-kv"><span class="k">Trades</span>{trades}</span>
      <span class="sc-kv"><span class="k">Win%</span>{win_rt:.1f}%</span>
    </div>
  </div>""")

        return '\n'.join(cards)

    # ── Per-TF Strategy Tabs ──────────────────────────────────────────────────
    def _build_strategy_tabs(self, all_strategies: dict,
                              all_trades: dict) -> str:
        tab_buttons = []
        tab_panels  = []
        first = True

        for tf, st in all_strategies.items():
            label  = st.get('label', tf)
            color  = TF_COLORS.get(tf, '#8b949e')
            active = 'active' if first else ''
            first  = False

            tab_buttons.append(
                f'<button class="tab-btn {active}" '
                f'onclick="switchTab(\'{tf}\')" '
                f'id="tabBtn-{tf}" '
                f'style="--tc:{color}">'
                f'{label}</button>'
            )

            port    = st.get('portfolio', {})
            summary = st.get('summary', {})
            model   = st.get('model', {})
            horizon = st.get('horizon', 1)
            interval= st.get('interval', tf)
            loop    = st.get('loop_secs', 0)

            equity     = port.get('equity', cfg.INITIAL_CAPITAL)
            ret_pct    = port.get('total_return_pct', 0.0)
            real_pnl   = port.get('realized_pnl', 0.0)
            unreal_pnl = port.get('unrealized_pnl', 0.0)
            commission = port.get('total_commission', 0.0)
            drawdown   = port.get('max_drawdown_pct', summary.get('max_drawdown_pct', 0.0))
            win_rt     = port.get('win_rate_pct', summary.get('win_rate_pct', 0.0))
            n_trades   = port.get('total_trades', 0)
            w_trades   = port.get('winning_trades', 0)
            l_trades   = port.get('losing_trades', 0)
            pos        = port.get('open_position')
            regime     = st.get('regime', 'UNKNOWN')
            r_mult     = st.get('regime_mult', 1.0)

            ret_color  = COLORS['accent_green'] if ret_pct >= 0 else COLORS['accent_red']
            regime_map = {'BULL': '#2ea043', 'CHOPPY': '#d29922', 'PANIC': '#f85149'}
            r_color    = regime_map.get(regime, '#8b949e')

            model_trained = model.get('trained', False)
            model_acc     = model.get('val_accuracy', 0.0) or 0.0
            model_retrain = model.get('retrain_due_in_mins', 0)
            acc_color     = ('#2ea043' if model_acc > 0.55
                             else '#d29922' if model_acc > 0.52
                             else '#f85149')

            if pos:
                side   = pos.get('side', '?')
                qty    = pos.get('qty', 0)
                entry  = pos.get('entry_price', 0)
                unreal = pos.get('unrealized_pnl', 0)
                uc     = COLORS['accent_green'] if unreal >= 0 else COLORS['accent_red']
                sc     = COLORS['accent_green'] if side == 'LONG' else COLORS['accent_red']
                pos_html = (f'<span style="color:{sc};font-weight:700">{side}</span> '
                            f'{qty:.2f} units @ ${entry:.4f} | '
                            f'<span style="color:{uc}">Unrealized: ${unreal:+.2f}</span>')
            else:
                pos_html = '<span style="color:#8b949e">No open position (FLAT)</span>'

            trades_df = all_trades.get(tf, pd.DataFrame())
            table_html = self._build_trade_table(trades_df)

            # Prediction info
            pred_mins = horizon * {'5m':5,'15m':15,'1h':60,'1d':1440}.get(tf, 1)
            pred_label = (f'{pred_mins} min' if pred_mins < 60
                          else f'{pred_mins//60} hr{"s" if pred_mins//60>1 else ""}')

            tab_panels.append(f"""
  <div class="tab-panel {active}" id="tab-{tf}">
    <div class="detail-grid">
      <!-- Strategy Info -->
      <div class="dcard">
        <div class="dcard-title" style="color:{color}">Strategy Settings</div>
        <div class="dkv"><span>Candle Interval</span><strong>{interval}</strong></div>
        <div class="dkv"><span>Loop Cadence</span><strong>Every {loop}s</strong></div>
        <div class="dkv"><span>Predict Horizon</span><strong>{horizon} bars → {pred_label} ahead</strong></div>
        <div class="dkv"><span>Regime</span>
          <strong style="color:{r_color}">{regime} (×{r_mult:.1f})</strong></div>
        <div class="dkv"><span>DB File</span>
          <strong style="color:#8b949e;font-size:11px">paper_trades_{tf}.db</strong></div>
      </div>
      <!-- Portfolio -->
      <div class="dcard">
        <div class="dcard-title">Portfolio</div>
        <div class="dkv"><span>Equity</span><strong>${equity:,.2f}</strong></div>
        <div class="dkv"><span>Return</span>
          <strong style="color:{ret_color}">{ret_pct:+.3f}%</strong></div>
        <div class="dkv"><span>Realized P&L</span>
          <strong style="color:{'#2ea043' if real_pnl>=0 else '#f85149'}">${real_pnl:+,.2f}</strong></div>
        <div class="dkv"><span>Unrealized P&L</span>
          <strong style="color:{'#2ea043' if unreal_pnl>=0 else '#f85149'}">${unreal_pnl:+,.2f}</strong></div>
        <div class="dkv"><span>Commission</span><strong>${commission:,.2f}</strong></div>
        <div class="dkv"><span>Position</span><span>{pos_html}</span></div>
      </div>
      <!-- Performance -->
      <div class="dcard">
        <div class="dcard-title">Performance</div>
        <div class="dkv"><span>Total Trades</span><strong>{n_trades}</strong></div>
        <div class="dkv"><span>Winners</span>
          <strong style="color:#2ea043">{w_trades}</strong></div>
        <div class="dkv"><span>Losers</span>
          <strong style="color:#f85149">{l_trades}</strong></div>
        <div class="dkv"><span>Win Rate</span>
          <strong style="color:{'#2ea043' if win_rt>=50 else '#f85149'}">{win_rt:.1f}%</strong></div>
        <div class="dkv"><span>Max Drawdown</span>
          <strong style="color:#f85149">-{drawdown:.2f}%</strong></div>
      </div>
      <!-- Model -->
      <div class="dcard">
        <div class="dcard-title">XGBoost Model</div>
        {'<div class="dkv"><span>Status</span><strong style="color:#2ea043">Trained</strong></div>' if model_trained else '<div class="dkv"><span>Status</span><strong style="color:#f85149">Not trained yet</strong></div>'}
        {'<div class="dkv"><span>Val Accuracy</span><strong style="color:' + acc_color + '">' + f'{model_acc:.2%}</strong></div>' if model_trained else ''}
        {'<div class="dkv"><span>Retrain In</span><strong>' + f'{model_retrain:.0f}m (or post-trade)</strong></div>' if model_trained else ''}
        {'<div class="dkv"><span>Bars Trained</span><strong>' + str(model.get("n_train_bars", 0)) + '</strong></div>' if model_trained else ''}
        {'<div class="dkv"><span>Features</span><strong>' + str(model.get("n_features", 0)) + '</strong></div>' if model_trained else ''}
      </div>
    </div>

    <!-- Trade Table -->
    <div class="trades-section">
      <div class="section-title">Recent Trades — {label}</div>
      {table_html}
    </div>

    <!-- Reset button for this strategy -->
    <div style="padding:12px 0 24px;text-align:right">
      <button class="reset-btn" onclick="confirmReset('{tf}')">
        Reset {label} Strategy
      </button>
    </div>
  </div>""")

        return f"""
<div class="tabs-nav">
  {''.join(tab_buttons)}
</div>
<div class="tabs-content">
  {''.join(tab_panels)}
</div>"""

    # ── Trade Table ───────────────────────────────────────────────────────────
    def _build_trade_table(self, trades: pd.DataFrame) -> str:
        if trades is None or trades.empty:
            return '<p style="color:#8b949e;padding:16px">No trades recorded yet for this strategy.</p>'

        rows = []
        for _, row in trades.head(50).iterrows():
            action  = str(row.get('action', '')).upper()
            pnl     = float(row.get('pnl', 0) or 0)
            pnl_str = f'${pnl:+.2f}' if pnl != 0 else '—'
            pnl_col = '#2ea043' if pnl > 0 else ('#f85149' if pnl < 0 else '#8b949e')
            act_col = ('#2ea043' if 'BUY' in action or 'LONG' in action
                       else '#f85149' if 'SELL' in action or 'SHORT' in action
                       else '#8b949e')
            ts  = str(row.get('timestamp', ''))[:16]
            qty = float(row.get('qty', 0) or 0)
            price = float(row.get('price', 0) or 0)
            eq    = float(row.get('equity', 0) or 0)
            rows.append(
                f'<tr>'
                f'<td>{ts}</td>'
                f'<td style="color:{act_col};font-weight:600">{action}</td>'
                f'<td>${price:.4f}</td>'
                f'<td>{qty:.2f}</td>'
                f'<td style="color:{pnl_col};font-weight:600">{pnl_str}</td>'
                f'<td>${eq:,.2f}</td>'
                f'</tr>'
            )

        return f"""
<div style="overflow-x:auto">
<table class="trade-table">
  <thead>
    <tr>
      <th>Time (UTC)</th><th>Action</th><th>Price</th>
      <th>Qty</th><th>P&L</th><th>Equity</th>
    </tr>
  </thead>
  <tbody>{''.join(rows)}</tbody>
</table>
</div>"""

    # ── HTML Wrapper ──────────────────────────────────────────────────────────
    def _wrap_html(self, equity_chart_html, tabs_html, cards_html,
                   all_strategies, regime, current_price) -> str:
        regime_map   = {'BULL': '#2ea043', 'CHOPPY': '#d29922', 'PANIC': '#f85149'}
        regime_color = regime_map.get(regime, '#8b949e')
        price_str    = f'${current_price:.2f}' if current_price else 'Connecting...'
        now_str      = datetime.utcnow().strftime('%H:%M UTC')

        # Aggregate stats across all strategies
        total_equity = sum(
            s.get('portfolio', {}).get('equity', 0)
            for s in all_strategies.values()
        )
        total_trades = sum(
            s.get('portfolio', {}).get('total_trades', 0)
            for s in all_strategies.values()
        )
        running_count = sum(1 for s in all_strategies.values() if s.get('running'))

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="{cfg.DASHBOARD_REFRESH}">
<title>PetroQuant | Multi-Strategy | WTI Paper Trading</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*, *::before, *::after {{ box-sizing:border-box;margin:0;padding:0; }}
body {{
  font-family:'Inter',system-ui,sans-serif;
  background:{COLORS['bg']};color:{COLORS['text_primary']};min-height:100vh;
}}

/* ── HEADER ── */
.header {{
  background:linear-gradient(135deg,#0d1117 0%,#161b22 100%);
  border-bottom:1px solid #21262d;
  padding:12px 24px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;
}}
.header-title {{
  font-size:18px;font-weight:700;
  background:linear-gradient(135deg,#58a6ff,#2ea043);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
}}
.header-badges {{ display:flex;gap:8px;align-items:center;flex-wrap:wrap; }}
.badge {{
  padding:5px 12px;border-radius:20px;font-size:12px;
  font-weight:600;border:1px solid;white-space:nowrap;
}}

/* ── SUMMARY ROW ── */
.summary-row {{
  display:flex;gap:8px;padding:10px 24px;flex-wrap:wrap;
  background:{COLORS['bg_panel']};border-bottom:1px solid #21262d;
}}
.sum-card {{
  background:{COLORS['bg_card']};border:1px solid #21262d;
  border-radius:8px;padding:10px 16px;flex:1;min-width:140px;
}}
.sum-label {{ font-size:10px;color:{COLORS['text_muted']};text-transform:uppercase;letter-spacing:.05em; }}
.sum-value {{ font-size:20px;font-weight:700;margin-top:2px; }}

/* ── STRATEGY CARDS ── */
.strategy-grid {{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
  gap:12px;padding:16px 24px;
}}
.strategy-card {{
  background:{COLORS['bg_card']};border:1px solid #21262d;
  border-radius:10px;padding:14px;transition:border-color 0.2s;
}}
.strategy-card:hover {{ border-color:#388bfd44; }}
.sc-header {{ display:flex;align-items:center;gap:8px;margin-bottom:8px; }}
.sc-tf {{ font-size:15px;font-weight:700; }}
.sc-badge {{
  font-size:10px;background:#21262d;color:#8b949e;
  border-radius:4px;padding:2px 6px;font-family:monospace;
}}
.sc-equity {{ font-size:22px;font-weight:700;margin:6px 0 2px; }}
.sc-return {{ font-size:13px;font-weight:600;margin-bottom:8px; }}
.sc-row {{ display:flex;justify-content:space-between;margin-top:6px;font-size:12px; }}
.sc-kv {{ display:flex;flex-direction:column;gap:2px; }}
.k {{ color:{COLORS['text_muted']};font-size:10px;text-transform:uppercase; }}

/* ── SECTION TITLE ── */
.section-title {{
  font-size:13px;font-weight:600;color:{COLORS['text_muted']};
  text-transform:uppercase;letter-spacing:.06em;
  padding:16px 24px 8px;border-top:1px solid #21262d;margin-top:8px;
}}

/* ── CHART ── */
.chart-wrap {{ padding:0 24px 8px; }}

/* ── TABS ── */
.tabs-nav {{
  display:flex;gap:4px;padding:12px 24px 0;
  border-bottom:1px solid #21262d;flex-wrap:wrap;
}}
.tab-btn {{
  background:transparent;border:1px solid #30363d;
  color:{COLORS['text_muted']};border-radius:8px 8px 0 0;
  padding:8px 20px;font-family:inherit;font-size:13px;font-weight:600;
  cursor:pointer;transition:all 0.2s;border-bottom:none;
  margin-bottom:-1px;
}}
.tab-btn.active {{
  background:{COLORS['bg_card']};color:#e6edf3;
  border-color:var(--tc,#388bfd);border-bottom-color:{COLORS['bg_card']};
  color:var(--tc,#388bfd);
}}
.tabs-content {{ padding:0 24px; }}
.tab-panel {{ display:none;padding:16px 0; }}
.tab-panel.active {{ display:block; }}

/* ── DETAIL GRID ── */
.detail-grid {{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
  gap:12px;margin-bottom:16px;
}}
.dcard {{
  background:{COLORS['bg_card']};border:1px solid #21262d;
  border-radius:8px;padding:14px;
}}
.dcard-title {{
  font-size:11px;font-weight:700;text-transform:uppercase;
  letter-spacing:.06em;margin-bottom:10px;color:{COLORS['text_muted']};
}}
.dkv {{
  display:flex;justify-content:space-between;align-items:center;
  padding:4px 0;font-size:12px;border-bottom:1px solid #21262d22;
}}
.dkv:last-child {{ border-bottom:none; }}
.dkv>span:first-child {{ color:{COLORS['text_muted']}; }}

/* ── TRADES ── */
.trades-section {{ margin:8px 0 16px; }}
.trade-table {{
  width:100%;border-collapse:collapse;font-size:12px;
}}
.trade-table th {{
  background:#161b22;color:{COLORS['text_muted']};
  padding:8px 12px;text-align:left;font-weight:600;
  text-transform:uppercase;font-size:10px;letter-spacing:.05em;
}}
.trade-table td {{ padding:7px 12px;border-bottom:1px solid #21262d22; }}
.trade-table tr:hover td {{ background:#ffffff05; }}

/* ── FOOTER ── */
.footer {{
  text-align:center;padding:16px;font-size:11px;
  color:{COLORS['text_muted']};border-top:1px solid #21262d;margin-top:16px;
}}

/* ── RESET BTN ── */
.reset-btn {{
  background:transparent;border:1px solid #f8514944;color:#f85149;
  border-radius:6px;padding:6px 14px;font-size:12px;font-family:inherit;
  cursor:pointer;transition:background 0.2s;font-weight:500;
}}
.reset-btn:hover {{ background:#f8514911; }}

/* ── TOAST ── */
#toast {{
  position:fixed;bottom:24px;right:24px;
  background:{COLORS['bg_card']};border:1px solid #388bfd44;
  border-radius:10px;padding:14px 20px;font-size:13px;color:#e6edf3;
  display:none;z-index:9999;box-shadow:0 4px 24px rgba(0,0,0,.5);
}}
</style>
</head>
<body>

<div id="toast"></div>

<!-- HEADER -->
<div class="header">
  <span class="header-title">&#9883; PetroQuant — Multi-Strategy WTI</span>
  <div class="header-badges">
    <div class="badge" style="color:{regime_color};border-color:{regime_color}44;background:{regime_color}11">
      Regime: {regime}
    </div>
    <div class="badge" style="color:#58a6ff;border-color:#388bfd44;background:#388bfd11">
      WTI {price_str}
    </div>
    <div class="badge" style="color:#8b949e;border-color:#30363d;background:#21262d">
      {running_count}/4 running &nbsp;|&nbsp; {now_str}
    </div>
    <button class="reset-btn" onclick="confirmReset('all')" style="margin-left:8px">
      Reset All
    </button>
  </div>
</div>

<!-- SUMMARY ROW -->
<div class="summary-row">
  <div class="sum-card">
    <div class="sum-label">Combined Equity</div>
    <div class="sum-value">${total_equity:,.0f}</div>
  </div>
  <div class="sum-card">
    <div class="sum-label">Strategies Running</div>
    <div class="sum-value" style="color:{'#2ea043' if running_count==4 else '#d29922'}">{running_count} / 4</div>
  </div>
  <div class="sum-card">
    <div class="sum-label">Total Trades (All TF)</div>
    <div class="sum-value">{total_trades}</div>
  </div>
  <div class="sum-card">
    <div class="sum-label">Initial Capital / Strategy</div>
    <div class="sum-value">${cfg.INITIAL_CAPITAL/1e6:.1f}M</div>
  </div>
</div>

<!-- STRATEGY OVERVIEW CARDS -->
<div class="section-title">Strategy Overview</div>
<div class="strategy-grid">
{cards_html}
</div>

<!-- COMBINED EQUITY CHART -->
<div class="section-title">Portfolio Equity — All Strategies</div>
<div class="chart-wrap">
{equity_chart_html}
</div>

<!-- STRATEGY DETAIL TABS -->
<div class="section-title">Strategy Detail</div>
{tabs_html}

<div class="footer">
  Auto-refreshes every {cfg.DASHBOARD_REFRESH}s &nbsp;|&nbsp;
  <a href="/trades" style="color:#388bfd">Download All Trades CSV</a> &nbsp;|&nbsp;
  <a href="/status" style="color:#388bfd">JSON Status</a> &nbsp;|&nbsp;
  <a href="/health" style="color:#388bfd">Health</a>
</div>

<script>
function showToast(msg, color) {{
  var t = document.getElementById('toast');
  t.style.borderColor = (color||'#388bfd') + '44';
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(function(){{ t.style.display = 'none'; }}, 5000);
}}

function switchTab(tf) {{
  document.querySelectorAll('.tab-panel').forEach(function(p){{ p.classList.remove('active'); }});
  document.querySelectorAll('.tab-btn').forEach(function(b){{ b.classList.remove('active'); }});
  document.querySelectorAll('.strategy-card').forEach(function(c){{ c.style.outline = 'none'; }});
  var panel = document.getElementById('tab-' + tf);
  var btn   = document.getElementById('tabBtn-' + tf);
  var card  = document.getElementById('card-' + tf);
  if (panel) panel.classList.add('active');
  if (btn)   btn.classList.add('active');
  if (card)  card.style.outline = '2px solid #388bfd44';
}}

function confirmReset(tf) {{
  var msg = tf === 'all'
    ? 'Reset ALL 4 strategies? This clears all trades.'
    : 'Reset ' + tf + ' strategy? This clears its trades.';
  if (!confirm(msg)) return;

  var url = tf === 'all' ? '/reset/all' : '/reset/' + tf;
  fetch(url, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: 'confirm=yes'
  }})
  .then(function(r) {{ return r.json(); }})
  .then(function(d) {{
    showToast((d.message || 'Reset done') + ' — reloading...', '#2ea043');
    setTimeout(function(){{ window.location.reload(); }}, 1500);
  }})
  .catch(function(){{ showToast('Reset failed', '#f85149'); }});
}}
</script>

</body>
</html>"""
