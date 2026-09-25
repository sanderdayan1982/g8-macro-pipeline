#!/usr/bin/env python3
"""
fetch_chf_snb.py — SNB data portal + SNB current-rates RSS → CHF curve, SARON, 10Y  — v1.0 (2026-09-13)

Runs on the operator's Mac (launchd, same job as fetch_nzd_b2.py): data.snb.ch rejects
datacenter IPs / non-browser TLS (GitHub Actions gets 403), a residential IP with
curl_cffi impersonating Safari is fine. push_nzd_to_github.py v1.2 uploads data/CHF_*.csv.

Sources
  1. Cube `rendeiduebd` — "Spot interest rates on Swiss Confederation bonds … – Day".
     Zero-coupon spot curve (Nelson-Siegel-Svensson), CHF 1J..10J, 20J, 30J, DAILY since
     1988-01-04, ONE methodology (the old cube `rendoblid` froze 2025-07-31). Published
     MONTHLY (the month's daily rows appear on the 1st business day of the next month).
       → data/CHF_SPOT_<n>Y.csv  (Date,Value)           n = 1..10, 20, 30
  2. Cube `zirepo` — SARON daily (H0 = overnight close). Published weekly (T+~7d).
  3. RSS https://www.snb.ch/public/rss/en/interestRates — official end-of-day values for
     the last ~5 days: R10 (10Y spot), SARH (SARON fixing), SNBLZ (policy rate). Fills the
     publication gap of 1 and 2 so the dashboard is daily.
       → data/CHF_NOM_10Y.csv  (Date,Value,Source)  Source = curve | rss
       → data/CHF_SARON.csv    (Date,Value,Source)  Source = zirepo | rss (rss = 2 decimals)
  Floor spread §02 = CHF_SARON − CH_POLICY (BIS) — SNB tiered remuneration (policy rate up to
  the threshold, policy − 0.25 pp above) makes SARON − policy a reserve-pressure reading.

Incremental: the curve is re-downloaded from (last local date − 45 d); first run downloads
the whole cube once. RSS/zirepo rows never overwrite a curve/zirepo row of the same date.
`--list` prints the dimension codes seen in each cube. Exit 0 = OK, 1 = fatal download.
"""
import csv
import os
import re
import sys
import time
from datetime import date, timedelta

try:
    from curl_cffi import requests as crequests
    _IMPERSONATE = True
except Exception:                                    # pragma: no cover
    crequests = None
    _IMPERSONATE = False
import requests

CUBE_CURVE = "https://data.snb.ch/api/cube/rendeiduebd/data/csv/en"
CUBE_SARON = "https://data.snb.ch/api/cube/zirepo/data/csv/en"
RSS_RATES = "https://www.snb.ch/public/rss/en/interestRates"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
    "Accept": "text/csv,application/xml,text/plain,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://data.snb.ch/en/topics/ziredev/cube/rendeiduebd",
}
OUTPUT_DIR = "data"
TIMEOUT = 120
RETRY_DELAYS = [0, 5, 15]
TENORS = {"1J": 1, "2J": 2, "3J": 3, "4J": 4, "5J": 5, "6J": 6, "7J": 7, "8J": 8, "9J": 9,
          "10J": 10, "20J": 20, "30J": 30}
CURVE_LOOKBACK_DAYS = 45


def _get(url):
    if _IMPERSONATE:
        for imp in ("safari17_0", "chrome124", "chrome"):
            try:
                r = crequests.get(url, headers=HEADERS, timeout=TIMEOUT, impersonate=imp)
                print(f"[fetch_chf_snb] curl_cffi {imp} → HTTP {r.status_code} ({len(r.content):,} B)")
                if r.status_code == 200:
                    return r.content
            except Exception as e:
                print(f"[fetch_chf_snb] curl_cffi {imp} error: {e}")
    r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
    print(f"[fetch_chf_snb] requests → HTTP {r.status_code}")
    r.raise_for_status()
    return r.content


def download(url):
    last = None
    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        if delay:
            time.sleep(delay)
        try:
            print(f"[fetch_chf_snb] attempt {attempt}/{len(RETRY_DELAYS)} GET {url}")
            return _get(url)
        except Exception as e:
            last = e
            print(f"[fetch_chf_snb] error: {e}")
            if "403" in str(e):
                break
    raise RuntimeError(f"download failed: {last}")


def parse_snb_csv(content):
    """SNB long CSV: preamble (CubeId, PublishingDate, blank) then ';'-separated rows."""
    text = content.decode("utf-8-sig", errors="replace")
    lines = [l for l in text.splitlines() if l.strip()]
    pub = None
    start = 0
    for i, l in enumerate(lines):
        if l.startswith('"PublishingDate"'):
            pub = l.split(";")[1].strip('"')
        if l.startswith('"Date"'):
            start = i
            break
    rows = list(csv.reader(lines[start:], delimiter=";"))
    header, body = rows[0], rows[1:]
    return pub, header, body


