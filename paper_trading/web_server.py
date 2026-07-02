# ============================================================================
# PETROQUANT PAPER TRADING — FLASK WEB SERVER (multi-strategy)
# ============================================================================
# Serves the live dashboard and JSON API for all 4 timeframe strategies.
#
# Endpoints:
#   GET  /                     → HTML dashboard (all strategies)
#   GET  /dashboard            → same as above
#   GET  /status               → JSON: all strategies' status
#   GET  /status/<tf>          → JSON: single strategy status (5m/15m/1h/1d)
#   GET  /trades               → CSV: all trades across all strategies
#   GET  /trades/json          → JSON: last 100 trades (optional ?tf=5m filter)
#   GET  /health               → {"status":"ok"} for uptime monitoring
#   POST /reset/<tf>           → reset one strategy (body: confirm=yes)
#   POST /reset/all            → reset all strategies (body: confirm=yes)
#   GET  /reset                → HTML confirmation page
# ============================================================================

from flask import Flask, Response, request, jsonify
import pandas as pd
import threading
import logging

from . import config as cfg

logger = logging.getLogger(__name__)


def create_web_server(engine_ref: dict) -> Flask:
    """
    Creates the Flask app.

    engine_ref must contain:
        engine_ref['engine']    → MultiStrategyEngine instance
        engine_ref['dashboard'] → LiveDashboard instance
    """
    app = Flask(__name__, static_folder=None)
    app.logger.disabled = True
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    # ── Dashboard ─────────────────────────────────────────────────────────────
    @app.route('/')
    @app.route('/dashboard')
    def dashboard():
        try:
            dash   = engine_ref.get('dashboard')
            engine = engine_ref.get('engine')

            all_status   = engine.get_all_status() if engine else {}
            current_price = engine.get_combined_price() if engine else None
            regime        = engine.get_current_regime() if engine else 'UNKNOWN'

            html = dash.render(
                all_strategies = all_status,
                current_price  = current_price,
                regime         = regime,
            )
            return Response(html, mimetype='text/html')

        except Exception as e:
            logger.error(f"[WebServer] Dashboard error: {e}", exc_info=True)
            return Response(f"<pre>Dashboard error: {e}</pre>", status=500)

    # ── All Strategies Status ─────────────────────────────────────────────────
    @app.route('/status')
    def status():
        try:
            engine = engine_ref.get('engine')
            if engine is None:
                return jsonify({'error': 'Engine not available'}), 503

            all_status = engine.get_all_status()

            return jsonify({
                'status'      : 'running',
                'strategies'  : all_status,
                'market_open' : engine.is_market_open(),
                'uptime_secs' : engine.uptime_seconds(),
                'n_strategies': len(all_status),
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ── Single Strategy Status ────────────────────────────────────────────────
    @app.route('/status/<tf>')
    def status_tf(tf: str):
        try:
            engine = engine_ref.get('engine')
            if engine is None:
                return jsonify({'error': 'Engine not available'}), 503

            runner = engine.get_strategy(tf)
            if runner is None:
                return jsonify({
                    'error': f"Unknown timeframe '{tf}'",
                    'valid': list(cfg.TIMEFRAME_CONFIGS.keys()),
                }), 400

            return jsonify({'status': 'ok', 'strategy': runner.get_status()})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ── Trade Log CSV (all TFs combined) ──────────────────────────────────────
    @app.route('/trades')
    def download_trades():
        try:
            engine  = engine_ref.get('engine')
            tf_filter = request.args.get('tf', None)
            frames  = []

            if engine:
                strategies = (
                    {tf_filter: engine.get_strategy(tf_filter)}
                    if tf_filter and engine.get_strategy(tf_filter)
                    else engine.strategies
                )
                for tf, runner in strategies.items():
                    df = runner.trade_log.get_trade_history()
                    if not df.empty:
                        df.insert(0, 'timeframe', tf)
                        frames.append(df)

            combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            csv_str  = combined.to_csv(index=False)
            filename = f"paper_trades{'_' + tf_filter if tf_filter else '_all'}.csv"

            return Response(
                csv_str,
                mimetype='text/csv',
                headers={'Content-Disposition': f'attachment; filename={filename}'}
            )
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ── Trade Log JSON ────────────────────────────────────────────────────────
    @app.route('/trades/json')
    def trades_json():
        try:
            engine    = engine_ref.get('engine')
            tf_filter = request.args.get('tf', None)
            limit     = int(request.args.get('limit', 100))
            frames    = []

            if engine:
                strategies = (
                    {tf_filter: engine.get_strategy(tf_filter)}
                    if tf_filter and engine.get_strategy(tf_filter)
                    else engine.strategies
                )
                for tf, runner in strategies.items():
                    df = runner.trade_log.get_trade_history(limit=limit)
                    if not df.empty:
                        df.insert(0, 'timeframe', tf)
                        frames.append(df)

            combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            return jsonify(combined.to_dict(orient='records'))
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ── Health Check ──────────────────────────────────────────────────────────
    @app.route('/health')
    def health():
        engine = engine_ref.get('engine')
        running_tfs = [
            tf for tf, r in (engine.strategies.items() if engine else {})
            if r._running
        ]
        return jsonify({
            'status'          : 'ok',
            'version'         : '2.0.0',
            'running_strategies': running_tfs,
            'uptime_s'        : engine.uptime_seconds() if engine else 0,
        })

    # ── Reset One Strategy ────────────────────────────────────────────────────
    @app.route('/reset', methods=['GET'])
    @app.route('/reset/<tf>', methods=['GET', 'POST'])
    def reset(tf: str = None):
        engine = engine_ref.get('engine')

        # GET: Show confirmation page
        if request.method == 'GET':
            tf_opts = ''.join(
                f'<option value="{t}"{"selected" if t == tf else ""}>{t}</option>'
                for t in cfg.TIMEFRAME_CONFIGS.keys()
            )
            return Response(f"""
                <html><body style="font-family:sans-serif;background:#0d1117;color:#e6edf3;padding:40px">
                <h2>&#9888; Reset Paper Strategy</h2>
                <p>This permanently deletes trades for the selected strategy
                and resets the portfolio to <strong>${cfg.INITIAL_CAPITAL:,.0f}</strong>.</p>
                <form method="POST">
                    <label>Strategy:
                        <select name="tf" style="margin:8px;padding:6px;background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:6px">
                            <option value="all">ALL STRATEGIES</option>
                            {tf_opts}
                        </select>
                    </label><br><br>
                    <input type="hidden" name="confirm" value="yes">
                    <button type="submit"
                        style="background:#f85149;color:#fff;border:none;padding:10px 24px;
                               font-size:16px;border-radius:6px;cursor:pointer">
                        Confirm Reset
                    </button>
                </form>
                <br><a href="/" style="color:#388bfd">&#8592; Back to dashboard</a>
                </body></html>
            """, mimetype='text/html')

        # POST: perform the reset
        confirm  = request.form.get('confirm', '') or (request.get_json(silent=True) or {}).get('confirm', '')
        tf_target = tf or request.form.get('tf', 'all')

        if confirm != 'yes':
            return jsonify({'error': 'Must submit confirm=yes'}), 400

        try:
            if not engine:
                return jsonify({'error': 'Engine not available'}), 503

            if tf_target == 'all':
                engine.reset_all()
                return jsonify({
                    'status' : 'reset',
                    'message': f'All strategies reset to ${cfg.INITIAL_CAPITAL:,.0f}',
                    'tf'     : 'all',
                })
            else:
                engine.reset_strategy(tf_target)
                return jsonify({
                    'status' : 'reset',
                    'message': f'{tf_target} strategy reset to ${cfg.INITIAL_CAPITAL:,.0f}',
                    'tf'     : tf_target,
                })
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            logger.error(f"[WebServer] Reset error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500

    return app


def run_web_server(app: Flask, port: int = cfg.WEB_PORT):
    """Runs Flask in a background daemon thread."""
    thread = threading.Thread(
        target=lambda: app.run(
            host        = '0.0.0.0',
            port        = port,
            debug       = False,
            use_reloader= False,
            threaded    = True,
        ),
        daemon=True,
        name='WebServer',
    )
    thread.start()
    logger.info(f"[WebServer] Started on http://0.0.0.0:{port}")
    return thread
