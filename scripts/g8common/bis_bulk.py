"""Official BIS WS_CBPOL flat bulk export: same daily series, independent delivery channel."""
import csv
import io
import math
import zipfile

URL = 'https://data.bis.org/static/bulk/WS_CBPOL_csv_flat.zip'


def parse(body, country, start, end):
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = [n for n in z.namelist() if n.rsplit('/',1)[-1] == 'WS_CBPOL_csv_flat.csv']
        if len(names) != 1 or z.getinfo(names[0]).file_size > 600_000_000:
            raise ValueError('BIS bulk: unexpected archive member or size')
        with z.open(names[0]) as f:
            return parse_csv(io.TextIOWrapper(f, encoding='utf-8-sig'), country, start, end)


def parse_csv(stream, country, start, end):
    reader = csv.DictReader(stream)
    columns = {k.split(':',1)[0]: k for k in reader.fieldnames or []}
    required = {'FREQ','REF_AREA','TIME_PERIOD','OBS_VALUE','UNIT_MEASURE','UNIT_MULT'}
    if not required <= columns.keys():
        raise ValueError('BIS bulk: required dimensions absent')
    out = {}
    for row in reader:
        def val(k): return (row.get(columns.get(k,'')) or '').split(':',1)[0].strip()
        if val('FREQ') != 'D' or val('REF_AREA') != country:
            continue
        d = val('TIME_PERIOD')
        if not start <= d <= end or val('OBS_STATUS') == 'M' or not val('OBS_VALUE'):
            continue
        if val('UNIT_MEASURE') != '368' or val('UNIT_MULT') != '0':
            raise ValueError('BIS bulk: units changed')
        v = float(val('OBS_VALUE'))
        if not math.isfinite(v):
            raise ValueError('BIS bulk: non-finite rate')
        key = d.replace('-', '')
        if key in out and out[key] != v:
            raise ValueError('BIS bulk: conflicting daily observation')
        out[key] = v
    if not out:
        raise ValueError('BIS bulk: daily country series missing')
    return sorted(out.items())


def verify_overlap(rows, published):
    candidate, old = dict(rows), dict(published)
    common = sorted(candidate.keys() & old.keys())[-20:]
    if len(common) < 20 or any(abs(candidate[d]-old[d]) > 0.00005 for d in common):
        raise ValueError('BIS bulk: 20 matching observations required before fallback publication')
    return len(common)
