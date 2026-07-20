"""
Data access layer for Fluxo — backed by Supabase (Postgres).
Single-user model (no auth), matching the CardVault approach.
"""
import os
import json
import calendar
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

# Local preview mode: when Supabase creds are missing we run against an
# in-memory store (seeded from a nearby MoneyBlox CSV) so the app is fully
# clickable without any setup. Writes persist for the running session only.
DEMO = not (SUPABASE_URL and SUPABASE_KEY)

_client: Client | None = None

_demo_txs: list = []
_demo_settings: dict = {}
_demo_seq = {'id': 0}
_demo_seeded = {'done': False}


def get_client() -> Client:
    global _client
    if _client is None:
        if DEMO:
            raise RuntimeError(
                "Supabase credentials missing. Create a .env file with "
                "SUPABASE_URL and SUPABASE_KEY (see .env.example)."
            )
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def _ensure_seed():
    """Populate the in-memory demo store once, from a MoneyBlox CSV if present."""
    if _demo_seeded['done']:
        return
    _demo_seeded['done'] = True
    _demo_settings.update({'currency': 'EUR', 'category_hierarchy': DEFAULT_HIERARCHY})

    import csv
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, 'sample.csv'),
        os.path.join(here, '..', 'revolut_july_moneyblox_with_merchant.csv'),
        os.path.join(here, '..', 'revolut_july_moneyblox.csv'),
    ]
    path = next((p for p in candidates if os.path.exists(p)), None)
    if not path:
        return
    try:
        with open(path, encoding='utf-8-sig') as fh:
            rows = list(csv.reader(fh))
    except OSError:
        return
    if not rows:
        return

    header = [h.strip().lower() for h in rows[0]]

    def idx(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    cols = {
        'date': idx('date'), 'type': idx('type', 'transaction_type'),
        'amount': idx('amount'), 'c1': idx('category 1', 'category_1'),
        'c2': idx('category 2', 'category_2'), 'c3': idx('category 3', 'category_3'),
        'method': idx('method'), 'details': idx('details', 'merchant'),
        'oneoff': idx('is one off', 'is_one_off'),
    }

    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue

        def g(key):
            i = cols[key]
            return r[i].strip() if i is not None and i < len(r) else ''

        iso = parse_date(g('date'))
        if not iso:
            continue
        _demo_seq['id'] += 1
        _demo_txs.append({
            'id': _demo_seq['id'],
            'date': iso,
            'is_one_off': _b(g('oneoff')),
            'transaction_type': (g('type') or 'outflow').lower(),
            'amount': abs(_f(g('amount'))),
            'category_1': g('c1') or 'Uncategorized',
            'category_2': g('c2'),
            'category_3': g('c3'),
            'method': g('method'),
            'details': g('details'),
        })


# --- CONSTANTS ---
TX_TYPES = ['outflow', 'inflow']

CURRENCIES = {
    'EUR': '€', 'USD': '$', 'GBP': '£', 'CHF': 'CHF ',
    'JPY': '¥', 'CAD': 'C$', 'AUD': 'A$',
}

# Default 3-level category tree, derived from the MoneyBlox CSV format.
# Shape: { category_1: { category_2: [category_3, ...] } }
DEFAULT_HIERARCHY = {
    'Food': {
        'Groceries': ['Delhaize', 'Carrefour'], 'Lunch': ['Office'],
        'Dinner': ['Delivery'], 'Drinks': [], 'Snacks': ['Carrefour'],
        'Snack': [], 'Breakfast': ['Office'], 'Aperitivo': [],
    },
    'Miscellaneous': {
        'Shopping': ['Clothes', 'Tech', 'Cigars', 'Anime/Manga/Cards',
                     'Books', 'Music', 'House'],
        'Gifts': [], 'Personal': ['Barbershop', 'Health', 'Sport', 'Gym', 'Half-Marathon'],
        'Entertainment': ['Tickets', 'Cinema'], 'Gambling': [], 'Unknown': [],
    },
    'Transportation': {
        'Car Rental': [], 'Uber': [], 'Bus': [], 'Train': [], 'Tolls': [],
        'Gas': [], 'Parking': [], 'Fines': [], 'Carsharing': [], 'Bike': [],
    },
    'Investments': {
        'Business': [], 'Bitcoin DCA': ['Saveback', 'Weekly Savings'],
        'RSUs': [], 'Stocks': [], 'Roundup': [], 'Pension Plan': [],
        'Crypto': [], 'Savings': [],
    },
    'Subscriptions': {
        'Amex': [], 'Phone Plan': [], 'iCloud': [], 'Gym': [], 'Sports': [],
        'Wolt': [], 'Spotify': [], 'Amazon': [], 'Kotcha': [], 'FinX': [],
        'Revolut': [], 'Spuerkeess': [], 'Mobile': [],
    },
    'Income': {
        'Business': ['Vinted'], 'Salary': ['Base', 'Vouchers', 'RSUs'],
        'Fantacalcio': [], 'Gambling': [], 'Gifts': [], 'Interest': [], 'Saveback': [],
    },
    'House': {'Rent': [], 'Charges': [], 'Cleaning': [], 'Groceries': []},
    'Travel': {'Hotel': [], 'Flight': [], 'Bus': [], 'Phone Plan': []},
    'Installments': {'iPhone': [], 'Airpods': []},
    'Shopping': {'Business': []},
}

# Quick-add buttons -> exact path in the hierarchy [cat1, cat2, cat3].
# icon is an emoji rendered on the button.
QUICK_CATEGORIES = [
    {'label': 'Lunch',     'icon': '🥪', 'path': ['Food', 'Lunch', '']},
    {'label': 'Groceries', 'icon': '🛒', 'path': ['Food', 'Groceries', '']},
    {'label': 'Dinner',    'icon': '🍝', 'path': ['Food', 'Dinner', '']},
    {'label': 'Delivery',  'icon': '🛵', 'path': ['Food', 'Dinner', 'Delivery']},
    {'label': 'Drinks',    'icon': '🍹', 'path': ['Food', 'Drinks', '']},
    {'label': 'Clothes',   'icon': '👕', 'path': ['Miscellaneous', 'Shopping', 'Clothes']},
    {'label': 'Snacks',    'icon': '🍫', 'path': ['Food', 'Snacks', '']},
    {'label': 'Business',  'icon': '💼', 'path': ['Investments', 'Business', '']},
]


def _f(val, default=0.0):
    try:
        return float(val) if val not in (None, '') else default
    except (ValueError, TypeError):
        return default


def _s(val, default=''):
    return str(val) if val not in (None, '') else default


def _b(val):
    """Coerce a form/db value to bool."""
    if isinstance(val, bool):
        return val
    return str(val).lower() in ('1', 'true', 'on', 'yes', 'y')


def parse_date(val):
    """Accept ISO (YYYY-MM-DD), M/D/YYYY, or those with a trailing time; return ISO or None."""
    if not val:
        return None
    val = str(val).strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y',
                '%Y-%m-%d %H:%M:%S', '%m/%d/%Y %H:%M:%S'):
        try:
            return datetime.strptime(val, fmt).date().isoformat()
        except ValueError:
            continue
    # Fallback: take an ISO date prefix (handles unexpected trailing text)
    try:
        return datetime.strptime(val[:10], '%Y-%m-%d').date().isoformat()
    except ValueError:
        return None


