"""
Fluxo — personal finance tracker.
Backend: Flask + Supabase (Postgres). Server-rendered Jinja templates.
"""
import io
import csv
import json

from flask import (Flask, render_template, request, jsonify, redirect,
                   url_for, send_file, Response)

import db

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 3600  # cache static assets 1h

try:
    from flask_compress import Compress
    Compress(app)  # gzip HTML/JSON — the transactions table shrinks ~10x
except ImportError:
    pass  # local dev without the dependency still works


# --- TEMPLATE HELPERS ---
@app.template_filter('money')
def money_filter(value):
    if value is None:
        return '0.00'
    value = float(value)
    if value < 0:
        return f"-{abs(value):,.2f}"
    return f"{value:,.2f}"


@app.context_processor
def inject_globals():
    """Make currency + category tree available to every template."""
    settings = db.get_settings()
    return {
        'currency_symbol': db.CURRENCIES.get(settings['currency'], '€'),
        'currency_code': settings['currency'],
        'hierarchy': settings['category_hierarchy'],
        'quick_categories': db.QUICK_CATEGORIES,
    }


def _truthy(param):
    return request.args.get(param) in ('1', 'true', 'on', 'yes')


# --- DASHBOARD ---
@app.route('/')
def dashboard():
    timeframe = request.args.get('timeframe', 'this_month')
    exclude_investments = _truthy('exclude_investments')
    exclude_rsu = _truthy('exclude_rsu')
    exclude_one_off = _truthy('exclude_one_off')

    all_txs = db.get_transactions()
    months = db.available_months(all_txs)

    compare = request.args.getlist('compare')
    if not compare:
        # Default: current month + up to 3 previous months.
        compare = months[:4]

    data = db.get_dashboard_data(
        timeframe=timeframe,
        exclude_investments=exclude_investments,
        exclude_rsu=exclude_rsu,
        exclude_one_off=exclude_one_off,
        compare_months=compare,
        all_txs=all_txs,
    )
    trends = db.get_trends(all_txs=all_txs)

    return render_template(
        'dashboard.html',
        data=data,
        trends=trends,
        trends_json=json.dumps(trends),
        months=months,
        selected_compare=compare,
        filters={
            'timeframe': timeframe,
            'exclude_investments': exclude_investments,
            'exclude_rsu': exclude_rsu,
            'exclude_one_off': exclude_one_off,
        },
        chart_json=json.dumps(data['chart']),
    )


