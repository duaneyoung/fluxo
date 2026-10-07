import io

import pytest

import amex
import db
from amex_fixture import foreign_pdf, refund_statement, sample_statement

SAMPLE = sample_statement()

EXPECTED = [
    ('2026-09-02', 'GAMESTOP 2918 AVELLINO AVELLINO', 187.88),
    ('2026-09-03', 'FANTALAB LONDON', 16.99),
    ('2026-09-03', 'EASYPARK ITALIA S.R.L MILANO', 4.40),
    ('2026-09-05', 'EASYPARK ITALIA S.R.L MILANO', 3.46),
    ('2026-09-05', 'IL CALICE CAPACCIO PAESTU', 20.00),
    ('2026-09-07', 'EASYPARK ITALIA S.R.L MILANO', 3.55),
    ('2026-09-08', 'X CORP. PAID FEATURES BASTROP', 21.23),
    ('2026-09-15', 'LUXAIR S.A. MUNSBACH', 107.02),
    ('2026-09-28', 'PAYPAL *ITUNESAPPST APP 214284208', 9.99),
    ('2026-09-28', 'PAYPAL *GOOGLE GOOGLE O 14361001', 1.99),
    ('2026-09-30', 'ELEVENLABS.IO NEW YORK', 6.61),
    ('2026-10-02', 'QUOTA ASSOCIATIVA MENSILE ESENTE DA IVA', 70.00),
    ('2026-10-02', 'IMPOSTA DI BOLLO', 2.00),
]


@pytest.fixture
def rows():
    return amex.parse_export(SAMPLE, 'estratto.pdf')


def by_merchant(rows, prefix):
    return next(r for r in rows if r['merchant'].startswith(prefix))


def test_thirteen_charges_and_total(rows):
    assert len(rows) == 14
    included = [r for r in rows if r['include']]
    assert len(included) == 13
    assert all(r['transaction_type'] == 'outflow' for r in included)
    assert round(sum(r['amount'] for r in included), 2) == 455.12


def test_dates_merchants_amounts(rows):
    got = [(r['date'], r['merchant'], r['amount']) for r in rows if r['include']]
    assert got == EXPECTED
    # "Data operazione" is used, not the posting date (06.09).
    assert by_merchant(rows, 'IL CALICE')['date'] == '2026-09-05'


def test_statement_payment_is_internal_transfer(rows):
    pay = by_merchant(rows, 'ADDEBITO IN C/C')
    assert pay['date'] == '2026-09-11'
    assert pay['amount'] == 883.64
    assert pay['transaction_type'] == 'inflow'  # CR on the line below the amount
    assert pay['include'] is False
    assert 'internal transfer' in pay['note']


def test_sublines_totals_and_points_ignored(rows):
    text = ' '.join(r['merchant'] for r in rows)
    for junk in ('Prodotti elettronici', 'Bar, Tavola', 'ITINERARIO', 'BIGLIETTO',
                 'PASSEGGERO', 'Nuovi addebiti', 'INTERESSI', 'Totale'):
        assert junk not in text
    amounts = {r['amount'] for r in rows}
    for bad in (5499, 5.499, 664, 6163, 6.163, 453.12, 455.12, 1234.56):
        assert bad not in amounts


def test_classification(rows):
    path = lambda r: (r['category_1'], r['category_2'], r['category_3'])
    assert path(by_merchant(rows, 'GAMESTOP')) == ('Miscellaneous', 'Shopping', 'Tech')
    assert path(by_merchant(rows, 'EASYPARK')) == ('Transportation', 'Parking', '')
    assert path(by_merchant(rows, 'IL CALICE')) == ('Food', 'Drinks', '')  # via sub-line
    assert path(by_merchant(rows, 'LUXAIR')) == ('Travel', 'Flight', '')
    assert path(by_merchant(rows, 'PAYPAL *ITUNES')) == ('Subscriptions', 'iCloud', '')
    assert path(by_merchant(rows, 'PAYPAL *GOOGLE')) == ('Miscellaneous', 'Unknown', '')
    assert path(by_merchant(rows, 'QUOTA')) == ('Subscriptions', 'Amex', '')
    assert path(by_merchant(rows, 'IMPOSTA')) == ('Subscriptions', 'Amex', '')


