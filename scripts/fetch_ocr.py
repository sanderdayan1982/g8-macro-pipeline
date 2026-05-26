"""
fetch_ocr.py
============
Scraper for Official Cash Rate (OCR) from Reserve Bank of New Zealand.

Source:   RBNZ Statistics — B2 Wholesale interest rates
Endpoint: https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/statistics/tables/b2/hb2-daily.csv
Format:   CSV with descriptive header rows then DATE, [columns]
Series:   OCR column (Official Cash Rate)

Output:
    data/OCR.csv  — OCR historical series in OHLCV format

License: Data sourced from Reserve Bank of New Zealand public statistics.
         RBNZ retains all rights to source data.

Notes:
    OCR is the policy rate (set by Monetary Policy Committee ~7-8 times/year),
    not a daily interbank fixing. Between policy meetings the value is flat.
    This is the appropriate proxy for NZD funding analysis since the NZ
    interbank market is too small for a meaningful overnight reference rate
    like SONIA or €STR.

    Like RBA, RBNZ identifies columns by name (not position) for resilience.
"""

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests


# Constants
RBNZ_URL = "https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/statistics/tables/b2/hb2-daily.csv"
TARGET_COLUMN_HINTS = [
    "Official Cash Rate",
    "OCR",
]
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "OCR.csv"
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 30
USER_AGENT = "xccy-g8/1.0 (https://github.com/sanderdayan1982/xccy-g8)"


def _try_parse_rbnz_date(s: str) -> datetime | None:
    """RBNZ uses formats DD/MM/YYYY or YYYY-MM-DD typically."""
    s = s.strip().strip('"')
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def find_data_column_index(rows: list[list[str]]) -> tuple[int, int]:
    """
    Locate the data start row and the OCR column index.

    RBNZ CSV structure: ~3-5 metadata rows, then a row with column titles,
    then data rows. We find the title row by searching for OCR hints,
    then the first row after that with a parseable date.

    Returns (data_start_row_index, ocr_column_index).
    """
    title_row_idx = None
    for i, row in enumerate(rows[:15]):
        if not row:
            continue
        joined = " | ".join(cell for cell in row if cell).lower()
        if any(hint.lower() in joined for hint in TARGET_COLUMN_HINTS):
            title_row_idx = i
            break

    if title_row_idx is None:
        raise ValueError(
            f"RBNZ B2 CSV structure unexpected: target hints {TARGET_COLUMN_HINTS} "
            "not found in first 15 rows"
        )

    title_row = rows[title_row_idx]
    ocr_col_idx = None
    for hint in TARGET_COLUMN_HINTS:
        for j, cell in enumerate(title_row):
            if cell and hint.lower() in cell.lower().strip():
                ocr_col_idx = j
                break
        if ocr_col_idx is not None:
            break

    if ocr_col_idx is None:
        raise ValueError("RBNZ B2 CSV: OCR column not found in title row")

    data_start_idx = None
    for i in range(title_row_idx + 1, min(title_row_idx + 15, len(rows))):
        row = rows[i]
        if not row or not row[0]:
            continue
        if _try_parse_rbnz_date(row[0]) is not None:
            data_start_idx = i
            break

    if data_start_idx is None:
        raise ValueError("RBNZ B2 CSV: no data rows found after title row")

    return data_start_idx, ocr_col_idx


def fetch_ocr_data(date_from: datetime, date_to: datetime) -> list[tuple[str, float]]:
    """
    Fetch OCR daily data from RBNZ Statistics B2.

    Returns list of (date_str_YYYYMMDD, rate_value) tuples sorted ascending.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv",
    }

    response = requests.get(RBNZ_URL, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    text = response.text
    if not text:
        raise ValueError("RBNZ response empty")

    all_rows = list(csv.reader(text.splitlines()))
    if not all_rows:
        raise ValueError("RBNZ response has no parseable rows")

    data_start_idx, ocr_col_idx = find_data_column_index(all_rows)

    rows: list[tuple[str, float]] = []
    for raw_row in all_rows[data_start_idx:]:
        if len(raw_row) <= ocr_col_idx:
            continue

        date_obj = _try_parse_rbnz_date(raw_row[0])
        if date_obj is None:
            continue

        if date_obj < date_from or date_obj > date_to:
            continue

        value_raw = raw_row[ocr_col_idx].strip().strip('"')
        if not value_raw or value_raw in ("-", "N/A", "..."):
            continue

        try:
            value = float(value_raw)
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

    print(f"Fetching OCR from {date_from.date()} to {today.date()}")
    print(f"RBNZ Statistical Table: B2 Wholesale interest rates")

    try:
        rows = fetch_ocr_data(date_from, today)
    except requests.HTTPError as exc:
        print(f"ERROR: RBNZ HTTP error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR: RBNZ network error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: OCR fetch failed: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print("ERROR: No OCR rows returned from RBNZ", file=sys.stderr)
        return 1

    write_csv(rows, OUTPUT_PATH)
    print(f"OK: Wrote {len(rows)} rows to {OUTPUT_PATH}")
    print(f"     Latest: {rows[-1][0]} = {rows[-1][1]:.4f}%")
    print(f"     Earliest: {rows[0][0]} = {rows[0][1]:.4f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