# --- TRANSACTIONS LIST ---
@app.route('/transactions')
def transactions_view():
    search = request.args.get('search', '')
    category_1 = request.args.get('category_1', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    txs = db.get_transactions(search, category_1, date_from, date_to)
    inflow = sum(t['amount'] for t in txs if t['transaction_type'] == 'inflow')
    outflow = sum(t['amount'] for t in txs if t['transaction_type'] == 'outflow')
    investments = sum(t['amount'] for t in txs
                      if t['transaction_type'] == 'outflow'
                      and t['category_1'] == 'Investments')

    # Render the latest N rows by default; "Show all" lifts the cap.
    # (Stats above are always computed over the full filtered set.)
    total = len(txs)
    show_all = request.args.get('all') == '1'
    limit = 300
    truncated = not show_all and total > limit
    visible = txs if show_all else txs[:limit]

    return render_template(
        'transactions.html',
        transactions=visible,
        total=total,
        truncated=truncated,
        summary={'inflow': inflow, 'outflow': outflow,
                 'net': inflow - outflow, 'count': total,
                 'investments': investments},
        filters={'search': search, 'category_1': category_1,
                 'date_from': date_from, 'date_to': date_to},
    )


# --- ADD / EDIT ---
@app.route('/transactions/add')
def add_transaction_view():
    return render_template('add_transaction.html', tx=None)


@app.route('/transactions/add', methods=['POST'])
def add_transaction():
    db.add_transaction(request.form)
    return redirect(url_for('transactions_view'))


@app.route('/transactions/edit/<int:tx_id>')
def edit_transaction_view(tx_id):
    tx = db.get_transaction(tx_id)
    if not tx:
        return redirect(url_for('transactions_view'))
    return render_template('add_transaction.html', tx=tx)


@app.route('/transactions/edit/<int:tx_id>', methods=['POST'])
def edit_transaction(tx_id):
    db.edit_transaction(tx_id, request.form)
    return redirect(url_for('transactions_view'))


@app.route('/transactions/delete/<int:tx_id>', methods=['POST'])
def delete_transaction(tx_id):
    db.delete_transaction(tx_id)
    return redirect(url_for('transactions_view'))


# --- CSV IMPORT / EXPORT ---
@app.route('/transactions/import', methods=['POST'])
def import_transactions():
    file = request.files.get('file')
    if not file:
        return redirect(url_for('add_transaction_view'))

    text = file.read().decode('utf-8-sig', errors='replace')
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return redirect(url_for('transactions_view'))

    # Support the MoneyBlox export shape:
    # Date, <blank>, Type, Amount, Category 1, Category 2, Category 3, Method, Details, Is One Off, [Merchant]
    header = [h.strip().lower() for h in rows[0]]
    parsed = []

    def idx(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    i_date = idx('date')
    i_type = idx('type', 'transaction_type')
    i_amount = idx('amount')
    i_c1 = idx('category 1', 'category_1')
    i_c2 = idx('category 2', 'category_2')
    i_c3 = idx('category 3', 'category_3')
    i_method = idx('method')
    i_details = idx('details', 'merchant')
    i_oneoff = idx('is one off', 'is_one_off')

    for r in rows[1:]:
        if not any(cell.strip() for cell in r):
            continue

        def g(i):
            return r[i].strip() if i is not None and i < len(r) else ''

        parsed.append({
            'date': g(i_date),
            'transaction_type': (g(i_type) or 'outflow').lower(),
            'amount': g(i_amount),
            'category_1': g(i_c1),
            'category_2': g(i_c2),
            'category_3': g(i_c3),
            'method': g(i_method),
            'details': g(i_details),
            'is_one_off': g(i_oneoff),
        })

    count = db.import_transactions(parsed)
    return redirect(url_for('transactions_view', imported=count))


@app.route('/transactions/import-revolut', methods=['POST'])
def import_revolut():
    """Parse a raw Revolut export and show the editable preview."""
    import revolut
    file = request.files.get('file')
    if not file:
        return redirect(url_for('add_transaction_view'))
    try:
        rows = revolut.parse_export(file.read(), file.filename)
    except ValueError as exc:
        return render_template('revolut_preview.html', rows_json='[]',
                               error=str(exc))

    # Flag potential duplicates: same date + amount + direction as an
    # existing transaction. Count-aware — two identical coffees only flag
    # as many as already exist in the DB.
    from collections import Counter
    existing = Counter(
        (t['date'], round(t['amount'], 2), t['transaction_type'])
        for t in db.get_transactions())
    for r in rows:
        key = (r['date'], round(r['amount'], 2), r['transaction_type'])
        if r['include'] and existing.get(key, 0) > 0:
            existing[key] -= 1
            r['include'] = False
            r['note'] = 'possible duplicate'

    return render_template('revolut_preview.html',
                           rows_json=json.dumps(rows), error=None)


@app.route('/transactions/import-revolut/confirm', methods=['POST'])
def import_revolut_confirm():
    """Insert the (user-edited) preview rows."""
    rows = request.get_json(silent=True) or []
    payload = [{
        'date': r.get('date'),
        'transaction_type': r.get('transaction_type', 'outflow'),
        'amount': r.get('amount'),
        'category_1': r.get('category_1'),
        'category_2': r.get('category_2'),
        'category_3': r.get('category_3'),
        'method': 'Revolut',
        'details': r.get('details', ''),
        'is_one_off': '',
    } for r in rows]
    count = db.import_transactions(payload)
    return jsonify({'imported': count})


@app.route('/transactions/export')
def export_csv():
    txs = db.get_transactions()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Date', '', 'Type', 'Amount', 'Category 1', 'Category 2',
                     'Category 3', 'Method', 'Details', 'Is One Off'])
    for t in txs:
        writer.writerow([
            t['date'], 'N', t['transaction_type'], t['amount'],
            t['category_1'], t['category_2'], t['category_3'],
            t['method'], t['details'], 'Y' if t['is_one_off'] else 'N',
        ])
    return Response(
        buf.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=fluxo_transactions.csv'})


# --- SETTINGS ---
@app.route('/settings')
def settings_view():
    settings = db.get_settings()
    return render_template('settings.html', settings=settings,
                           currencies=db.CURRENCIES)


@app.route('/settings/currency', methods=['POST'])
def save_currency():
    db.update_settings(currency=request.form.get('currency', 'EUR'))
    return redirect(url_for('settings_view'))


@app.route('/settings/categories', methods=['POST'])
def save_categories():
    try:
        hierarchy = json.loads(request.form.get('hierarchy', '{}'))
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid category data'}), 400
    db.update_settings(hierarchy=hierarchy)
    return jsonify({'success': True})


@app.route('/networth')
def networth_view():
    import networth
    assets = db.get_assets()
    table_missing = assets is None
    assets = assets or []

    btc = networth.btc_price_eur()
    stocks, crypto = [], []
    for a in assets:
        if a['kind'] == 'stock':
            price = networth.stock_quote_eur(a['label'])
            stocks.append({**a, 'price': price,
                           'value': round(price * a['quantity'], 2) if price else None})
        else:
            qty = a['quantity']
            live = networth.btc_address_balance(a['address']) if a['address'] else None
            if live is not None:
                qty = live
            crypto.append({**a, 'qty': qty, 'live': live is not None,
                           'value': round(qty * btc, 2) if btc else None})

    cards = networth.cardvault_snapshot()
    totals = {
        'stocks': round(sum(s['value'] or 0 for s in stocks), 2),
        'crypto': round(sum(c['value'] or 0 for c in crypto), 2),
        'collectibles': cards['value'] if cards else 0,
    }
    totals['net'] = round(sum(totals.values()), 2)

    return render_template('networth.html', stocks=stocks, crypto=crypto,
                           cards=cards, totals=totals, btc_price=btc,
                           table_missing=table_missing)


@app.route('/networth/add', methods=['POST'])
def networth_add():
    db.add_asset(request.form)
    return redirect(url_for('networth_view'))


@app.route('/networth/delete/<int:asset_id>', methods=['POST'])
def networth_delete(asset_id):
    db.delete_asset(asset_id)
    return redirect(url_for('networth_view'))


@app.route('/health')
def health():
    """Cheap keep-alive target — no DB, no templates."""
    return 'ok', 200


@app.route('/manifest.json')
def manifest():
    return jsonify({
        "name": "Fluxo",
        "short_name": "Fluxo",
        "description": "Personal finance tracker",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0e1020",
        "theme_color": "#0e1020",
        "orientation": "portrait",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ]
    })


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
