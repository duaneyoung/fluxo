"""Tests for the /api/v1 JSON Blueprint. Demo mode only (see conftest.py):
no Supabase, and _compute_networth is stubbed so no quote is fetched."""
import pytest

import db

TOKEN = 'test-token-not-a-secret'
AUTH = {'Authorization': f'Bearer {TOKEN}'}

# Captured at import, before conftest's app_module fixture stubs it to [].
_REAL_GET_TRANSACTIONS = db.get_transactions


@pytest.fixture
def api(app_module, client, monkeypatch):
    monkeypatch.setenv('API_TOKEN', TOKEN)
    # Use the real in-memory demo store, emptied and isolated per test.
    monkeypatch.setattr(db, '_demo_txs', [])
    monkeypatch.setattr(db, '_demo_seq', {'id': 0})
    monkeypatch.setattr(db, '_demo_seeded', {'done': True})
    monkeypatch.setattr(db, '_demo_settings',
                        {'currency': 'EUR', 'category_hierarchy': db.DEFAULT_HIERARCHY})
    monkeypatch.setattr(db, 'get_transactions', _REAL_GET_TRANSACTIONS)
    return client


TX = {'date': '2025-03-04', 'transaction_type': 'outflow', 'amount': '12.50',
      'category_1': 'Food', 'category_2': 'Lunch', 'category_3': '',
      'method': 'Card', 'details': 'Sandwich', 'is_one_off': False}


# --- auth ---
def test_fail_closed_when_token_unset(app_module, client, monkeypatch):
    monkeypatch.delenv('API_TOKEN', raising=False)
    resp = client.get('/api/v1/meta', headers=AUTH)
    assert resp.status_code == 503
    assert resp.get_json() == {'error': 'api_disabled'}


def test_fail_closed_even_with_password_set(app_module, client, monkeypatch):
    monkeypatch.delenv('API_TOKEN', raising=False)
    monkeypatch.setattr(app_module, 'APP_PASSWORD', 'pw')
    resp = client.get('/api/v1/meta')
    assert resp.status_code == 503
    assert resp.is_json


@pytest.mark.parametrize('headers', [
    {}, {'Authorization': 'Bearer wrong'}, {'Authorization': TOKEN},
    {'Authorization': 'Basic ' + TOKEN}, {'Authorization': 'Bearer '},
])
def test_401_json_never_redirect(app_module, client, monkeypatch, headers):
    monkeypatch.setenv('API_TOKEN', TOKEN)
    monkeypatch.setattr(app_module, 'APP_PASSWORD', 'pw')
    resp = client.get('/api/v1/transactions', headers=headers)
    assert resp.status_code == 401
    assert resp.get_json() == {'error': 'unauthorized'}
    assert 'Location' not in resp.headers


def test_session_cookie_not_accepted(app_module, client, monkeypatch):
    monkeypatch.setenv('API_TOKEN', TOKEN)
    monkeypatch.setattr(app_module, 'APP_PASSWORD', 'pw')
    with client.session_transaction() as sess:
        sess['auth'] = True
    assert client.get('/api/v1/meta').status_code == 401


def test_web_routes_still_redirect(app_module, client, monkeypatch):
    monkeypatch.setattr(app_module, 'APP_PASSWORD', 'pw')
    resp = client.get('/transactions')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


# --- read endpoints ---
def test_meta(api, app_module, monkeypatch):
    monkeypatch.setattr(app_module, 'APP_PASSWORD', 'pw')  # token bypasses session
    api.post('/api/v1/transactions', json=TX, headers=AUTH)
    body = api.get('/api/v1/meta', headers=AUTH).get_json()
    assert body['currency'] == 'EUR'
    assert body['symbol'] == '€'
    assert body['hierarchy'] == db.DEFAULT_HIERARCHY
    assert body['quick_categories'] == db.QUICK_CATEGORIES
    assert body['months'] == ['2025-03']


def test_dashboard(api):
    api.post('/api/v1/transactions', json=TX, headers=AUTH)
    resp = api.get('/api/v1/dashboard?timeframe=all&business_only=1'
                   '&compare=2025-03', headers=AUTH)
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) >= {'stats', 'breakdown', 'chart', 'recent', 'trends',
                         'months', 'selected_compare'}
    assert body['stats']['outflow'] == 12.5
    assert body['selected_compare'] == ['2025-03']
    assert body['trends']['business_only'] is True
    assert body['recent'][0]['details'] == 'Sandwich'


