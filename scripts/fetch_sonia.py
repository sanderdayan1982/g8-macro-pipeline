"""
fetch_sonia.py
==============
Scraper for Sterling Overnight Index Average (SONIA) from Bank of England.

Source: Bank of England Interactive Database (IADB)
Series code: IUDSOIA (Daily Sterling Overnight Index Average)
Endpoint: https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp

Output: data/SONIA.csv in Pine Seeds format
        DATE,OPEN,HIGH,LOW,CLOSE,VOLUME
        YYYYMMDD,value,value,value,value,0

License: Data sourced from Bank of England public statistics.
         Bank of England retains all rights to source data.
"""

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import requests


# Constants
SERIES_CODE = "IUDSOIA"
BOE_URL = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "SONIA.csv"
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 30
USER_AGENT = "xccy-g8-seeds/1.0 (https://github.com/sanderdayan1982/xccy-g8-seeds)"


def build_request_url(date_from: datetime, date_to: datetime) -> str:
    """Build BoE IADB query URL for SONIA series."""
    params = {
        "Travel": "NIxAZxSUx",
        "FromSeries": "1",
        "ToSeries": "50",
        "DAT": "RNG",
        "FD": date_from.strftime("%d"),
        "FM": date_from.strftime("%b"),
        "FY": date_from.strftime("%Y"),
        "TD": date_to.strftime("%d"),
        "TM": date_to.strftime("%b"),
        "TY": date_to.strftime("%Y"),
        "FNY": "Y",
        "CSVF": "TN",
        "html.x": "66",
        "html.y": "26",
        "SeriesCodes": SERIES_CODE,
        "UsingCodes": "Y",
        "Filter": "N",
        "title": SERIES_CODE,
        "VPD": "Y",
    }
    return f"{BOE_URL}?{urlencode(params)}"


def fetch_sonia_data(date_from: datetime, date_to: datetime) -> list[tuple[str, float]]:
    """
    Fetch SONIA daily data from Bank of England.

    Returns list of (date_str_YYYYMMDD, rate_value) tuples sorted ascending.
    """
    url = build_request_url(date_from, date_to)
    headers = {"User-Agent": USER_AGENT}

    response = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    if not response.text or "DATE" not in response.text.upper():
        raise ValueError("BoE response empty or missing DATE header")

    rows: list[tuple[str, float]] = []
    reader = csv.reader(response.text.splitlines())
    header = next(reader, None)
    if not header:
        raise ValueError("BoE response has no rows")

    for row in reader:
        if len(row) < 2:
            continue
        try:
            date_obj = datetime.strptime(row[0].strip(), "%d %b %Y")
            value = float(row[1].strip())
        except (ValueError, IndexError):
            continue
        rows.append((date_obj.strftime("%Y%m%d"), value))

    rows.sort(key=lambda r: r[0])
    return rows


def write_csv(rows: list[tuple[str, float]], output_path: Path) -> None:
    """Write rows to Pine Seeds OHLCV format."""
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

    print(f"Fetching SONIA from {date_from.date()} to {today.date()}")
    try:
        rows = fetch_sonia_data(date_from, today)
    except Exception as exc:
        print(f"ERROR: SONIA fetch failed: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print("ERROR: No SONIA rows returned from BoE", file=sys.stderr)
        return 1

    write_csv(rows, OUTPUT_PATH)
    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")
    print(f"Latest: {rows[-1][0]} = {rows[-1][1]:.4f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
