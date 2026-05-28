"""
fetch_eur_bills.py
==================
Scraper for euro-area AAA-rated sovereign yield curve spot rates from the
European Central Bank Data Portal.

Source:   ECB Data Portal — Financial market data, yield curve (dataset YC)
Endpoint: https://data-api.ecb.europa.eu/service/data/YC/{KEY}?format=csvdata
Keys:     YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_{TENOR}
          where TENOR ∈ {3M, 6M, 1Y, 2Y, 5Y, 10Y}

Dimensions in the key (SDMX):
    B          — Daily (businessweek)
    U2         — Euro area (changing composition)
    EUR        — Euro
    4F         — ECB as provider
    G_N_A      — Government bond, nominal, all issuers whose rating is triple A
    SV_C_YM    — Svensson model, continuous compounding, yield error minimisation
    SR_{tenor} — Spot Rate at the given maturity

Why ECB AAA curve (and not Bundesbank Bunds):
    The ECB AAA yield curve is the institutional EUR safe-asset benchmark. It
    is the same metric used in ECB working papers and BIS CIP/basis research
    for cross-currency analysis. Using a single-country curve (Bunds) as an
    EUR proxy is an approximation; the ECB AAA aggregate IS the official
    EUR safe asset for monetary-area-wide purposes. This also keeps the EUR
    chain consistent: €STR (ECB) + AAA curve (ECB) = single institutional
    voice for the EUR leg of the basis engine.

Why 3M as bill_short_EUR:
    The ECB publishes the AAA curve down to 3-month spot rates daily, so EUR
    can use the symmetric 3M/3M pair against US (DGS3MO from fetch_us_bills.py).
    This is preferable to the longer-tenor anchors required for CHF (2Y) and
    JPY (1Y) where shorter daily curves are not published. EUR therefore
    follows the same tenor structure as CAD and AUD.

Format:   CSV with rich SDMX metadata (40 columns). We parse by column NAME
          (DictReader) and only read TIME_PERIOD and OBS_VALUE. Verified
          empirically 2026-05-28: returns daily data through 2026-05-27.

Output:
    data/EUR_BILL_3M.csv   — 3-month AAA spot rate (bill_short_EUR for engine)
    data/EUR_BILL_6M.csv   — 6-month
    data/EUR_BILL_1Y.csv   — 1-year
    data/EUR_BILL_2Y.csv   — 2-year   (synthetic curve Phase 6)
    data/EUR_BILL_5Y.csv   — 5-year   (synthetic curve)
    data/EUR_BILL_10Y.csv  — 10-year  (benchmark)

License: ECB Data Portal public statistics. Citation:
         European Central Bank, AAA-rated euro area government bond yield curve,
         retrieved from the ECB Data Portal (dataset YC).
"""

import csv
import sys
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import requests


# Constants
ECB_BASE = "https://data-api.ecb.europa.eu/service/data/YC"
KEY_TEMPLATE = "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_{tenor}"
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 45
USER_AGENT = "xccy-g8/1.0 (https://github.com/sanderdayan1982/xccy-g8)"

# Tenor -> (SDMX tenor code, output filename)
TENORS = {
    "3M":  ("3M",  "EUR_BILL_3M.csv"),   # bill_short_EUR for engine
    "6M":  ("6M",  "EUR_BILL_6M.csv"),
    "1Y":  ("1Y",  "EUR_BILL_1Y.csv"),
    "2Y":  ("2Y",  "EUR_BILL_2Y.csv"),   # synthetic curve Phase 6
    "5Y":  ("5Y",  "EUR_BILL_5Y.csv"),
    "10Y": ("10Y", "EUR_BILL_10Y.csv"),  # benchmark
}

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"

# CSV columns we read (the response has ~40 metadata columns; we only need these)
TIME_PERIOD_COL = "TIME_PERIOD"
OBS_VALUE_COL = "OBS_VALUE"
OBS_STATUS_COL = "OBS_STATUS"


def fetch_ecb_yc_series(
    tenor_code: str,
    date_from: datetime,
    date_to: datetime,
) -> list[tuple[str, float]]:
    """
    Fetch a single tenor from the ECB YC dataset via SDMX REST.

    Returns list of (date_str_YYYYMMDD, value) tuples sorted ascending.
    """
    key = KEY_TEMPLATE.format(tenor=tenor_code)
    url = f"{ECB_BASE}/{key}"
    params = {
        "format": "csvdata",
        "startPeriod": date_from.strftime("%Y-%m-%d"),
        "endPeriod": date_to.strftime("%Y-%m-%d"),
    }
    headers = {"User-Agent": USER_AGENT, "Accept": "text/csv"}

    response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    text = response.text
    if not text or TIME_PERIOD_COL not in text:
        raise ValueError(
            f"ECB YC response empty or missing '{TIME_PERIOD_COL}' header for {key}"
        )

    reader = csv.DictReader(StringIO(text))
    if not reader.fieldnames:
        raise ValueError(f"ECB YC: no header row for {key}")

    if TIME_PERIOD_COL not in reader.fieldnames or OBS_VALUE_COL not in reader.fieldnames:
        raise ValueError(
            f"ECB YC: expected columns missing for {key}. "
            f"Got fieldnames: {reader.fieldnames[:10]}..."
        )

    rows: list[tuple[str, float]] = []
    for row in reader:
        time_period = (row.get(TIME_PERIOD_COL) or "").strip()
        obs_value = (row.get(OBS_VALUE_COL) or "").strip()
        obs_status = (row.get(OBS_STATUS_COL) or "").strip().upper()

        if not time_period or not obs_value:
            continue
        # Filter missing observations (defensive; ECB rarely has these)
        if obs_status == "M" or obs_value.upper() == "NAN":
            continue

        try:
            date_obj = datetime.strptime(time_period, "%Y-%m-%d")
        except ValueError:
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

    print(f"Fetching EUR AAA yield curve from {date_from.date()} to {today.date()}")
    print(f"Source: ECB Data Portal (dataset YC, AAA-rated euro area sovereigns)")
    print(f"Tenors: {list(TENORS.keys())}")
    print()

    successes = 0
    failures = 0

    for tenor_label, (tenor_code, filename) in TENORS.items():
        output_path = OUTPUT_DIR / filename
        print(f"[{tenor_label}] Key: YC.{KEY_TEMPLATE.format(tenor=tenor_code)}")

        try:
            rows = fetch_ecb_yc_series(tenor_code, date_from, today)
        except requests.HTTPError as exc:
            print(f"[{tenor_label}] ERROR: ECB HTTP error: {exc}", file=sys.stderr)
            failures += 1
            continue
        except requests.RequestException as exc:
            print(f"[{tenor_label}] ERROR: ECB network error: {exc}", file=sys.stderr)
            failures += 1
            continue
        except Exception as exc:
            print(f"[{tenor_label}] ERROR: fetch failed: {exc}", file=sys.stderr)
            failures += 1
            continue

        if not rows:
            print(f"[{tenor_label}] ERROR: No rows returned", file=sys.stderr)
            failures += 1
            continue

        write_csv(rows, output_path)
        print(f"[{tenor_label}] OK: Wrote {len(rows)} rows to {filename}")
        print(f"        Latest:   {rows[-1][0]} = {rows[-1][1]:.4f}%")
        print(f"        Earliest: {rows[0][0]} = {rows[0][1]:.4f}%")
        print()
        successes += 1

    print(f"Summary: {successes} OK, {failures} failed (of {len(TENORS)} total)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
