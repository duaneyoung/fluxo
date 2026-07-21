"""
IBKR Activity Statement import — parses the daily statement CSV and
extracts everything the Net Worth tab tracks:

  stocks    -> symbol + quantity                (priced live via Yahoo)
  warrants  -> name + ISIN + quantity           (priced live via Onvista)
  options   -> per-contract positions           (priced live via Yahoo, OCC symbol)
  cash      -> ending cash in base currency EUR (statement value)
"""
import csv
import io
import re
from datetime import datetime

# "AMZN 21AUG26 280 C" -> root / expiry / strike / right
_OPT_RE = re.compile(r'^(\w+)\s+(\d{2}[A-Z]{3}\d{2})\s+([\d.]+)\s+([CP])$')


def occ_symbol(ib_symbol):
    """IBKR option symbol -> OCC contract symbol Yahoo quotes directly,
    e.g. 'AMZN 21AUG26 280 C' -> 'AMZN260821C00280000'. None if unparseable."""
    m = _OPT_RE.match(ib_symbol.strip().upper())
    if not m:
        return None
    root, expiry, strike, right = m.groups()
    try:
        d = datetime.strptime(expiry, '%d%b%y')
    except ValueError:
        return None
    return f"{root}{d.strftime('%y%m%d')}{right}{int(round(float(strike) * 1000)):08d}"


def _f(val, default=0.0):
    try:
        return float(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return default


def parse_statement(file_bytes):
    text = file_bytes.decode('utf-8-sig', errors='replace')
    rows = list(csv.reader(io.StringIO(text)))

    # Symbol -> (ISIN, description) for warrants, from Financial Instrument Information
    warrant_info = {}
    for r in rows:
        # [3]=Symbol [4]=Description [5]=Conid [6]=Security ID (ISIN)
        if (len(r) > 6 and r[0] == 'Financial Instrument Information'
                and r[1] == 'Data' and r[2] == 'Warrants'):
            warrant_info[r[3]] = {'isin': r[6], 'name': r[4]}

    stocks, warrants, options = [], [], []
    cash_eur = None

    for r in rows:
        if len(r) > 12 and r[0] == 'Open Positions':
            # Data/Summary rows: [2]=discriminator [3]=category [5]=symbol [7]=qty [12]=value
            if r[1] == 'Data' and r[2] == 'Summary':
                if r[3] == 'Stocks':
                    stocks.append({'symbol': r[5], 'quantity': _f(r[7])})
                elif r[3] == 'Warrants':
                    info = warrant_info.get(r[5], {})
                    warrants.append({
                        'symbol': r[5],
                        'name': info.get('name', r[5]),
                        'isin': info.get('isin', ''),
                        'quantity': _f(r[7]),
                    })
                elif r[3] == 'Equity and Index Options':
                    # Quantity is signed (negative = short); ×100 multiplier
                    # is applied at valuation time.
                    options.append({
                        'symbol': r[5],
                        'occ': occ_symbol(r[5]),
                        'quantity': _f(r[7]),
                    })

        # Cash Report: Ending Cash / Base Currency Summary -> Total column
        if (len(r) > 4 and r[0] == 'Cash Report' and r[1] == 'Data'
                and r[2] == 'Ending Cash' and r[3] == 'Base Currency Summary'):
            cash_eur = _f(r[4])

    if not stocks and not warrants:
        raise ValueError('Not an IBKR activity statement — no Open Positions found')

    return {
        'stocks': stocks,
        'warrants': warrants,
        'options': options,
        'cash_eur': cash_eur,
    }
