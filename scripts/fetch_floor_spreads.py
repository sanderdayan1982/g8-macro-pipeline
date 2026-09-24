#!/usr/bin/env python3
"""
fetch_floor_spreads.py — v1.0 (2026-06-12)
G8 Macro Pipeline — money-market floor references not yet in the repo.

Fetches the three deposit-floor / policy-reference rates needed for the
dashboard's Floor Spreads section (RFR − floor = domestic reserve pressure):

    FLOOR_USD.csv  ← FRED IORB        (Interest on Reserve Balances, daily)
    FLOOR_EUR.csv  ← ECB SDMX  FM.D.U2.EUR.4F.KR.DFR.LEV  (Deposit Facility)
    FLOOR_CAD.csv  ← BoC Valet V39079 (Target for the Overnight Rate)

GBP / JPY / CHF / AUD floors already exist in data/ as {GB,JP,CH,AU}_POLICY.csv
(BIS WS_CBPOL, fetched by fetch_bis_policy.py).

Output format: repo-standard OHLCV
    DATE,OPEN,HIGH,LOW,CLOSE,VOLUME   ·  DATE = YYYYMMDD  ·  O=H=L=C  ·  V=0

Tolerant per source: failures reported in summary; exit 1 if any source failed
(workflow runs this step with continue-on-error: true).

Lote 3B (2026-09-24): HTTP y publicación por g8common.ingest — reintentos con presupuesto, Retry-After,
validación por serie, escritura atómica y conservación del último dato válido; registro en
data/_ingest/latest/actions__fetch_floor_spreads.json (avisos de ingest_watch).
"""

import csv
import io
import json
import os
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g8common import ingest as _ingest  # noqa: E402  (lote 3B: HTTP común + publicación segura por fichero)

_ctx = None       # contexto de ingestión (lo crea main())

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
START = "2021-06-01"
FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()

UA = {"User-Agent": "Mozilla/5.0 (g8-macro-pipeline floor-spreads)"}


def http_get(url, retries=3, timeout=60):
    """Lote 3B: g8http con el registro del descargador (reintentos 1+3, Retry-After por ámbito, presupuesto,
    TLS verificado, redirecciones). La API de FRED se clasifica aparte para reconocer la clave rechazada."""
    provider = "fred_api" if "api.stlouisfed.org" in url else "generic"
    return _ctx.requests(provider=provider).get(url, headers=UA, timeout=timeout).text


def render_ohlcv(series):
    """Mismo formato byte a byte que el escritor original (CRLF, V=0), en memoria."""
    f = io.StringIO(newline="")
    w = csv.writer(f, lineterminator="\r\n")
    w.writerow(["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
    for d, v in series:
        ymd = d.replace("-", "")
        w.writerow([ymd, f"{v:.4f}", f"{v:.4f}", f"{v:.4f}", f"{v:.4f}", 0])
    return f.getvalue().encode("utf-8")


def write_ohlcv(name, series):
    """series: list of (date_iso, value) sorted ascending. Lote 3B: fusión monótona + escritura atómica; una
    respuesta corta, retrocedida o implausible no sustituye al último dato válido."""
    rep = _ctx.publish(name, render_ohlcv(series), retain_from=START.replace("-", ""))
    print(f"  {rep['status']}: {name} ({len(series)} filas descargadas, última {series[-1][0]} = "
          f"{series[-1][1]:.4f}) {rep.get('detail', '')}")
    if rep["status"] in ("INVALID", "REGRESSION_BLOCKED"):
        raise RuntimeError(f"{name}: {rep['status']} — {rep.get('detail', '')}")


# ── USD: FRED IORB ──────────────────────────────────────────────────────────

def fetch_usd():
    out = None
    if FRED_KEY:
        url = ("https://api.stlouisfed.org/fred/series/observations"
               f"?series_id=IORB&api_key={FRED_KEY}&file_type=json"
               f"&observation_start={START}")
        try:
            data = json.loads(http_get(url))
            out = [(o["date"], float(o["value"]))
                   for o in data.get("observations", []) if o.get("value") not in (".", "", None)]
        except _ingest.HTTPError as e:                      # F7: clave rechazada → endpoint sin clave, visible
            if e.g8cls != "FAIL_AUTH":
                raise
            print("::error title=FRED_API_KEY::FRED rechaza la clave (FAIL_AUTH) en IORB; se usa fredgraph sin clave")
    else:
        print("::warning title=FRED_API_KEY::FRED_API_KEY no definida; IORB por fredgraph sin clave")
    if out is None:
        text = http_get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=IORB&cosd={START}")
        out = []
        for row in csv.reader(io.StringIO(text)):
            if len(row) >= 2 and len(row[0]) == 10 and row[0][4] == "-" and row[1] not in (".", ""):
                try:
                    out.append((row[0], float(row[1])))
                except ValueError:
                    continue
    if len(out) < 500:
        raise RuntimeError(f"IORB too short: {len(out)} obs")
    print(f"    [FRED IORB] {len(out)} obs  {out[0][0]} → {out[-1][0]}")
    write_ohlcv("FLOOR_USD.csv", out)


# ── EUR: ECB Deposit Facility Rate ──────────────────────────────────────────

def fetch_eur():
    url = ("https://data-api.ecb.europa.eu/service/data/FM/"
           "D.U2.EUR.4F.KR.DFR.LEV"
           f"?format=csvdata&startPeriod={START}")
    text = http_get(url)
    rdr = csv.DictReader(io.StringIO(text))
    out = []
    for row in rdr:
        d = row.get("TIME_PERIOD", "")
        v = row.get("OBS_VALUE", "")
        if len(d) == 10 and v not in ("", None):
            try:
                out.append((d, float(v)))
            except ValueError:
                continue
    out.sort()
    if len(out) < 500:
        raise RuntimeError(f"DFR too short: {len(out)} obs")
    print(f"    [ECB DFR] {len(out)} obs  {out[0][0]} → {out[-1][0]}")
    write_ohlcv("FLOOR_EUR.csv", out)


# ── CAD: BoC Target for the Overnight Rate ──────────────────────────────────

def fetch_cad():
    url = ("https://www.bankofcanada.ca/valet/observations/V39079/json"
           f"?start_date={START}")
    data = json.loads(http_get(url))
    out = []
    for o in data.get("observations", []):
        d = o.get("d", "")
        cell = o.get("V39079", {})
        v = cell.get("v") if isinstance(cell, dict) else None
        if len(d) == 10 and v not in ("", None):
            try:
                out.append((d, float(v)))
            except ValueError:
                continue
    out.sort()
    if len(out) < 500:
        raise RuntimeError(f"V39079 too short: {len(out)} obs")
    print(f"    [Valet V39079] {len(out)} obs  {out[0][0]} → {out[-1][0]}")
    write_ohlcv("FLOOR_CAD.csv", out)


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    global _ctx
    _ctx = _ingest.Ingest("fetch_floor_spreads")
    jobs = [("USD", fetch_usd), ("EUR", fetch_eur), ("CAD", fetch_cad)]
    failed = []
    for tag, fn in jobs:
        print(f"[{tag}] " + "=" * 60)
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            print(f"[{tag}] FAILED: {e}")
            failed.append(tag)
    print(f"Summary: {len(jobs) - len(failed)} OK, {len(failed)} failed of {len(jobs)}")
    sys.exit(_ctx.finish(1 if failed else 0))


if __name__ == "__main__":
    main()
