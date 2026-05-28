"""
fetch_cad_bills.py
==================
Scraper for Canadian Treasury Bill yields (3M, 6M, 1Y) from Bank of Canada.

Source:   Bank of Canada Valet API
Endpoint: https://www.bankofcanada.ca/valet/observations/{series}/csv
Series:   TB.CDN.90D.MID  (V39065) — 3-month T-bill, secondary market mid yield
          TB.CDN.180D.MID (V39066) — 6-month T-bill, secondary market mid yield
          TB.CDN.1Y.MID   (V39067) — 1-year T-bill, secondary market mid yield

Format:   CSV with metadata preamble, then "OBSERVATIONS" section with header
          row "date,<series...>" and data rows. Verified empirically 2026-05-28:
          - Daily frequency (business days)
          - Columns returned in ALPHABETICAL order, NOT request order
            => MUST parse by column name (DictReader), not by position
          - Missing values are empty strings ""

Output:
    data/CAD_BILL_3M.csv  — 3-month T-bill yield
    data/CAD_BILL_6M.csv  — 6-month T-bill yield
    data/CAD_BILL_1Y.csv  — 1-year T-bill yield

Why secondary market mid (TB.CDN.*.MID) vs auction average (V8069130x):
    The .MID series are DAILY secondary-market mid-market closing yields,
    analogous to the US DGS constant maturity series. The auction-average
    series (V80691303/304/305) are WEEKLY (Tuesdays only) and unsuitable for
    a daily basis engine.

Why 3M as bill_short_CAD (not 1M):
    The 1-month Canadian T-bill was only issued temporarily (May 2024-July 2025)
    to support the transition after CDOR ceased publication (June 2024). The
    3-month is the stable, liquid short tenor, consistent with the bill_short
    choice for all other currencies.

License: Bank of Canada Valet API, terms at https://www.bankofcanada.ca/terms/
         Data may be used with attribution to Bank of Canada.
"""

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests


# Constants
VALET_BASE = "https://www.bankofcanada.ca/valet/observations"
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 30
USER_AGENT = "xccy-g8/1.0 (https://github.com/sanderdayan1982/xccy-g8)"

# Tenor -> Valet series ID mapping
# All series: secondary-market mid-market closing yields, DAILY
BILL_SERIES = {
    "3M": "TB.CDN.90D.MID",   # V39065
    "6M": "TB.CDN.180D.MID",  # V39066
    "1Y": "TB.CDN.1Y.MID",    # V39067
}

# Output filenames
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_NAMES = {
    "3M": "CAD_BILL_3M.csv",
    "6M": "CAD_BILL_6M.csv",
    "1Y": "CAD_BILL_1Y.csv",
}


def fetch_cad_bills(
    date_from: datetime,
    date_to: datetime,
) -> dict[str, list[tuple[str, float]]]:
    """
    Fetch all three CAD T-bill tenors in a single Valet API call.

    Returns dict mapping tenor ("3M"/"6M"/"1Y") to a list of
    (date_str_YYYYMMDD, value) tuples sorted ascending.

    The Valet API returns all requested series in one CSV. Columns appear in
    alphabetical order regardless of request order, so we parse by column name.
    """
    series_csv = ",".join(BILL_SERIES.values())
    url = f"{VALET_BASE}/{series_csv}/csv"
    params = {
        "start_date": date_from.strftime("%Y-%m-%d"),
        "end_date": date_to.strftime("%Y-%m-%d"),
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv",
    }

    response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    text = response.text
    if not text or "OBSERVATIONS" not in text:
        raise ValueError(
            "BoC Valet response empty or missing OBSERVATIONS section. "
            "API may have changed."
        )

    # The CSV has a metadata preamble before the actual data table.
    # Find the OBSERVATIONS marker, then the header row follows it.
    lines = text.splitlines()
    obs_idx = None
    for i, line in enumerate(lines):
        if line.strip().strip('"') == "OBSERVATIONS":
            obs_idx = i
            break

    if obs_idx is None or obs_idx + 1 >= len(lines):
        raise ValueError("BoC Valet: OBSERVATIONS section not found or empty")

    # Parse from the header row (immediately after OBSERVATIONS marker)
    data_block = "\n".join(lines[obs_idx + 1:])
    reader = csv.DictReader(data_block.splitlines())

    if not reader.fieldnames or "date" not in reader.fieldnames:
        raise ValueError(
            f"BoC Valet: expected 'date' column not found. Got: {reader.fieldnames}"
        )

    # Verify all requested series are present as columns
    for tenor, series_id in BILL_SERIES.items():
        if series_id not in reader.fieldnames:
            raise ValueError(
                f"BoC Valet: series {series_id} ({tenor}) not in response columns: "
                f"{reader.fieldnames}"
            )

    # Collect per-tenor rows
    results: dict[str, list[tuple[str, float]]] = {t: [] for t in BILL_SERIES}

    for row in reader:
        date_str = (row.get("date") or "").strip()
        if not date_str:
            continue

        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        for tenor, series_id in BILL_SERIES.items():
            value_str = (row.get(series_id) or "").strip()
            if not value_str:
                # Missing value for this tenor on this date
                continue
            try:
                value = float(value_str)
            except ValueError:
                continue
            results[tenor].append((date_obj.strftime("%Y%m%d"), value))

    for tenor in results:
        results[tenor].sort(key=lambda r: r[0])

    return results


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

    print(f"Fetching CAD T-bills from {date_from.date()} to {today.date()}")
    print(f"Tenors: {list(BILL_SERIES.keys())}")
    print(f"Source: Bank of Canada Valet API (single call)")
    print()

    try:
        results = fetch_cad_bills(date_from, today)
    except requests.HTTPError as exc:
        print(f"ERROR: BoC Valet HTTP error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR: BoC Valet network error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: CAD bills fetch failed: {exc}", file=sys.stderr)
        return 1

    successes = 0
    failures = 0

    for tenor in BILL_SERIES:
        rows = results.get(tenor, [])
        output_path = OUTPUT_DIR / OUTPUT_NAMES[tenor]

        if not rows:
            print(f"[{tenor}] ERROR: No rows returned", file=sys.stderr)
            failures += 1
            continue

        write_csv(rows, output_path)
        print(f"[{tenor}] OK: Wrote {len(rows)} rows to {output_path.name}")
        print(f"        Latest:   {rows[-1][0]} = {rows[-1][1]:.4f}%")
        print(f"        Earliest: {rows[0][0]} = {rows[0][1]:.4f}%")
        print()
        successes += 1

    print(f"Summary: {successes} OK, {failures} failed (of {len(BILL_SERIES)} total)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
