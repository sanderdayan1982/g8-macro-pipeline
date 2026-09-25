"""
fetch_jpy_bills.py
==================
Scraper for Japanese Government Bond (JGB) constant-maturity yields from the
Japanese Ministry of Finance (MOF).

Source:   MOF Japan — JGB Interest Rate data (two endpoints, merged)
Endpoints:
    1. CURRENT MONTH (fresh, daily):
       https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv
       Small file (~2 KB), holds the running current month, updated on MOF's
       daily-ish cycle. THIS is the source of fresh data.
    2. HISTORICAL (stable base):
       https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv
       Large file (~1.1 MB), full history back to 1974. NOTE: this file is
       only refreshed periodically (observed last-modified 2026-01-06), so it
       must NOT be the sole source — it lags by months. It provides the stable
       multi-year history; the current-month file provides freshness.

Why both:
    Using `jgbcme_all.csv` alone (the previous design) froze the data at the
    file's last server-side refresh (~Jan 2026 -> data died Apr 30). The
    current-month `jgbcme.csv` carries the live tail. Merging the two yields
    full 5-year history AND fresh data, deduplicating on date with the
    current-month file winning on any overlap.

Anti-cache:
    MOF's current-month CSV carries a note: "If you cannot download the latest
    csv data, please clear the browser's cache." We send Cache-Control and
    Pragma no-cache headers plus a cache-busting query param to force a fresh
    copy on every run.

Tenors:   1Y, 2Y, 3Y, 4Y, 5Y, 6Y, 7Y, 8Y, 9Y, 10Y, 15Y, 20Y, 25Y, 30Y, 40Y
          (we extract a curated subset).

Why 1Y as bill_short_JPY (not 3M):
    Japan does not publish a daily constant-maturity yield below 1Y. The 1Y
    JGB is the shortest live daily curve point. In the engine, JPY is paired
    against US 1Y for tenor symmetry.

Format:   Both CSVs share the same shape:
            Line 1: "Interest Rate (Month YYYY),,,...,(Unit : %)"
            Line 2: "Date,1Y,2Y,3Y,...,40Y"
          Then dated rows: "YYYY/M/D,val,val,..." (no zero-padding).
          Missing values appear as "-". CRLF line endings.

Output:
    data/JPY_BILL_1Y.csv   — 1-year JGB yield (bill_short_JPY for engine)
    data/JPY_BILL_2Y.csv   — 2-year
    data/JPY_BILL_3Y.csv   — 3-year
    data/JPY_BILL_5Y.csv   — 5-year  (curve cross-validation)
    data/JPY_BILL_10Y.csv  — 10-year (benchmark)
    data/JPY_BILL_20Y.csv  — 20-year (synthetic curve Phase 6)

License: MOF Japan public data. Usage permitted per Japanese government open
         data policy. Citation: Ministry of Finance, Japan — JGB Interest Rate
         Historical Data.
"""

import csv
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests


# Constants
MOF_CURRENT_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/"
    "interest_rate/jgbcme.csv"
)
MOF_HISTORICAL_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/"
    "interest_rate/historical/jgbcme_all.csv"
)
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 60
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Curated tenors to extract: short-end for engine + curve benchmarks
TENORS = {
    "1Y": "JPY_BILL_1Y.csv",   # bill_short_JPY for engine
    "2Y": "JPY_BILL_2Y.csv",
    "3Y": "JPY_BILL_3Y.csv",
    "5Y": "JPY_BILL_5Y.csv",
    "10Y": "JPY_BILL_10Y.csv",
    "20Y": "JPY_BILL_20Y.csv",
}

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"

# Marker for missing values in the source CSV
MISSING_MARKER = "-"


def _parse_mof_date(s: str) -> Optional[datetime]:
    """
    MOF date format is YYYY/M/D (no zero-padding), e.g. '2026/4/30'.
    Python's %m and %d are tolerant of unpadded values when reading.
    """
    s = s.strip()
    try:
        return datetime.strptime(s, "%Y/%m/%d")
    except ValueError:
        return None


