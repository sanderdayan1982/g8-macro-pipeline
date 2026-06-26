#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_eur_real.py
=================
EUR (núcleo alemán) REAL 10Y diario, real de MERCADO, desde los linkers del Bundesbank.

QUÉ HACE
--------
La Bundesbank publica cada día hábil la tabla "Prices and yields of listed Federal
securities": un XLSX mensual con UNA HOJA POR DÍA HÁBIL. Cada hoja lista TODOS los
Federal securities, incluidos los 3 Bund inflation-linked alemanes (Bund 14/21/15
"index.") con su yield REAL de mercado puro. De la hoja más reciente:

  * REAL10 = interpolación a 10 años de los linkers (real de mercado)
  * NOM10  = interpolación a 10 años de los Bund nominales (mismo snapshot)
  * BE10   = NOM10 - REAL10  (breakeven, internamente consistente)

Salida: data/RY_G8_EUR.csv  con cabecera idéntica al resto de tu pipeline:
        DATE,NOM10,REAL10,BE10   (DATE = YYYYMMDD, valores en %)

POR QUÉ
-------
Reemplaza el benchmark real MENSUAL del ECB (modelado) por un real DIARIO de
mercado. DE-core ~ EUR-real: el propio benchmark del ECB es una cesta de linkers
FR+DE; Alemania es el ancla AAA. Doble upgrade: mensual->diario y modelo->mercado.

CAVEATS HONESTOS (van marcados en la cabecera QUALITY del CSV)
--------------------------------------------------------------
  * DE-only, no la cesta agregada FR+DE del ECB.
  * 10Y va INTERPOLADO: no hay linker justo a 10y (el bracket es ~6.8y y ~19.8y).
  * Mercado de linkers alemán pequeño (~1% del volumen) -> algo de ruido.

AUTO-DESCUBRIMIENTO (clave: la URL cambia cada mes)
---------------------------------------------------
El enlace mensual lleva blob-id + hash + el YYYY-MM en el nombre, así que CAMBIA
cada mes. NUNCA se hardcodea: se raspa la página de listado y se elige el enlace
del mes vigente (fallback: el mes más reciente disponible).

FRESHNESS GATE
--------------
Si la hoja más reciente es más vieja que STALENESS_LIMIT_DAYS, sale con código !=0
(CI en rojo). Mata la clase de fallo "stale silencioso" igual que el de gilts.

USO
---
  python3 fetch_eur_real.py                 # produccion: descubre, descarga, escribe CSV
  python3 fetch_eur_real.py --test FILE.xlsx # valida el parseo contra un XLSX local (no escribe)
  python3 fetch_eur_real.py --dry-run        # descubre+descarga+parsea, imprime, NO escribe

