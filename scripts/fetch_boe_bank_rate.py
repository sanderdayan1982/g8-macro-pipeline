#!/usr/bin/env python3
"""
fetch_boe_bank_rate.py
=======================
Scrapes the Bank of England Bank Rate (policy rate) from the BoE Interactive
Statistical Database (IADB).

Output: data/BOE_BANK_RATE.csv
Format: DATE,OPEN,HIGH,LOW,CLOSE,VOLUME (YYYYMMDD dates)

v2 fixes:
- Strips optional metadata preamble before CSV header
- Detects HTML error responses
- More descriptive error messages for debugging
"""

import sys
import io
import time
import datetime as dt
from pathlib import Path

import requests
import pandas as pd

SERIES_CODE = "IUDBEDR"
OUTPUT_FILE = "data/BOE_BANK_RATE.csv"
START_DATE  = dt.date(2021, 1, 1)
END_DATE    = dt.date.today()

MAX_RETRIES = 3
TIMEOUT_SEC = 30
BACKOFF_SEC = 5

BOE_BASE_URL = "https://www.bankofengland.co.uk/boeapps/iadb/fromshowcolumns.asp"


def build_url(series_code, from_date, to_date):
    months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    params = {
        'Travel': 'NIxAZxSUx', 'FromSeries': '1', 'ToSeries': '50', 'DAT': 'RNG',
        'FD': str(from_date.day), 'FM': months[from_date.month - 1], 'FY': str(from_date.year),
        'TD': str(to_date.day), 'TM': months[to_date.month - 1], 'TY': str(to_date.year),
        'VFD': 'Y', 'CSVF': 'TN', 'C': '5DA', 'Filter': 'N',
        'SeriesCodes': series_code, 'UsingCodes': 'Y', 'VPD': 'Y',
    }
    query = '&'.join(f'{k}={v}' for k, v in params.items())
    return f'{BOE_BASE_URL}?{query}'


def fetch_with_retry(url):
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                              'AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/121.0.0.0 Safari/537.36',
                'Accept': 'text/csv, text/plain, */*',
            }
            resp = requests.get(url, headers=headers, timeout=TIMEOUT_SEC)
            resp.raise_for_status()
            if not resp.text or len(resp.text) < 50:
                raise ValueError(f"Response too short ({len(resp.text)} chars)")
            return resp.text
        except requests.exceptions.Timeout:
            last_err = f"timeout after {TIMEOUT_SEC}s"
            print(f"[Attempt {attempt}/{MAX_RETRIES}] BoE network error: {last_err}")
        except requests.exceptions.HTTPError:
            last_err = f"HTTP error"
            print(f"[Attempt {attempt}/{MAX_RETRIES}] BoE HTTP error: {last_err}")
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            print(f"[Attempt {attempt}/{MAX_RETRIES}] BoE error: {last_err}")

        if attempt < MAX_RETRIES:
            sleep_for = BACKOFF_SEC * attempt
            print(f"[Retry] Waiting {sleep_for}s before next attempt...")
            time.sleep(sleep_for)

    raise RuntimeError(f"BoE fetch failed after {MAX_RETRIES} attempts: {last_err}")


