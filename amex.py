"""
American Express Italia import — parses the monthly "Estratto Conto" PDF
(Carta Platino, Italian locale) into Fluxo preview rows.

The transaction table has the columns "Data operazione" | "Contabilizzata
in data" | "Descrizione dell'operazione" | "Importo in valuta non Euro" |
"Importo in Euro". Plain text extraction follows the PDF content stream,
which writes the amounts column as a separate block after the
descriptions, so rows and amounts come out detached. Instead we use
pypdf's layout mode, which places glyphs by their x/y coordinates and
gives one visual line per table row.

Classification mirrors intesa.py: merchant-keyword rules first, then a
fallback map from Amex's merchant-category sub-line ("Prodotti
elettronici", "Bar, Tavola Calda, Pub", ...). The statement payment
("ADDEBITO IN C/C SALVO BUON FINE ... CR", the direct debit from the
current account) is kept but pre-deselected as an internal transfer, as
intesa.py does. Duplicate flagging is shared with the other bank imports
(app._flag_duplicates).
"""
import io
import re
from datetime import datetime

AMOUNT = r'\d{1,3}(?:\.\d{3})*,\d{2}'

# "02.09.26   03.09.26   GAMESTOP 2918 AVELLINO AVELLINO   187,88 [CR]"
# Two leading DD.MM.YY dates, then the description; the LAST amount on the
# line is the EUR column (an optional foreign-currency amount sits before it).
TX_RE = re.compile(
    rf'^\s*(\d{{2}}\.\d{{2}}\.\d{{2}})\s+(\d{{2}}\.\d{{2}}\.\d{{2}})\s+(.+?)\s+({AMOUNT})(?:\s+(CR))?\s*$')
TOTAL_RE = re.compile(rf'^\s*Totale\b.*?({AMOUNT})\s*$', re.I)
FOREIGN_TAIL_RE = re.compile(rf'\s+(?:[A-Z]{{3}}\s+)?{AMOUNT}(?:\s+[A-Z]{{3}})?$')
CR_TAIL_RE = re.compile(r'(?:^|\s)CR\s*$')

# --- Merchant keyword rules (checked first) ---
# (keyword in description, (cat1, cat2, cat3), restrict to tx_type or None)
# First match wins — order matters.
RULES = [
    ('quota associativa', ('Subscriptions', 'Amex', ''), None),   # card fee
    ('imposta di bollo',  ('Subscriptions', 'Amex', ''), None),   # stamp duty
    ('easypark',          ('Transportation', 'Parking', ''), None),
    ('itunes',            ('Subscriptions', 'iCloud', ''), None),  # before paypal
    ('apple.com',         ('Subscriptions', 'iCloud', ''), None),
    ('paypal',            ('Miscellaneous', 'Unknown', ''), None),
    ('gamestop',          ('Miscellaneous', 'Shopping', 'Tech'), None),
    ('elevenlabs',        ('Miscellaneous', 'Shopping', 'Tech'), None),
    ('amazon',            ('Miscellaneous', 'Shopping', 'Tech'), None),
    ('fantalab',          ('Miscellaneous', 'Entertainment', ''), None),
    ('x corp',            ('Miscellaneous', 'Entertainment', ''), None),
    ('luxair',            ('Travel', 'Flight', ''), None),
    ('ryanair',           ('Travel', 'Flight', ''), None),
    ('easyjet',           ('Travel', 'Flight', ''), None),
    ('lufthansa',         ('Travel', 'Flight', ''), None),
    ('ita airways',       ('Travel', 'Flight', ''), None),
    ('uber',              ('Transportation', 'Uber', ''), None),
    ('mcdonald',          ('Food', 'Dinner', ''), None),
]

# --- Amex merchant-category sub-line -> Fluxo (cat1, cat2, cat3) fallback ---
# Matched by substring on the lowercased label (first key found wins).
CATEGORY_MAP = {
    'prodotti elettronici':  ('Miscellaneous', 'Shopping', 'Tech'),
    'bar, tavola calda, pub': ('Food', 'Drinks', ''),
    'ristorant':             ('Food', 'Dinner', ''),
    'supermercat':           ('Food', 'Groceries', ''),
    'alimentari':            ('Food', 'Groceries', ''),
    'carburant':             ('Transportation', 'Gas', ''),
    'stazioni di servizio':  ('Transportation', 'Gas', ''),
    'parchegg':              ('Transportation', 'Parking', ''),
    'compagnie aeree':       ('Travel', 'Flight', ''),
    'linee aeree':           ('Travel', 'Flight', ''),
    'hotel':                 ('Travel', 'Hotel', ''),
    'alberghi':              ('Travel', 'Hotel', ''),
    'abbigliamento':         ('Miscellaneous', 'Shopping', 'Clothes'),
    'farmaci':               ('Miscellaneous', 'Personal', 'Health'),
}

