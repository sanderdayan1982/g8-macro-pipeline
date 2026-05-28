"""
fetch_aud_bills.py
==================
Scraper for Australian Bank Accepted Bill / NCD yields (1M, 3M, 6M) from the
Reserve Bank of Australia.

Source:   RBA Statistical Tables — F1 Money Market Daily
Endpoint: https://www.rba.gov.au/statistics/tables/csv/f1-data.csv
          (same table as AONIA / fetch_aonia.py — one CSV holds both the RFR
          and the bank bill yields, but we keep separate scrapers for clarity:
          AONIA = overnight RFR, this = short-end bills for the basis engine)

Series (parsed by stable RBA Series ID, NOT by column title):
    FIRMMBAB30D   — Bank Accepted Bills / NCDs, 1 month  (source: ASX)
    FIRMMBAB90D   — Bank Accepted Bills / NCDs, 3 months (source: ASX) [bill_short_AUD]
    FIRMMBAB180D  — Bank Accepted Bills / NCDs, 6 months (source: ASX)

Output:
    data/AUD_BILL_1M.csv  — 1-month BAB/NCD yield
    data/AUD_BILL_3M.csv  — 3-month BAB/NCD yield (bill_short_AUD for engine)
    data/AUD_BILL_6M.csv  — 6-month BAB/NCD yield

Why Bank Accepted Bills (BABs) as bill_short_AUD:
    The 3-month BAB/NCD rate is Australia's short-term funding benchmark, the
    AUD analogue of NZ's BKBM, Canada's 90-day T-bill, and the US 3-month bill.
    It is the institutionally correct short-end for the cross-currency basis.
    (The F1 table also carries Treasury Notes and OIS; BABs are the funding
    benchmark, so they are used here. TNs could serve as cross-validation in a
    future version.)

Why parse by Series ID instead of column title:
    RBA F1 has a "Series ID" metadata row with stable codes (FIRMMBAB90D etc.)
    that do not change even if column titles are reworded or reordered. This is
    more robust than the title-based detection used in fetch_aonia.py.

Format:   CSV with ~10 metadata rows (Title, Description, Frequency, Type,
          Units, Source, Publication date, Series ID), then dated data rows.
          Dates are DD-Mon-YYYY (e.g. "04-Jan-2011"). Verified 2026-05-28.

Output values stored in PERCENT (e.g. 4.9700), consistent with all other
scrapers. Missing cells are skipped.

License: Data sourced from Reserve Bank of Australia public statistics,
         licensed under Creative Commons Attribution 4.0 (CC BY 4.0).
         Bank bill data originally sourced from ASX.
"""

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests


# Constants
RBA_URL = "https://www.rba.gov.au/statistics/tables/csv/f1-data.csv"
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 30
USER_AGENT = "xccy-g8/1.0 (https://github.com/sanderdayan1982/xccy-g8)"

# Tenor -> (RBA Series ID, output filename)
BILL_SERIES = {
    "1M": ("FIRMMBAB30D", "AUD_BILL_1M.csv"),
    "3M": ("FIRMMBAB90D", "AUD_BILL_3M.csv"),
    "6M": ("FIRMMBAB180D", "AUD_BILL_6M.csv"),
}

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"

# Label of the metadata row that lists the stable series codes
SERIES_ID_ROW_LABEL = "Series ID"
MAX_METADATA_ROWS = 25


def _try_parse_rba_date(s: str) -> datetime | None:
    """RBA date format is typically DD-Mon-YYYY (e.g. '04-Jan-2011')."""
    s = s.strip()
    for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def fetch_aud_bills(
    date_from: datetime,
    date_to: datetime,
) -> dict[str, list[tuple[str, float]]]:
    """
    Fetch all three AUD BAB tenors from RBA F1 in a single download.

    Returns dict mapping tenor ("1M"/"3M"/"6M") to a list of
    (date_str_YYYYMMDD, value) tuples sorted ascending.

    Columns are located by their stable Series ID (FIRMMBAB*), found in the
    "Series ID" metadata row.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "text/csv"}
    response = requests.get(RBA_URL, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    text = response.text
    if not text or "F1" not in text[:200]:
        raise ValueError("RBA F1 response empty or unexpected header")

    all_rows = list(csv.reader(text.splitlines()))
    if not all_rows:
        raise ValueError("RBA F1: no rows parsed")

    # Locate the "Series ID" metadata row to map series codes to column indices
    series_id_row = None
    for row in all_rows[:MAX_METADATA_ROWS]:
        if row and row[0].strip() == SERIES_ID_ROW_LABEL:
            series_id_row = row
            break

    if series_id_row is None:
        raise ValueError(
            f"RBA F1: '{SERIES_ID_ROW_LABEL}' metadata row not found in first "
            f"{MAX_METADATA_ROWS} rows. Table format may have changed."
        )

    # Map each wanted series code to its column index
    col_for_tenor: dict[str, int] = {}
    for tenor, (series_id, _) in BILL_SERIES.items():
        for j, cell in enumerate(series_id_row):
            if cell.strip() == series_id:
                col_for_tenor[tenor] = j
                break

    missing = [t for t in BILL_SERIES if t not in col_for_tenor]
    if missing:
        raise ValueError(
            f"RBA F1: series IDs for tenors {missing} not found in Series ID row. "
            f"Looked for: {[BILL_SERIES[t][0] for t in missing]}"
        )

    # Collect data rows (those starting with a parseable date)
    results: dict[str, list[tuple[str, float]]] = {t: [] for t in BILL_SERIES}

    for row in all_rows:
        if not row:
            continue
        date_obj = _try_parse_rba_date(row[0])
        if date_obj is None:
            continue
        if date_obj < date_from or date_obj > date_to:
            continue

        date_str = date_obj.strftime("%Y%m%d")
        for tenor, col_idx in col_for_tenor.items():
            if col_idx >= len(row):
                continue
            value_raw = row[col_idx].strip()
            if not value_raw:
                continue
            try:
                value = float(value_raw)
            except ValueError:
                continue
            results[tenor].append((date_str, value))

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

    print(f"Fetching AUD bank bills from {date_from.date()} to {today.date()}")
    print(f"RBA Statistical Table: F1 (Money Market Daily)")
    print(f"Tenors: {list(BILL_SERIES.keys())}")
    print()

    try:
        results = fetch_aud_bills(date_from, today)
    except requests.HTTPError as exc:
        print(f"ERROR: RBA HTTP error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR: RBA network error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: AUD bills fetch failed: {exc}", file=sys.stderr)
        return 1

    successes = 0
    failures = 0

    for tenor, (series_id, filename) in BILL_SERIES.items():
        rows = results.get(tenor, [])
        output_path = OUTPUT_DIR / filename

        if not rows:
            print(f"[{tenor}] ERROR: No rows for {series_id}", file=sys.stderr)
            failures += 1
            continue

        write_csv(rows, output_path)
        print(f"[{tenor}] OK ({series_id}): Wrote {len(rows)} rows to {filename}")
        print(f"        Latest:   {rows[-1][0]} = {rows[-1][1]:.4f}%")
        print(f"        Earliest: {rows[0][0]} = {rows[0][1]:.4f}%")
        print()
        successes += 1

    print(f"Summary: {successes} OK, {failures} failed (of {len(BILL_SERIES)} total)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
