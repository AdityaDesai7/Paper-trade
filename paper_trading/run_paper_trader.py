#!/usr/bin/env python3
# ============================================================================
# PETROQUANT PAPER TRADING — ENTRY POINT (multi-strategy v2.0)
# ============================================================================
# Boots all 4 strategy runners simultaneously:
#   5m  — fetches 5m WTI candles, predicts 15min ahead, trades every 5min
#   15m — fetches 15m WTI candles, predicts 30min ahead, trades every 15min
#   1h  — fetches 1h  WTI candles, predicts 2h ahead,   trades every 1h
#   1d  — fetches 1d  WTI candles, predicts next day,   trades once daily
#
# Each strategy has its own: model, portfolio, trade DB, loop thread.
# No shared state. No global config mutations.
#
# Run modes:
#   python paper_trading/run_paper_trader.py              → full engine (default)
#   python paper_trading/run_paper_trader.py --status     → print current status
#   python paper_trading/run_paper_trader.py --no-server  → engine without web server
# ============================================================================

import sys
import os
import argparse
import time
import signal
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper_trading import config as cfg
from paper_trading.engine     import MultiStrategyEngine
from paper_trading.dashboard_live import LiveDashboard
from paper_trading.web_server import create_web_server, run_web_server

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description='PetroQuant Multi-Strategy Paper Trading Engine',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
All 4 strategies (5m, 15m, 1h, 1d) run simultaneously.
Each has its own portfolio, model, and database.

Examples:
  python paper_trading/run_paper_trader.py           # Start all strategies
  python paper_trading/run_paper_trader.py --status  # Print status and exit
  python paper_trading/run_paper_trader.py --no-server  # No web UI
        """
    )
    parser.add_argument('--status',    action='store_true',
                        help='Print current status for all strategies and exit')
    parser.add_argument('--no-server', action='store_true',
                        help='Run engine without web server')
    args = parser.parse_args()

    _print_banner()

    # Initialize multi-strategy engine
    engine = MultiStrategyEngine()

    # ── Mode: status ─────────────────────────────────────────────────────────
    if args.status:
        print("\n" + "=" * 70)
        print("  PetroQuant — Multi-Strategy Status")
        print("=" * 70)
        for tf, runner in engine.strategies.items():
            snap    = runner.portfolio.get_snapshot()
            summary = runner.trade_log.get_summary()
            print(f"\n  [{tf}] {runner._label}")
            print(f"    Interval  : {runner._candle_interval} candles")
            print(f"    Horizon   : {runner._horizon} bars ahead")
            print(f"    Equity    : ${snap.get('equity', cfg.INITIAL_CAPITAL):,.2f}")
            print(f"    Return    : {snap.get('total_return_pct', 0):+.3f}%")
            print(f"    Trades    : {summary.get('total_trades', 0)}")
            print(f"    Win Rate  : {summary.get('win_rate_pct', 0):.1f}%")
            print(f"    DB File   : paper_trades_{tf}.db")
        print("=" * 70)
        return

    # ── Mode: full engine (default) ───────────────────────────────────────────
    dashboard = LiveDashboard(engine=engine)

    engine_ref = {
        'engine'   : engine,
        'dashboard': dashboard,
    }

    if not args.no_server:
        app = create_web_server(engine_ref)
        run_web_server(app, port=cfg.WEB_PORT)
        print(f"\n[OK] Web server running at http://0.0.0.0:{cfg.WEB_PORT}")
        print(f"  Dashboard  : http://localhost:{cfg.WEB_PORT}/")
        print(f"  Status     : http://localhost:{cfg.WEB_PORT}/status")
        print(f"  Trades     : http://localhost:{cfg.WEB_PORT}/trades")
        print(f"  Health     : http://localhost:{cfg.WEB_PORT}/health")
        print(f"  Reset      : http://localhost:{cfg.WEB_PORT}/reset")

    # Start all 4 trading loops
    engine.start_all()
    print(f"\n[OK] All 4 strategies started. Press Ctrl+C to stop.\n")

    # Graceful shutdown
    def _shutdown(sig, frame):
        print("\n\n[Engine] Shutting down all strategies...")
        engine.stop_all()
        for tf, runner in engine.strategies.items():
            snap = runner.portfolio.get_snapshot(runner.last_price)
            print(f"  [{tf}] Final equity: ${snap['equity']:,.2f} "
                  f"({snap['total_return_pct']:+.3f}%)")
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while True:
            time.sleep(10)
    except SystemExit:
        pass


def _print_banner():
    print("""
=============================================================
      PetroQuant - Multi-Strategy Paper Trading v2.0
                                                           
  Asset      : WTI Crude Oil Futures (CL=F)              
  Strategies : 5m | 15m | 1h | 1d (all run in parallel)  
  Capital    : $1,000,000 per strategy (independent)       
  Signal     : XGBoost — per-TF data, per-TF prediction   
  Regime     : Daily HMM (BULL / CHOPPY / PANIC)           
=============================================================
""")


if __name__ == '__main__':
    main()