DEFAULT_OUTFLOW = ('Miscellaneous', 'Unknown', '')
DEFAULT_INFLOW = ('Income', '', '')

# Payment of the previous statement by direct debit from the current
# account — already booked on the Intesa side; pre-deselected in preview.
INTERNAL_PATTERNS = ('addebito in c/c',)

# Airline itinerary sub-lines — never taken as the category hint.
SUBLINE_SKIP = ('itinerario', 'da:', 'a:', 'vettore', 'classe',
                'num. biglietto', 'nome passeggero')


def classify(text, amex_category, tx_type):
    t = (text or '').lower()
    for key, path, only in RULES:
        if key in t and (only is None or only == tx_type):
            return path
    cat = (amex_category or '').lower()
    for key, path in CATEGORY_MAP.items():
        if key in cat:
            return path
    return DEFAULT_INFLOW if tx_type == 'inflow' else DEFAULT_OUTFLOW


def _it_amount(s):
    """'1.234,56' -> 1234.56"""
    return float(s.replace('.', '').replace(',', '.'))


def _read_lines(file_bytes):
    """Return (visual lines across all pages, collapsed lowercase text)."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        pages = [page.extract_text(extraction_mode='layout') or ''
                 for page in reader.pages]
    except Exception:
        raise ValueError('Could not read the PDF — is it an American Express statement?')
    lines = [ln for p in pages for ln in p.splitlines()]
    flat = ' '.join(' '.join(pages).split()).lower()
    return lines, flat


def parse_lines(lines):
    """Parse extracted visual lines into preview rows. Returns
    (rows, statement_total or None)."""
    entries = []          # (op_date, description, amount, credit, hint)
    statement_total = None
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        i += 1
        tot = TOTAL_RE.match(line)
        if tot:
            statement_total = (statement_total or 0) + _it_amount(tot.group(1))
            continue
        m = TX_RE.match(line)
        if not m:
            continue  # headers, headings, summary box, legal text, points
        try:
            op_date = datetime.strptime(m.group(1), '%d.%m.%y').date()
        except ValueError:
            continue
        desc = ' '.join(FOREIGN_TAIL_RE.sub('', m.group(3)).split())
        amount = _it_amount(m.group(4))
        credit = m.group(5) == 'CR'

        # The line right below may carry "CR" (credit marker under the
        # amount) and/or the merchant-category label.
        hint = ''
        j = i
        while j < n and not lines[j].strip():
            j += 1
        if j < n and not TX_RE.match(lines[j]) and not TOTAL_RE.match(lines[j]):
            sub = lines[j].strip()
            if CR_TAIL_RE.search(sub):
                credit = True
                sub = CR_TAIL_RE.sub('', sub).strip()
                if not sub:
                    i = j + 1  # consume the bare "CR" line
            low = sub.lower()
            if sub and not low.startswith(SUBLINE_SKIP) \
                    and any(k in low for k in CATEGORY_MAP):
                hint = sub
        entries.append((op_date, desc, amount, credit, hint))

    out = []
    for op_date, desc, amount, credit, hint in entries:
        tx_type = 'inflow' if credit else 'outflow'
        include, note = True, ''
        if credit and any(p in desc.lower() for p in INTERNAL_PATTERNS):
            include, note = False, 'internal transfer (statement payment)'
        c1, c2, c3 = classify(desc, hint, tx_type)
        out.append({
            'date': op_date.isoformat(),
            'merchant': desc,
            'transaction_type': tx_type,
            'amount': round(amount, 2),
            'category_1': c1, 'category_2': c2, 'category_3': c3,
            'method': 'Amex',
            'include': include,
            'note': note,
        })
    return out, statement_total


def parse_export(file_bytes, filename=None):
    """Parse an American Express "Estratto Conto" PDF into preview rows
    (same dict shape revolut.parse_export produces, plus method)."""
    lines, flat = _read_lines(file_bytes)
    if 'american express' not in flat or 'estratto conto' not in flat:
        raise ValueError('Not an American Express statement (Estratto Conto PDF expected)')

    out, statement_total = parse_lines(lines)
    if not out:
        raise ValueError('No transactions found — is it an American Express "Estratto Conto"?')

    # Sanity check against the statement's own "Totale ..." lines. Don't
    # block the import (the user reviews the preview anyway) — surface it.
    if statement_total is not None:
        net = sum(r['amount'] if r['transaction_type'] == 'outflow' else -r['amount']
                  for r in out if r['note'] != 'internal transfer (statement payment)')
        if abs(net - statement_total) > 0.005:
            first = next((r for r in out if not r['note']), None)
            if first:
                first['note'] = f'check: rows total {net:.2f} vs statement {statement_total:.2f}'
    return out
