"""
g8common/cb_direct.py — Acta P-4 (2026-10-01)
==============================================
Tipo oficial leído DIRECTAMENTE del banco central, por delante del BIS (WS_CBPOL publica con días o
semanas de retraso), solo donde GitHub Actions llega:

  AU  RBA tabla F1 (diaria) · Series ID FIRMMCRTD «Cash Rate Target»
      https://www.rba.gov.au/statistics/tables/csv/f1-data.csv   (misma tabla que fetch_aonia.py)
  GB  BoE IADB · IUDBEDR «Official Bank Rate» (diaria)
      https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp (mismo endpoint que fetch_sonia.py)

Verificado en la fuente el 1-oct-2026: F1 FIRMMCRTD 4.60 desde el 30-sep (BIS: 4.35 hasta el 24-sep);
IUDBEDR 3.75 hasta el 30-sep (BIS hasta el 28-sep).
No llegan desde Actions (403): SNB (data.snb.ch), BoJ, RBNZ → siguen BIS + decisiones verificadas (Acta P-1).

Doctrina
  · El banco central es la fuente primaria: en una fecha que publican ambos, manda el banco central y la
    discrepancia con el BIS se avisa. Por delante del BIS, el banco central prolonga la serie.
  · Fuente directa caída o con formato cambiado → aviso explícito y se sigue con BIS + decisiones (nunca se inventa).
"""

import csv
import io
from datetime import datetime

RBA_F1_URL = "https://www.rba.gov.au/statistics/tables/csv/f1-data.csv"
RBA_SERIES = "FIRMMCRTD"
BOE_IADB_URL = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
BOE_SERIES = "IUDBEDR"
USER_AGENT = "g8-macro-pipeline/1.0 (https://github.com/sanderdayan1982/g8-macro-pipeline)"

SOURCES = {
    "AU": "RBA F1 FIRMMCRTD (Cash Rate Target, daily)",
    "GB": "BoE IADB IUDBEDR (Official Bank Rate, daily)",
}


class DirectError(ValueError):
    pass


def parse_rba_f1(text, series=RBA_SERIES):
    """RBA F1 CSV → list[(yyyymmdd, float)] para el Series ID dado. Celdas vacías se omiten."""
    rows = list(csv.reader(io.StringIO(text)))
    sid = next((r for r in rows[:30] if r and r[0].strip() == "Series ID"), None)
    if sid is None:
        raise DirectError("RBA F1: falta la fila 'Series ID' (¿cambió el formato?)")
    try:
        col = [c.strip() for c in sid].index(series)
    except ValueError:
        raise DirectError("RBA F1: serie %s ausente (columnas: %s)" % (series, ",".join(sid[1:8])))
    out = []
    for r in rows:
        if len(r) <= col or not r[0].strip() or not r[col].strip():
            continue
        try:
            d = datetime.strptime(r[0].strip(), "%d-%b-%Y")
            out.append((d.strftime("%Y%m%d"), float(r[col])))
        except ValueError:
            continue
    if not out:
        raise DirectError("RBA F1: %s sin observaciones" % series)
    return sorted(out)


def parse_boe_iadb(text, series=BOE_SERIES):
    """BoE IADB CSV (DATE,<code>; '30 Sep 2026') → list[(yyyymmdd, float)]."""
    rdr = csv.reader(io.StringIO(text))
    head = next(rdr, None)
    if not head or head[0].strip().upper() != "DATE" or series not in [h.strip() for h in head]:
        raise DirectError("BoE IADB: cabecera inesperada %r (¿cambió el formato?)" % (head,))
    col = [h.strip() for h in head].index(series)
    out = []
    for r in rdr:
        if len(r) <= col:
            continue
        try:
            d = datetime.strptime(r[0].strip(), "%d %b %Y")
            out.append((d.strftime("%Y%m%d"), float(r[col])))
        except ValueError:
            continue
    if not out:
        raise DirectError("BoE IADB: %s sin observaciones" % series)
    return sorted(out)


def fetch(country, get, date_from, date_to):
    """→ list[(yyyymmdd, float)] dentro de [date_from, date_to]. get = sustituto de requests.get (g8http).
    Lanza DirectError (formato) o la excepción HTTP del transporte."""
    headers = {"User-Agent": USER_AGENT, "Accept": "text/csv"}
    if country == "AU":
        r = get(RBA_F1_URL, headers=headers, timeout=60)
        r.raise_for_status()
        rows = parse_rba_f1(r.text)
    elif country == "GB":
        params = {"csv.x": "yes", "Datefrom": date_from.strftime("%d/%b/%Y"), "Dateto": "now",
                  "SeriesCodes": BOE_SERIES, "CSVF": "TN", "UsingCodes": "Y", "VPD": "Y", "VFD": "N"}
        r = get(BOE_IADB_URL, params=params, headers=headers, timeout=60)
        r.raise_for_status()
        rows = parse_boe_iadb(r.text)
    else:
        return []
    lo, hi = date_from.strftime("%Y%m%d"), date_to.strftime("%Y%m%d")
    return [x for x in rows if lo <= x[0] <= hi]


def merge(bis_rows, direct_rows, country):
    """BIS + banco central. Mismo día: manda el banco central (aviso si difieren). Por delante: banco central.
    → (rows, añadidas_por_delante, corregidas, avisos)."""
    warnings = []
    by = dict(bis_rows or [])
    last_bis = max(by) if by else ""
    fixed = 0
    for k, v in direct_rows:
        if k in by and abs(by[k] - v) > 1e-9:
            warnings.append("discrepancia %s %s: BIS %.4f vs banco central %.4f (manda el banco central)"
                            % (country, k, by[k], v))
            fixed += 1
        by[k] = v
    ahead = sum(1 for k, _ in direct_rows if k > last_bis)
    return sorted(by.items()), ahead, fixed, warnings

