"""
fetch_acm.py
============
Scraper for the U.S. Treasury 10-Year Term Premium from the
Adrian-Crump-Moench (ACM) five-factor, no-arbitrage term structure model
estimated by Federal Reserve Bank of New York staff.

Source:   New York Fed — Treasury Term Premia data product
Endpoint: https://www.newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xls
Sheet:    "ACM Daily"
Column:   ACMTP10  (10-year zero-coupon term premium, in percent)

Output:
    data/ACM_TP_10Y.csv  — ACM 10Y term premium in OHLCV format

Format of XLS (verified empirically 2026-06-09):
    - 2 sheets: "ACM Monthly", "ACM Daily"
    - 31 columns in each:
        DATE, ACMY01..10 (fitted yields), ACMTP01..10 (term premia),
        ACMRNY01..10 (risk-neutral yields)
    - DATE format: DD-MMM-YYYY (e.g. "05-Jun-2026")
    - Values in percent (full precision, ~6 decimals)
    - Sheet "ACM Daily" has 16,209 rows from 1961-06-14 to present.
    - Update cadence: ~weekly by NY Fed (typical lag of 2-3 business days).

Notes:
    - The file is ~10MB. Reading the whole sheet with pandas/xlrd takes ~3-5s.
    - This scraper extracts ONLY ACMTP10 (10Y term premium) to align with the
      G8 macro use case (long-end real-yield decomposition). Extending to other
      tenors is trivial — add more entries to TENOR_MAP and write per-tenor
      output files in the same pattern as convert_manual.py.
    - The model methodology is the five-factor, no-arbitrage affine model
      described in Adrian, Crump and Moench (2013), "Pricing the Term Structure
      with Linear Regressions", Journal of Financial Economics 110(1).
    - NOT to be confused with the Kim-Wright model (FRED THREEFYTP10), which
      is a different three-factor model from Kim and Wright (2005). The two
      estimates are highly correlated (~0.95) but methodologically distinct.

License: NY Fed data is public-domain U.S. government work. Suggested citation:
    "Tobias Adrian, Richard Crump, and Emanuel Moench (2013).
     Treasury Term Premia: 1961-Present. Federal Reserve Bank of New York."

Run (in GitHub Actions, same pattern as other scrapers):
    python3 scripts/fetch_acm.py

Requires:
    pandas (already in requirements.txt for other scrapers)
    xlrd >= 2.0.1  (must be added to requirements.txt — pure-Python legacy .xls)
"""

import csv
import sys
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests


# Constants
ACM_URL = "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xls"
ACM_SHEET = "ACM Daily"
ACM_DATE_FORMAT = "%d-%b-%Y"  # e.g. "05-Jun-2026"

# Tenor map: column in XLS -> output filename in data/
# Extend here to add more tenors (e.g. ACMTP02 -> ACM_TP_2Y.csv)
TENOR_MAP = {
    "ACMTP10": "ACM_TP_10Y.csv",
}

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 60
USER_AGENT = "g8-macro-pipeline/1.0 (https://github.com/sanderdayan1982/g8-macro-pipeline)"


def fetch_acm_xls(url: str) -> bytes:
    """Download the ACM xls file as bytes."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.ms-excel,application/octet-stream",
    }
    response = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    if not response.content or len(response.content) < 1000:
        raise ValueError(
            f"ACM xls download too small ({len(response.content)} bytes), "
            f"expected ~10MB. Endpoint may have changed."
        )
    return response.content


def parse_acm_daily(
    xls_bytes: bytes,
    column: str,
    date_from: datetime,
    date_to: datetime,
) -> list[tuple[str, float]]:
    """
    Parse ACM Daily sheet and extract (date, value) pairs for the given column
    within the [date_from, date_to] window.

    Returns list of (YYYYMMDD_str, value) sorted ascending.
    """
    df = pd.read_excel(
        BytesIO(xls_bytes),
        sheet_name=ACM_SHEET,
        engine="xlrd",
    )

    if "DATE" not in df.columns:
        raise ValueError(
            f"ACM sheet {ACM_SHEET!r}: 'DATE' column missing. "
            f"Got columns: {list(df.columns)}"
        )

    if column not in df.columns:
        raise ValueError(
            f"ACM sheet {ACM_SHEET!r}: column {column!r} missing. "
            f"Available: {list(df.columns)}"
        )

    # DATE column comes as string "DD-MMM-YYYY". Parse to datetime.
    df["_dt"] = pd.to_datetime(df["DATE"], format=ACM_DATE_FORMAT, errors="coerce")

    bad = df["_dt"].isna().sum()
    if bad > 0:
        print(
            f"  WARNING: {bad} row(s) with unparseable DATE, skipping",
            file=sys.stderr,
        )

    df = df.dropna(subset=["_dt", column])
    df = df[(df["_dt"] >= date_from) & (df["_dt"] <= date_to)]
    df = df.sort_values("_dt", ascending=True)

    rows: list[tuple[str, float]] = []
    for _, r in df.iterrows():
        date_str = r["_dt"].strftime("%Y%m%d")
        try:
            value = float(r[column])
        except (TypeError, ValueError):
            continue
        rows.append((date_str, value))

    return rows


def write_csv(rows: list[tuple[str, float]], output_path: Path) -> None:
    """Write rows to OHLCV format CSV (O=H=L=C for rates, V=0)."""
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

    print(f"Fetching ACM term premia from {ACM_URL}")
    print(f"Window: {date_from.date()} to {today.date()}")

    try:
        xls_bytes = fetch_acm_xls(ACM_URL)
    except requests.HTTPError as exc:
        print(f"ERROR: NY Fed HTTP error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR: NY Fed network error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: ACM download failed: {exc}", file=sys.stderr)
        return 1

    print(f"Downloaded {len(xls_bytes):,} bytes from NY Fed")
    print()

    successes = 0
    failures = 0
    for column, filename in TENOR_MAP.items():
        try:
            rows = parse_acm_daily(xls_bytes, column, date_from, today)
        except Exception as exc:
            print(f"[{column}] ERROR: parse failed: {exc}", file=sys.stderr)
            failures += 1
            continue

        if not rows:
            print(f"[{column}] WARNING: no rows in window, skipping", file=sys.stderr)
            failures += 1
            continue

        output_path = DATA_DIR / filename
        write_csv(rows, output_path)
        print(f"[{column}] OK: Wrote {len(rows)} rows to {filename}")
        print(f"          Latest:   {rows[-1][0]} = {rows[-1][1]:+.4f}%")
        print(f"          Earliest: {rows[0][0]} = {rows[0][1]:+.4f}%")
        successes += 1

    print()
    print(f"Summary: {successes} OK, {failures} failed (of {len(TENOR_MAP)} total)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