def read_dv(path):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                out[r["Date"]] = (float(r["Value"]), r.get("Source") or "")
            except (KeyError, ValueError, TypeError):
                continue
    return out


def write_dv(path, series, with_source=False):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("Date,Value,Source\n" if with_source else "Date,Value\n")
        for d in sorted(series):
            v, src = series[d]
            fh.write(f"{d},{round(v, 6)}" + (f",{src}\n" if with_source else "\n"))


def fetch_curve(list_only=False):
    local10 = read_dv(os.path.join(OUTPUT_DIR, "CHF_SPOT_10Y.csv"))
    url = CUBE_CURVE
    if local10:
        since = (date.fromisoformat(max(local10)) - timedelta(days=CURVE_LOOKBACK_DAYS)).isoformat()
        url += f"?fromDate={since}"
    pub, header, body = parse_snb_csv(download(url))
    print(f"[fetch_chf_snb] curve cube published {pub} · {len(body):,} rows")
    if list_only:
        print("  D0:", sorted({r[1] for r in body if len(r) > 3}))
        print("  D1:", sorted({r[2] for r in body if len(r) > 3}))
        return {}
    series = {t: read_dv(os.path.join(OUTPUT_DIR, f"CHF_SPOT_{y}Y.csv")) for t, y in TENORS.items()}
    n_new = 0
    for r in body:
        if len(r) < 4 or r[1] != "CHF" or r[2] not in TENORS or not r[3].strip():
            continue
        try:
            v = float(r[3])
        except ValueError:
            continue
        if r[0] not in series[r[2]] or series[r[2]][r[0]][0] != v:
            n_new += 1
        series[r[2]][r[0]] = (v, "curve")
    for t, y in TENORS.items():
        write_dv(os.path.join(OUTPUT_DIR, f"CHF_SPOT_{y}Y.csv"), series[t])
    last = max(series["10J"]) if series["10J"] else "NONE"
    print(f"[fetch_chf_snb] curve: {len(series['10J']):,} dates, last {last} (10Y {series['10J'].get(last, ('?',))[0]}), {n_new} rows new/changed")
    return series


def fetch_saron(list_only=False):
    pub, header, body = parse_snb_csv(download(CUBE_SARON))
    print(f"[fetch_chf_snb] zirepo published {pub} · {len(body):,} rows")
    if list_only:
        print("  D0:", sorted({r[1] for r in body if len(r) > 2}))
        return {}
    out = {}
    for r in body:
        if len(r) >= 3 and r[1] == "H0" and r[2].strip():
            try:
                out[r[0]] = (float(r[2]), "zirepo")
            except ValueError:
                pass
    return out


def fetch_rss():
    """{code: {date: value}} for R10 / SARH / SNBLZ from the SNB current-rates RSS."""
    x = download(RSS_RATES).decode("utf-8", errors="replace")
    out = {}
    for m in re.finditer(r"<title>CH: (-?[\d.]+) (\w+) (\d{4}-\d{2}-\d{2}) SNB", x):
        v, code, d = m.groups()
        out.setdefault(code, {})[d] = float(v)
    print("[fetch_chf_snb] rss: " + ", ".join(f"{k} {len(v)}d (last {max(v)}={v[max(v)]})" for k, v in out.items() if k in ("R10", "SARH", "SNBLZ")))
    return out


def merge_with_rss(base, rss_vals, path):
    """base = authoritative rows (curve/zirepo); rss fills only dates the base lacks."""
    merged = dict(read_dv(path))
    for d, (v, src) in base.items():
        merged[d] = (v, src)
    for d, v in (rss_vals or {}).items():
        if d not in merged or merged[d][1] == "rss":
            merged[d] = (v, "rss")
    write_dv(path, merged, with_source=True)
    last = max(merged)
    print(f"[fetch_chf_snb] {os.path.basename(path)}: {len(merged):,} rows, last {last} = {merged[last][0]} ({merged[last][1]})")


def main():
    list_only = "--list" in sys.argv
    try:
        curve = fetch_curve(list_only)
        saron = fetch_saron(list_only)
        if list_only:
            return 0
        rss = fetch_rss()
    except Exception as e:
        print(f"[fetch_chf_snb] FATAL: {e}", file=sys.stderr)
        return 1
    merge_with_rss(curve.get("10J", {}), rss.get("R10"), os.path.join(OUTPUT_DIR, "CHF_NOM_10Y.csv"))
    merge_with_rss(saron, rss.get("SARH"), os.path.join(OUTPUT_DIR, "CHF_SARON.csv"))
    print("[fetch_chf_snb] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
