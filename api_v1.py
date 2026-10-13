"""
JSON API (v1) for the native iOS client.

Thin wrappers over the existing db.* / net-worth helpers — no business logic
lives here. Auth (bearer token, fail closed) is enforced by app._auth_gate
for every /api/v1/* path before any handler runs.
"""
import math
import sys

from flask import Blueprint, current_app, jsonify, request

import db

bp = Blueprint('api_v1', __name__, url_prefix='/api/v1')

_DEFAULT_LIMIT = 300


def _truthy(param):
    return request.args.get(param) in ('1', 'true', 'on', 'yes')


def _int_arg(name, default):
    try:
        return max(0, int(request.args.get(name, default)))
    except (TypeError, ValueError):
        return default


def _app_module():
    """The module that created the Flask app ('app' under gunicorn,
    '__main__' under `python app.py`) — avoids a circular import and a
    second copy of app.py when run directly."""
    return sys.modules[current_app.import_name]


def _is_number(v):
    """int, float or numeric string; rejects None/''/text/NaN/Inf."""
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _invalid_tx(payload):
    """Minimal input check so the API can't write rows the web form would
    never produce. Returns the list of bad field names."""
    bad = []
    if not db.parse_date(payload.get('date')):
        bad.append('date')
    if payload.get('transaction_type', 'outflow') not in db.TX_TYPES:
        bad.append('transaction_type')
    if not _is_number(payload.get('amount')):
        bad.append('amount')
    return bad


def _not_found():
    return jsonify({'error': 'not_found'}), 404


@bp.get('/meta')
def meta():
    settings = db.get_settings()
    return jsonify({
        'currency': settings['currency'],
        'symbol': db.CURRENCIES.get(settings['currency'], '€'),
        'hierarchy': settings['category_hierarchy'],
        'quick_categories': db.QUICK_CATEGORIES,
        'months': db.available_months(db.get_transactions()),
    })


@bp.get('/dashboard')
def dashboard():
    timeframe = request.args.get('timeframe', 'this_month')
    business_only = _truthy('business_only')
    all_txs = db.get_transactions()
    months = db.available_months(all_txs)
    compare = request.args.getlist('compare') or request.args.getlist('compare[]')
    if not compare:
        compare = months[:4]  # same default as the web dashboard

    data = db.get_dashboard_data(
        timeframe=timeframe,
        exclude_investments=_truthy('exclude_investments'),
        exclude_rsu=_truthy('exclude_rsu'),
        exclude_one_off=_truthy('exclude_one_off'),
        compare_months=compare,
        all_txs=all_txs,
    )
    trends = db.get_trends(all_txs=all_txs, business_only=business_only)
    return jsonify({**data, 'trends': trends, 'months': months,
                    'selected_compare': compare})


@bp.get('/transactions')
def list_transactions():
    txs = db.get_transactions(request.args.get('search', ''),
                              request.args.get('category_1', ''),
                              request.args.get('date_from', ''),
                              request.args.get('date_to', ''))
    inflow = sum(t['amount'] for t in txs if t['transaction_type'] == 'inflow')
    outflow = sum(t['amount'] for t in txs if t['transaction_type'] == 'outflow')
    investments = sum(t['amount'] for t in txs
                      if t['transaction_type'] == 'outflow'
                      and t['category_1'] == 'Investments')
    limit = _int_arg('limit', _DEFAULT_LIMIT)
    offset = _int_arg('offset', 0)
    # Summary always covers the full filtered set, like the web page.
    return jsonify({
        'items': txs[offset:offset + limit],
        'total': len(txs),
        'limit': limit,
        'offset': offset,
        'summary': {'inflow': inflow, 'outflow': outflow,
                    'net': inflow - outflow, 'count': len(txs),
                    'investments': investments},
    })


@bp.get('/transactions/<int:tx_id>')
def get_transaction(tx_id):
    tx = db.get_transaction(tx_id)
    return jsonify(tx) if tx else _not_found()


@bp.post('/transactions')
def create_transaction():
    payload = request.get_json(silent=True) or {}
    bad = _invalid_tx(payload)
    if bad:
        return jsonify({'error': 'invalid', 'fields': bad}), 400
    row = db.add_transaction(payload)
    return jsonify(db._row_to_tx(row)), 201


@bp.put('/transactions/<int:tx_id>')
def update_transaction(tx_id):
    payload = request.get_json(silent=True) or {}
    bad = _invalid_tx(payload)
    if bad:
        return jsonify({'error': 'invalid', 'fields': bad}), 400
    row = db.edit_transaction(tx_id, payload)
    return jsonify(db._row_to_tx(row)) if row else _not_found()


@bp.delete('/transactions/<int:tx_id>')
def delete_transaction(tx_id):
    if db.get_transaction(tx_id) is None:
        return _not_found()
    db.delete_transaction(tx_id)
    return jsonify({'deleted': tx_id})


@bp.get('/networth')
def networth():
    """Mirror of networth_view: computes the valuation AND saves today's
    snapshot itself (the daily snapshot hook skips this endpoint)."""
    import networth as nw_mod
    if request.args.get('refresh') == '1':
        nw_mod.clear_cache()
    nw = _app_module()._compute_networth()
    # Skipped when a quote timed out, so a partial total never overwrites it.
    if not nw['table_missing'] and nw['complete'] and nw['totals']['net'] > 0:
        db.save_networth_snapshot(nw['totals'])
    return jsonify({
        'totals': nw['totals'],
        'markets': nw['markets'],
        'crypto': nw['crypto'],
        'manual': nw['manual'],
        'cards': nw['cards'],
        'btc_price': nw['btc'],
        'table_missing': nw['table_missing'],
        'complete': nw['complete'],
        'history': db.get_networth_history() or [],
        'fetch_times': nw_mod.section_fetch_times(),
    })
