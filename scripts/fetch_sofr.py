"""
fetch_sofr.py
=============
Scraper for USD Secured Overnight Financing Rate (SOFR) from FRED.

Source:   FRED (Federal Reserve Economic Data, St. Louis Fed)
Endpoint: https://fred.stlouisfed.org/graph/fredgraph.csv
Series:   SOFR
          (Federal Reserve Bank of New York, Secured Overnight Financing Rate)
Format:   CSV with header "DATE,SOFR" and rows "YYYY-MM-DD,value"
Note:     Missing values (weekends, holidays) appear as "." and are filtered.

Output:
    data/SOFR.csv  — SOFR historical series in OHLCV format

License: FRED data is publicly available without API key for fredgraph endpoint.
         SOFR is published by the Federal Reserve Bank of New York.
         Citation required for academic/commercial use:
         Federal Reserve Bank of New York, Secured Overnight Financing Rate [SOFR],
         retrieved from FRED, Federal Reserve Bank of St. Louis;
         https://fred.stlouisfed.org/series/SOFR.

Notes:
    SOFR is the institutional reference rate for USD in the XCCY G8 basis engine,
    serving as the equivalent of €STR for EUR, SONIA for GBP, SARON for CHF, etc.
    All other RFRs are normalized against SOFR in the proxy formula:
      XCCY_basis(X) = (RFR_X - bill_short_X) - (SOFR - bill_short_US) + asw_correction
"""

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests


# Constants
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_SERIES_ID = "SOFR"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "SOFR.csv"
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 30
USER_AGENT = "xccy-g8/1.0 (https://github.com/sanderdayan1982/xccy-g8)"


def fetch_sofr_data(date_from: datetime, date_to: datetime) -> list[tuple[str, float]]:
    """
    Fetch SOFR daily data from FRED via fredgraph CSV endpoint.

    Returns list of (date_str_YYYYMMDD, rate_value) tuples sorted ascending.
    """
    params = {
        "id": FRED_SERIES_ID,
        "cosd": date_from.strftime("%Y-%m-%d"),
        "coed": date_to.strftime("%Y-%m-%d"),
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv",
    }

    response = requests.get(
        FRED_URL,
        params=params,
        headers=headers,
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    text = response.text
    if not text or "DATE" not in text.upper():
        raise ValueError("FRED response empty or missing CSV header")

    reader = csv.reader(text.splitlines())
    header = next(reader, None)
    if not header or len(header) < 2:
        raise ValueError("FRED CSV header malformed")

    rows: list[tuple[str, float]] = []
    for raw_row in reader:
        if len(raw_row) < 2:
            continue

        date_str = raw_row[0].strip()
        value_str = raw_row[1].strip()

        if not date_str or not value_str or value_str == ".":
            # FRED uses "." for missing values (weekends, holidays)
            continue

        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        try:
            value = float(value_str)
        except ValueError:
            continue

        rows.append((date_obj.strftime("%Y%m%d"), value))

    rows.sort(key=lambda r: r[0])
    return rows


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

    print(f"Fetching SOFR from {date_from.date()} to {today.date()}")
    print(f"FRED Series: {FRED_SERIES_ID}")

    try:
        rows = fetch_sofr_data(date_from, today)
    except requests.HTTPError as exc:
        print(f"ERROR: FRED HTTP error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR: FRED network error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: SOFR fetch failed: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print("ERROR: No SOFR rows returned from FRED", file=sys.stderr)
        return 1

    write_csv(rows, OUTPUT_PATH)
    print(f"OK: Wrote {len(rows)} rows to {OUTPUT_PATH}")
    print(f"     Latest: {rows[-1][0]} = {rows[-1][1]:.4f}%")
    print(f"     Earliest: {rows[0][0]} = {rows[0][1]:.4f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
