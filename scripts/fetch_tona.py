"""
fetch_tona.py
=============
Scraper for Tokyo Overnight Average Rate (TONA) from Bank of Japan.

Strategy: Double-layered resilience
    PRIMARY:  BoJ Time-Series Data Search API (modern REST, launched Feb 18, 2026)
              https://www.stat-search.boj.or.jp/api/v1/getDataCode
              Database: FM01 (Uncollateralized Overnight Call Rate, average)
              Series:   STRDCLUCON
              Format:   JSON
              Verified against official BoJ API Manual (api_manual_en.pdf, p.26)

    FALLBACK: BoJ legacy CGI endpoint
              https://www.stat-search.boj.or.jp/ssi/cgi-bin/famecgi2
              Series:   IR01'MUTCALAL
              Format:   CSV (with defensive parsing)

Output:
    data/TONA.csv  — TONA historical series in OHLCV format

License: Data sourced from Bank of Japan public statistics.
         BoJ retains all rights to source data.

Notes:
    The modern API is the preferred source because it was designed by BoJ
    for programmatic access (won't block datacenter IPs like GitHub Actions).
    The CGI legacy fallback provides resilience in case the modern API has
    any issue (e.g. rate limit, transient outage, series code changes).
"""

import csv
import sys
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from urllib.parse import urlencode

import requests


# Primary endpoint constants (modern API, verified against official manual)
BOJ_API_URL = "https://www.stat-search.boj.or.jp/api/v1/getDataCode"
API_DB = "FM01"
API_SERIES = "STRDCLUCON"

# Fallback endpoint constants (legacy CGI)
BOJ_CGI_URL = "https://www.stat-search.boj.or.jp/ssi/cgi-bin/famecgi2"
CGI_SERIES = "IR01'MUTCALAL"

# Common configuration
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "TONA.csv"
HISTORY_YEARS = 5
TIMEOUT_SECONDS = 30
USER_AGENT = "xccy-g8/1.0 (https://github.com/sanderdayan1982/xccy-g8)"


# =============================================================================
# PRIMARY: Modern REST API (BoJ official, launched February 2026)
# =============================================================================

def fetch_tona_via_modern_api(date_from: datetime) -> list[tuple[str, float]]:
    """
    Fetch TONA daily data from the modern BoJ REST API.

    Reference: https://www.stat-search.boj.or.jp/info/api_manual_en.pdf
    Series:    FM01/STRDCLUCON (Uncollateralized Overnight Call Rate, average)

    Returns list of (date_str_YYYYMMDD, rate_value) tuples sorted ascending.
    """
    params = {
        "format": "json",
        "lang": "en",
        "db": API_DB,
        "code": API_SERIES,
        "startDate": date_from.strftime("%Y%m"),
    }
    url = f"{BOJ_API_URL}?{urlencode(params)}"

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }

    response = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    data = response.json()

    status = data.get("STATUS")
    if status != "200" and status != 200:
        message = data.get("MESSAGE", "unknown error")
        raise ValueError(f"BoJ API returned non-200 STATUS: {status} ({message})")

    output_section = None
    for key in ("DATAS", "DATAS_INFO", "API_OUTPUT", "series", "data"):
        if key in data and isinstance(data[key], list):
            output_section = data[key]
            break

    if output_section is None:
        for value in data.values():
            if isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, dict) and "VALUES" in first:
                    output_section = value
                    break

    if not output_section:
        raise ValueError("BoJ API response: no series output section found")

    target_series = None
    for series in output_section:
        if not isinstance(series, dict):
            continue
        code = series.get("SERIES_CODE", "")
        if code == API_SERIES or API_SERIES in code:
            target_series = series
            break

    if target_series is None:
        target_series = output_section[0] if isinstance(output_section[0], dict) else None

    if target_series is None:
        raise ValueError(f"BoJ API response: series {API_SERIES} not found")

    dates = target_series.get("SURVEY_DATES", [])
    values = target_series.get("VALUES", [])

    if not dates or not values or len(dates) != len(values):
        raise ValueError(
            f"BoJ API response: malformed dates/values arrays "
            f"(dates={len(dates) if dates else 0}, values={len(values) if values else 0})"
        )

    rows: list[tuple[str, float]] = []
    for date_raw, value_raw in zip(dates, values):
        if value_raw is None or value_raw == "":
            continue

        try:
            date_str = str(date_raw).strip()
            if len(date_str) == 8:
                date_obj = datetime.strptime(date_str, "%Y%m%d")
            elif len(date_str) == 10:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            else:
                continue
            value = float(value_raw)
        except (ValueError, TypeError):
            continue

        rows.append((date_obj.strftime("%Y%m%d"), value))

    rows.sort(key=lambda r: r[0])
    return rows


# =============================================================================
# FALLBACK: Legacy CGI endpoint (defensive parsing)
# =============================================================================

