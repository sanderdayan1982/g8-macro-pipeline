"""
fetch_bis_policy.py
===================
Generic scraper for G8 central bank policy rates from BIS SDMX RESTful API.

Source:   BIS Data Portal — Central Bank Policy Rates (CBPOL)
Endpoint: https://stats.bis.org/api/v1/data/WS_CBPOL/D.{COUNTRY}/all?format=csv
Dataflow: WS_CBPOL (Central Bank Policy Rates)
Key:      D.{COUNTRY} (Daily frequency, ISO 2-letter country code)
Format:   CSV with full SDMX metadata columns + observations

Origin:   BIS retrieves official policy rates directly from each central bank.
          BIS is the institutional intermediary; data is central-bank-sourced.

Usage:
    python scripts/fetch_bis_policy.py GB     # BoE Bank Rate     -> data/GB_POLICY.csv
    python scripts/fetch_bis_policy.py JP     # BoJ Policy Rate   -> data/JP_POLICY.csv
    python scripts/fetch_bis_policy.py CH     # SNB Policy Rate   -> data/CH_POLICY.csv
    python scripts/fetch_bis_policy.py AU     # RBA Cash Rate     -> data/AU_POLICY.csv

Output:
    data/{COUNTRY}_POLICY.csv  — Policy rate in OHLCV format

License: BIS Terms of permitted use of BIS statistics (data.bis.org/help/legal).
         BIS statistics may be used for analytical/research purposes with attribution.

Notes:
    - This scraper consolidates 4 missing G8 policy rates into a single,
      maintainable script (was previously planned as 4 separate scrapers).
    - NZD OCR is fetched by the existing fetch_bis_ocr.py (same BIS dataflow,
      country code 'NZ'). Kept separate to preserve backward compatibility.
    - USD, EUR, CAD policy rates are NOT fetched here — they are already
      available natively in TradingView feeds (FRED:IORB, ECON:EUDIR, ECON:CAINTR).
    - BIS API v2 does not expose WS_CBPOL yet (verified 2026-05-27 returns 500).
      v1 is the stable endpoint for this dataset.
"""

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_COUNTRIES = {
    "GB": "BoE Bank Rate (UK)",
    "JP": "BoJ Policy Balance Rate (Japan)",
    "CH": "SNB Policy Rate (Switzerland)",
    "AU": "RBA Cash Rate Target (Australia)",
}

BIS_URL_TEMPLATE = "https://stats.bis.org/api/v1/data/WS_CBPOL/D.{country}/all"
OUTPUT_TEMPLATE = "data/{country}_POLICY.csv"

HISTORY_YEARS = 5
TIMEOUT_SECONDS = 60
USER_AGENT = "g8-macro-pipeline/1.0 (https://github.com/sanderdayan1982/g8-macro-pipeline)"

TIME_PERIOD_COL = "TIME_PERIOD"
OBS_VALUE_COL = "OBS_VALUE"
OBS_STATUS_COL = "OBS_STATUS"


# ─────────────────────────────────────────────────────────────────────────────
# FETCH
# ─────────────────────────────────────────────────────────────────────────────

def fetch_bis_policy_rate(
    country: str,
    date_from: datetime,
    date_to: datetime,
) -> list[tuple[str, float]]:
    """
    Fetch daily policy rate for a single country from BIS SDMX REST API v1.
    """
    url = BIS_URL_TEMPLATE.format(country=country)
    params = {"format": "csv"}
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv",
    }

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    text = response.text
    if not text or "TIME_PERIOD" not in text:
        raise ValueError(
            f"BIS response for {country} empty or missing CSV header. "
            "API may have changed."
        )

    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        raise ValueError(f"BIS CSV for {country}: no header row detected")

    if TIME_PERIOD_COL not in reader.fieldnames or OBS_VALUE_COL not in reader.fieldnames:
        raise ValueError(
            f"BIS CSV for {country}: expected columns {TIME_PERIOD_COL!r} and "
            f"{OBS_VALUE_COL!r} not found. Got: {reader.fieldnames}"
        )

    rows: list[tuple[str, float]] = []
    for row_dict in reader:
        time_period = (row_dict.get(TIME_PERIOD_COL) or "").strip()
        obs_value = (row_dict.get(OBS_VALUE_COL) or "").strip()
        obs_status = (row_dict.get(OBS_STATUS_COL) or "").strip().upper()

        if not time_period or not obs_value:
            continue

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


# ─────────────────────────────────────────────────────────────────────────────
# WRITE
# ─────────────────────────────────────────────────────────────────────────────

def write_csv(rows: list[tuple[str, float]], output_path: Path) -> None:
    """Write rows to OHLCV format CSV (O=H=L=C for daily rates, V=0)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
        for date_str, value in rows:
            v = f"{value:.4f}"
            writer.writerow([date_str, v, v, v, v, "0"])


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python fetch_bis_policy.py <COUNTRY_CODE>", file=sys.stderr)
        print(f"Supported codes: {', '.join(SUPPORTED_COUNTRIES.keys())}", file=sys.stderr)
        return 2

    country = sys.argv[1].upper()

    if country not in SUPPORTED_COUNTRIES:
        print(f"ERROR: Unsupported country code '{country}'", file=sys.stderr)
        print(f"Supported codes: {', '.join(SUPPORTED_COUNTRIES.keys())}", file=sys.stderr)
        return 2

    label = SUPPORTED_COUNTRIES[country]
    today = datetime.utcnow()
    date_from = today - timedelta(days=365 * HISTORY_YEARS)

    output_path = (
        Path(__file__).resolve().parent.parent
        / OUTPUT_TEMPLATE.format(country=country)
    )

    print(f"Fetching {label} from {date_from.date()} to {today.date()}")
    print(f"BIS Dataflow: WS_CBPOL / D.{country} (Daily, {country})")

    try:
        rows = fetch_bis_policy_rate(country, date_from, today)
    except requests.HTTPError as exc:
        print(f"ERROR: BIS HTTP error for {country}: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR: BIS network error for {country}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {country} policy fetch failed: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print(f"ERROR: No {country} policy rows returned from BIS", file=sys.stderr)
        return 1

    write_csv(rows, output_path)
    print(f"OK: Wrote {len(rows)} rows to {output_path}")
    print(f"     Latest:   {rows[-1][0]} = {rows[-1][1]:.4f}%")
    print(f"     Earliest: {rows[0][0]} = {rows[0][1]:.4f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
