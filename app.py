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
    )
    return render_template(
        'dashboard.html',
        data=data,
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

    return render_template(
        'transactions.html',
        transactions=txs,
        summary={'inflow': inflow, 'outflow': outflow,
                 'net': inflow - outflow, 'count': len(txs)},
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
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    })


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