def test_list_transactions_paging_and_summary(api):
    for i in range(5):
        api.post('/api/v1/transactions',
                 json={**TX, 'date': f'2025-03-0{i + 1}', 'details': f'tx{i}'},
                 headers=AUTH)
    api.post('/api/v1/transactions',
             json={**TX, 'transaction_type': 'inflow', 'amount': 100,
                   'category_1': 'Income'}, headers=AUTH)
    body = api.get('/api/v1/transactions?limit=2&offset=1', headers=AUTH).get_json()
    assert body['total'] == 6
    assert body['limit'] == 2 and body['offset'] == 1
    assert len(body['items']) == 2
    assert body['summary'] == {'inflow': 100.0, 'outflow': 62.5, 'net': 37.5,
                               'count': 6, 'investments': 0}
    filtered = api.get('/api/v1/transactions?search=tx3', headers=AUTH).get_json()
    assert [t['details'] for t in filtered['items']] == ['tx3']


# --- write endpoints ---
def test_create_returns_row(api):
    resp = api.post('/api/v1/transactions', json=TX, headers=AUTH)
    assert resp.status_code == 201
    row = resp.get_json()
    assert isinstance(row['id'], int)
    assert row['amount'] == 12.5
    assert row['date'] == '2025-03-04'
    assert row['category_2'] == 'Lunch'
    assert row['is_one_off'] is False
    got = api.get(f"/api/v1/transactions/{row['id']}", headers=AUTH).get_json()
    assert got == row


def test_create_rejects_bad_input(api):
    resp = api.post('/api/v1/transactions',
                    json={'date': 'nope', 'transaction_type': 'x'}, headers=AUTH)
    assert resp.status_code == 400
    assert resp.get_json() == {'error': 'invalid',
                               'fields': ['date', 'transaction_type']}
    assert db._demo_txs == []


def test_update_returns_row(api):
    tx_id = api.post('/api/v1/transactions', json=TX, headers=AUTH).get_json()['id']
    resp = api.put(f'/api/v1/transactions/{tx_id}',
                   json={**TX, 'amount': 20, 'details': 'Pasta'}, headers=AUTH)
    assert resp.status_code == 200
    row = resp.get_json()
    assert row['id'] == tx_id and row['amount'] == 20.0 and row['details'] == 'Pasta'


def test_update_missing_is_404(api):
    resp = api.put('/api/v1/transactions/999', json=TX, headers=AUTH)
    assert resp.status_code == 404
    assert resp.get_json() == {'error': 'not_found'}


def test_delete(api):
    tx_id = api.post('/api/v1/transactions', json=TX, headers=AUTH).get_json()['id']
    resp = api.delete(f'/api/v1/transactions/{tx_id}', headers=AUTH)
    assert resp.get_json() == {'deleted': tx_id}
    assert api.get(f'/api/v1/transactions/{tx_id}', headers=AUTH).status_code == 404


# --- net worth ---
NW = {'markets': [{'id': 1, 'type': 'Stock', 'ident': 'AMZN', 'value': 100.0}],
      'crypto': [], 'manual': [], 'cards': {'value': 50.0},
      'totals': {'markets': 100.0, 'crypto': 0, 'other': 0,
                 'collectibles': 50.0, 'net': 150.0},
      'btc': 90000.0, 'table_missing': False, 'complete': True}


@pytest.fixture
def nw_stubs(app_module, monkeypatch):
    calls = {'compute': 0, 'saved': [], 'cleared': 0}

    def compute(budget=None):
        calls['compute'] += 1
        return NW

    import networth
    monkeypatch.setattr(app_module, '_compute_networth', compute)
    monkeypatch.setattr(db, 'save_networth_snapshot',
                        lambda totals: calls['saved'].append(totals) or True)
    monkeypatch.setattr(db, 'get_networth_history',
                        lambda: [{'date': '2025-03-01', 'total': 140.0}])
    monkeypatch.setattr(networth, 'clear_cache',
                        lambda: calls.__setitem__('cleared', calls['cleared'] + 1))
    return calls


def test_networth_saves_snapshot(api, nw_stubs):
    resp = api.get('/api/v1/networth?refresh=1', headers=AUTH)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['totals']['net'] == 150.0
    assert body['complete'] is True
    assert body['history'] == [{'date': '2025-03-01', 'total': 140.0}]
    assert set(body['fetch_times']) == {'markets', 'crypto', 'collectibles'}
    assert nw_stubs['saved'] == [NW['totals']]
    assert nw_stubs['cleared'] == 1


def test_networth_partial_does_not_save(api, nw_stubs, app_module, monkeypatch):
    monkeypatch.setattr(app_module, '_compute_networth',
                        lambda budget=None: {**NW, 'complete': False})
    body = api.get('/api/v1/networth', headers=AUTH).get_json()
    assert body['complete'] is False
    assert nw_stubs['saved'] == []


def test_snapshot_hook_skips_api_networth(api, nw_stubs, app_module, monkeypatch):
    spawned = []
    monkeypatch.setattr(app_module, '_take_daily_snapshot',
                        lambda: spawned.append(1))
    app_module._snap_state['date'] = None  # unclaimed day
    api.get('/api/v1/networth', headers=AUTH)
    assert app_module._snap_state['date'] is None
    assert spawned == []
