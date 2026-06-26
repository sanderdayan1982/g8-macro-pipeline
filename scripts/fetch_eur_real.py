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

BASE = "https://www.bundesbank.de"
# Patron de los enlaces XLSX mensuales. Ancla en la RUTA (/resource/blob/...), por
# lo que casa tanto si el HTML trae el href relativo (/resource/...) como absoluto
# (https://www.bundesbank.de/resource/...). group(0)=ruta -> se le antepone BASE.
XLSX_RE = re.compile(
    r"/resource/blob/\d+/[0-9a-fA-F]+/[0-9A-F]+/"
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
        matches.setdefault(ym, BASE + m.group(0))
    if not matches:
        hint = ""
        if "excel-data" in html:
            hint = " (el HTML SI contiene 'excel-data' -> el regex no casa: revisa el patron)"
        elif "resource/blob" in html:
            hint = " (hay 'resource/blob' pero no '-excel-data.xlsx' -> ¿solo PDF en esta vista?)"
        else:
            hint = " (el HTML no contiene 'excel-data' ni 'resource/blob' -> contenido distinto/JS; len=%d)" % len(html)
        raise RuntimeError("DESCUBRIMIENTO FALLIDO: no encontre ningun enlace "
                           "*-excel-data.xlsx en el listado del Bundesbank." + hint)
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


BRACKET_TOL_Y = 1.5   # backfill: 10Y puede extrapolarse como mucho 1.5y mas alla del
                      # span de linkers; mas alla, se descarta la fila (real no fiable).


def compute_sheet(ws, sheet_date, strict=True, require_bracket=False,
                  bracket_tol=BRACKET_TOL_Y):
    """Extrae linkers + nominales de UNA hoja e interpola a 10Y.
    strict=True  -> lanza si faltan datos (uso diario: la ultima hoja siempre los tiene).
    strict=False -> devuelve None si faltan datos (uso backfill: salta ese dia).
    require_bracket=True -> ademas exige que 10Y caiga dentro del span de linkers
                            (+/- bracket_tol); si no, None (real seria extrapolacion
                            grande, no fiable). Guardarrail del backfill."""
    linkers, nominals = [], []
    for row in ws.iter_rows(values_only=True):
        b = _row_bond(row, sheet_date)
        if b is None:
            continue
        desc, resid, y, is_linker = b
        (linkers if is_linker else nominals).append((resid, y, desc))

    if len(linkers) < 2 or len(nominals) < 2:
        if strict:
            raise RuntimeError("PARSEO FALLIDO: hoja %s con %d linkers / %d nominales "
                               "(esperaba >=2 de cada)." % (sheet_date.isoformat(),
                                                            len(linkers), len(nominals)))
        return None

    if require_bracket:
        lr = sorted(r for (r, _, _) in linkers)
        if not (lr[0] - bracket_tol <= TARGET_TENOR_Y <= lr[-1] + bracket_tol):
            return None   # 10Y demasiado lejos del span de linkers -> real no fiable

    real10 = _interp([(r, y) for (r, y, _) in linkers], TARGET_TENOR_Y)
    nom10 = _interp([(r, y) for (r, y, _) in nominals], TARGET_TENOR_Y)
    return {
        "date": sheet_date,
        "sheet": sheet_date.strftime("%d.%m.%Y"),
        "NOM10": round(nom10, 4),
        "REAL10": round(real10, 4),
        "BE10": round(nom10 - real10, 4),
        "linkers": sorted(linkers),
        "n_nominals": len(nominals),
    }


def _sheets_by_date(xlsx_bytes_or_path):
    wb = load_workbook(xlsx_bytes_or_path, read_only=True, data_only=True)
    dated = [(_parse_sheet_date(s), s) for s in wb.sheetnames]
    dated = [(d, s) for (d, s) in dated if d is not None]
    if not dated:
        raise RuntimeError("PARSEO FALLIDO: ninguna hoja con nombre DD.MM.YYYY "
                           "(layout inesperado).")
    return wb, sorted(dated, key=lambda t: t[0])


def parse_workbook(xlsx_bytes_or_path):
    """Diario: elige la hoja con fecha MAXIMA e interpola a 10Y (strict)."""
    wb, dated = _sheets_by_date(xlsx_bytes_or_path)
    sheet_date, sheet_name = dated[-1]
    return compute_sheet(wb[sheet_name], sheet_date, strict=True)


def parse_all_sheets(xlsx_bytes_or_path, require_bracket=True):
    """Backfill: una fila por hoja (dia habil), saltando las que no dan un REAL10
    fiable (guardarrail de bracket). Devuelve lista de results ordenada por fecha."""
    wb, dated = _sheets_by_date(xlsx_bytes_or_path)
    out = []
    for sheet_date, sheet_name in dated:
        r = compute_sheet(wb[sheet_name], sheet_date, strict=False,
                          require_bracket=require_bracket)
        if r is not None:
            out.append(r)
    return out


# --------------------------------------------------------------------------- #
# 3) ESCRITURA idempotente de RY_G8_EUR.csv  (upsert por DATE)
# --------------------------------------------------------------------------- #
HEADER_COMMENT = "# QUALITY=%s | %s | generated by fetch_eur_real.py" % (QUALITY_TAG, SOURCE_NOTE)
COLS = ["DATE", "NOM10", "REAL10", "BE10"]


def _result_row(result):
    d = result["date"].strftime("%Y%m%d")
    return d, [d, "%g" % result["NOM10"], "%g" % result["REAL10"], "%g" % result["BE10"]]


def _upsert(new_rows, path=OUT_CSV):
    """new_rows: dict {YYYYMMDD: [d,nom,real,be]}. Lee existente, fusiona, reescribe.
    csv.reader maneja comillas/terminadores; last-write-wins por fecha."""
    rows = {}
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
    rows.update(new_rows)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(HEADER_COMMENT + "\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(COLS)
        for k in sorted(rows.keys()):
            w.writerow(rows[k])
    return len(rows)


def write_csv(result, path=OUT_CSV):
    d, row = _result_row(result)
    _upsert({d: row}, path)
    return d


def write_rows(results, path=OUT_CSV):
    """Backfill: upsert idempotente de muchos results de una vez.
    Devuelve (filas_nuevas_aportadas, total_filas_en_csv)."""
    nr = dict(_result_row(r) for r in results)
    total = _upsert(nr, path)
    return len(nr), total


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