# --- SETTINGS ---
_settings_cache = {'data': None, 'at': 0.0}
_SETTINGS_TTL = 60  # seconds; settings change rarely, invalidated on update


def get_settings():
    if DEMO:
        _ensure_seed()
        return {'currency': _demo_settings.get('currency', 'EUR'),
                'category_hierarchy': _demo_settings.get('category_hierarchy', DEFAULT_HIERARCHY)}

    import time as _time
    if _settings_cache['data'] is not None and _time.time() - _settings_cache['at'] < _SETTINGS_TTL:
        return _settings_cache['data']

    client = get_client()
    rows = client.table('settings').select('*').eq('id', 1).execute().data
    if not rows:
        client.table('settings').insert({
            'id': 1, 'currency': 'EUR',
            'category_hierarchy': DEFAULT_HIERARCHY,
        }).execute()
        result = {'currency': 'EUR', 'category_hierarchy': DEFAULT_HIERARCHY}
        _settings_cache.update(data=result, at=_time.time())
        return result

    row = rows[0]
    hierarchy = row.get('category_hierarchy')
    if isinstance(hierarchy, str):
        try:
            hierarchy = json.loads(hierarchy)
        except (ValueError, TypeError):
            hierarchy = {}
    # Backfill default tree for brand-new installs.
    if not hierarchy:
        hierarchy = DEFAULT_HIERARCHY
        client.table('settings').update(
            {'category_hierarchy': hierarchy}).eq('id', 1).execute()
    result = {'currency': _s(row.get('currency'), 'EUR'), 'category_hierarchy': hierarchy}
    _settings_cache.update(data=result, at=_time.time())
    return result


