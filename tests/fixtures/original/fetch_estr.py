"""
fetch_estr.py
=============
Scraper for Euro Short-Term Rate (€STR) from European Central Bank.

Source: ECB Data Portal (modern endpoint, replaces legacy SDW)
Endpoint: https://data-api.ecb.europa.eu/service/data/EST
Series:   EST.B.EU000A2X2A25.WT  (€STR daily rate)
Format:   SDMX-CSV

Output:
    data/ESTR.csv  — €STR historical series in OHLCV format
    data/EUR_BASIS_ECB.csv  — (optional) EUR/USD basis reference if available

License: Data sourced from European Central Bank public statistics.
         ECB retains all rights to source data.
"""

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import requests


# Constants
ECB_BASE_URL = "https://data-api.ecb.europa.eu/service/data"
ESTR_SERIES = "EST/B.EU000A2X2A25.WT"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "ESTR.csv"
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 30
USER_AGENT = "xccy-g8/1.0 (https://github.com/sanderdayan1982/xccy-g8)"


def build_request_url(series_key: str, date_from: datetime, date_to: datetime) -> str:
    """Build ECB Data Portal URL for a given series with date range."""
    base = f"{ECB_BASE_URL}/{series_key}"
    params = {
        "startPeriod": date_from.strftime("%Y-%m-%d"),
        "endPeriod": date_to.strftime("%Y-%m-%d"),
        "format": "csvdata",
    }
    return f"{base}?{urlencode(params)}"


def fetch_estr_data(date_from: datetime, date_to: datetime) -> list[tuple[str, float]]:
    """
    Fetch €STR daily series from ECB Data Portal.

    Returns list of (date_str_YYYYMMDD, rate_value) tuples sorted ascending.
    """
    url = build_request_url(ESTR_SERIES, date_from, date_to)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv",
    }

    response = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    text = response.text
    if not text or "TIME_PERIOD" not in text:
        raise ValueError("ECB response empty or missing expected SDMX-CSV columns")

    rows: list[tuple[str, float]] = []
    reader = csv.DictReader(text.splitlines())

    for record in reader:
        date_raw = record.get("TIME_PERIOD", "").strip()
        value_raw = record.get("OBS_VALUE", "").strip()

        if not date_raw or not value_raw:
            continue

        try:
            date_obj = datetime.strptime(date_raw, "%Y-%m-%d")
            value = float(value_raw)
        except (ValueError, TypeError):
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

    print(f"Fetching €STR from {date_from.date()} to {today.date()}")
    print(f"ECB Data Portal series: {ESTR_SERIES}")

    try:
        rows = fetch_estr_data(date_from, today)
    except requests.HTTPError as exc:
        print(f"ERROR: ECB HTTP error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR: ECB network error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: €STR fetch failed: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print("ERROR: No €STR rows returned from ECB", file=sys.stderr)
        return 1

    write_csv(rows, OUTPUT_PATH)
    print(f"OK: Wrote {len(rows)} rows to {OUTPUT_PATH}")
    print(f"     Latest: {rows[-1][0]} = {rows[-1][1]:.4f}%")
    print(f"     Earliest: {rows[0][0]} = {rows[0][1]:.4f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
