#!/usr/bin/env python3
"""fetch_tona_mac.py — v1.1 (2026-09-24, lote 3B)
TONA (BoJ FM01'STRDCLUCON) descargado en el Mac, como NZD/CHF. El fetch de GitHub Actions
(scripts/fetch_tona.py) dejó de actualizar el 16-sep. SIN VERIFICAR: la causa atribuida (el BoJ rechaza IP de
centro de datos) es una hipótesis no demostrada; queda pendiente de la sonda F8. No usar como hecho.
Mismo endpoint y mismo formato OHLCV que scripts/fetch_tona.py → data/TONA.csv (5 años).
push_nzd_to_github.py lo publica fusionándolo contra la rama. No sobrescribe nada si la descarga falla.

v1.1: HTTP por g8common (reintentos 1+3 con presupuesto, Retry-After persistido, STATUS del JSON del BoJ
validado; mismo transporte curl_cffi→requests); escritura atómica y validada; registro en state/fetch_tona.json.
Fuente y frecuencia: sin cambios.
"""
import csv, io, json, os, sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g8common import ingest as _ingest, macfetch  # noqa: E402

URL = "https://www.stat-search.boj.or.jp/api/v1/getDataCode"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "TONA.csv")
_F = None


def _fetch():
    global _F
    if _F is None:
        _F = macfetch.Fetch("tona", os.path.dirname(os.path.dirname(OUT)))   # carpeta del Mac (data/ y state/)
    return _F


def get(params):
    """JSON del BoJ. Un STATUS de error dentro del JSON (HTTP 200) cuenta como fallo (500/503 → transitorio)."""
    body = _fetch().get(URL, params=params, timeout=45, validate=_ingest.boj_status,
                        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    return json.loads(body.decode("utf-8"))


def render_csv(rows):
    """Mismo contenido byte a byte que el escritor v1.0, en memoria."""
    f = io.StringIO(newline="")
    w = csv.writer(f); w.writerow(["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
    for ds, x in rows:
        s = "%.4f" % x; w.writerow([ds, s, s, s, s, "0"])
    return f.getvalue().encode("utf-8")


def main():
    today = datetime.utcnow()
    p = {"format": "json", "lang": "en", "db": "FM01", "code": "STRDCLUCON",
         "startDate": (today - timedelta(days=365 * 5)).strftime("%Y%m"), "endDate": today.strftime("%Y%m")}
    F = _fetch()
    try:
        d = get(p)
    except Exception as e:
        print("[fetch_tona] FATAL %s — TONA.csv no se toca" % e)
        F.error("descarga BoJ: %s" % e)
        return F.finish(1)
    if d.get("STATUS") != 200:
        print("[fetch_tona] FATAL API STATUS=%s %s" % (d.get("STATUS"), d.get("MESSAGE")))
        F.error("BoJ STATUS=%s" % d.get("STATUS"))
        return F.finish(1)
    ser = [s for s in d.get("RESULTSET", []) if s.get("SERIES_CODE") == "STRDCLUCON"]
    if not ser:
        print("[fetch_tona] FATAL serie no encontrada"); F.error("serie STRDCLUCON no encontrada"); return F.finish(1)
    v = ser[0]["VALUES"]
    rows = sorted((str(a), float(b)) for a, b in zip(v["SURVEY_DATES"], v["VALUES"]) if b is not None and len(str(a)) == 8)
    if len(rows) < 500:
        print("[fetch_tona] FATAL solo %d filas — TONA.csv no se toca" % len(rows))
        F.error("respuesta corta: %d filas" % len(rows))
        return F.finish(1)
    if not F.write(OUT, render_csv(rows), expect_header=["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]):
        return F.finish(1)
    print("[fetch_tona] TONA.csv: %d rows, latest %s = %.4f" % (len(rows), rows[-1][0], rows[-1][1]))
    return F.finish(0)


if __name__ == "__main__":
    sys.exit(main())
