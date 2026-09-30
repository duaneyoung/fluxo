"""
Net worth valuations — live prices with a small in-memory TTL cache.

  stocks       -> Yahoo quotes (free, no key), non-EUR auto-converted to EUR
  bitcoin      -> CoinGecko / Binance / Kraken price (EUR)
  BTC address  -> Blockstream / blockchain.info / mempool.space balance
  collectibles -> CardVault Supabase (in-stock items at slab price or
                  Cardmarket trend, same as CardVault's own market value)

Every fetcher fails soft (returns None) so the page renders even when a
provider is down.
"""
import os
import threading
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

CARDVAULT_URL = os.environ.get('CARDVAULT_SUPABASE_URL')
CARDVAULT_KEY = os.environ.get('CARDVAULT_SUPABASE_KEY')

_cache = {}
_TTL = 600  # seconds
_FAIL_TTL = 120  # after a failed fetch, don't retry (and re-wait) for this long

_last_good = {}  # key -> value; survives TTL expiry as a stale fallback
_failed = {}     # key -> ts of the last failed fetch (negative cache)


def _cached(key):
    """Fresh cached value, else None. After a recent failure, returns the
    last good value instead so a dead provider doesn't cost a timeout on
    every page load."""
    hit = _cache.get(key)
    if hit and time.time() - hit[1] < _TTL:
        return hit[0]
    if time.time() - _failed.get(key, 0) < _FAIL_TTL:
        return _last_good.get(key)
    return None


def _store(key, value):
    _cache[key] = (value, time.time())
    _last_good[key] = value
    _failed.pop(key, None)
    return value


def _fail(key):
    """Record a failed fetch; returns the stale fallback (or None)."""
    _failed[key] = time.time()
    return _last_good.get(key)


def last_good(key):
    return _last_good.get(key)


def clear_cache():
    """Drop all cached quotes so the next compute re-fetches everything.
    _last_good survives — it's the stale-fallback, not a freshness cache."""
    _cache.clear()
    _failed.clear()


def section_fetch_times():
    """Oldest successful fetch per section (unix ts) — conservative, so the
    'updated at' label never claims data is fresher than its stalest quote."""
    def oldest(pred):
        ts = [t for k, (_, t) in _cache.items() if pred(k)]
        return min(ts) if ts else None
    return {
        'markets': oldest(lambda k: k.startswith(('q:', 'w:', 'o:'))),
        'crypto': oldest(lambda k: k == 'btc' or k.startswith('addr:')),
        'collectibles': oldest(lambda k: k == 'cardvault'),
    }


def _get(url, **kw):
    return httpx.get(url, timeout=kw.pop('timeout', 8), follow_redirects=True, **kw)


def btc_price_eur():
    """BTC price in EUR with a provider fallback chain — CoinGecko rate-limits
    datacenter IPs (e.g. Render), so a single source blanks out intermittently."""
    if (v := _cached('btc')) is not None:
        return v

    providers = (
        lambda: float(_get('https://api.coingecko.com/api/v3/simple/price',
                           params={'ids': 'bitcoin', 'vs_currencies': 'eur'})
                      .json()['bitcoin']['eur']),
        lambda: float(_get('https://api.binance.com/api/v3/ticker/price',
                           params={'symbol': 'BTCEUR'}).json()['price']),
        lambda: float(next(iter(_get('https://api.kraken.com/0/public/Ticker',
                                     params={'pair': 'XBTEUR'})
                                .json()['result'].values()))['c'][0]),
    )
    for fetch in providers:
        try:
            return _store('btc', fetch())
        except Exception:
            continue
    # All providers down: serve the last price we ever saw rather than blanking.
    return _fail('btc')


_UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
_fx_lock = threading.Lock()


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
    # Single-flight: concurrent USD quotes share one FX fetch instead of
    # each hitting Yahoo (which throttles bursts).
    with _fx_lock:
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
        return _fail(key)


def option_quote_eur(occ_symbol):
    """Live per-share premium for a US option contract (OCC symbol, e.g.
    AMZN260821C00280000) via Yahoo, converted to EUR. Position value is
    premium × 100 × signed quantity — applied by the caller."""
    key = f'o:{occ_symbol.upper()}'
    if (v := _cached(key)) is not None:
        return v
    try:
        price, currency = _yahoo_quote(occ_symbol)
        return _store(key, round(price * _fx_to_eur(currency), 3))
    except Exception:
        return _fail(key)


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
            return _fail(key)
        return _store(key, round(float(price), 3))
    except Exception:
        return _fail(key)


def _esplora_balance(base, address):
    """Esplora API (Blockstream / mempool.space): confirmed + unconfirmed sats."""
    d = _get(f'{base}/api/address/{address}', timeout=6).json()
    c, m = d['chain_stats'], d['mempool_stats']
    return (c['funded_txo_sum'] - c['spent_txo_sum']
            + m['funded_txo_sum'] - m['spent_txo_sum'])


def btc_address_balance(address):
    """Balance of a BTC address in BTC (incl. unconfirmed), with a provider
    fallback chain. mempool.space alone blanked out intermittently (DNS/rate
    limits), which silently froze the wallet at its typed-in quantity."""
    key = f'addr:{address}'
    if (v := _cached(key)) is not None:
        return v
    providers = (
        lambda: _esplora_balance('https://blockstream.info', address),
        lambda: int(_get('https://blockchain.info/balance',
                         params={'active': address}, timeout=6)
                    .json()[address]['final_balance']),
        lambda: _esplora_balance('https://mempool.space', address),
    )
    for fetch in providers:
        try:
            return _store(key, round(fetch() / 1e8, 8))
        except Exception:
            continue
    return _fail(key)