def test_every_category_exists_in_tree(rows):
    tree = db.DEFAULT_HIERARCHY
    paths = [path for _, path, _ in amex.RULES] + list(amex.CATEGORY_MAP.values()) \
        + [amex.DEFAULT_INFLOW, amex.DEFAULT_OUTFLOW] \
        + [(r['category_1'], r['category_2'], r['category_3']) for r in rows]
    for c1, c2, c3 in paths:
        assert c1 in tree
        if c2:
            assert c2 in tree[c1]
        if c3:
            assert c3 in tree[c1][c2]


def test_method_and_totals_reconcile(rows):
    assert all(r['method'] == 'Amex' for r in rows)
    assert not any(r['note'].startswith('check:') for r in rows)


def test_refund_and_thousands():
    rows = amex.parse_export(refund_statement())
    assert [(r['amount'], r['transaction_type'], r['include']) for r in rows] == [
        (1234.56, 'outflow', True), (25.00, 'inflow', True)]


def test_parse_lines_cr_on_category_line_and_foreign_amount():
    lines = [
        '01.09.26   02.09.26   SHOP SRL ROMA                       15,00',
        '                      Abbigliamento                       CR',
        '03.09.26   04.09.26   HOTEL NY NEW YORK      USD 120,00   110,50',
        '                      Totale nuove operazioni             95,50',
    ]
    rows, total = amex.parse_lines(lines)
    assert [(r['merchant'], r['transaction_type'], r['amount']) for r in rows] == [
        ('SHOP SRL ROMA', 'inflow', 15.00), ('HOTEL NY NEW YORK', 'outflow', 110.50)]
    assert (rows[0]['category_1'], rows[0]['category_2'], rows[0]['category_3']) == \
        ('Miscellaneous', 'Shopping', 'Clothes')
    assert total == 95.50


def test_totals_mismatch_is_flagged(monkeypatch):
    real = amex.parse_lines
    monkeypatch.setattr(amex, 'parse_lines',
                        lambda lines: (real(lines)[0][:-1], real(lines)[1]))  # drop bollo
    rows = amex.parse_export(SAMPLE)
    assert any(r['note'].startswith('check:') for r in rows)


def test_errors():
    with pytest.raises(ValueError, match='Could not read'):
        amex.parse_export(b'not a pdf')
    with pytest.raises(ValueError, match='Not an American Express'):
        amex.parse_export(foreign_pdf())


def test_it_amount():
    assert amex._it_amount('1.234,56') == 1234.56
    assert amex._it_amount('2,00') == 2.0


# --- Flask wiring ---

def test_route_preview(client):
    resp = client.post('/transactions/import-amex',
                       data={'file': (io.BytesIO(SAMPLE), 'estratto.pdf')},
                       content_type='multipart/form-data')
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'Review American Express import' in body
    assert 'GAMESTOP' in body


def test_route_wrong_file(client):
    resp = client.post('/transactions/import-amex',
                       data={'file': (io.BytesIO(b'hello'), 'x.pdf')},
                       content_type='multipart/form-data')
    assert resp.status_code == 200
    assert 'Could not read the PDF' in resp.get_data(as_text=True)


def test_duplicate_flagging(app_module, monkeypatch):
    monkeypatch.setattr(app_module.db, 'get_transactions', lambda *a, **k: [
        {'date': '2026-09-03', 'amount': 16.99, 'transaction_type': 'outflow'}])
    rows = app_module._flag_duplicates(amex.parse_export(SAMPLE))
    fanta = by_merchant(rows, 'FANTALAB')
    assert fanta['include'] is False and fanta['note'] == 'possible duplicate'
    assert by_merchant(rows, 'ADDEBITO')['note'] == 'internal transfer (statement payment)'


def test_upload_form_present(client):
    resp = client.get('/transactions/add')
    assert resp.status_code == 200
    assert '/transactions/import-amex' in resp.get_data(as_text=True)
