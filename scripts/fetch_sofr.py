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

import io

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g8common import ingest as _ingest  # noqa: E402  (lote 3: HTTP con reintentos + publicación segura)

requests = None   # lo asigna main(): sustituto de requests.get basado en g8http


# Constants
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_SERIES_ID = "SOFR"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "SOFR.csv"
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 90
RETRY_ATTEMPTS = 4  # lote 3: política de g8http (intento + 3 reintentos); solo se usa en el mensaje de error
USER_AGENT = "g8-macro-pipeline/1.0 (https://github.com/sanderdayan1982/g8-macro-pipeline)"


# lote 3: _request_with_retry eliminado — g8http reintenta (1+3, 10/40/90 s, Retry-After) sin anidar capas.

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


def render_csv(rows) -> bytes:
    """Mismo formato byte a byte que write_csv, en memoria (lote 3)."""
    f = io.StringIO(newline="")
    writer = csv.writer(f)
    writer.writerow(["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
    for date_str, value in rows:
        v = f"{value:.4f}"
        writer.writerow([date_str, v, v, v, v, "0"])
    return f.getvalue().encode("utf-8")


def main() -> int:
    global requests
    _ctx = _ingest.Ingest('fetch_sofr')
    requests = _ctx.requests(provider='fred')
    today = datetime.utcnow()
    date_from = today - timedelta(days=365 * HISTORY_YEARS)

    print(f"Fetching SOFR from {date_from.date()} to {today.date()}")
    print(f"FRED Series: {FRED_SERIES_ID}")

    try:
        rows = fetch_sofr_data(date_from, today)
    except requests.HTTPError as exc:
        print(f"ERROR: FRED HTTP error: {exc}", file=sys.stderr)
        return _ctx.finish(1)
    except requests.RequestException as exc:
        print(f"ERROR: FRED network error after {RETRY_ATTEMPTS} attempts: {exc}",
              file=sys.stderr)
        return _ctx.finish(1)
    except Exception as exc:
        print(f"ERROR: SOFR fetch failed: {exc}", file=sys.stderr)
        return _ctx.finish(1)

    if not rows:
        print("ERROR: No SOFR rows returned from FRED", file=sys.stderr)
        return _ctx.finish(1)

    _rep = _ctx.publish(OUTPUT_PATH.name, render_csv(rows), retain_from=date_from.strftime("%Y%m%d"))

    print(f"Publicación {OUTPUT_PATH.name}: {_rep['status']} {_rep.get('detail', '')}")
    print(f"Descarga: {len(rows)} filas para {OUTPUT_PATH.name}")
    print(f"     Latest: {rows[-1][0]} = {rows[-1][1]:.4f}%")
    print(f"     Earliest: {rows[0][0]} = {rows[0][1]:.4f}%")
    return _ctx.finish(0)


if __name__ == "__main__":
    sys.exit(main())