def _try_parse_boj_date(s: str) -> datetime | None:
    """BoJ uses multiple date formats. Try common ones in order."""
    s = s.strip().strip('"')
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _try_parse_value(s: str) -> float | None:
    """Defensive value parser: handles commas, dashes, NA markers."""
    s = s.strip().strip('"').replace(",", "")
    if not s or s in ("-", "N/A", "NA", "..."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _find_date_value_columns(all_rows: list[list[str]]) -> tuple[int, int, int]:
    """Auto-detect data start row and (date_col, value_col) indices."""
    for i, row in enumerate(all_rows[:15]):
        if not row:
            continue
        for date_col in range(min(3, len(row))):
            parsed_date = _try_parse_boj_date(row[date_col])
            if parsed_date is None:
                continue
            for value_col in range(date_col + 1, len(row)):
                parsed_value = _try_parse_value(row[value_col])
                if parsed_value is not None:
                    return i, date_col, value_col

    raise ValueError(
        "BoJ CGI response: no row with parseable (date, value) pair "
        "found in first 15 rows"
    )


def fetch_tona_via_legacy_cgi(date_from: datetime, date_to: datetime) -> list[tuple[str, float]]:
    """
    Fallback fetch: legacy BoJ CGI endpoint.

    Used only if the modern REST API fails for any reason.
    """
    params = {
        "cgi": "$nme_a000_en",
        "rep_date": "1",
        "hdnSeriesCodeList": CGI_SERIES,
        "hdnRSMode": "EXP",
        "hdnYyyyFrom": str(date_from.year),
        "hdnMmFrom": f"{date_from.month:02d}",
        "hdnDdFrom": f"{date_from.day:02d}",
        "hdnYyyyTo": str(date_to.year),
        "hdnMmTo": f"{date_to.month:02d}",
        "hdnDdTo": f"{date_to.day:02d}",
        "hdnCsvDownload": "1",
        "hdnExpType": "csv",
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv, application/octet-stream",
    }

    response = requests.get(BOJ_CGI_URL, params=params, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    if response.encoding is None or response.encoding.lower() == "iso-8859-1":
        response.encoding = "shift_jis"

    text = response.text.lstrip("\ufeff")
    if not text:
        raise ValueError("BoJ CGI response empty")

    all_rows = list(csv.reader(StringIO(text)))
    if not all_rows:
        raise ValueError("BoJ CGI response has no parseable rows")

    data_start_idx, date_col, value_col = _find_date_value_columns(all_rows)

    rows: list[tuple[str, float]] = []
    for raw_row in all_rows[data_start_idx:]:
        if len(raw_row) <= max(date_col, value_col):
            continue

        date_obj = _try_parse_boj_date(raw_row[date_col])
        if date_obj is None:
            continue

        if date_obj < date_from or date_obj > date_to:
            continue

        value = _try_parse_value(raw_row[value_col])
        if value is None:
            continue

        rows.append((date_obj.strftime("%Y%m%d"), value))

    rows.sort(key=lambda r: r[0])
    return rows


# =============================================================================
# OUTPUT
# =============================================================================

def write_csv(rows: list[tuple[str, float]], output_path: Path) -> None:
    """Write rows to OHLCV format CSV (O=H=L=C for daily rates, V=0)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
        for date_str, value in rows:
            v = f"{value:.4f}"
            writer.writerow([date_str, v, v, v, v, "0"])


# =============================================================================
# MAIN: Orchestrate primary → fallback strategy
# =============================================================================

def main() -> int:
    today = datetime.utcnow()
    date_from = today - timedelta(days=365 * HISTORY_YEARS)

    print(f"Fetching TONA from {date_from.date()} to {today.date()}")

    rows: list[tuple[str, float]] = []
    source_used = ""

    # --- Primary: modern REST API ---
    print(f"[Primary] BoJ REST API v1, db={API_DB}, code={API_SERIES}")
    try:
        rows = fetch_tona_via_modern_api(date_from)
        source_used = "modern_api"
    except requests.HTTPError as exc:
        print(f"[Primary] HTTP error: {exc} — falling back to CGI", file=sys.stderr)
    except requests.RequestException as exc:
        print(f"[Primary] Network error: {exc} — falling back to CGI", file=sys.stderr)
    except Exception as exc:
        print(f"[Primary] Parse error: {exc} — falling back to CGI", file=sys.stderr)

    # --- Fallback: legacy CGI ---
    if not rows:
        print(f"[Fallback] BoJ legacy CGI, series={CGI_SERIES}")
        try:
            rows = fetch_tona_via_legacy_cgi(date_from, today)
            source_used = "legacy_cgi"
        except requests.HTTPError as exc:
            print(f"[Fallback] HTTP error: {exc}", file=sys.stderr)
        except requests.RequestException as exc:
            print(f"[Fallback] Network error: {exc}", file=sys.stderr)
        except Exception as exc:
            print(f"[Fallback] Parse error: {exc}", file=sys.stderr)

    if not rows:
        print("ERROR: TONA fetch failed via both primary and fallback sources", file=sys.stderr)
        return 1

    write_csv(rows, OUTPUT_PATH)
    print(f"OK: Wrote {len(rows)} rows to {OUTPUT_PATH}")
    print(f"     Source used: {source_used}")
    print(f"     Latest: {rows[-1][0]} = {rows[-1][1]:.4f}%")
    print(f"     Earliest: {rows[0][0]} = {rows[0][1]:.4f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
