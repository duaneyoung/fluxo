import json
import re

import db


def tx(i, d, ttype, amount, c1, c2='', c3=''):
    return {
        'id': i, 'date': d, 'is_one_off': False, 'transaction_type': ttype,
        'amount': amount, 'category_1': c1, 'category_2': c2, 'category_3': c3,
        'method': 'Card', 'details': f'tx {i}',
    }


TXS = [
    # 2025-01: mix of business and non-business
    tx(1, '2025-01-05', 'inflow', 120.0, 'Income', 'Business', 'Vinted'),
    tx(2, '2025-01-10', 'inflow', 2000.0, 'Income', 'Salary', 'Base'),
    tx(3, '2025-01-12', 'outflow', 45.0, 'Investments', 'Business'),
    tx(4, '2025-01-15', 'outflow', 300.0, 'Investments', 'Stocks'),
    tx(5, '2025-01-20', 'outflow', 12.5, 'Food', 'Lunch'),
    # Investments > Business inflow must not count as a business cost
    tx(6, '2025-01-22', 'inflow', 30.0, 'Investments', 'Business'),
    # 2025-02: no business activity at all
    tx(7, '2025-02-10', 'inflow', 2000.0, 'Income', 'Salary', 'Base'),
    tx(8, '2025-02-11', 'outflow', 20.0, 'Food', 'Groceries'),
    # 2025-03: business only, no subcategory 3
    tx(9, '2025-03-01', 'inflow', 80.0, 'Income', 'Business'),
    tx(10, '2025-03-02', 'outflow', 60.0, 'Investments', 'Business'),
    # Similar-looking but different paths: must be ignored
    tx(11, '2025-03-03', 'outflow', 99.0, 'Shopping', 'Business'),
    tx(12, '2025-03-04', 'inflow', 7.0, 'Income', 'business'),
]


def test_off_matches_existing_behavior():
    t = db.get_trends(all_txs=TXS)
    assert t['business_only'] is False
    assert t['labels'] == ['Jan 25', 'Feb 25', 'Mar 25']
    assert t['income'] == [2150.0, 2000.0, 87.0]
    assert t['expenses'] == [357.5, 20.0, 159.0]
    assert t['investments'] == [345.0, 0.0, 60.0]


def test_on_only_business_paths():
    t = db.get_trends(all_txs=TXS, business_only=True)
    assert t['business_only'] is True
    assert t['income'] == [120.0, 0.0, 80.0]
    assert t['expenses'] == [45.0, 0.0, 60.0]
    assert t['investments'] == t['expenses']


def test_on_keeps_same_months():
    off = db.get_trends(all_txs=TXS)
    on = db.get_trends(all_txs=TXS, business_only=True)
    assert on['labels'] == off['labels']


def test_on_without_business_txs_is_zeroed_not_none():
    non_business = [t for t in TXS if t['category_2'] != 'Business']
    t = db.get_trends(all_txs=non_business, business_only=True)
    assert t is not None
    assert t['income'] == [0.0] * len(t['labels'])
    assert t['expenses'] == [0.0] * len(t['labels'])
    assert db.get_trends(all_txs=[], business_only=True) is None


def _business_input(html):
    m = re.search(r'<input[^>]*name="business_only"[^>]*>', html)
    assert m, 'business_only toggle missing'
    return m.group(0)


def _trends_json(html):
    m = re.search(r'const TRENDS = (.*);', html)
    return json.loads(m.group(1))


def test_dashboard_toggle_off(app_module, client, monkeypatch):
    monkeypatch.setattr(app_module.db, 'get_transactions', lambda *a, **k: TXS)
    resp = client.get('/')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    inp = _business_input(html)
    assert 'form="filter-form"' in inp
    assert 'checked' not in inp
    assert 'of which investments' in html
    t = _trends_json(html)
    assert t['business_only'] is False
    assert t['income'] == [2150.0, 2000.0, 87.0]


def test_dashboard_toggle_on(app_module, client, monkeypatch):
    monkeypatch.setattr(app_module.db, 'get_transactions', lambda *a, **k: TXS)
    resp = client.get('/?business_only=1')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'checked' in _business_input(html)
    assert 'toggle-chip kbd on' in html
    assert 'Business revenue' in html
    assert 'Income › Business vs Investments › Business' in html
    t = _trends_json(html)
    assert t['business_only'] is True
    assert t['income'] == [120.0, 0.0, 80.0]
    assert t['expenses'] == [45.0, 0.0, 60.0]