def update_settings(currency=None, hierarchy=None):
    _settings_cache.update(data=None, at=0.0)  # invalidate cache on any write
    if DEMO:
        _ensure_seed()
        if currency is not None:
            _demo_settings['currency'] = currency
        if hierarchy is not None:
            _demo_settings['category_hierarchy'] = hierarchy
        return
    update = {'updated_at': datetime.utcnow().isoformat()}
    if currency is not None:
        update['currency'] = currency
    if hierarchy is not None:
        update['category_hierarchy'] = hierarchy
    get_client().table('settings').update(update).eq('id', 1).execute()


def currency_symbol():
    return CURRENCIES.get(get_settings()['currency'], '€')


# --- TRANSACTIONS: READ ---
def _row_to_tx(r):
    return {
        'id': r.get('id'),
        'date': _s(r.get('date')),
        'is_one_off': _b(r.get('is_one_off')),
        'transaction_type': _s(r.get('transaction_type'), 'outflow'),
        'amount': _f(r.get('amount')),
        'category_1': _s(r.get('category_1')),
        'category_2': _s(r.get('category_2')),
        'category_3': _s(r.get('category_3')),
        'method': _s(r.get('method')),
        'details': _s(r.get('details')),
    }


_tx_cache = {'data': None, 'at': 0.0}
_TX_TTL = 60  # seconds; invalidated on any write


def _invalidate_tx_cache():
    _tx_cache.update(data=None, at=0.0)


def _fetch_all_transactions():
    """Full transaction list, newest first — cached in memory for _TX_TTL."""
    import time as _time
    if _tx_cache['data'] is not None and _time.time() - _tx_cache['at'] < _TX_TTL:
        return _tx_cache['data']

    client = get_client()
    # PostgREST caps each request at 1000 rows, so paginate through all.
    page, offset, rows = 1000, 0, []
    while True:
        chunk = (client.table('transactions').select('*')
                 .order('date', desc=True).order('id', desc=True)
                 .range(offset, offset + page - 1).execute().data)
        rows.extend(chunk)
        if len(chunk) < page:
            break
        offset += page
    txs = [_row_to_tx(r) for r in rows]
    _tx_cache.update(data=txs, at=_time.time())
    return txs


def get_transactions(search='', category_1='', date_from='', date_to=''):
    if DEMO:
        _ensure_seed()
        txs = sorted(_demo_txs, key=lambda t: (t['date'], t['id']), reverse=True)
        txs = [dict(t) for t in txs]
    else:
        # Filters run in Python over the cached list (trivial at this scale)
        # so most page loads make zero Supabase round trips.
        txs = list(_fetch_all_transactions())

    if category_1:
        txs = [t for t in txs if t['category_1'] == category_1]
    if date_from:
        txs = [t for t in txs if t['date'] >= date_from]
    if date_to:
        txs = [t for t in txs if t['date'] <= date_to]
    if search:
        s = search.lower()
        txs = [t for t in txs if s in ' '.join([
            t['category_1'], t['category_2'], t['category_3'],
            t['method'], t['details']
        ]).lower()]
    return txs


