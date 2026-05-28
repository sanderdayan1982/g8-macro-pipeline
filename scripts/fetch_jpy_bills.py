"""
fetch_jpy_bills.py
==================
Scraper for Japanese Government Bond (JGB) constant-maturity yields from the
Japanese Ministry of Finance (MOF).

Source:   MOF Japan — JGB Interest Rate Historical Data
Endpoint: https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/
              historical/jgbcme_all.csv
Note:     Direct static CSV download (Apache, no auth, no session token).
          Verified empirically 2026-05-28: returns 1.18 MB plain CSV,
          identical across requests, last update Apr 30, 2026.

Tenors:   The CSV publishes daily JGB yields across 15 maturities:
          1Y, 2Y, 3Y, 4Y, 5Y, 6Y, 7Y, 8Y, 9Y, 10Y, 15Y, 20Y, 25Y, 30Y, 40Y.
          We extract a curated set used in the engine and the synthetic curve.

Why 1Y as bill_short_JPY (not 3M):
    Japan does not publish a daily constant-maturity yield below 1Y. The MOF
    Treasury Discount Bills page only provides AUCTION results (weekly, not
    continuous daily). The 1Y JGB is the shortest live daily curve point.
    This mirrors the CHF case (2Y minimum from SNB). In the engine, JPY is
    paired against US 1Y (DGS1, already in fetch_us_bills.py) for tenor
    symmetry, eliminating curve-slope bias.

Format:   CSV with 2-line preamble:
            Line 1: "Interest Rate,,,...,(Unit : %)"
            Line 2: "Date,1Y,2Y,3Y,...,40Y"
          Then dated data rows: "YYYY/M/D,val,val,..." (no zero-padding).
          Missing values appear as "-" (long tenors before issuance).
          CRLF line endings.

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
from datetime import datetime, timedelta
from pathlib import Path

import requests


# Constants
MOF_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/"
    "interest_rate/historical/jgbcme_all.csv"
)
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 60  # The full CSV is ~1.2 MB; allow generous timeout
USER_AGENT = "xccy-g8/1.0 (https://github.com/sanderdayan1982/xccy-g8)"

# Curated tenors to extract: short-end for engine + curve benchmarks
# (Other tenors 4Y/6Y/7Y/8Y/9Y/15Y/25Y/30Y/40Y are in the CSV but not extracted
#  to keep the data/ folder focused. Can be added later if needed.)
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


def _parse_mof_date(s: str) -> datetime | None:
    """
    MOF date format is YYYY/M/D (no zero-padding), e.g. '2026/4/30'.
    Python's %m and %d are tolerant of unpadded values when reading, so this
    works for both '2026/4/30' and '2026/04/30'.
    """
    s = s.strip()
    try:
        return datetime.strptime(s, "%Y/%m/%d")
    except ValueError:
        return None


def fetch_jpy_bills(
    date_from: datetime,
    date_to: datetime,
) -> dict[str, list[tuple[str, float]]]:
    """
    Fetch all curated JGB tenors from MOF in a single download.

    Returns dict mapping tenor name ("1Y"/"2Y"/etc) to a list of
    (date_str_YYYYMMDD, value) tuples sorted ascending.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "text/csv"}
    response = requests.get(MOF_URL, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    text = response.text
    if not text or "Interest Rate" not in text[:200]:
        raise ValueError(
            "MOF response empty or unexpected header. URL may have changed."
        )

    lines = text.splitlines()
    if len(lines) < 3:
        raise ValueError("MOF CSV has fewer than 3 lines (preamble + data expected)")

    # Line 2 (index 1) is the header. Line 1 (index 0) is title/units.
    header_line = lines[1]
    header = next(csv.reader([header_line]))
    if not header or header[0].strip() != "Date":
        raise ValueError(
            f"MOF CSV: header row 2 should start with 'Date', got: {header[:3]}"
        )

    # Map each wanted tenor to its column index
    col_for_tenor: dict[str, int] = {}
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

    # Parse data rows (lines from index 2 onward)
    results: dict[str, list[tuple[str, float]]] = {t: [] for t in TENORS}

    data_reader = csv.reader(lines[2:])
    for row in data_reader:
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
            results[tenor].append((date_str, value))

    for tenor in results:
        results[tenor].sort(key=lambda r: r[0])

    return results


def write_csv(rows: list[tuple[str, float]], output_path: Path) -> None:
    """Write rows to OHLCV format CSV (O=H=L=C for daily rates, V=0)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
        for date_str, value in rows:
            v = f"{value:.4f}"
            writer.writerow([date_str, v, v, v, v, "0"])


def main() -> int:
    today = datetime.utcnow()
    date_from = today - timedelta(days=365 * HISTORY_YEARS)

    print(f"Fetching JGB yields from {date_from.date()} to {today.date()}")
    print(f"Source: MOF Japan Interest Rate Historical Data")
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
        successes += 1

    print(f"Summary: {successes} OK, {failures} failed (of {len(TENORS)} total)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
