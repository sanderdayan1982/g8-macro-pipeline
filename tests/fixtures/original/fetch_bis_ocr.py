"""
fetch_bis_ocr.py
================
Scraper for NZD Official Cash Rate (OCR) from BIS SDMX RESTful API.

Source:   BIS Data Portal — Central Bank Policy Rates (CBPOL)
Endpoint: https://stats.bis.org/api/v1/data/WS_CBPOL/D.NZ/all?format=csv
Dataflow: WS_CBPOL (Central Bank Policy Rates)
Key:      D.NZ (Daily frequency, New Zealand)
Format:   CSV with full SDMX metadata columns + observations
          Verified empirically (2026-05-27): returns data from 1985-01-04 onwards
Origin:   BIS retrieves OCR directly from Reserve Bank of New Zealand.
          BIS is the institutional intermediary; data is RBNZ-sourced.

Output:
    data/NZD_OCR.csv  — NZD Official Cash Rate in OHLCV format

License: BIS Terms of permitted use of BIS statistics (data.bis.org/help/legal).
         BIS statistics may be used for analytical/research purposes with attribution.

Notes:
    - This scraper replaces the previous fetch_ocr.py which attempted to scrape
      the RBNZ website directly (blocked by Cloudflare). BIS provides the same
      data through a clean REST API without anti-bot protection.
    - The series has a documented break on 17 Mar 1999 (overnight cash rate ->
      official cash rate). For 5-year backfill this break is not relevant.
    - Missing values are represented as "NaN" with OBS_STATUS="M" in the source.
      We filter these out.
    - The series is intended for the v3.0 dashboard as visual reference of NZD
      policy rate. The basis engine for NZD is postponed to v3.1 pending a
      reliable source for the 90-day bank bill (BKBM).
    - BIS API v2 does not expose WS_CBPOL yet (verified 2026-05-27 returns 500).
      v1 is the stable endpoint for this dataset.
"""

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests


# Constants
BIS_URL = "https://stats.bis.org/api/v1/data/WS_CBPOL/D.NZ/all"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "NZD_OCR.csv"
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 60  # BIS responses can be larger than other scrapers
USER_AGENT = "xccy-g8/1.0 (https://github.com/sanderdayan1982/xccy-g8)"

# CSV column indices (verified empirically against API response 2026-05-27)
# Header: FREQ,REF_AREA,UNIT_MEASURE,UNIT_MULT,TIME_FORMAT,COMPILATION,DECIMALS,
#         SOURCE_REF,SUPP_INFO_BREAKS,TITLE,TIME_PERIOD,OBS_VALUE,OBS_STATUS,
#         OBS_CONF,OBS_PRE_BREAK
TIME_PERIOD_COL = "TIME_PERIOD"
OBS_VALUE_COL = "OBS_VALUE"
OBS_STATUS_COL = "OBS_STATUS"


def fetch_bis_ocr_data(date_from: datetime, date_to: datetime) -> list[tuple[str, float]]:
    """
    Fetch NZD OCR daily data from BIS SDMX REST API v1.

    Returns list of (date_str_YYYYMMDD, rate_value) tuples sorted ascending.
    Missing observations (OBS_STATUS=M, value=NaN) are filtered out.
    """
    params = {"format": "csv"}
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv",
    }

    response = requests.get(
        BIS_URL,
        params=params,
        headers=headers,
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    text = response.text
    if not text or "TIME_PERIOD" not in text:
        raise ValueError(
            "BIS response empty or missing CSV header. "
            "API may have changed."
        )

    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        raise ValueError("BIS CSV: no header row detected")

    if TIME_PERIOD_COL not in reader.fieldnames or OBS_VALUE_COL not in reader.fieldnames:
        raise ValueError(
            f"BIS CSV: expected columns {TIME_PERIOD_COL!r} and {OBS_VALUE_COL!r} "
            f"not found. Got: {reader.fieldnames}"
        )

    rows: list[tuple[str, float]] = []
    for row_dict in reader:
        time_period = (row_dict.get(TIME_PERIOD_COL) or "").strip()
        obs_value = (row_dict.get(OBS_VALUE_COL) or "").strip()
        obs_status = (row_dict.get(OBS_STATUS_COL) or "").strip().upper()

        if not time_period or not obs_value:
            continue

        # Filter missing observations (weekends, holidays)
        if obs_status == "M" or obs_value.upper() == "NAN":
            continue

        try:
            date_obj = datetime.strptime(time_period, "%Y-%m-%d")
        except ValueError:
            continue

        if date_obj < date_from or date_obj > date_to:
            continue

        try:
            value = float(obs_value)
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

    print(f"Fetching NZD OCR from {date_from.date()} to {today.date()}")
    print(f"BIS Dataflow: WS_CBPOL / D.NZ (Daily, New Zealand)")

    try:
        rows = fetch_bis_ocr_data(date_from, today)
    except requests.HTTPError as exc:
        print(f"ERROR: BIS HTTP error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR: BIS network error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: NZD OCR fetch failed: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print("ERROR: No NZD OCR rows returned from BIS", file=sys.stderr)
        return 1

    write_csv(rows, OUTPUT_PATH)
    print(f"OK: Wrote {len(rows)} rows to {OUTPUT_PATH}")
    print(f"     Latest: {rows[-1][0]} = {rows[-1][1]:.4f}%")
    print(f"     Earliest: {rows[0][0]} = {rows[0][1]:.4f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