Stdlib pura salvo openpyxl (ya lo usa tu scraper de gilts). Sin claves, sin auth.
"""

import sys
import os
import re
import ssl
import csv
import argparse
import datetime as dt
import urllib.request
import urllib.error

try:
    from openpyxl import load_workbook
except ImportError:
    sys.stderr.write("ERROR: falta openpyxl. En CI: pip install openpyxl\n")
    raise

# --------------------------------------------------------------------------- #
# CONFIG
# --------------------------------------------------------------------------- #
LISTING_URL = ("https://www.bundesbank.de/dynamic/action/en/service/"
               "federal-securities/prices-and-yields/810710/"
               "prices-and-yields-of-listed-federal-securities")
# Fallback de descubrimiento (mismo contenido, endpoint de busqueda ordenado por Latest)
LISTING_FALLBACK = "https://www.bundesbank.de/action/en/810710/bbksearch?sort=&query=*"

OUT_CSV = os.path.join("data", "RY_G8_EUR.csv")
TARGET_TENOR_Y = 10.0
STALENESS_LIMIT_DAYS = 7            # dias de calendario; hoja mas nueva no puede pasarse
QUALITY_TAG = "PROXY_DE_INTERP"     # DE-only + 10Y interpolado (honesto)
SOURCE_NOTE = "DE linkers interp to 10Y (Bundesbank; HICP basis)"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Patron de los enlaces XLSX mensuales (blob-id + hash + HASH-constante + YYYY-MM)
XLSX_RE = re.compile(
    r"https://www\.bundesbank\.de/resource/blob/\d+/[0-9a-fA-F]+/[0-9A-F]+/"
    r"(\d{4})-(\d{2})-excel-data\.xlsx"
)
DATE_RE = re.compile(r"^\s*(\d{2})\.(\d{2})\.(\d{4})\s*$")   # DD.MM.YYYY


# --------------------------------------------------------------------------- #
# HTTP con fallback SSL relajado (red Bata con proxy de certificado self-signed)
# --------------------------------------------------------------------------- #
def _open(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", "")
        if "CERTIFICATE_VERIFY_FAILED" in str(reason) or isinstance(reason, ssl.SSLError):
            sys.stderr.write("[ssl] verify fallo -> reintento sin verificacion (red local)\n")
            ctx = ssl._create_unverified_context()
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        raise


def http_text(url):
    with _open(url) as r:
        return r.read().decode("utf-8", "replace")


def http_bytes(url):
    with _open(url) as r:
        return r.read()


# --------------------------------------------------------------------------- #
# 1) DESCUBRIMIENTO del XLSX del mes vigente
# --------------------------------------------------------------------------- #
def discover_xlsx_url():
    """Raspa el listado y devuelve (url, (year, month)) del mes vigente; si no
    existe aun (rollover de mes), el mes mas reciente disponible."""
    html = ""
    for src in (LISTING_URL, LISTING_FALLBACK):
        try:
            html = http_text(src)
            if XLSX_RE.search(html):
                break
        except Exception as e:
            sys.stderr.write("[discover] aviso: fallo %s (%s)\n" % (src, e))
    matches = {}  # (y,m) -> url   (dedup; conserva el primero = top "Latest")
    for m in XLSX_RE.finditer(html):
        ym = (int(m.group(1)), int(m.group(2)))
        matches.setdefault(ym, m.group(0))
    if not matches:
        raise RuntimeError("DESCUBRIMIENTO FALLIDO: no encontre ningun enlace "
                           "*-excel-data.xlsx en el listado del Bundesbank. "
                           "Posible cambio de layout de la pagina.")
    today = dt.date.today()
    cur = (today.year, today.month)
    if cur in matches:
        return matches[cur], cur
    latest = max(matches.keys())
    sys.stderr.write("[discover] aviso: mes vigente %04d-%02d no publicado aun; "
                     "uso el mas reciente %04d-%02d\n" % (cur[0], cur[1], latest[0], latest[1]))
    return matches[latest], latest


# --------------------------------------------------------------------------- #
# 2) PARSEO de la hoja mas reciente -> (sheet_date, NOM10, REAL10, BE10, detalle)
# --------------------------------------------------------------------------- #
def _parse_sheet_date(name):
    m = DATE_RE.match(name)
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def _row_bond(row, sheet_date):
    """Devuelve (desc, residual_years, yield, is_linker) si la fila es un bono
    valido; si no, None. Indices posicionales estables (validados):
    [4]=desc  [5]=maturity DD.MM.YYYY  [9]=yield(%)  ('index.' en desc = linker)."""
    if row is None or len(row) < 10:
        return None
    desc, mat, yld = row[4], row[5], row[9]
    if not isinstance(desc, str):
        return None
    md = _parse_sheet_date(mat) if isinstance(mat, str) else None
    if md is None:
        return None
    try:
        y = float(yld)
    except (TypeError, ValueError):
        return None
    if not (-5.0 <= y <= 12.0):     # sanity band de yield
        return None
    resid = (md - sheet_date).days / 365.25
    if resid <= 0:
        return None
    is_linker = "index" in desc.lower()
    return (desc.strip(), resid, y, is_linker)


def _interp(points, x):
    """Interpolacion lineal en (residual, yield). points: lista de (resid, yield).
    Si x cae fuera del rango, extrapola con los dos extremos (con aviso)."""
    pts = sorted(points)
    if len(pts) == 1:
        return pts[0][1]
    below = [p for p in pts if p[0] <= x]
    above = [p for p in pts if p[0] > x]
    if below and above:
        x0, y0 = below[-1]
        x1, y1 = above[0]
    elif not below:                 # x por debajo del minimo
        (x0, y0), (x1, y1) = pts[0], pts[1]
        sys.stderr.write("[interp] aviso: %.2fy por debajo del bracket; extrapolo\n" % x)
    else:                           # x por encima del maximo
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        sys.stderr.write("[interp] aviso: %.2fy por encima del bracket; extrapolo\n" % x)
    if x1 == x0:
        return y0
    return y0 + (x - x0) * (y1 - y0) / (x1 - x0)


def parse_workbook(xlsx_bytes_or_path):
    """Carga el workbook, elige la hoja con fecha MAXIMA, extrae linkers y
    nominales, interpola a 10Y. Devuelve dict con resultados + detalle."""
    wb = load_workbook(xlsx_bytes_or_path, read_only=True, data_only=True)
    dated = [(_parse_sheet_date(s), s) for s in wb.sheetnames]
    dated = [(d, s) for (d, s) in dated if d is not None]
    if not dated:
        raise RuntimeError("PARSEO FALLIDO: ninguna hoja con nombre DD.MM.YYYY "
                           "(layout inesperado).")
    sheet_date, sheet_name = max(dated, key=lambda t: t[0])
    ws = wb[sheet_name]

    linkers, nominals = [], []
    for row in ws.iter_rows(values_only=True):
        b = _row_bond(row, sheet_date)
        if b is None:
            continue
        desc, resid, y, is_linker = b
        if is_linker:
            linkers.append((resid, y, desc))
        else:
            nominals.append((resid, y, desc))

    if len(linkers) < 2:
        raise RuntimeError("PARSEO FALLIDO: encontre %d linkers (esperaba >=2 para "
                           "interpolar a 10Y). Hoja %s." % (len(linkers), sheet_name))
    if len(nominals) < 2:
        raise RuntimeError("PARSEO FALLIDO: encontre %d Bund nominales (esperaba >=2). "
                           "Hoja %s." % (len(nominals), sheet_name))

    real10 = _interp([(r, y) for (r, y, _) in linkers], TARGET_TENOR_Y)
    nom10 = _interp([(r, y) for (r, y, _) in nominals], TARGET_TENOR_Y)
    be10 = nom10 - real10

    return {
        "date": sheet_date,
        "sheet": sheet_name,
        "NOM10": round(nom10, 4),
        "REAL10": round(real10, 4),
        "BE10": round(be10, 4),
        "linkers": sorted(linkers),
        "n_nominals": len(nominals),
    }


# --------------------------------------------------------------------------- #
# 3) ESCRITURA idempotente de RY_G8_EUR.csv  (upsert por DATE)
# --------------------------------------------------------------------------- #
HEADER_COMMENT = "# QUALITY=%s | %s | generated by fetch_eur_real.py" % (QUALITY_TAG, SOURCE_NOTE)
COLS = ["DATE", "NOM10", "REAL10", "BE10"]


def write_csv(result, path=OUT_CSV):
    datestr = result["date"].strftime("%Y%m%d")
    rows = {}
    # leer existente (si lo hay) para upsert -- csv.reader maneja comillas/terminadores
    if os.path.exists(path):
        with open(path, "r", newline="") as f:
            for parts in csv.reader(f):
                if not parts:
                    continue
                head = parts[0].strip()
                if head.startswith("#") or head == "DATE":
                    continue
                if len(parts) >= 4 and head.isdigit():
                    rows[head] = [p.strip() for p in parts[:4]]
    rows[datestr] = [datestr, "%g" % result["NOM10"], "%g" % result["REAL10"], "%g" % result["BE10"]]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(HEADER_COMMENT + "\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(COLS)
        for k in sorted(rows.keys()):
            w.writerow(rows[k])
    return datestr


# --------------------------------------------------------------------------- #
# 4) FRESHNESS GATE
# --------------------------------------------------------------------------- #
def freshness_gate(result):
    age = (dt.date.today() - result["date"]).days
    if age > STALENESS_LIMIT_DAYS:
        sys.stderr.write(
            "FRESHNESS GATE FALLO: la hoja mas reciente es %s (%d dias), "
            "limite %d. CI en rojo a proposito.\n"
            % (result["date"].isoformat(), age, STALENESS_LIMIT_DAYS))
        return False
    return True


def _print_summary(result):
    print("Hoja mas reciente : %s  (date=%s)" % (result["sheet"], result["date"].isoformat()))
    print("Linkers (resid y, real%):")
    for r, y, desc in result["linkers"]:
        print("   %-22s %5.2fy  real %.3f%%" % (desc, r, y))
    print("Nominales usados  : %d puntos de curva" % result["n_nominals"])
    print("-> NOM10  = %.3f%%" % result["NOM10"])
    print("-> REAL10 = %.3f%%  (interpolado a 10Y)" % result["REAL10"])
    print("-> BE10   = %.3f%%  (= NOM10 - REAL10)" % result["BE10"])


# --------------------------------------------------------------------------- #
# MAIN
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="EUR(DE) real 10Y diario desde Bundesbank linkers")
    ap.add_argument("--test", metavar="XLSX", help="valida el parseo contra un XLSX local (no escribe, no freshness)")
    ap.add_argument("--dry-run", action="store_true", help="descubre+descarga+parsea, imprime, NO escribe")
    args = ap.parse_args()

    # Modo test: parsea un fichero local, sin red, sin escribir, sin gate
    if args.test:
        result = parse_workbook(args.test)
        _print_summary(result)
        print("\n[--test] OK (no se escribio CSV, no se aplico freshness gate).")
        return 0

    # Produccion / dry-run
    url, ym = discover_xlsx_url()
    sys.stderr.write("[discover] XLSX %04d-%02d -> %s\n" % (ym[0], ym[1], url))
    data = http_bytes(url)
    import io
    result = parse_workbook(io.BytesIO(data))
    _print_summary(result)

    if not freshness_gate(result):
        return 1

    if args.dry_run:
        print("\n[--dry-run] OK (no se escribio CSV).")
        return 0

    datestr = write_csv(result)
    print("\nEscrito %s -> fila %s" % (OUT_CSV, datestr))
    return 0


if __name__ == "__main__":
    sys.exit(main())