def _fetch_one(url: str, label: str, cache_bust: bool) -> str:
    """
    Download a single MOF CSV with anti-cache headers.
    Returns the response text. Raises on HTTP/network error.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv,*/*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    params = {}
    if cache_bust:
        # Cache-busting query param to defeat any intermediary caching
        params["_"] = str(int(time.time()))

    print(f"  Fetching {label}: {url}")
    response = requests.get(
        url, headers=headers, params=params, timeout=TIMEOUT_SECONDS
    )
    response.raise_for_status()
    return response.text


def _parse_mof_csv(
    text: str,
    date_from: datetime,
    date_to: datetime,
    col_for_tenor: Optional[Dict[str, int]] = None,
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, int]]:
    """
    Parse one MOF CSV body.

    Returns:
        - dict mapping tenor -> {date_str_YYYYMMDD: value}
        - the resolved col_for_tenor mapping (so caller can reuse / verify)

    If col_for_tenor is provided, it's reused (both files share schema); else
    it's resolved from this file's header.
    """
    if not text or "Interest Rate" not in text[:200]:
        raise ValueError("MOF response empty or unexpected header.")

    lines = text.splitlines()
    if len(lines) < 3:
        raise ValueError("MOF CSV has fewer than 3 lines.")

    # Line 2 (index 1) is the header row
    header = next(csv.reader([lines[1]]))
    if not header or header[0].strip() != "Date":
        raise ValueError(
            f"MOF CSV header row should start with 'Date', got: {header[:3]}"
        )

    if col_for_tenor is None:
        col_for_tenor = {}
        for tenor in TENORS:
            for j, cell in enumerate(header):
                if cell.strip() == tenor:
                    col_for_tenor[tenor] = j
                    break
        missing = [t for t in TENORS if t not in col_for_tenor]
        if missing:
            raise ValueError(
                f"MOF CSV: requested tenors {missing} not in header: {header}"
            )

    out: Dict[str, Dict[str, float]] = {t: {} for t in TENORS}

    for row in csv.reader(lines[2:]):
        if not row or not row[0]:
            continue
        date_obj = _parse_mof_date(row[0])
        if date_obj is None:
            continue
        if date_obj < date_from or date_obj > date_to:
            continue
        date_str = date_obj.strftime("%Y%m%d")
        for tenor, col_idx in col_for_tenor.items():
            if col_idx >= len(row):
                continue
            value_raw = row[col_idx].strip()
            if not value_raw or value_raw == MISSING_MARKER:
                continue
            try:
                value = float(value_raw)
            except ValueError:
                continue
            out[tenor][date_str] = value

    return out, col_for_tenor


def fetch_jpy_bills(
    date_from: datetime,
    date_to: datetime,
) -> Dict[str, List[Tuple[str, float]]]:
    """
    Fetch JGB tenors from BOTH MOF endpoints and merge.

    Strategy:
        1. Parse historical file (stable multi-year base).
        2. Parse current-month file (fresh tail).
        3. Merge per tenor per date; current-month wins on overlap.
        4. Return sorted lists.

    Resilience:
        If the current-month fetch fails, we still return historical data
        (degraded but not broken). If historical fails but current-month
        works, we return current-month only (fresh but short). Only if BOTH
        fail do we raise.
    """
    hist_data: Dict[str, Dict[str, float]] = {t: {} for t in TENORS}
    curr_data: Dict[str, Dict[str, float]] = {t: {} for t in TENORS}
    col_map: Optional[Dict[str, int]] = None

    hist_ok = False
    curr_ok = False

    # 1. Historical (stable base) — no cache-bust needed, it's large/static
    try:
        hist_text = _fetch_one(MOF_HISTORICAL_URL, "historical (jgbcme_all)", cache_bust=False)
        hist_data, col_map = _parse_mof_csv(hist_text, date_from, date_to, None)
        hist_ok = True
        hist_count = sum(len(v) for v in hist_data.values())
        print(f"  Historical parsed: {hist_count} (tenor,date) points")
    except (requests.RequestException, ValueError) as e:
        print(f"  WARNING: historical fetch/parse failed: {e}", file=sys.stderr)

    # 2. Current month (fresh tail) — cache-bust ON
    try:
        curr_text = _fetch_one(MOF_CURRENT_URL, "current month (jgbcme)", cache_bust=True)
        curr_data, col_map = _parse_mof_csv(curr_text, date_from, date_to, col_map)
        curr_ok = True
        curr_count = sum(len(v) for v in curr_data.values())
        print(f"  Current-month parsed: {curr_count} (tenor,date) points")
    except (requests.RequestException, ValueError) as e:
        print(f"  WARNING: current-month fetch/parse failed: {e}", file=sys.stderr)

    if not hist_ok and not curr_ok:
        raise ValueError("Both MOF endpoints failed — no JPY data available")

    # 3. Merge: start with historical, overlay current-month (current wins)
    merged: Dict[str, Dict[str, float]] = {t: {} for t in TENORS}
    for tenor in TENORS:
        merged[tenor].update(hist_data.get(tenor, {}))
        merged[tenor].update(curr_data.get(tenor, {}))  # current overwrites

    # 4. Convert to sorted lists
    results: Dict[str, List[Tuple[str, float]]] = {}
    for tenor in TENORS:
        rows = sorted(merged[tenor].items(), key=lambda kv: kv[0])
        results[tenor] = [(d, v) for d, v in rows]

    return results


def write_csv(rows: List[Tuple[str, float]], output_path: Path) -> None:
    """Write rows to OHLCV format CSV (O=H=L=C for daily rates, V=0)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
        for date_str, value in rows:
            v = f"{value:.4f}"
            writer.writerow([date_str, v, v, v, v, "0"])


def main() -> int:
    today = datetime.now(timezone.utc).replace(tzinfo=None)
    date_from = today - timedelta(days=365 * HISTORY_YEARS)

    print(f"Fetching JGB yields from {date_from.date()} to {today.date()}")
    print(f"Source: MOF Japan (current-month + historical, merged)")
    print(f"Tenors: {list(TENORS.keys())}")
    print()

    try:
        results = fetch_jpy_bills(date_from, today)
    except requests.HTTPError as exc:
        print(f"ERROR: MOF HTTP error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR: MOF network error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: JPY bills fetch failed: {exc}", file=sys.stderr)
        return 1

    print()
    successes = 0
    failures = 0

    for tenor, filename in TENORS.items():
        rows = results.get(tenor, [])
        output_path = OUTPUT_DIR / filename

        if not rows:
            print(f"[{tenor}] ERROR: No rows returned", file=sys.stderr)
            failures += 1
            continue

        write_csv(rows, output_path)
        print(f"[{tenor}] OK: Wrote {len(rows)} rows to {filename}")
        print(f"        Latest:   {rows[-1][0]} = {rows[-1][1]:.4f}%")
        print(f"        Earliest: {rows[0][0]} = {rows[0][1]:.4f}%")

    print()
    print(f"Summary: {successes} OK, {failures} failed (of {len(TENORS)} total)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
