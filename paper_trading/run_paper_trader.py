#!/usr/bin/env python3
# ============================================================================
# PETROQUANT PAPER TRADING — ENTRY POINT
# ============================================================================
# Run modes:
#   python run_paper_trader.py              → start engine + web server (default)
#   python run_paper_trader.py --once       → run one cycle and exit
#   python run_paper_trader.py --dashboard  → generate dashboard only, no trades
#   python run_paper_trader.py --reset      → reset paper account (asks confirmation)
#   python run_paper_trader.py --status     → print current account status
#
# Environment:
#   On Railway.app: just runs this file (railway.toml sets the startCommand)
#   Locally: python paper_trading/run_paper_trader.py
# ============================================================================

import sys
import os
import argparse
import time
import signal
import logging

# ── Ensure project root is on the path ───────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper_trading import config as cfg
from paper_trading.engine      import PaperTradingEngine
from paper_trading.web_server  import create_web_server, run_web_server

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='PetroQuant Paper Trading Engine',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python paper_trading/run_paper_trader.py              # Full engine + web server
  python paper_trading/run_paper_trader.py --once       # One cycle, then exit
  python paper_trading/run_paper_trader.py --dashboard  # Dashboard only
  python paper_trading/run_paper_trader.py --status     # Account status
  python paper_trading/run_paper_trader.py --reset      # Reset account
        """
    )
    parser.add_argument('--once',      action='store_true', help='Run one cycle and exit')
    parser.add_argument('--dashboard', action='store_true', help='Generate dashboard and exit')
    parser.add_argument('--reset',     action='store_true', help='Reset paper account')
    parser.add_argument('--status',    action='store_true', help='Print account status')
    parser.add_argument('--no-server', action='store_true', help='Run engine without web server')
    args = parser.parse_args()

    _print_banner()

    # ── Initialize engine ────────────────────────────────────────────────────
    engine = PaperTradingEngine(loop_interval_secs=60)

    # ── Mode: reset ──────────────────────────────────────────────────────────
    if args.reset:
        confirm = input("\n⚠  Reset paper account? This deletes ALL trades. Type 'yes' to confirm: ")
        if confirm.strip().lower() == 'yes':
            engine.reset_account()
            print("✓ Account reset to ${:,.0f}".format(cfg.INITIAL_CAPITAL))
        else:
            print("✗ Reset cancelled.")
        return

    # ── Mode: status ─────────────────────────────────────────────────────────
    if args.status:
        trade_log = engine.trade_log
        summary   = trade_log.get_summary()
        snap      = engine.portfolio.get_snapshot()
        print("\n" + "=" * 55)
        print("  PetroQuant Paper Trading — Account Status")
        print("=" * 55)
        print(f"  Capital        : ${cfg.INITIAL_CAPITAL:,.2f}")
        print(f"  Equity         : ${summary.get('latest_equity', cfg.INITIAL_CAPITAL):,.2f}")
        print(f"  Total Return   : {summary.get('total_return_pct', 0):+.3f}%")
        print(f"  Realized P&L   : ${summary.get('total_realized_pnl', 0):+,.2f}")
        print(f"  Total Trades   : {summary.get('total_trades', 0)}")
        print(f"  Win Rate       : {summary.get('win_rate_pct', 0):.1f}%")
        print(f"  Max Drawdown   : {summary.get('max_drawdown_pct', 0):.2f}%")
        print(f"  Commission Paid: ${summary.get('total_commission', 0):,.2f}")
        print(f"  First Trade    : {summary.get('first_trade', 'N/A')}")
        print(f"  Last Trade     : {summary.get('last_trade', 'N/A')}")
        print("=" * 55)
        return

    # ── Mode: dashboard only ──────────────────────────────────────────────────
    if args.dashboard:
        from paper_trading.price_feed import fetch_1min_candles, fetch_latest_price
        from paper_trading.daily_regime import DailyRegimeDetector
        from paper_trading.dashboard_live import LiveDashboard

        print("\n[Dashboard] Fetching latest data...")
        price_df = fetch_1min_candles()
        current  = fetch_latest_price()
        regime, _ = DailyRegimeDetector().get_regime()

        dash = LiveDashboard(engine.trade_log, engine.portfolio, price_df)
        html = dash.render(current_price=current or 0, regime=regime)
        path = dash.save(html)
        print(f"[Dashboard] Saved → {path}")
        print(f"[Dashboard] Open in browser: file://{path}")
        return

    # ── Mode: once ────────────────────────────────────────────────────────────
    if args.once:
        print("\n[Engine] Running single cycle...")
        result = engine.run_once()
        print(f"\n[Engine] Result: {result}")
        print(f"[Engine] Dashboard: {cfg.DASHBOARD_PATH}")
        return

    # ── Mode: full engine (default) ───────────────────────────────────────────
    # Create shared state ref for web server
    engine_ref = {
        'engine'   : engine,
        'portfolio': engine.portfolio,
        'trade_log': engine.trade_log,
        'dashboard': engine.dashboard,
    }

    # Start web server (unless --no-server)
    if not args.no_server:
        app = create_web_server(engine_ref)
        run_web_server(app, port=cfg.WEB_PORT)
        print(f"\n✓ Web server running at http://0.0.0.0:{cfg.WEB_PORT}")
        print(f"  → Dashboard : http://localhost:{cfg.WEB_PORT}/")
        print(f"  → Status    : http://localhost:{cfg.WEB_PORT}/status")
        print(f"  → Trades    : http://localhost:{cfg.WEB_PORT}/trades")

    # Start trading loop in background
    engine.start()
    print("\n✓ Paper trading engine started. Press Ctrl+C to stop.\n")

    # ── Graceful shutdown on Ctrl+C ───────────────────────────────────────────
    def _shutdown(sig, frame):
        print("\n\n[Engine] Shutting down gracefully...")
        engine.stop()
        snap = engine.portfolio.get_snapshot(engine.last_price)
        print(f"[Engine] Final equity: ${snap['equity']:,.2f} "
              f"({snap['total_return_pct']:+.3f}%)")
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Keep main thread alive
    try:
        while True:
            time.sleep(10)
    except SystemExit:
        pass


# ─────────────────────────────────────────────────────────────────────────────
def _print_banner():
    print("""
==========================================================
         PetroQuant - Paper Trading Engine v1.0           
                                                          
  Asset    : WTI Crude Oil Futures (CL=F)                 
  Interval : 1-minute candles                             
  Signal   : XGBoost -> 5-minute direction prediction      
  Regime   : Daily HMM (BULL / CHOPPY / PANIC)            
  Capital  : $1,000,000 paper USD                         
==========================================================
""")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    main()
