"""
Net worth valuations — live prices with a small in-memory TTL cache.

  stocks       -> Stooq quotes (free, no key), USD auto-converted to EUR
  bitcoin      -> CoinGecko simple price (EUR)
  BTC address  -> mempool.space balance (optional per-wallet)
  collectibles -> CardVault Supabase (in-stock items at Cardmarket trend)

Every fetcher fails soft (returns None) so the page renders even when a
provider is down.
"""
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

CARDVAULT_URL = os.environ.get('CARDVAULT_SUPABASE_URL')
CARDVAULT_KEY = os.environ.get('CARDVAULT_SUPABASE_KEY')

_cache = {}
_TTL = 600  # seconds


def _cached(key):
    hit = _cache.get(key)
    if hit and time.time() - hit[1] < _TTL:
        return hit[0]
    return None


def _store(key, value):
    _cache[key] = (value, time.time())
    return value


def _get(url, **kw):
    return httpx.get(url, timeout=8, follow_redirects=True, **kw)


def btc_price_eur():
    if (v := _cached('btc')) is not None:
        return v
    try:
        r = _get('https://api.coingecko.com/api/v3/simple/price',
                 params={'ids': 'bitcoin', 'vs_currencies': 'eur'})
        return _store('btc', float(r.json()['bitcoin']['eur']))
    except Exception:
        return None


_UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}


def _yahoo_quote(symbol):
    """(price, currency) from Yahoo's chart endpoint."""
    r = _get(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}',
             headers=_UA)
    meta = r.json()['chart']['result'][0]['meta']
    return float(meta['regularMarketPrice']), meta.get('currency', 'EUR')


def _fx_to_eur(currency):
    """Conversion rate: 1 unit of `currency` in EUR."""
    if currency == 'EUR':
        return 1.0
    key = f'fx:{currency}'
    if (v := _cached(key)) is not None:
        return v
    price, _ = _yahoo_quote(f'{currency}EUR=X')
    return _store(key, price)


def stock_quote_eur(symbol):
    """Live price for a Yahoo ticker (e.g. AAPL, VWCE.DE), converted to EUR."""
    key = f'q:{symbol.upper()}'
    if (v := _cached(key)) is not None:
        return v
    try:
        price, currency = _yahoo_quote(symbol)
        return _store(key, round(price * _fx_to_eur(currency), 2))
    except Exception:
        return None


def warrant_quote_eur(isin):
    """Live quote for a German-listed warrant/certificate via Onvista (EUR).
    Uses last trade, falling back to bid (thin issuer paper often has no last)."""
    key = f'w:{isin.upper()}'
    if (v := _cached(key)) is not None:
        return v
    try:
        r = _get(f'https://api.onvista.de/api/v1/derivatives/ISIN:{isin.upper()}/snapshot',
                 headers=_UA)
        q = r.json().get('quote', {})
        price = q.get('last') if q.get('last') is not None else q.get('bid')
        if price is None:
            return None
        return _store(key, round(float(price), 3))
    except Exception:
        return None


def btc_address_balance(address):
    """Confirmed balance of a BTC address, in BTC."""
    key = f'addr:{address}'
    if (v := _cached(key)) is not None:
        return v
    try:
        r = _get(f'https://mempool.space/api/address/{address}')
        d = r.json()['chain_stats']
        sats = d['funded_txo_sum'] - d['spent_txo_sum']
        return _store(key, round(sats / 1e8, 8))
    except Exception:
        return None


def cardvault_snapshot():
    """Collectibles valuation straight from CardVault's Supabase:
    in-stock items at Cardmarket trend (cost as fallback per item)."""
    if (v := _cached('cardvault')) is not None:
        return v
    if not (CARDVAULT_URL and CARDVAULT_KEY):
        return None
    try:
        from supabase import create_client
        client = create_client(CARDVAULT_URL, CARDVAULT_KEY)
        purchases = client.table('purchases').select(
            'code,purchase_price,grading_cost,cardmarket_id').execute().data
        sold = {r['item_code'] for r in
                client.table('sales').select('item_code').execute().data}
        trends = {r['id_product']: r.get('trend') for r in
                  client.table('market_prices').select('id_product,trend').execute().data}
        in_stock = [p for p in purchases if p['code'] not in sold]

        def f(x):
            return float(x) if x is not None else 0.0

        def trend(p):
            t = trends.get(p.get('cardmarket_id')) if p.get('cardmarket_id') else None
            return float(t) if t is not None else None

        cost = sum(f(p['purchase_price']) + f(p['grading_cost']) for p in in_stock)
        value = sum(
            t if (t := trend(p)) is not None
            else f(p['purchase_price']) + f(p['grading_cost'])
            for p in in_stock)
        priced = sum(1 for p in in_stock if trend(p) is not None)
        return _store('cardvault', {
            'items': len(in_stock),
            'priced': priced,
            'cost': round(cost, 2),
            'value': round(value, 2),
        })
    except Exception:
        return None
