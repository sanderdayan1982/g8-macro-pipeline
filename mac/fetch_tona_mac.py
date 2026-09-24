#!/usr/bin/env python3
"""fetch_tona_mac.py — v1.0 (2026-09-24)
TONA (BoJ FM01'STRDCLUCON) descargado en el Mac, como NZD/CHF. El fetch de GitHub Actions
(scripts/fetch_tona.py) dejó de actualizar el 16-sep (BoJ rechaza IPs de datacenter).
Mismo endpoint y mismo formato OHLCV que scripts/fetch_tona.py → data/TONA.csv (5 años).
push_nzd_to_github.py v1.3 lo sube solo si cambió. No sobrescribe nada si la descarga falla.
"""
import csv, json, os, sys
from datetime import datetime, timedelta

URL = "https://www.stat-search.boj.or.jp/api/v1/getDataCode"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "TONA.csv")


def get(params):
    last = None
    try:
        from curl_cffi import requests as cr
        for imp in ("safari17_0", "chrome124"):
            try:
                r = cr.get(URL, params=params, impersonate=imp, timeout=45)
                print("[fetch_tona] curl_cffi %s → HTTP %s" % (imp, r.status_code))
                if r.status_code == 200:
                    return r.json()
                last = "HTTP %s" % r.status_code
            except Exception as e:
                last = str(e)
    except ImportError:
        pass
    import requests
    r = requests.get(URL, params=params, timeout=45, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    print("[fetch_tona] requests → HTTP %s" % r.status_code)
    if r.status_code == 200:
        return r.json()
    raise RuntimeError("BoJ inaccesible: %s / %s" % (last, r.status_code))


def main():
    today = datetime.utcnow()
    p = {"format": "json", "lang": "en", "db": "FM01", "code": "STRDCLUCON",
         "startDate": (today - timedelta(days=365 * 5)).strftime("%Y%m"), "endDate": today.strftime("%Y%m")}
    try:
        d = get(p)
    except Exception as e:
        print("[fetch_tona] FATAL %s — TONA.csv no se toca" % e)
        return 1
    if d.get("STATUS") != 200:
        print("[fetch_tona] FATAL API STATUS=%s %s" % (d.get("STATUS"), d.get("MESSAGE")))
        return 1
    ser = [s for s in d.get("RESULTSET", []) if s.get("SERIES_CODE") == "STRDCLUCON"]
    if not ser:
        print("[fetch_tona] FATAL serie no encontrada"); return 1
    v = ser[0]["VALUES"]
    rows = sorted((str(a), float(b)) for a, b in zip(v["SURVEY_DATES"], v["VALUES"]) if b is not None and len(str(a)) == 8)
    if len(rows) < 500:
        print("[fetch_tona] FATAL solo %d filas — TONA.csv no se toca" % len(rows)); return 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
        for ds, x in rows:
            s = "%.4f" % x; w.writerow([ds, s, s, s, s, "0"])
    print("[fetch_tona] TONA.csv: %d rows, latest %s = %.4f" % (len(rows), rows[-1][0], rows[-1][1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