def _fetch_all(client, table, cols, page_size=1000):
    """Every row of a table, paging past PostgREST's 1000-row cap."""
    out, start = [], 0
    while True:
        page = client.table(table).select(cols) \
            .range(start, start + page_size - 1).execute().data
        out.extend(page)
        if len(page) < page_size:
            return out
        start += page_size


def _norm_company(s):
    """Mirror of CardVault graded_prices.norm_company."""
    s = (s or '').strip().upper()
    return {'BECKETT': 'BGS', 'BVG': 'BGS', 'CGC CARDS': 'CGC',
            'TAG GRADING': 'TAG', 'ACE GRADING': 'ACE'}.get(s, s)


def _norm_grade(s):
    """Mirror of CardVault graded_prices.norm_grade: '10.0' -> '10',
    '10 Black Label' -> '10 BL', 'Pristine 10' -> '10 P'."""
    import re
    raw = str(s if s is not None else '')
    m = re.search(r'\d+(?:[.,]\d+)?', raw)
    if not m:
        return ''
    v = float(m.group(0).replace(',', '.'))
    g = str(int(v)) if v == int(v) else str(v)
    low = raw.lower()
    if 'black' in low:
        return g + ' BL'
    if 'pristine' in low:
        return g + ' P'
    return g


_cv_client = None  # CardVault Supabase client, created once per process


def cardvault_snapshot():
    """Collectibles valuation straight from CardVault's Supabase, matching
    CardVault's own market value: in-stock items at slab price (graded +
    Collectr-linked) or Cardmarket trend, cost as fallback per item."""
    if (v := _cached('cardvault')) is not None:
        return v
    if not (CARDVAULT_URL and CARDVAULT_KEY):
        return None
    try:
        from supabase import create_client
        global _cv_client
        if _cv_client is None:
            _cv_client = create_client(CARDVAULT_URL, CARDVAULT_KEY)
        client = _cv_client
        purchases = _fetch_all(
            client, 'purchases',
            'code,purchase_price,grading_cost,cardmarket_id,in_bundle,'
            'graded,grade,grading_company,collectr_id,price_company')
        sold = {r['item_code'] for r in _fetch_all(client, 'sales', 'item_code')}
        trends = {r['id_product']: r.get('trend') for r in
                  _fetch_all(client, 'market_prices', 'id_product,trend')}
        # Slab prices (Collectr) — CardVault values graded items linked to a
        # Collectr product at the price for their company + grade, ahead of
        # the raw Cardmarket trend. Fail-soft if the table doesn't exist.
        try:
            graded = {(str(r['collectr_id']), r['grading_company'], r['grade']): r.get('price')
                      for r in _fetch_all(client, 'graded_prices',
                                          'collectr_id,grading_company,grade,price')}
        except Exception:
            graded = {}
        # Set -> member purchase codes. Sets have no Cardmarket link of their
        # own; CardVault values them at the sum of their members' trends, so
        # mirror that here to keep the collectibles figure identical.
        try:
            bm = _fetch_all(client, 'bundle_members', 'set_code,purchase_code')
        except Exception:
            bm = []
        members_by_set = {}
        for r in bm:
            members_by_set.setdefault(r.get('set_code'), []).append(r.get('purchase_code'))
        row_by_code = {p['code']: p for p in purchases}

        # Exclude cards consumed into a set (in_bundle): the set row already
        # carries their value, so counting both double-counts. This mirrors
        # CardVault's get_purchases(), keeping the two apps aligned.
        in_stock = [p for p in purchases
                    if p['code'] not in sold and not p.get('in_bundle')]

        def f(x):
            return float(x) if x is not None else 0.0

        def own_trend(p):
            t = trends.get(p.get('cardmarket_id')) if p.get('cardmarket_id') else None
            return float(t) if t is not None else None

        def set_trend(set_code):
            """Sum of the set's linked members' trends; None if none linked."""
            total, linked = 0.0, 0
            for mc in members_by_set.get(set_code, []):
                m = row_by_code.get(mc)
                if not m:
                    continue
                t = own_trend(m)
                if t is not None:
                    total += t
                    linked += 1
            return round(total, 2) if linked else None

        def slab_price(p):
            """Collectr slab price for a graded, Collectr-linked item, keyed
            like CardVault's _graded_key (proxy company wins if set)."""
            cid = str(p.get('collectr_id') or '').strip()
            if not cid or (p.get('graded') or 'N') != 'Y':
                return None
            company = (p.get('price_company') or '').strip() or p.get('grading_company')
            price = graded.get((cid, _norm_company(company), _norm_grade(p.get('grade'))))
            return float(price) if price is not None else None

        def market(p):
            """Market value for a position, same precedence as CardVault's
            get_purchases: set members' sum for a set row, else slab price,
            else the item's own Cardmarket trend. None when unpriced."""
            if p['code'] in members_by_set:
                return set_trend(p['code'])
            s = slab_price(p)
            return s if s is not None else own_trend(p)

        def cost_of(p):
            return f(p['purchase_price']) + f(p['grading_cost'])

        cost = sum(cost_of(p) for p in in_stock)
        value = sum(
            m if (m := market(p)) is not None else cost_of(p)
            for p in in_stock)
        priced = sum(1 for p in in_stock if market(p) is not None)
        return _store('cardvault', {
            'items': len(in_stock),
            'priced': priced,
            'cost': round(cost, 2),
            'value': round(value, 2),
        })
    except Exception as exc:
        print(f'[networth] CardVault snapshot failed: {exc}')
        return _fail('cardvault')
