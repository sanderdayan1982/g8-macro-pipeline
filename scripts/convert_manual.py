"""
convert_manual.py v2
====================
Converts the manually-maintained input file into the OHLCV per-tenor CSVs that
the XCCY engine consumes — CHF ONLY.

v2 changes (2026-06-09):
    - NZD REMOVED from scope. NZD bills + bonds now come from RBNZ B2 XLSX
      (see scripts/fetch_nzd_b2.py — currently fed manually until RBNZ replies
      to programmatic-access request). NZD CSVs in data/ should NEVER be
      overwritten by this script anymore.
    - manual_input.csv header reduced to: DATE,CHF_3M,CHF_6M,CHF_1Y
    - Old NZD columns (NZD_3M, NZD_6M, NZD_1Y) are ignored if still present
      in manual_input.csv (backward compatibility during migration).

Why manual (CHF only now):
    SARON Compound (the CHF RFR) is only available daily at full precision
    from SIX under a commercial licence. The free SNB cubes are either stale
    (zirepo, ~bi-weekly) or rounded to 2 decimals (snbgwdzid, illustrative-
    only). Swiss government bills are thinly traded. Until a free daily
    full-precision automated source for CHF emerges, the operator copies
    3M / 6M / 1Y sovereign yields from TradingView's "Curvas de rendimiento"
    table into manual_input.csv.

Source of values:
    TradingView -> "Curvas de rendimiento" (World government bond yields).
    The operator copies the 3M / 6M / 1Y CHF sovereign yields into
    manual_input.csv, one row per observation date.

Input file (data/manual_input.csv):
    DATE,CHF_3M,CHF_6M,CHF_1Y
    20260531,-0.020,-0.060,0.240
    20260601,-0.018,-0.055,0.245
    ...
    - DATE: YYYYMMDD integer (same convention as all other files).
    - Values in PERCENT (decimals allowed, negatives allowed for CHF).
    - Add a new row each time you update; history accumulates here.
    - Blank cells are allowed (a tenor missing on a given day is skipped for
      that day only).

Output (overwrites each run, rebuilt from full input history):
    data/CHF_BILL_3M.csv
    data/CHF_BILL_6M.csv
    data/CHF_BILL_1Y.csv

NZD CSVs (NZD_BILL_30D/60D/90D.csv, NZD_BOND_1Y/2Y/5Y/10Y.csv) are now
managed by fetch_nzd_b2.py and are NEVER touched by this script.

Format of outputs: DATE,OPEN,HIGH,LOW,CLOSE,VOLUME with O=H=L=C (it's a rate,
not a price), VOLUME=0 — identical schema to every other file in data/.

Run:
    cd ~/Desktop/xccy-g8 && source venv/bin/activate
    python3 scripts/convert_manual.py

Note: This is NOT part of the daily GitHub Actions workflow. It is run locally
      by the operator whenever manual_input.csv is updated, then committed.
"""

import csv
import sys
from pathlib import Path
from typing import Dict, List, Tuple


# The input file lives in data/ alongside the outputs
INPUT_FILENAME = "manual_input.csv"

# v2: CHF-only mapping. NZD removed.
COLUMN_TO_FILE = {
    "CHF_3M": "CHF_BILL_3M.csv",
    "CHF_6M": "CHF_BILL_6M.csv",
    "CHF_1Y": "CHF_BILL_1Y.csv",
}

# v2: new expected header (CHF only)
EXPECTED_HEADER_V2 = ["DATE", "CHF_3M", "CHF_6M", "CHF_1Y"]

# v2: tolerated legacy header (with NZD columns — they will be ignored)
LEGACY_HEADER_V1 = ["DATE", "CHF_3M", "CHF_6M", "CHF_1Y", "NZD_3M", "NZD_6M", "NZD_1Y"]

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _validate_date(date_str: str) -> bool:
    """Check DATE is an 8-digit YYYYMMDD integer."""
    date_str = date_str.strip()
    if len(date_str) != 8 or not date_str.isdigit():
        return False
    year = int(date_str[:4])
    month = int(date_str[4:6])
    day = int(date_str[6:8])
    return 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31