def parse_boe_csv(csv_text, series_code):
    """
    Parse BoE IADB CSV response, handling optional metadata preamble.

    BoE may prepend descriptive text lines before the actual CSV header.
    We detect the real CSV start by looking for a line containing 'DATE'
    AND the series code.
    """
    # Detect HTML error response (BoE returns HTML when something is wrong)
    if csv_text.strip().lower().startswith(('<!doctype', '<html', '<?xml')):
        raise ValueError(
            "BoE returned HTML instead of CSV — likely an error page. "
            f"First 200 chars: {csv_text[:200]}"
        )

    # Normalize line endings
    lines = csv_text.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    # Print first few lines for debugging (visible in GitHub Actions logs)
    print(f"[parse] Response has {len(lines)} lines total")
    print(f"[parse] First 3 lines:")
    for i, line in enumerate(lines[:3]):
        print(f"  Line {i+1}: {line[:100]}")

    # Find the header line — must contain both 'DATE' and the series code
    header_idx = -1
    for i, line in enumerate(lines):
        line_upper = line.upper()
        if 'DATE' in line_upper and series_code.upper() in line_upper:
            header_idx = i
            break

    if header_idx == -1:
        raise ValueError(
            f"Could not find CSV header with 'DATE' and '{series_code}'. "
            f"First 5 lines: {[l[:80] for l in lines[:5]]}"
        )

    print(f"[parse] CSV header found at line {header_idx + 1}")

    # Reconstruct clean CSV starting from the header
    clean_csv = '\n'.join(lines[header_idx:])

    try:
        df = pd.read_csv(io.StringIO(clean_csv), skipinitialspace=True)
    except Exception as e:
        raise ValueError(f"pandas could not parse CSV after preamble strip: {e}")

    if df.empty:
        raise ValueError("BoE returned empty CSV body")

    # Identify columns (case-insensitive)
    date_col = None
    rate_col = None
    for col in df.columns:
        col_clean = str(col).strip().upper()
        if 'DATE' in col_clean and date_col is None:
            date_col = col
        elif series_code.upper() in col_clean and rate_col is None:
            rate_col = col

    if date_col is None or rate_col is None:
        raise ValueError(
            f"Could not detect DATE/{series_code} columns. "
            f"Columns found: {list(df.columns)}"
        )

    print(f"[parse] Detected columns: date='{date_col}', rate='{rate_col}'")

    df['_date'] = pd.to_datetime(df[date_col], format='%d %b %Y', errors='coerce')
    df['_rate'] = pd.to_numeric(df[rate_col], errors='coerce')
    df = df.dropna(subset=['_date', '_rate']).sort_values('_date')

    if df.empty:
        raise ValueError("No valid rows after parsing dates/rates")

    return df[['_date', '_rate']].rename(columns={'_date': 'date', '_rate': 'rate'})


def expand_to_daily(df, end_date):
    if df.empty:
        return df
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    start = df.index.min().normalize()
    end = pd.Timestamp(end_date)
    business_days = pd.bdate_range(start=start, end=end)
    df_daily = df.reindex(business_days, method='ffill')
    df_daily.index.name = 'date'
    return df_daily.reset_index()


def to_ohlcv_format(df):
    return pd.DataFrame({
        'DATE':   df['date'].dt.strftime('%Y%m%d'),
        'OPEN':   df['rate'].round(4),
        'HIGH':   df['rate'].round(4),
        'LOW':    df['rate'].round(4),
        'CLOSE':  df['rate'].round(4),
        'VOLUME': 0,
    })


def main():
    print(f"Fetching BoE Bank Rate from {START_DATE} to {END_DATE}")
    print(f"BoE Series: {SERIES_CODE} (Official Bank Rate)")

    url = build_url(SERIES_CODE, START_DATE, END_DATE)
    try:
        csv_text = fetch_with_retry(url)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return 1

    try:
        df_raw = parse_boe_csv(csv_text, SERIES_CODE)
        print(f"Parsed {len(df_raw)} rate-change observations from BoE")
    except ValueError as e:
        print(f"ERROR parsing CSV: {e}")
        return 1

    df_daily = expand_to_daily(df_raw, END_DATE)
    print(f"Expanded to {len(df_daily)} daily observations")

    df_out = to_ohlcv_format(df_daily)

    output_path = Path(OUTPUT_FILE)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(output_path, index=False, lineterminator='\r\n')

    last_date = df_daily['date'].max().strftime('%Y-%m-%d')
    last_rate = df_daily['rate'].iloc[-1]
    print(f"✓ Saved {len(df_out)} rows to {OUTPUT_FILE}")
    print(f"  Latest: {last_date} → {last_rate:.4f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
