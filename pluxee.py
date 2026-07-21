"""
Pluxee import — parses the "Transaction report" PDF from the Pluxee
Luxembourg portal into Fluxo preview rows.

It's a lunch-voucher card, so nearly everything is Food; the time of
day separates breakfast from lunch/dinner, and the monthly "Reloading"
credit is the salary voucher top-up. Same philosophy as revolut.py:
deterministic rules, the editable preview screen handles the tail.
"""
import io
import re
from datetime import datetime

# One entry: "17-Jul-2026 10:20:21 <merchant> [Purchase|Reloading] -10.90 EUR"
# (label and amount may sit on their own lines in the extracted text)
ENTRY_RE = re.compile(
    r'(\d{2}-[A-Za-z]{3}-\d{4})\s+(\d{2}):\d{2}:\d{2}\s+(.+?)\s+(-?\d+[.,]\d{2})\s*EUR',
    re.DOTALL)

SUPERMARKETS = ('auchan', 'proxy', 'delhaize', 'carrefour', 'cactus',
                'lidl', 'aldi', 'match', 'monop')


def _classify(merchant, hour, tx_type):
    m = merchant.lower()
    if tx_type == 'inflow':
        return ('Income', 'Salary', 'Vouchers')  # monthly voucher reload
    if 'kiosk' in m:
        return ('Food', 'Snacks', '')
    if any(s in m for s in SUPERMARKETS):
        return ('Food', 'Groceries', '')
    if hour < 10:
        return ('Food', 'Breakfast', 'Office')
    if hour >= 17:
        return ('Food', 'Dinner', '')
    return ('Food', 'Lunch', '')


def parse_export(file_bytes, filename=None):
    """Parse a Pluxee transaction-report PDF into preview rows
    (same dict shape revolut.parse_export produces, plus method)."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        text = '\n'.join((page.extract_text() or '') for page in reader.pages)
    except Exception:
        raise ValueError('Could not read the PDF — is it a Pluxee transaction report?')
    if 'pluxee' not in text.lower():
        raise ValueError('Not a Pluxee transaction report')

    out = []
    for d, hh, merchant, amount in ENTRY_RE.findall(text):
        try:
            iso = datetime.strptime(d, '%d-%b-%Y').date().isoformat()
            value = float(amount.replace(',', '.'))
        except ValueError:
            continue
        # The extracted merchant may carry the "Purchase"/"Reloading" label
        # on a following line — drop those tokens, keep the rest.
        merchant = ' '.join(w for w in merchant.split()
                            if w.lower() not in ('purchase', 'reloading')).strip()
        tx_type = 'inflow' if value > 0 else 'outflow'
        c1, c2, c3 = _classify(merchant, int(hh), tx_type)
        out.append({
            'date': iso,
            'merchant': merchant,
            'transaction_type': tx_type,
            'amount': round(abs(value), 2),
            'category_1': c1, 'category_2': c2, 'category_3': c3,
            'method': 'Pluxee',
            'include': True,
            'note': '',
        })
    if not out:
        raise ValueError('No transactions found — is it a Pluxee transaction report?')
    return out
