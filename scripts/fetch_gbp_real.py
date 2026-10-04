#!/usr/bin/env python3
"""BoE official current-month nominal/real/inflation spot curves, 10 years, RPI.
Append through existing ingest validation; preserve history and reject inconsistent triplets.
Same definition as IUDMNZC/IUDMRZC/IUDMIZC; no interpolation or proxy substitution.
"""
import io
import csv
import math
import zipfile
from datetime import date, datetime
import openpyxl
from g8common import ingest

IADB = 'https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp'
URL = 'https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/latest-yield-curve-data.zip'
NAMES = {k: 'GLC %s daily data current month.xlsx' % k.title() for k in ('nominal','real','inflation')}


def workbooks(body, depth=0):
    if depth > 1:
        raise ValueError('BoE: archive nested more than once')
    found = {}
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        for name in z.namelist():
            if z.getinfo(name).file_size > 20_000_000:
                raise ValueError('BoE: unexpectedly large workbook')
            base = name.rsplit('/',1)[-1].lower()
            if base.endswith('.zip'):
                sub = workbooks(z.read(name), depth+1)
            else:
                sub = {k:z.read(name) for k,n in NAMES.items() if base == n.lower()}
            if found.keys() & sub.keys():
                raise ValueError('BoE: ambiguous duplicate workbook')
            found.update(sub)
    return found


def spot10(body, kind, today):
    wb = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    try:
        rows = list(wb['4. spot curve'].values)
    finally:
        wb.close()
    title = ' '.join(str(x).lower() for x in rows[0] if x)
    if kind not in title or 'spot curve' not in title:
        raise ValueError('BoE: unexpected curve definition')
    head = next((r for r in rows[:8] if r[0] == 'years:'), None)
    cols = [i for i,x in enumerate(head or []) if isinstance(x,(int,float)) and abs(x-10)<1e-6]
    if len(cols) != 1:
        raise ValueError('BoE: unique 10-year tenor missing')
    out = {}
    for row in rows:
        if not isinstance(row[0],datetime):
            continue
        d = row[0].date()
        if d > today:
            raise ValueError('BoE: future observation')
        v = row[cols[0]]
        if not isinstance(v,(int,float)) or not math.isfinite(v):
            raise ValueError('BoE: invalid 10-year observation')
        if d in out:
            raise ValueError('BoE: duplicate date')
        out[d] = v
    if not out:
        raise ValueError('BoE: no dated observations')
    return out


def parse(body, today):
    books = workbooks(body)
    if set(books) != set(NAMES):
        raise ValueError('BoE: nominal, real and inflation workbooks required')
    curves = {k:spot10(v,k,today) for k,v in books.items()}
    dates = set(curves['nominal'])
    if any(set(v) != dates for v in curves.values()):
        raise ValueError('BoE: curve dates do not align')
    rows = []
    for d in sorted(dates):
        nom,real,bei = [curves[k][d] for k in ('nominal','real','inflation')]
        if abs(nom-real-bei)>1e-6 or not -4<=real<=6 or not -2<=bei<=6:
            raise ValueError('BoE: identity or existing real/BE bounds failed')
        rows.append((d.strftime('%Y%m%d'),nom,real,nom-real))
    return rows


def render(rows):
    return ('# QUALITY=CLEAN | Index-linked gilts ZC (BoE; RPI basis) | official spot workbooks\nDATE,NOM10,REAL10,BE10\n'+
            ''.join('%s,%.4f,%.4f,%.4f\n'%r for r in rows)).encode()


def parse_iadb(text, today):
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    cols = ['IUDMNZC','IUDMRZC','IUDMIZC']
    if not {'DATE',*cols} <= set(reader.fieldnames or []):
        raise ValueError('BoE IADB: required series missing')
    for row in reader:
        if not all(row[c].strip() for c in cols):
            continue
        d = datetime.strptime(row['DATE'], '%d %b %Y').date()
        n,r,b = [float(row[c]) for c in cols]
        if d > today or not all(math.isfinite(v) for v in (n,r,b)) or abs(n-r-b)>0.0002 or not -4<=r<=6 or not -2<=b<=6:
            raise ValueError('BoE IADB: date, identity or bounds failed')
        rows.append((d.strftime('%Y%m%d'),n,r,n-r))
    if not rows:
        raise ValueError('BoE IADB: no complete observations')
    return sorted(rows)


def collect(ctx, today):
    get = ctx.requests(provider='boe', max_attempts=2).get
    try:
        return parse(get(URL, timeout=90, headers={'Cache-Control':'no-cache'}).content, today)
    except Exception as exc:
        ctx.degraded('BoE workbooks unavailable; try IADB: %s' % exc)
    params = {'csv.x':'yes','Datefrom':'01/Jan/2003','Dateto':today.strftime('%d/%b/%Y'),
              'SeriesCodes':'IUDMNZC,IUDMRZC,IUDMIZC','CSVF':'TN','UsingCodes':'Y','VPD':'Y','VFD':'N'}
    return parse_iadb(get(IADB,params=params,timeout=90).text,today)


def main():
    ctx = ingest.Ingest('fetch_gbp_real')
    try:
        rows = collect(ctx, date.today())
        rep = ctx.publish('RY_G8_GBP.csv', render(rows), expect_header=['DATE','NOM10','REAL10','BE10'], measures=['NOM10','REAL10','BE10'])
        print('BoE real: %s through %s' % (rep['status'], rows[-1][0]))
        return ctx.finish(0 if rep['status'] in ('PUBLISH','NOOP') else 1)
    except Exception as e:
        ctx.fail(str(e)); print('BoE real retained: '+str(e)); return ctx.finish(1)


if __name__ == '__main__':
    raise SystemExit(main())
