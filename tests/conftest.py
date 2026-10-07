import os
import sys
from datetime import date

import pytest

# Force demo mode (in-memory store) and an open auth gate BEFORE db/app are
# imported — load_dotenv() won't override variables that are already set,
# so the real Supabase credentials in .env are never used by the tests.
os.environ['SUPABASE_URL'] = ''
os.environ['SUPABASE_KEY'] = ''
os.environ['APP_PASSWORD'] = ''

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def app_module(monkeypatch):
    import app as app_module
    # Pre-claim today's net-worth snapshot so no background thread (and no
    # external HTTP) is spawned by the before-request hook.
    app_module._snap_state['date'] = date.today().isoformat()
    assert app_module.db.DEMO
    monkeypatch.setattr(app_module.db, 'get_transactions', lambda *a, **k: [])
    return app_module


@pytest.fixture
def client(app_module):
    return app_module.app.test_client()
