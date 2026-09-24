#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_jpy_real.py
=================
JPY REAL 10Y + BEI diario, real de MERCADO, desde los JGBi del JSDA.

QUÉ HACE
--------
El JSDA (Japan Securities Dealers Association) publica cada día hábil las
"Reference Statistical Prices [Yields] for OTC Bond Transactions" (CSV), que
incluyen los JGBi (10-year Inflation-Indexed Bonds) como PRECIO limpio per-100-face.
De ahí:

  * REAL10 = YTM real del JGBi ON-THE-RUN (el más nuevo, ~10y) calculado del
            precio + su cupón fijo (tabla del MoF) + vencimiento.
  * NOM10  = nominal 10Y interpolado de los JGB con cupón (code 02, compound yield).
  * BE10   = NOM10 - REAL10  (Breakeven Inflation 10Y).

MÉTODO
------
Replica el cálculo OFICIAL del BEI del MoF (Ministry of Finance): compound yield
del JGBi NUEVO vs el 10-Year nominal. Usar solo el on-the-run evita los JGBi viejos
ilíquidos (quotes con el mínimo de reporting members → ruido/kinks). La indexación
NO entra: el yield se computa del precio per-100-face + cupón + vencimiento (el MoF
lo confirma), y los bonos líquidos recientes encajan suaves sin ajustarla.

Salida: data/RY_G8_JPY.csv  (DATE,NOM10,REAL10,BE10 ; DATE=YYYYMMDD ; valores en %)

URL (sin descubrimiento, construible desde la fecha)
----------------------------------------------------
  https://market.jsda.or.jp/en/statistics/bonds/prices/otc/files/{YYYY}/ES{YYMMDD}.csv
  ('ES' = precios/yields ; 'ER' = rating matrix, NO). Publicado 10:00 JST días hábiles;
  la fecha del fichero es la fecha de publicación (quotes de las 15:00 del día hábil previo).

CUPONES (tabla estática del MoF — Auction_Results_for_JGBs.xls, hoja 10年物価連動)
------------------------------------------------------------------------------
  Cuando se emita un JGBi nuevo (~anual, la subasta de mayo), añade una línea a COUPONS.
  Si el on-the-run no tiene cupón conocido, el scraper AVISA ruidoso y usa el más nuevo
  con cupón conocido (sigue produciendo, pero te alerta a añadir el nuevo).

FRESHNESS GATE
--------------
Sale !=0 (CI rojo) si el fichero más reciente que encuentra es más viejo que
STALENESS_LIMIT_DAYS. Mata la clase de fallo "stale silencioso".

USO
---
  python3 fetch_jpy_real.py                 # producción: baja el CSV del día, escribe
  python3 fetch_jpy_real.py --test FILE.csv # valida el parseo/cálculo contra un CSV local
  python3 fetch_jpy_real.py --dry-run       # baja+calcula, imprime, NO escribe
