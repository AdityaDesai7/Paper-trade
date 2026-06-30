# ============================================================================
# PETROQUANT PAPER TRADING — FLASK WEB SERVER
# ============================================================================
# Serves the live dashboard and JSON API endpoints so you can monitor
# the paper trader from any device (phone/laptop) while the engine runs
# on a cloud server (Railway.app).
#
# Endpoints:
#   GET /              → HTML dashboard (auto-refresh every 60s)
#   GET /dashboard     → same as above
#   GET /status        → JSON: current position, equity, last signal
#   GET /trades        → CSV download: full trade log
#   GET /trades/json   → JSON: last 100 trades
#   GET /health        → {"status":"ok"} for uptime monitoring
#   GET /reset         → ⚠ Reset all trades (protected by ?confirm=yes)
# ============================================================================

from flask import Flask, Response, request, jsonify, send_file
import threading
import logging
import os
import io

from . import config as cfg

logger = logging.getLogger(__name__)


def create_web_server(engine_ref: dict) -> Flask:
    """
    Creates the Flask app. engine_ref is a dict holding live references:
      engine_ref['engine']    → PaperTradingEngine instance
      engine_ref['portfolio'] → Portfolio instance
      engine_ref['trade_log'] → TradeLog instance
      engine_ref['dashboard'] → LiveDashboard instance

    Using a dict reference allows the web server to always see the latest state.
    """
    app = Flask(__name__, static_folder=None)
    app.logger.disabled = True
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)   # silence Flask request logs

    # ── Dashboard ─────────────────────────────────────────────────────────────
    @app.route('/')
    @app.route('/dashboard')
    def dashboard():
        try:
            dash      = engine_ref.get('dashboard')
            portfolio = engine_ref.get('portfolio')
            engine    = engine_ref.get('engine')

            current_price = None
            regime        = 'UNKNOWN'
            model_status  = {}

            if engine:
                current_price = engine.last_price
                regime        = engine.current_regime
                model_status  = engine.model.get_model_status() if engine.model else {}

            html = dash.render(
                current_price=current_price or 0,
                regime=regime,
                model_status=model_status,
            )
            return Response(html, mimetype='text/html')

        except Exception as e:
            logger.error(f"[WebServer] Dashboard error: {e}", exc_info=True)
            return Response(f"<pre>Dashboard error: {e}</pre>", status=500)

    # ── Status JSON ───────────────────────────────────────────────────────────
    @app.route('/status')
    def status():
        try:
            portfolio  = engine_ref.get('portfolio')
            engine     = engine_ref.get('engine')
            trade_log  = engine_ref.get('trade_log')

            current_price = engine.last_price if engine else None
            snap = portfolio.get_snapshot(current_price) if portfolio else {}
            summary = trade_log.get_summary() if trade_log else {}

            return jsonify({
                'status'        : 'running',
                'current_price' : current_price,
                'regime'        : engine.current_regime if engine else 'UNKNOWN',
                'last_signal'   : engine.last_signal if engine else 'UNKNOWN',
                'last_prob'     : engine.last_prob if engine else None,
                'portfolio'     : snap,
                'summary'       : summary,
                'model'         : engine.model.get_model_status() if engine and engine.model else {},
                'market_open'   : engine.is_market_open() if engine else False,
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ── Trade Log CSV ─────────────────────────────────────────────────────────
    @app.route('/trades')
    def download_trades():
        try:
            trade_log = engine_ref.get('trade_log')
            df = trade_log.get_trade_history()
            csv_str = df.to_csv(index=False)
            return Response(
                csv_str,
                mimetype='text/csv',
                headers={'Content-Disposition': 'attachment; filename=paper_trades.csv'}
            )
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ── Trade Log JSON ────────────────────────────────────────────────────────
    @app.route('/trades/json')
    def trades_json():
        try:
            trade_log = engine_ref.get('trade_log')
            limit = int(request.args.get('limit', 100))
            df = trade_log.get_trade_history(limit=limit)
            return jsonify(df.to_dict(orient='records'))
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ── Health Check ──────────────────────────────────────────────────────────
    @app.route('/health')
    def health():
        engine = engine_ref.get('engine')
        return jsonify({
            'status'   : 'ok',
            'version'  : '1.0.0',
            'running'  : engine._running if engine else False,
            'uptime_s' : engine.uptime_seconds() if engine else 0,
        })

    # ── Reset (protected) ─────────────────────────────────────────────────────
    @app.route('/reset')
    def reset():
        confirm = request.args.get('confirm', '')
        if confirm != 'yes':
            return Response("""
                <html><body style="font-family:sans-serif;background:#0d1117;color:#e6edf3;padding:40px">
                <h2>⚠️ Reset Paper Account</h2>
                <p>This will delete ALL trades and reset the portfolio to $1,000,000.</p>
                <a href="/reset?confirm=yes" style="color:#f85149">Click here to confirm reset</a>
                <br><br><a href="/" style="color:#388bfd">← Back to dashboard</a>
                </body></html>
            """, mimetype='text/html')

        try:
            trade_log = engine_ref.get('trade_log')
            engine    = engine_ref.get('engine')
            if engine:
                engine.reset_account()
            return jsonify({'status': 'reset', 'message': 'Account reset to $1,000,000'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    return app


def run_web_server(app: Flask, port: int = cfg.WEB_PORT):
    """Runs Flask in a background daemon thread."""
    thread = threading.Thread(
        target=lambda: app.run(
            host='0.0.0.0',
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True,
        ),
        daemon=True,
        name='WebServer'
    )
    thread.start()
    logger.info(f"[WebServer] Started on http://0.0.0.0:{port}")
    return thread