def get_transaction(tx_id):
    if DEMO:
        _ensure_seed()
        return next((dict(t) for t in _demo_txs if t['id'] == int(tx_id)), None)
    rows = get_client().table('transactions').select('*').eq('id', tx_id).execute().data
    return _row_to_tx(rows[0]) if rows else None


# --- TRANSACTIONS: WRITE ---
def _form_to_row(form):
    return {
        'date': parse_date(form.get('date')),
        'is_one_off': _b(form.get('is_one_off')),
        'transaction_type': form.get('transaction_type', 'outflow'),
        'amount': abs(_f(form.get('amount'))),
        'category_1': form.get('category_1', ''),
        'category_2': form.get('category_2', '') or None,
        'category_3': form.get('category_3', '') or None,
        'method': form.get('method', '') or None,
        'details': form.get('details', '') or None,
    }


def _row_demo_normalize(row):
    """Turn a DB-shaped row (None-able) into the demo dict shape (empty strings)."""
    return {
        'date': row['date'], 'is_one_off': bool(row['is_one_off']),
        'transaction_type': row['transaction_type'], 'amount': row['amount'],
        'category_1': row['category_1'] or '', 'category_2': row['category_2'] or '',
        'category_3': row['category_3'] or '', 'method': row['method'] or '',
        'details': row['details'] or '',
    }


def add_transaction(form):
    _invalidate_tx_cache()
    if DEMO:
        _ensure_seed()
        _demo_seq['id'] += 1
        row = {'id': _demo_seq['id'], **_row_demo_normalize(_form_to_row(form))}
        _demo_txs.append(row)
        return
    get_client().table('transactions').insert(_form_to_row(form)).execute()


def edit_transaction(tx_id, form):
    _invalidate_tx_cache()
    if DEMO:
        _ensure_seed()
        for i, t in enumerate(_demo_txs):
            if t['id'] == int(tx_id):
                _demo_txs[i] = {'id': t['id'], **_row_demo_normalize(_form_to_row(form))}
                break
        return
    get_client().table('transactions').update(_form_to_row(form)).eq('id', tx_id).execute()


def delete_transaction(tx_id):
    _invalidate_tx_cache()
    if DEMO:
        _ensure_seed()
        _demo_txs[:] = [t for t in _demo_txs if t['id'] != int(tx_id)]
        return
    get_client().table('transactions').delete().eq('id', tx_id).execute()


def import_transactions(rows):
    """Bulk insert. rows = list of dicts (already normalized). Returns count."""
    _invalidate_tx_cache()
    payload = []
    for r in rows:
        iso = parse_date(r.get('date'))
        if not iso:
            continue
        payload.append({
            'date': iso,
            'is_one_off': _b(r.get('is_one_off')),
            'transaction_type': (r.get('transaction_type') or 'outflow').lower(),
            'amount': abs(_f(r.get('amount'))),
            'category_1': r.get('category_1', '') or 'Uncategorized',
            'category_2': r.get('category_2', '') or None,
            'category_3': r.get('category_3', '') or None,
            'method': r.get('method', '') or None,
            'details': r.get('details', '') or None,
        })
    if DEMO:
        _ensure_seed()
        for p in payload:
            _demo_seq['id'] += 1
            _demo_txs.append({'id': _demo_seq['id'], **_row_demo_normalize(p)})
        return len(payload)
    if payload:
        get_client().table('transactions').insert(payload).execute()
    return len(payload)


# --- ANALYTICS / DASHBOARD ---
def _is_rsu(t):
    return 'RSU' in (t['category_2'].upper(), t['category_3'].upper())