"""

import sys
import os
import re
import ssl
import csv
import io
import argparse
import datetime as dt
import urllib.request
import urllib.error
import socket

# Forzar IPv4: en redes con IPv6 roto (p.ej. Starlink->Japón) urllib se cuelga probando
# IPv6 primero. Esto resuelve solo IPv4, como hace el navegador en la práctica. Inofensivo
# en GitHub Actions (runners IPv4-only). Desactivable con FORCE_IPV4=0.
if os.environ.get("FORCE_IPV4", "1") != "0":
    _orig_gai = socket.getaddrinfo
    def _gai_ipv4(host, port, family=0, type=0, proto=0, flags=0):
        return _orig_gai(host, port, socket.AF_INET, type, proto, flags)
    socket.getaddrinfo = _gai_ipv4

# --------------------------------------------------------------------------- #
# CONFIG
# --------------------------------------------------------------------------- #
JSDA_BASE = os.environ.get(
    "JSDA_BASE", "https://market.jsda.or.jp/en/statistics/bonds/prices/otc/files")
OUT_CSV = os.path.join("data", "RY_G8_JPY.csv")
TARGET_TENOR_Y = 10.0
STALENESS_LIMIT_DAYS = 7
LOOKBACK_DAYS = 9               # días hacia atrás a probar para hallar el último CSV
ENCODING = "shift_jis"
OTR_MIN_RESID = 8.5            # el on-the-run 10Y debe rondar ~9-10y; aviso si baja de esto

QUALITY_TAG = "PROXY_OTR_JGBI"
SOURCE_NOTE = "On-the-run 10Y JGBi real (JSDA price + MoF coupon; MoF BEI method)"

# Cupones fijos por serie JGBi (%), de Auction_Results_for_JGBs.xls (hoja 10年物価連動).
# Tabla COMPLETA #1-#31. Al emitirse un JGBi nuevo (~anual), añade una línea aquí.
COUPONS = {
    1: 1.200, 2: 1.100, 3: 0.500, 4: 0.500, 5: 0.800, 6: 0.800, 7: 0.800,
    8: 1.000, 9: 1.100, 10: 1.100, 11: 1.200, 12: 1.200, 13: 1.300, 14: 1.200,
    15: 1.400, 16: 1.400, 17: 0.100, 18: 0.100, 19: 0.100, 20: 0.100, 21: 0.100,
    22: 0.100, 23: 0.100, 24: 0.100, 25: 0.200, 26: 0.005, 27: 0.005,
    28: 0.005, 29: 0.005, 30: 0.005, 31: 0.600,
}

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
JGBI_NAME_RE = re.compile(r"I/L\s*(\d+)", re.I)


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
            return urllib.request.urlopen(req, timeout=timeout, context=ssl._create_unverified_context())
        raise


def _url_for(d):
    return "%s/%04d/ES%02d%02d%02d.csv" % (JSDA_BASE, d.year, d.year % 100, d.month, d.day)


def fetch_latest_csv():
    """Prueba desde hoy hacia atrás hasta hallar el último ES disponible.
    Devuelve (date_intentada, texto)."""
    today = dt.date.today()
    last_err = None
    for back in range(0, LOOKBACK_DAYS + 1):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:           # sáb/dom: el JSDA no publica
            continue
        url = _url_for(d)
        try:
            with _open(url) as r:
                if getattr(r, "status", 200) == 200:
                    data = r.read()
                    if data and len(data) > 1000:
                        return d, data.decode(ENCODING, "replace")
        except urllib.error.HTTPError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError("No encontré ningún ES*.csv del JSDA en los últimos %d días (%s)."
                       % (LOOKBACK_DAYS, last_err))


# --------------------------------------------------------------------------- #
# PARSEO
# --------------------------------------------------------------------------- #
def _pdate(s):
    s = str(s).strip()
    if len(s) == 8 and s.isdigit():
        try:
            return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    return None


def parse_csv(text):
    """Devuelve (file_date, jgbi, noms):
       jgbi = [(serie#, maturity, price)]  desde code 05
       noms = [(residual_y, compound_yield)]  desde code 02 (nominal con cupón)"""
    file_date = None
    jgbi, nom_rows = [], []
    for r in csv.reader(io.StringIO(text)):
        if len(r) < 8:
            continue
        code = r[1].strip()
        d0 = _pdate(r[0])
        if d0 and file_date is None:
            file_date = d0
        if code == "05":                       # JGBi (inflation-indexed)
            m = JGBI_NAME_RE.search(r[3] or "")
            mat = _pdate(r[4])
            try:
                price = float(r[7])
            except (TypeError, ValueError):
                continue
            if m and mat and 50.0 < price < 200.0:
                jgbi.append((int(m.group(1)), mat, price))
        elif code == "02":                      # JGB nominal con cupón
            mat = _pdate(r[4])
            try:
                cy = float(r[6])                # Average Compound Yield
            except (TypeError, ValueError):
                continue
            if mat and cy < 90.0:               # 999.999 = centinela
                nom_rows.append((mat, cy))
    if file_date is None:
        raise RuntimeError("PARSEO FALLIDO: no hallé fecha de fichero (col 0).")
    return file_date, jgbi, nom_rows


# --------------------------------------------------------------------------- #
# CÁLCULO
# --------------------------------------------------------------------------- #
def ytm_real(price, coupon_pct, maturity, settle, freq=2):
    """YTM real de un bono con cupón semestral. Bisección sobre el precio."""
    cfs, d = [], maturity
    while d > settle:
        cfs.append(d)
        m, y = d.month - 6, d.year
        if m <= 0:
            m += 12; y -= 1
        try:
            d = d.replace(year=y, month=m)
        except ValueError:
            d = d.replace(year=y, month=m, day=28)
    cfs.sort()
    c = coupon_pct / freq
    def pv(yld):
        tot = 0.0
        for cd in cfs:
            t = (cd - settle).days / 365.25
            cf = c + (100.0 if cd == maturity else 0.0)
            tot += cf / ((1 + yld / freq) ** (freq * t))
        return tot
    lo, hi = -0.15, 0.15
    for _ in range(100):
        mid = (lo + hi) / 2
        if pv(mid) > price:
            lo = mid
        else:
            hi = mid
    return mid * 100.0


def _interp_nom(nom_rows, settle, x=TARGET_TENOR_Y):
    pts = sorted((( (mat - settle).days / 365.25, cy) for mat, cy in nom_rows
                  if 0.5 < (mat - settle).days / 365.25 < 40))
    below = [p for p in pts if p[0] <= x]
    above = [p for p in pts if p[0] > x]
    if not (below and above):
        raise RuntimeError("NOM10 FALLIDO: la curva nominal no enmarca 10Y.")
    x0, y0 = below[-1]; x1, y1 = above[0]
    return y0 + (x - x0) * (y1 - y0) / (x1 - x0)


def compute(file_date, jgbi, nom_rows):
    if not jgbi:
        raise RuntimeError("CÁLCULO FALLIDO: no hallé filas JGBi (code 05).")
    settle = file_date
    newest_num = max(n for n, _, _ in jgbi)
    known = [(n, mat, pr) for (n, mat, pr) in jgbi if n in COUPONS]
    if not known:
        raise RuntimeError("CÁLCULO FALLIDO: ningún JGBi con cupón conocido (revisa COUPONS).")
    otr_num, otr_mat, otr_price = max(known, key=lambda t: t[0])
    if otr_num < newest_num:
        sys.stderr.write("[jpy] AVISO: existe JGBi #%d más nuevo SIN cupón en COUPONS. "
                         "Uso #%d (con cupón). Añade el cupón del #%d del Excel del MoF.\n"
                         % (newest_num, otr_num, newest_num))
    resid = (otr_mat - settle).days / 365.25
    if resid < OTR_MIN_RESID:
        sys.stderr.write("[jpy] AVISO: el on-the-run #%d tiene residual %.2fy (<%.1f). "
                         "¿Falta añadir un JGBi nuevo a COUPONS?\n" % (otr_num, resid, OTR_MIN_RESID))

    real10 = ytm_real(otr_price, COUPONS[otr_num], otr_mat, settle)
    nom10 = _interp_nom(nom_rows, settle)
    return {
        "date": file_date,
        "otr_num": otr_num, "otr_resid": resid, "otr_price": otr_price,
        "otr_coupon": COUPONS[otr_num],
        "NOM10": round(nom10, 4),
        "REAL10": round(real10, 4),
        "BE10": round(nom10 - real10, 4),
        "n_jgbi": len(jgbi), "n_nom": len(nom_rows),
    }


# --------------------------------------------------------------------------- #
# CSV idempotente + freshness gate
# --------------------------------------------------------------------------- #
HEADER_COMMENT = "# QUALITY=%s | %s | generated by fetch_jpy_real.py" % (QUALITY_TAG, SOURCE_NOTE)
COLS = ["DATE", "NOM10", "REAL10", "BE10"]


def write_csv(result, path=OUT_CSV):
    datestr = result["date"].strftime("%Y%m%d")
    rows = {}
    if os.path.exists(path):
        with open(path, "r", newline="") as f:
            for parts in csv.reader(f):
                if not parts:
                    continue
                h = parts[0].strip()
                if h.startswith("#") or h == "DATE":
                    continue
                if len(parts) >= 4 and h.isdigit():
                    rows[h] = [p.strip() for p in parts[:4]]
    rows[datestr] = [datestr, "%g" % result["NOM10"], "%g" % result["REAL10"], "%g" % result["BE10"]]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(HEADER_COMMENT + "\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(COLS)
        for k in sorted(rows):
            w.writerow(rows[k])
    return datestr


def freshness_gate(result):
    age = (dt.date.today() - result["date"]).days
    if age > STALENESS_LIMIT_DAYS:
        sys.stderr.write("FRESHNESS GATE FALLO: fichero %s (%d días > %d). CI en rojo.\n"
                         % (result["date"].isoformat(), age, STALENESS_LIMIT_DAYS))
        return False
    return True


def _summary(res):
    print("Fecha fichero : %s" % res["date"].isoformat())
    print("On-the-run    : JGBi #%d  resid %.2fy  cupón %.3f%%  precio %.2f"
          % (res["otr_num"], res["otr_resid"], res["otr_coupon"], res["otr_price"]))
    print("JGBi en hoja  : %d   |   nominales (code 02): %d" % (res["n_jgbi"], res["n_nom"]))
    print("-> NOM10  = %.3f%%" % res["NOM10"])
    print("-> REAL10 = %.3f%%  (on-the-run JGBi)" % res["REAL10"])
    print("-> BE10   = %.3f%%  (= NOM10 - REAL10)" % res["BE10"])


# --------------------------------------------------------------------------- #
# MAIN
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="JPY real 10Y + BEI diario (JGBi on-the-run, JSDA)")
    ap.add_argument("--test", metavar="CSV", help="valida contra un ES*.csv local (no red, no escribe)")
    ap.add_argument("--dry-run", action="store_true", help="baja+calcula, imprime, NO escribe")
    args = ap.parse_args()

    if args.test:
        text = open(args.test, encoding=ENCODING, errors="replace").read()
        fd, jgbi, noms = parse_csv(text)
        _summary(compute(fd, jgbi, noms))
        print("\n[--test] OK (no se escribió CSV).")
        return 0

    fd, text = fetch_latest_csv()
    sys.stderr.write("[jpy] usando ES de %s\n" % fd.isoformat())
    fd2, jgbi, noms = parse_csv(text)
    res = compute(fd2, jgbi, noms)
    _summary(res)
    if not freshness_gate(res):
        return 1
    if args.dry_run:
        print("\n[--dry-run] OK (no se escribió CSV).")
        return 0
    ds = write_csv(res)
    print("\nEscrito %s -> fila %s" % (OUT_CSV, ds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
