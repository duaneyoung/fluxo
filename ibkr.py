"""
IBKR Activity Statement import — parses the daily statement CSV and
extracts everything the Net Worth tab tracks:

  stocks    -> symbol + quantity                (priced live via Yahoo)
  warrants  -> name + ISIN + quantity           (priced live via Onvista)
  options   -> one net EUR value line           (statement value; tiny, no live source)
  cash      -> ending cash in base currency EUR (statement value)
"""
import csv
import io


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

    stocks, warrants = [], []
    options_value_eur = None
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
            # EUR total for options: net value already in base currency
            elif r[1] == 'Total' and r[3] == 'Equity and Index Options' and r[4] == 'EUR':
                options_value_eur = _f(r[12])

        # Cash Report: Ending Cash / Base Currency Summary -> Total column
        if (len(r) > 4 and r[0] == 'Cash Report' and r[1] == 'Data'
                and r[2] == 'Ending Cash' and r[3] == 'Base Currency Summary'):
            cash_eur = _f(r[4])

    if not stocks and not warrants:
        raise ValueError('Not an IBKR activity statement — no Open Positions found')

    return {
        'stocks': stocks,
        'warrants': warrants,
        'options_value_eur': options_value_eur,
        'cash_eur': cash_eur,
    }
