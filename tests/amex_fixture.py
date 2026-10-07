"""Synthetic American Express "Estratto Conto" PDFs for the amex.py tests,
built with pypdf only (already a dependency). Text positions follow the
real statement's table columns; the amounts column is written as a
separate block at the end of each content stream, reproducing the
extraction order of the real PDF."""
import io

from pypdf import PdfWriter
from pypdf.generic import (DecodedStreamObject, DictionaryObject, NameObject)

# Column x-positions: Data operazione | Contabilizzata | Descrizione |
# Importo in valuta non Euro | Importo in Euro
X_OP, X_POST, X_DESC, X_FX, X_EUR = 40, 90, 150, 400, 520


def _esc(s):
    return s.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def build_pdf(pages):
    """pages: list of [(x, y, text), ...] in content-stream order."""
    writer = PdfWriter()
    for ops in pages:
        page = writer.add_blank_page(595, 842)
        font = DictionaryObject({
            NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica'),
            NameObject('/Encoding'): NameObject('/WinAnsiEncoding'),
        })
        font_ref = writer._add_object(font)
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): font_ref}),
        })
        body = ''.join(f'BT /F1 8 Tf 1 0 0 1 {x} {y} Tm ({_esc(t)}) Tj ET\n'
                       for x, y, t in ops)
        stream = DecodedStreamObject()
        stream.set_data(body.encode('latin-1'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _header(y=800):
    return [
        (40, y, 'Carta Platino American Express'),
        (40, y - 12, 'Estratto Conto'),
        (40, y - 24, 'Titolare SIG GIOVANNI IANDOLO'),
        (40, y - 36, 'Numero di Carta xxxx-xxxxxx-24009'),
        (300, y - 36, 'Data Chiusura 02.10.2026'),
    ]


def _table(rows, y):
    """rows: (op, post, desc, eur, sublines, cr_below). Returns
    (text_ops, amount_ops, next_y) — amounts kept as a separate block."""
    text_ops, amount_ops = [], []
    for op, post, desc, eur, sublines, cr_below in rows:
        text_ops += [(X_OP, y, op), (X_POST, y, post), (X_DESC, y, desc)]
        amount_ops.append((X_EUR, y, eur))
        y -= 12
        if cr_below:
            amount_ops.append((X_EUR, y, 'CR'))
            if not sublines:
                y -= 12
        for s in sublines:
            text_ops.append((X_DESC, y, s))
            y -= 12
    return text_ops, amount_ops, y


def sample_statement():
    """The 4-page statement transcribed from the user's sample."""
    # --- Page 1 ---
    p1 = _header() + [
        (40, 740, 'Saldo Precedente'), (160, 740, 'Accrediti'),
        (260, 740, 'Addebiti'), (360, 740, 'Saldo Attuale'),
        (40, 728, '883,64'), (160, 728, '883,64'),
        (260, 728, '455,12'), (360, 728, '455,12'),
        (40, 712, 'Importo Dovuto 455,12 EUR'),
        (X_OP, 690, 'Data operazione'), (X_POST + 10, 690, 'Contabilizzata in data'),
        (X_DESC + 60, 690, "Descrizione dell'operazione"),
        (X_FX - 20, 690, 'Importo in valuta non Euro'), (X_EUR, 690, 'Importo in Euro'),
    ]
    t, a, y = _table([('11.09.26', '11.09.26', 'ADDEBITO IN C/C SALVO BUON FINE',
                       '883,64', [], True)], 674)
    p1 += t
    amounts = a
    p1 += [(X_DESC, y, 'Nuovi addebiti per SIG GIOVANNI IANDOLO'),
           (X_DESC, y - 12, 'Carta xxxx-xxxxxx-24009')]
    t, a, y = _table([
        ('02.09.26', '03.09.26', 'GAMESTOP 2918 AVELLINO AVELLINO', '187,88',
         ['Prodotti elettronici'], False),
        ('03.09.26', '03.09.26', 'FANTALAB LONDON', '16,99', [], False),
        ('03.09.26', '03.09.26', 'EASYPARK ITALIA S.R.L MILANO', '4,40', [], False),
        ('05.09.26', '05.09.26', 'EASYPARK ITALIA S.R.L MILANO', '3,46', [], False),
        ('05.09.26', '06.09.26', 'IL CALICE CAPACCIO PAESTU', '20,00',
         ['Bar, Tavola Calda, Pub'], False),
        ('07.09.26', '07.09.26', 'EASYPARK ITALIA S.R.L MILANO', '3,55', [], False),
        ('08.09.26', '08.09.26', 'X CORP. PAID FEATURES BASTROP', '21,23', [], False),
    ], y - 30)
    p1 += t
    amounts += a
    p1 += [(40, 160, 'AVVISO DI PAGAMENTO'),
           (40, 148, 'American Express Italia S.r.l. Viale Alexandre Gustave Eiffel 15 00148 Roma'),
           (40, 136, 'Importo Dovuto 455,12')]
    p1 += amounts  # amounts column emitted last, as in the real PDF

    # --- Page 2: legal text ---
    p2 = [
        (40, 800, 'Modalita di Pagamento'),
        (40, 788, 'Il pagamento avviene tramite addebito in conto corrente entro 15 giorni.'),
        (40, 760, 'Tasso di cambio'),
        (40, 748, 'Maggiorazione del 2,00 per cento sul tasso applicato 1.234,56'),
        (40, 720, 'Modalita di rimborso della quota'),
        (40, 708, 'Quota mensile 70,00 rimborsabile pro rata entro 30 giorni.'),
    ]

    # --- Page 3: remaining charges + totals ---
    p3 = _header()
    t, a, y = _table([
        ('15.09.26', '16.09.26', 'LUXAIR S.A. MUNSBACH', '107,02', [
            'ITINERARIO: DA: LUXEMBOURG A: CATANIA VETTORE: LG CLASSE: Y',
            'DA: CATANIA A: LUXEMBOURG VETTORE: LG CLASSE: Y',
            'NUM. BIGLIETTO 1490000000000 NOME PASSEGGERO IANDOLO GIOVANNI'], False),
        ('28.09.26', '29.09.26', 'PAYPAL *ITUNESAPPST APP 214284208', '9,99', [], False),
        ('28.09.26', '29.09.26', 'PAYPAL *GOOGLE GOOGLE O 14361001', '1,99', [], False),
        ('30.09.26', '30.09.26', 'ELEVENLABS.IO NEW YORK', '6,61', [], False),
        ('02.10.26', '02.10.26', 'QUOTA ASSOCIATIVA MENSILE ESENTE DA IVA', '70,00', [], False),
    ], 740)
    p3 += t
    amounts = a
    p3.append((X_DESC, y, 'Totale nuove operazioni riferite a SIG GIOVANNI IANDOLO'))
    amounts.append((X_EUR, y, '453,12'))
    p3.append((X_DESC, y - 24, 'INTERESSI, ALTRI ADDEBITI E ACCREDITI'))
    t, a, y = _table([('02.10.26', '02.10.26', 'IMPOSTA DI BOLLO', '2,00', [], False)], y - 40)
    p3 += t
    amounts += a
    p3.append((X_DESC, y, 'Totale interessi, altri addebiti e accrediti sopra descritti'))
    amounts.append((X_EUR, y, '2,00'))
    p3 += amounts

    # --- Page 4: Membership Rewards points ---
    p4 = [
        (40, 800, 'Membership Rewards'),
        (40, 780, 'Saldo punti precedente'), (300, 780, '5.499'),
        (40, 768, 'Punti maturati'), (300, 768, '664'),
        (40, 756, 'Saldo punti attuale'), (300, 756, '6.163'),
    ]
    return build_pdf([p1, p2, p3, p4])


def refund_statement():
    """One page: a charge with a thousands separator and a same-line CR refund."""
    p = _header()
    t, a, _ = _table([
        ('10.09.26', '11.09.26', 'AMAZON EU SARL LUXEMBOURG', '1.234,56', [], False),
        ('12.09.26', '13.09.26', 'AMAZON EU SARL LUXEMBOURG', '25,00', [], False),
    ], 700)
    p += t
    a[1] = (X_EUR, a[1][1], '25,00 CR')
    p += a
    return build_pdf([p])


def foreign_pdf():
    """A readable PDF that is not an Amex statement."""
    return build_pdf([[(40, 800, 'Some other bank statement'), (40, 788, '01.09.26 01.09.26 SHOP 10,00')]])
