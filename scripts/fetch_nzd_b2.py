"""
fetch_nzd_b2.py — RBNZ Table B2 (Wholesale interest rates) daily close  — v1.3 (2026-09-13)

Sources NZD short-end and government bond yields from a single RBNZ XLSX:
  - BKBM 30/60/90D bank bill yields (NZD analogue of BBSW/SOFR-bills)
  - NZ Government Bonds 1Y/2Y/5Y/10Y constant maturity closing yields
  - Overnight interbank cash rate (INM.DN.NZK) → NZD_CASH_ON.csv  (§02 floor NZD = cash − OCR)

Endpoint:
  https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/statistics/series/b/b2/hb2-daily-close.xlsx

Outputs (one CSV per series, Date,Value with YYYY-MM-DD dates) into ./data/.

Network (v1.2+): the RBNZ WAF rejects python-requests' TLS fingerprint even from
residential IPs (403). We download with curl_cffi impersonating Safari when
available (pip install --user curl_cffi) and fall back to requests otherwise.
Runs locally on the operator's Mac via launchd (Trading_Sander/g8-nzd);
push_nzd_to_github.py uploads the CSVs to data/ through the GitHub REST API.
NOT run by GitHub Actions (datacenter IPs are blocked by the RBNZ WAF).

Column matching is done by Series Id (row 5 of the Data sheet), NOT by column position.
`--list` prints every column's header rows (1–5) to pin new Series Ids.
"""

import os
import sys
import time
from io import BytesIO
from openpyxl import load_workbook

try:
    from curl_cffi import requests as crequests      # TLS impersonation
    _IMPERSONATE = True
except Exception:                                    # pragma: no cover
    crequests = None
    _IMPERSONATE = False
import requests

URL = (
    "https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/"
    "statistics/series/b/b2/hb2-daily-close.xlsx"
)
REFERER = "https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates/wholesale-interest-rates"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
    "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/octet-stream,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": REFERER,
}

OUTPUT_DIR = "data"
TIMEOUT_SECONDS = 90
RETRY_DELAYS = [0, 5, 15]

SERIES_MAP = {
    "INM.DB01.NZZV":   "NZD_BILL_30D.csv",
    "INM.DB02.NZZV":   "NZD_BILL_60D.csv",
    "INM.DB03.NZZV":   "NZD_BILL_90D.csv",
    "INM.DG101.NZZCF": "NZD_BOND_1Y.csv",
    "INM.DG102.NZZCF": "NZD_BOND_2Y.csv",
    "INM.DG105.NZZCF": "NZD_BOND_5Y.csv",
    "INM.DG110.NZZCF": "NZD_BOND_10Y.csv",
    # v1.3: pinned from `--list` on 2026-09-13 (col 5: Cash rate | Overnight interbank cash rate | %pa)
    "INM.DN.NZK":      "NZD_CASH_ON.csv",
}
# Label discovery disabled: "overnight" also matches the Overnight Deposit Rate (INM.DD1.N),
# which is the RBNZ floor, not the traded cash rate. Pin by Series Id only.
OPTIONAL_BY_LABEL = {}

SHEET_NAME = "Data"
SERIES_ID_ROW = 5
DATA_START_ROW = 6
DATE_COL = 1


def _get(url):
    """One HTTP GET: curl_cffi (Safari TLS) first, plain requests as fallback."""
    if _IMPERSONATE:
        for imp in ("safari17_0", "chrome124", "chrome"):
            try:
                r = crequests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS, impersonate=imp)
                print(f"[fetch_nzd_b2] curl_cffi impersonate={imp} → HTTP {r.status_code}")
                if r.status_code == 200:
                    return r.content
            except Exception as e:
                print(f"[fetch_nzd_b2] curl_cffi {imp} error: {e}")
    r = requests.get(url, timeout=TIMEOUT_SECONDS, headers=HEADERS)
    print(f"[fetch_nzd_b2] requests → HTTP {r.status_code}")
    r.raise_for_status()
    return r.content