def read_manual_input(input_path: Path) -> Dict[str, List[Tuple[str, float]]]:
    """
    Read manual_input.csv and return {column_name: [(date_str, value), ...]}.

    Sanity checks:
        - header matches EXPECTED_HEADER_V2 or LEGACY_HEADER_V1 (NZD ignored)
        - dates are valid YYYYMMDD
        - values parse as float (blank cells skipped for that tenor/day)
        - duplicate dates: last one wins (with a warning)
    """
    if not input_path.exists():
        raise FileNotFoundError(
            f"Manual input not found: {input_path}\n"
            f"Create it with header: {','.join(EXPECTED_HEADER_V2)}"
        )

    with input_path.open("r", newline="") as f:
        reader = csv.reader(f)
        rows = [r for r in reader if r and any(cell.strip() for cell in r)]

    if not rows:
        raise ValueError("manual_input.csv is empty")

    header = [h.strip() for h in rows[0]]

    # v2: accept both v1 (legacy with NZD columns) and v2 (CHF only) headers
    if header == EXPECTED_HEADER_V2:
        print(f"  Header: v2 (CHF only)")
    elif header == LEGACY_HEADER_V1:
        print(f"  Header: v1 legacy (NZD columns will be IGNORED)")
        print(f"  NOTE: NZD is now sourced from RBNZ B2 via fetch_nzd_b2.py.")
        print(f"        Consider trimming manual_input.csv to v2 header: "
              f"{','.join(EXPECTED_HEADER_V2)}")
    else:
        raise ValueError(
            f"Header mismatch.\n"
            f"  Expected v2: {EXPECTED_HEADER_V2}\n"
            f"  Or legacy v1: {LEGACY_HEADER_V1}\n"
            f"  Got:         {header}"
        )

    # Build col_idx only for columns we actively process (CHF tenors).
    # Any NZD_* columns in a legacy file are silently ignored.
    col_idx = {name: header.index(name) for name in COLUMN_TO_FILE}

    # Accumulate per column, dict keyed by date for dedup (last wins)
    acc: Dict[str, Dict[str, float]] = {name: {} for name in COLUMN_TO_FILE}

    for line_no, row in enumerate(rows[1:], start=2):
        if len(row) < len(EXPECTED_HEADER_V2):
            print(f"  WARNING line {line_no}: too few columns, skipping: {row}",
                  file=sys.stderr)
            continue

        date_str = row[0].strip()
        if not _validate_date(date_str):
            print(f"  WARNING line {line_no}: invalid DATE '{date_str}', skipping row",
                  file=sys.stderr)
            continue

        for name, idx in col_idx.items():
            if idx >= len(row):
                continue
            raw = row[idx].strip()
            if raw == "":
                continue  # tenor missing this day — fine, skip just this cell
            try:
                value = float(raw)
            except ValueError:
                print(f"  WARNING line {line_no}: '{name}'='{raw}' not a number, skipping cell",
                      file=sys.stderr)
                continue
            if date_str in acc[name]:
                print(f"  NOTE line {line_no}: duplicate date {date_str} for {name}, "
                      f"overwriting previous value", file=sys.stderr)
            acc[name][date_str] = value

    # Convert to sorted lists
    result: Dict[str, List[Tuple[str, float]]] = {}
    for name in COLUMN_TO_FILE:
        result[name] = sorted(acc[name].items(), key=lambda kv: kv[0])

    return result


def write_csv(rows: List[Tuple[str, float]], output_path: Path) -> None:
    """Write rows to OHLCV format CSV (O=H=L=C for rates, V=0)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
        for date_str, value in rows:
            v = f"{value:.4f}"
            writer.writerow([date_str, v, v, v, v, "0"])


def main() -> int:
    input_path = DATA_DIR / INPUT_FILENAME

    print(f"Reading manual input: {input_path}")
    try:
        data = read_manual_input(input_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print()
    successes = 0
    failures = 0

    for column, filename in COLUMN_TO_FILE.items():
        rows = data.get(column, [])
        output_path = DATA_DIR / filename

        if not rows:
            print(f"[{column}] WARNING: no data rows, skipping {filename}",
                  file=sys.stderr)
            failures += 1
            continue

        write_csv(rows, output_path)
        print(f"[{column}] OK: Wrote {len(rows)} row(s) to {filename}")
        print(f"          Latest:   {rows[-1][0]} = {rows[-1][1]:.4f}%")
        if len(rows) > 1:
            print(f"          Earliest: {rows[0][0]} = {rows[0][1]:.4f}%")
        successes += 1

    print()
    print(f"Summary: {successes} OK, {failures} skipped (of {len(COLUMN_TO_FILE)} total)")
    print(f"Note: NZD CSVs (BILL_30D/60D/90D + BOND_1Y/2Y/5Y/10Y) are managed")
    print(f"      separately by fetch_nzd_b2.py and are NOT touched by this script.")
    return 0 if successes > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
