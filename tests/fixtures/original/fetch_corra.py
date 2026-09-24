"""
fetch_corra.py
==============
Scraper for Canadian Overnight Repo Rate Average (CORRA) from Bank of Canada.

Source:   Bank of Canada Valet API (modern REST JSON, since 2017)
Endpoint: https://www.bankofcanada.ca/valet/observations/AVG.INTWO/json
Series:   AVG.INTWO (Canadian Overnight Repo Rate Average)
Format:   JSON REST

Output:
    data/CORRA.csv  — CORRA historical series in OHLCV format

License: Data sourced from Bank of Canada public statistics.
         BoC retains all rights to source data.

Notes:
    BoC Valet API is the cleanest of the G7 central bank APIs:
    - REST JSON, no auth
    - Predictable structure
    - Excellent documentation at https://www.bankofcanada.ca/valet/docs
    - Date range via start_date and end_date query parameters

    Documented schema:
        {
          "observations": [
            {"d": "YYYY-MM-DD", "AVG.INTWO": {"v": "X.XXXX"}},
            ...
          ]
        }
"""

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import requests


# Constants
SERIES_CODE = "AVG.INTWO"
BOC_URL = f"https://www.bankofcanada.ca/valet/observations/{SERIES_CODE}/json"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "CORRA.csv"
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 30
USER_AGENT = "xccy-g8/1.0 (https://github.com/sanderdayan1982/xccy-g8)"


def build_request_url(date_from: datetime, date_to: datetime) -> str:
    """Build BoC Valet API URL with date range query parameters."""
    params = {
        "start_date": date_from.strftime("%Y-%m-%d"),
        "end_date": date_to.strftime("%Y-%m-%d"),
    }
    return f"{BOC_URL}?{urlencode(params)}"


def fetch_corra_data(date_from: datetime, date_to: datetime) -> list[tuple[str, float]]:
    """
    Fetch CORRA daily data from Bank of Canada Valet API.

    Returns list of (date_str_YYYYMMDD, rate_value) tuples sorted ascending.
    """
    url = build_request_url(date_from, date_to)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    response = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    data = response.json()
    if "observations" not in data:
        raise ValueError("BoC Valet response missing 'observations' key")

    observations = data["observations"]
    if not isinstance(observations, list):
        raise ValueError("BoC Valet response 'observations' is not a list")

    rows: list[tuple[str, float]] = []
    for obs in observations:
        date_raw = obs.get("d")
        series_obj = obs.get(SERIES_CODE)

        if date_raw is None or series_obj is None:
            continue

        value_raw = series_obj.get("v") if isinstance(series_obj, dict) else None
        if value_raw is None or value_raw == "":
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

    print(f"Fetching CORRA from {date_from.date()} to {today.date()}")
    print(f"BoC Valet API series: {SERIES_CODE}")

    try:
        rows = fetch_corra_data(date_from, today)
    except requests.HTTPError as exc:
        print(f"ERROR: BoC HTTP error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR: BoC network error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: CORRA fetch failed: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print("ERROR: No CORRA rows returned from BoC", file=sys.stderr)
        return 1

    write_csv(rows, OUTPUT_PATH)
    print(f"OK: Wrote {len(rows)} rows to {OUTPUT_PATH}")
    print(f"     Latest: {rows[-1][0]} = {rows[-1][1]:.4f}%")
    print(f"     Earliest: {rows[0][0]} = {rows[0][1]:.4f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
