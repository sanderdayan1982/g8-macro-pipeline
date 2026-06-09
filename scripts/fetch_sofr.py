"""
fetch_us_bills.py
=================
Scraper for US Treasury Constant Maturity yields (3M, 6M, 1Y, 2Y) from FRED.

Source:   FRED (Federal Reserve Economic Data, St. Louis Fed)
          Series sourced via H.15 Statistical Release (Federal Reserve Board)
Endpoint: https://fred.stlouisfed.org/graph/fredgraph.csv
Series:   DGS3MO, DGS6MO, DGS1, DGS2 (constant maturity, investment basis)

Format:   CSV with header "DATE,<SERIES_ID>" and rows "YYYY-MM-DD,value"
Note:     Missing values (weekends, holidays) appear as "." and are filtered.

Output:
    data/US_BILL_3M.csv  — 3-month T-bill constant maturity yield
    data/US_BILL_6M.csv  — 6-month T-bill constant maturity yield
    data/US_BILL_1Y.csv  — 1-year T-bill constant maturity yield
    data/US_BILL_2Y.csv  — 2-year T-note constant maturity yield

Why constant maturity (DGS*) vs discount basis (DTB*):
    Constant maturity series are quoted on investment basis (BEY), making them
    directly comparable with European bund yields, JGBs, gilts, etc. This is
    the institutional standard used by Bloomberg/Refinitiv terminals and
    required for cross-currency basis analysis. Discount basis (DTB3/DTB6) is
    only used for direct T-bill trading and is not unit-comparable across
    currencies.

Why three tenors (not just 3M):
    The XCCY basis term structure carries information per institutional
    research (Borio, McCauley, McGuire, Sushko — "Covered Interest Parity Lost",
    BIS Quarterly Review, Sept 2016):
      - 3M basis: captures short-term funding stress (interbank liquidity,
        year-end / quarter-end effects, Libor-OIS dynamics).
      - 6M basis: intermediate tenor, useful for synthetic curve smoothing.
      - 1Y basis: captures structural hedging demand (FX-hedged investment
        flows, ALM matching by insurance / pension funds).
    Having all three available at backfill time avoids the cost of partial
    history reconstruction in Phase 6 (synthetic sovereign curve).

License: FRED data is publicly available without API key for fredgraph endpoint.
         Source citation: Board of Governors of the Federal Reserve System (US),
         retrieved from FRED, Federal Reserve Bank of St. Louis.
         Treasury constant maturity data is from the H.15 Statistical Release.

Robustness (v2 — 2026-06-09):
    FRED endpoint exhibits intermittent Read timeouts from GitHub Actions
    runners (ubuntu-latest) during peak hours. Two production failures observed
    2026-06-09 with TIMEOUT_SECONDS=30. Fix: raise timeout to 90s and retry
    transient network errors up to 3 times with exponential backoff (0/5/15s).
    HTTPError (4xx/5xx) is NOT retried — that indicates a query-level bug.
"""

import csv
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests


# Constants
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 90
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (0, 5, 15)  # delay before attempts 1, 2, 3
USER_AGENT = "g8-macro-pipeline/1.0 (https://github.com/sanderdayan1982/g8-macro-pipeline)"

# Tenor -> FRED series ID mapping
# All series: Treasury Constant Maturity Rate, Investment Basis (H.15)
BILL_SERIES = {
    "3M": "DGS3MO",  # engine basis for USD/EUR/CAD/AUD
    "6M": "DGS6MO",  # engine basis for GBP
    "1Y": "DGS1",    # engine basis for JPY
    "2Y": "DGS2",    # engine basis for CHF (Confederation short-end is 2Y minimum)
}

# Output filenames
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_NAMES = {
    "3M": "US_BILL_3M.csv",
    "6M": "US_BILL_6M.csv",
    "1Y": "US_BILL_1Y.csv",
    "2Y": "US_BILL_2Y.csv",
}


def _request_with_retry(
    url: str,
    params: dict,
    headers: dict,
    timeout: int,
) -> requests.Response:
    """
    GET with retry on transient network errors.

    Retries up to RETRY_ATTEMPTS times on Timeout / ConnectionError, with
    exponential backoff between attempts. Does NOT retry HTTPError — that
    indicates a permanent issue (bad query, endpoint moved) which should
    surface immediately.
    """
    last_exc: Exception | None = None
    for attempt_idx in range(RETRY_ATTEMPTS):
        delay = RETRY_BACKOFF_SECONDS[attempt_idx]
        if delay > 0:
            print(
                f"    Retry attempt {attempt_idx + 1}/{RETRY_ATTEMPTS} "
                f"after {delay}s backoff",
                file=sys.stderr,
            )
            time.sleep(delay)
        try:
            return requests.get(url, params=params, headers=headers, timeout=timeout)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_exc = exc
            print(
                f"    Transient network error on attempt {attempt_idx + 1}: {exc}",
                file=sys.stderr,
            )
            continue
    assert last_exc is not None
    raise last_exc


def fetch_fred_series(
    series_id: str,
    date_from: datetime,
    date_to: datetime,
) -> list[tuple[str, float]]:
    """
    Fetch a single FRED series via fredgraph CSV endpoint.

    Returns list of (date_str_YYYYMMDD, value) tuples sorted ascending.
    Missing observations (FRED "." marker) are filtered out.
    """
    params = {
        "id": series_id,
        "cosd": date_from.strftime("%Y-%m-%d"),
        "coed": date_to.strftime("%Y-%m-%d"),
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv",
    }

    response = _request_with_retry(
        FRED_URL,
        params=params,
        headers=headers,
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    text = response.text
    if not text or "DATE" not in text.upper():
        raise ValueError(
            f"FRED response empty or missing CSV header for {series_id}"
        )

    reader = csv.reader(text.splitlines())
    header = next(reader, None)
    if not header or len(header) < 2:
        raise ValueError(f"FRED CSV header malformed for {series_id}")

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

    print(f"Fetching US T-bills from {date_from.date()} to {today.date()}")
    print(f"Tenors: {list(BILL_SERIES.keys())}")
    print()

    successes = 0
    failures = 0

    for tenor, series_id in BILL_SERIES.items():
        output_path = OUTPUT_DIR / OUTPUT_NAMES[tenor]
        print(f"[{tenor}] FRED series: {series_id}")

        try:
            rows = fetch_fred_series(series_id, date_from, today)
        except requests.HTTPError as exc:
            print(f"[{tenor}] ERROR: FRED HTTP error: {exc}", file=sys.stderr)
            failures += 1
            continue
        except requests.RequestException as exc:
            print(f"[{tenor}] ERROR: FRED network error after {RETRY_ATTEMPTS} attempts: {exc}",
                  file=sys.stderr)
            failures += 1
            continue
        except Exception as exc:
            print(f"[{tenor}] ERROR: fetch failed: {exc}", file=sys.stderr)
            failures += 1
            continue

        if not rows:
            print(f"[{tenor}] ERROR: No rows returned from FRED", file=sys.stderr)
            failures += 1
            continue

        write_csv(rows, output_path)
        print(f"[{tenor}] OK: Wrote {len(rows)} rows to {output_path.name}")
        print(f"        Latest:   {rows[-1][0]} = {rows[-1][1]:.4f}%")
        print(f"        Earliest: {rows[0][0]} = {rows[0][1]:.4f}%")
        print()
        successes += 1

    print(f"Summary: {successes} OK, {failures} failed (of {len(BILL_SERIES)} total)")

    # Exit code: 0 if all succeeded, 1 if any failed
    # This semantic allows partial-success runs to be visible in Actions
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