def download_xlsx(url: str) -> bytes:
    last_exc = None
    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        if delay:
            print(f"[fetch_nzd_b2] retry in {delay}s...")
            time.sleep(delay)
        try:
            print(f"[fetch_nzd_b2] attempt {attempt}/{len(RETRY_DELAYS)} GET {url}")
            content = _get(url)
            print(f"[fetch_nzd_b2] downloaded {len(content):,} bytes")
            return content
        except Exception as e:
            last_exc = e
            print(f"[fetch_nzd_b2] error: {e}")
            if "403" in str(e):
                break                       # WAF verdict: retrying won't help
    raise RuntimeError(f"[fetch_nzd_b2] download failed: {last_exc}")


def locate_series_columns(ws) -> dict:
    found = {}
    for row in ws.iter_rows(min_row=SERIES_ID_ROW, max_row=SERIES_ID_ROW, values_only=False):
        for cell in row:
            if isinstance(cell.value, str) and cell.value in SERIES_MAP:
                found[cell.value] = cell.column
    missing = set(SERIES_MAP.keys()) - set(found.keys())
    if missing:
        raise RuntimeError(f"[fetch_nzd_b2] missing Series Ids in XLSX (RBNZ schema changed?): {sorted(missing)}")
    import re as _re
    headers = {}
    for row in ws.iter_rows(min_row=1, max_row=SERIES_ID_ROW, values_only=False):
        for cell in row:
            if isinstance(cell.value, str):
                headers.setdefault(cell.column, []).append(cell.value)
    for pattern, fname in OPTIONAL_BY_LABEL.items():
        hit = None
        for col, labels in headers.items():
            if any(_re.search(pattern, lb, _re.I) for lb in labels) and col not in found.values():
                hit = col
                break
        if hit is None:
            print(f"[fetch_nzd_b2] WARN optional series /{pattern}/ not found — {fname} not written")
        else:
            sid = "LABEL:" + fname
            SERIES_MAP[sid] = fname
            found[sid] = hit
            print(f"[fetch_nzd_b2] optional /{pattern}/ → column {hit} ({' | '.join(headers[hit])[:80]}) → {fname}")
    return found


def list_columns(ws) -> None:
    cols = {}
    for row in ws.iter_rows(min_row=1, max_row=SERIES_ID_ROW, values_only=False):
        for cell in row:
            if cell.value is not None:
                cols.setdefault(cell.column, []).append(str(cell.value))
    for col in sorted(cols):
        print(f"col {col:3d}: " + " | ".join(cols[col]))


def extract_series(ws, col_map: dict) -> dict:
    out = {sid: [] for sid in col_map}
    targets = [(sid, col - 1) for sid, col in col_map.items()]
    date_idx = DATE_COL - 1
    for row in ws.iter_rows(min_row=DATA_START_ROW, values_only=True):
        if not row or row[date_idx] is None:
            continue
        try:
            date_str = row[date_idx].strftime("%Y-%m-%d")
        except AttributeError:
            continue
        for sid, idx in targets:
            if idx >= len(row):
                continue
            v = row[idx]
            if v is None or v == "":
                continue
            try:
                out[sid].append((date_str, float(v)))
            except (TypeError, ValueError):
                continue
    return out


def write_csv(path: str, rows) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("Date,Value\n")
        for date_str, value in rows:
            f.write(f"{date_str},{value}\n")


def main() -> int:
    try:
        content = download_xlsx(URL)
    except Exception as e:
        print(f"[fetch_nzd_b2] FATAL download: {e}", file=sys.stderr)
        return 1
    try:
        wb = load_workbook(BytesIO(content), data_only=True)
        if SHEET_NAME not in wb.sheetnames:
            raise RuntimeError(f"sheet '{SHEET_NAME}' not found. Sheets: {wb.sheetnames}")
        ws = wb[SHEET_NAME]
        if "--list" in sys.argv:
            list_columns(ws)
            return 0
        col_map = locate_series_columns(ws)
        print(f"[fetch_nzd_b2] located all {len(col_map)} target series")
        data = extract_series(ws, col_map)
    except Exception as e:
        print(f"[fetch_nzd_b2] FATAL parse: {e}", file=sys.stderr)
        return 2
    total = 0
    for sid, rows in data.items():
        filename = SERIES_MAP[sid]
        write_csv(os.path.join(OUTPUT_DIR, filename), rows)
        ld, lv = rows[-1] if rows else ("NONE", float("nan"))
        print(f"[fetch_nzd_b2] {filename}: {len(rows):,} rows, latest {ld} = {lv}")
        total += len(rows)
    print(f"[fetch_nzd_b2] OK — {len(data)} files, {total:,} total rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