def _apply_toggles(txs, exclude_investments=False, exclude_rsu=False, exclude_one_off=False):
    out = []
    for t in txs:
        if exclude_investments and t['category_1'] == 'Investments':
            continue
        if exclude_rsu and _is_rsu(t):
            continue
        if exclude_one_off and t['is_one_off']:
            continue
        out.append(t)
    return out


def _timeframe_bounds(timeframe):
    """Return (date_from_iso, date_to_iso) for a named timeframe."""
    today = date.today()
    if timeframe == 'this_month':
        start = today.replace(day=1)
        return start.isoformat(), today.isoformat()
    if timeframe == 'last_month':
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        start = last_prev.replace(day=1)
        return start.isoformat(), last_prev.isoformat()
    if timeframe == 'ytd':
        return today.replace(month=1, day=1).isoformat(), today.isoformat()
    if timeframe == 'last_3_months':
        start = (today.replace(day=1) - timedelta(days=62)).replace(day=1)
        return start.isoformat(), today.isoformat()
    return '', ''  # 'all'


def get_dashboard_data(timeframe='this_month', exclude_investments=False,
                       exclude_rsu=False, exclude_one_off=False, compare_months=None,
                       all_txs=None):
    """Compute stats, category breakdown, recent list, and the cumulative
    spending chart series (current month + comparison months).
    Pass all_txs to reuse an already-fetched list and avoid a second DB trip."""
    if all_txs is None:
        all_txs = get_transactions()

    # --- Stats for the selected timeframe ---
    df, dt = _timeframe_bounds(timeframe)
    scoped = [t for t in all_txs if (not df or t['date'] >= df) and (not dt or t['date'] <= dt)]
    scoped = _apply_toggles(scoped, exclude_investments, exclude_rsu, exclude_one_off)

    inflow = sum(t['amount'] for t in scoped if t['transaction_type'] == 'inflow')
    outflow = sum(t['amount'] for t in scoped if t['transaction_type'] == 'outflow')
    invest = sum(t['amount'] for t in all_txs
                 if t['category_1'] == 'Investments'
                 and t['transaction_type'] == 'outflow'
                 and (not df or t['date'] >= df) and (not dt or t['date'] <= dt))

    stats = {
        'inflow': inflow,
        'outflow': outflow,
        'net': inflow - outflow,
        'investments': invest,
        'count': len(scoped),
    }

    # --- Category breakdown (outflow only, by category_1) ---
    cats = {}
    for t in scoped:
        if t['transaction_type'] != 'outflow':
            continue
        cats[t['category_1']] = cats.get(t['category_1'], 0) + t['amount']
    breakdown = sorted(
        [{'category': k, 'amount': v, 'pct': (v / outflow * 100) if outflow else 0}
         for k, v in cats.items()],
        key=lambda x: x['amount'], reverse=True)

    # --- Cumulative daily outflow chart: current month + comparison months ---
    chart = _cumulative_chart(all_txs, exclude_investments, exclude_rsu,
                              exclude_one_off, compare_months)

    recent = all_txs[:10]

    return {'stats': stats, 'breakdown': breakdown, 'chart': chart, 'recent': recent}


def get_trends(all_txs=None):
    """Monthly income / expenses / investments series across full history,
    plus MoM and YoY deltas computed on the last *complete* month (the
    current month is partial and would always read as a drop)."""
    if all_txs is None:
        all_txs = get_transactions()

    monthly = {}
    for t in all_txs:
        if not t['date']:
            continue
        m = t['date'][:7]
        d = monthly.setdefault(m, {'income': 0.0, 'expenses': 0.0, 'investments': 0.0})
        if t['transaction_type'] == 'inflow':
            d['income'] += t['amount']
        else:
            d['expenses'] += t['amount']
            if t['category_1'] == 'Investments':
                d['investments'] += t['amount']

    months = sorted(monthly)
    if not months:
        return None

    today = date.today()
    first_of_month = today.replace(day=1)
    last_full = (first_of_month - timedelta(days=1)).strftime('%Y-%m')
    prev_full = (first_of_month - timedelta(days=1)).replace(day=1)
    prev_full = (prev_full - timedelta(days=1)).strftime('%Y-%m')
    yoy_ref = f"{int(last_full[:4]) - 1}{last_full[4:]}"

    def val(month, key):
        return monthly.get(month, {}).get(key)

    def pct(cur, prev):
        if cur is None or prev is None or prev == 0:
            return None
        return round((cur - prev) / prev * 100, 1)

    deltas = {}
    for key in ('income', 'expenses', 'investments'):
        deltas[key] = {
            'mom': pct(val(last_full, key), val(prev_full, key)),
            'yoy': pct(val(last_full, key), val(yoy_ref, key)),
        }

    return {
        'labels': [datetime.strptime(m, '%Y-%m').strftime('%b %y') for m in months],
        'income': [round(monthly[m]['income'], 2) for m in months],
        'expenses': [round(monthly[m]['expenses'], 2) for m in months],
        'investments': [round(monthly[m]['investments'], 2) for m in months],
        'ref_label': datetime.strptime(last_full, '%Y-%m').strftime('%B %Y'),
        'deltas': deltas,
    }


def _month_key(d):
    return d[:7]  # 'YYYY-MM'


def available_months(all_txs=None):
    """Distinct 'YYYY-MM' present in the data, newest first."""
    txs = all_txs if all_txs is not None else get_transactions()
    months = sorted({_month_key(t['date']) for t in txs if t['date']}, reverse=True)
    return months


def _cumulative_chart(all_txs, exclude_investments, exclude_rsu, exclude_one_off, compare_months):
    """Build cumulative daily outflow series per month, indexed by day-of-month."""
    txs = _apply_toggles([t for t in all_txs if t['transaction_type'] == 'outflow'],
                         exclude_investments, exclude_rsu, exclude_one_off)

    today = date.today()
    current_key = today.strftime('%Y-%m')

    months = compare_months or []
    if current_key not in months:
        months = [current_key] + [m for m in months if m != current_key]
    if not months:
        months = [current_key]

    max_days = max(calendar.monthrange(int(m[:4]), int(m[5:7]))[1] for m in months)
    labels = list(range(1, max_days + 1))

    series = []
    ranking_today = []
    for m in months:
        yr, mo = int(m[:4]), int(m[5:7])
        days_in_month = calendar.monthrange(yr, mo)[1]
        daily = [0.0] * (days_in_month + 1)  # index by day 1..N
        for t in txs:
            if _month_key(t['date']) == m:
                try:
                    day = int(t['date'][8:10])
                    daily[day] += t['amount']
                except (ValueError, IndexError):
                    continue
        cumulative, running = [], 0.0
        for d in range(1, max_days + 1):
            if d <= days_in_month:
                running += daily[d]
                cumulative.append(round(running, 2))
            else:
                cumulative.append(None)
        # Only draw the current month up to today.
        if m == current_key:
            cutoff = today.day
            cumulative = [v if (i + 1) <= cutoff else None for i, v in enumerate(cumulative)]
        series.append({
            'month': m,
            'label': datetime(yr, mo, 1).strftime('%b %Y'),
            'is_current': m == current_key,
            'data': cumulative,
        })

    # Ranking badge: rank current-month cumulative spend at today's day-of-month
    # against the same day number in the other displayed months.
    day_idx = today.day - 1
    for s in series:
        val = s['data'][day_idx] if day_idx < len(s['data']) else None
        if val is None:
            # fall back to last known cumulative value
            known = [v for v in s['data'] if v is not None]
            val = known[-1] if known else 0
        ranking_today.append((s['month'], val))
    ranking_today.sort(key=lambda x: x[1], reverse=True)
    rank = next((i + 1 for i, (m, _) in enumerate(ranking_today) if m == current_key), 1)

    return {
        'labels': labels,
        'series': series,
        'rank': rank,
        'rank_total': len(series),
        'rank_day': today.day,
    }
