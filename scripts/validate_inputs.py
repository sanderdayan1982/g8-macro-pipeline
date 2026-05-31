"""
validate_inputs.py
==================
Gate validator for XCCY G8 v3.0 dashboard pipeline.

Reads all CSV files in data/ and validates against SERIES_METADATA registry.
Emits data/validation_report.json that compute_basis.py consumes.

Validation tiers:
  - schema (mandatory, sets schema_ok=False on failure)
  - sanity (warnings, do NOT set usable_for_basis=False)
  - usability (sets usable_for_basis based on completeness and freshness)

Freshness model (per-series, 2026-05-31):
  Each series declares an `update_method` and `cadence` that govern how strict
  the freshness check is. Sources publish at different rates — treating a
  monthly-batch source with a daily ruler produced false failures. Categories:
    - "daily"  (warn 3 biz-days / fail 7):   automated daily feeds
    - "manual" (no staleness fail; report-only): operator-updated CSVs (CHF, NZD)
  The validator now models the REALITY of each source rather than an idealized
  daily expectation.

Currency coverage (8 currencies):
  USD, EUR, GBP, JPY, CAD, AUD automated; CHF and NZD manual (sovereign yields
  copied from TradingView world government-bond-yield table). SARON family was
  removed 2026-05-31 — CHF migrated from RFR-compound to sovereign gov_bill for
  methodological coherence (all 8 currencies now use sovereign curve).

Output JSON structure: see end of file for schema reference.

License: Sander Bignotte / xccy-g8 project.
"""

import csv
import json
import math
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional


# ============================================================================
# FRESHNESS POLICY (per cadence category)
# ============================================================================
# warn_bizdays / fail_bizdays are business-day thresholds on how old the most
# recent observation may be. "manual" uses fail=None => never fails on
# staleness (operator controls cadence); it only reports days_since_update.

FRESHNESS_POLICY = {
    "daily":  {"warn_bizdays": 3,  "fail_bizdays": 7},
    "manual": {"warn_bizdays": None, "fail_bizdays": None},  # report-only
}


# ============================================================================
# SERIES_METADATA REGISTRY (40 series, frozen 2026-05-31)
# ============================================================================
# Each entry defines the validation contract for one CSV file in data/.
# Modifying ranges requires team review — they govern range_sanity warnings.
# Fields:
#   currency, tenor, instrument_type, credit_nature, exp_min, exp_max
#   update_method: "auto" | "manual"
#   cadence:       freshness policy key ("daily" | "manual")

SERIES_METADATA = {
    # USD — 5 series (automated daily, FRED)
    "SOFR":        {"currency": "USD", "tenor": "ON", "instrument_type": "rfr_overnight", "credit_nature": "near_rfr",   "exp_min": -0.5, "exp_max": 10.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},
    "US_BILL_3M":  {"currency": "USD", "tenor": "3M", "instrument_type": "gov_bill",      "credit_nature": "sovereign",  "exp_min": -0.5, "exp_max": 10.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},
    "US_BILL_6M":  {"currency": "USD", "tenor": "6M", "instrument_type": "gov_bill",      "credit_nature": "sovereign",  "exp_min": -0.5, "exp_max": 10.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},
    "US_BILL_1Y":  {"currency": "USD", "tenor": "1Y", "instrument_type": "gov_bill",      "credit_nature": "sovereign",  "exp_min": -0.5, "exp_max": 10.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},
    "US_BILL_2Y":  {"currency": "USD", "tenor": "2Y", "instrument_type": "gov_note",      "credit_nature": "sovereign",  "exp_min": -0.5, "exp_max": 10.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},

    # EUR — 7 series (synthetic AAA curve from ECB, automated daily)
    "ESTR":         {"currency": "EUR", "tenor": "ON",  "instrument_type": "rfr_overnight",   "credit_nature": "near_rfr",                "exp_min": -1.5, "exp_max": 8.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},
    "EUR_BILL_3M":  {"currency": "EUR", "tenor": "3M",  "instrument_type": "gov_curve_aaa",   "credit_nature": "synthetic_aaa_sovereign", "exp_min": -1.5, "exp_max": 8.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},
    "EUR_BILL_6M":  {"currency": "EUR", "tenor": "6M",  "instrument_type": "gov_curve_aaa",   "credit_nature": "synthetic_aaa_sovereign", "exp_min": -1.5, "exp_max": 8.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},
    "EUR_BILL_1Y":  {"currency": "EUR", "tenor": "1Y",  "instrument_type": "gov_curve_aaa",   "credit_nature": "synthetic_aaa_sovereign", "exp_min": -1.5, "exp_max": 8.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},
    "EUR_BILL_2Y":  {"currency": "EUR", "tenor": "2Y",  "instrument_type": "gov_curve_aaa",   "credit_nature": "synthetic_aaa_sovereign", "exp_min": -1.5, "exp_max": 8.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},
    "EUR_BILL_5Y":  {"currency": "EUR", "tenor": "5Y",  "instrument_type": "gov_curve_aaa",   "credit_nature": "synthetic_aaa_sovereign", "exp_min": -1.0, "exp_max": 8.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},
    "EUR_BILL_10Y": {"currency": "EUR", "tenor": "10Y", "instrument_type": "gov_curve_aaa",   "credit_nature": "synthetic_aaa_sovereign", "exp_min": -0.5, "exp_max": 8.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},

    # GBP — 6 series (no 3M; automated daily, BoE)
    "SONIA":        {"currency": "GBP", "tenor": "ON",  "instrument_type": "rfr_overnight", "credit_nature": "near_rfr",  "exp_min": -0.5, "exp_max": 15.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},
    "GBP_BILL_6M":  {"currency": "GBP", "tenor": "6M",  "instrument_type": "gov_gilt",      "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 15.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},
    "GBP_BILL_1Y":  {"currency": "GBP", "tenor": "1Y",  "instrument_type": "gov_gilt",      "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 15.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},
    "GBP_BILL_2Y":  {"currency": "GBP", "tenor": "2Y",  "instrument_type": "gov_gilt",      "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 15.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},
    "GBP_BILL_5Y":  {"currency": "GBP", "tenor": "5Y",  "instrument_type": "gov_gilt",      "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 15.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},
    "GBP_BILL_10Y": {"currency": "GBP", "tenor": "10Y", "instrument_type": "gov_gilt",      "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 15.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},

    # CHF — 3 series (sovereign gov yields, MANUAL from TradingView; SARON family removed 2026-05-31)
    "CHF_BILL_3M": {"currency": "CHF", "tenor": "3M", "instrument_type": "gov_bill", "credit_nature": "sovereign", "exp_min": -2.0, "exp_max": 5.0, "update_method": "manual", "cadence": "manual", "continuity_class": "manual"},
    "CHF_BILL_6M": {"currency": "CHF", "tenor": "6M", "instrument_type": "gov_bill", "credit_nature": "sovereign", "exp_min": -2.0, "exp_max": 5.0, "update_method": "manual", "cadence": "manual", "continuity_class": "manual"},
    "CHF_BILL_1Y": {"currency": "CHF", "tenor": "1Y", "instrument_type": "gov_bill", "credit_nature": "sovereign", "exp_min": -2.0, "exp_max": 5.0, "update_method": "manual", "cadence": "manual", "continuity_class": "manual"},

    # JPY — 7 series (no 3M, no 6M; automated daily, MOF)
    "TONA":         {"currency": "JPY", "tenor": "ON",  "instrument_type": "rfr_overnight", "credit_nature": "near_rfr",  "exp_min": -0.5, "exp_max": 5.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},
    "JPY_BILL_1Y":  {"currency": "JPY", "tenor": "1Y",  "instrument_type": "gov_jgb",       "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 5.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},
    "JPY_BILL_2Y":  {"currency": "JPY", "tenor": "2Y",  "instrument_type": "gov_jgb",       "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 5.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},
    "JPY_BILL_3Y":  {"currency": "JPY", "tenor": "3Y",  "instrument_type": "gov_jgb",       "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 5.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},
    "JPY_BILL_5Y":  {"currency": "JPY", "tenor": "5Y",  "instrument_type": "gov_jgb",       "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 6.0, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},
    "JPY_BILL_10Y": {"currency": "JPY", "tenor": "10Y", "instrument_type": "gov_jgb",       "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 6.5, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},
    "JPY_BILL_20Y": {"currency": "JPY", "tenor": "20Y", "instrument_type": "gov_jgb",       "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 7.5, "update_method": "auto", "cadence": "daily", "continuity_class": "fitted_curve"},

    # CAD — 4 series (automated daily)
    "CORRA":       {"currency": "CAD", "tenor": "ON", "instrument_type": "rfr_overnight", "credit_nature": "near_rfr",  "exp_min": -0.5, "exp_max": 10.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},
    "CAD_BILL_3M": {"currency": "CAD", "tenor": "3M", "instrument_type": "gov_bill",      "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 10.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},
    "CAD_BILL_6M": {"currency": "CAD", "tenor": "6M", "instrument_type": "gov_bill",      "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 10.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},
    "CAD_BILL_1Y": {"currency": "CAD", "tenor": "1Y", "instrument_type": "gov_bill",      "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 10.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},

    # AUD — 4 series (no 1Y; automated daily)
    "AONIA":       {"currency": "AUD", "tenor": "ON", "instrument_type": "rfr_overnight", "credit_nature": "near_rfr",  "exp_min": -0.5, "exp_max": 12.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},
    "AUD_BILL_1M": {"currency": "AUD", "tenor": "1M", "instrument_type": "gov_bill",      "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 12.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},
    "AUD_BILL_3M": {"currency": "AUD", "tenor": "3M", "instrument_type": "gov_bill",      "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 12.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},
    "AUD_BILL_6M": {"currency": "AUD", "tenor": "6M", "instrument_type": "gov_bill",      "credit_nature": "sovereign", "exp_min": -0.5, "exp_max": 12.0, "update_method": "auto", "cadence": "daily", "continuity_class": "observed"},

    # NZD — 4 series (OCR automated + 3 sovereign gov yields MANUAL from TradingView)
    "NZD_OCR":     {"currency": "NZD", "tenor": "ON", "instrument_type": "policy_rate", "credit_nature": "policy_proxy", "exp_min": -0.5, "exp_max": 10.0, "update_method": "auto",   "cadence": "daily", "continuity_class": "observed"},
    "NZD_BILL_3M": {"currency": "NZD", "tenor": "3M", "instrument_type": "gov_bill",    "credit_nature": "sovereign",    "exp_min": -0.5, "exp_max": 12.0, "update_method": "manual", "cadence": "manual", "continuity_class": "manual"},
    "NZD_BILL_6M": {"currency": "NZD", "tenor": "6M", "instrument_type": "gov_bill",    "credit_nature": "sovereign",    "exp_min": -0.5, "exp_max": 12.0, "update_method": "manual", "cadence": "manual", "continuity_class": "manual"},
    "NZD_BILL_1Y": {"currency": "NZD", "tenor": "1Y", "instrument_type": "gov_bill",    "credit_nature": "sovereign",    "exp_min": -0.5, "exp_max": 12.0, "update_method": "manual", "cadence": "manual", "continuity_class": "manual"},
}

# Validation thresholds (global — except freshness, which is per-series cadence)
SPIKE_WARN_SIGMA = 3.0
SPIKE_SEVERE_SIGMA = 5.0
SPIKE_TRAILING_WINDOW = 60       # days for rolling std calc (on daily changes)
SPIKE_MIN_STDEV = 0.02           # pp; volatility floor — skip spike check when
                                 # recent daily moves are smaller than this
                                 # (avoids spurious z-scores on pinned rates)
FLATLINE_WARN_DAYS = 10          # consecutive identical values
GAP_SILENT_DAYS = 3              # likely holiday, no flag
GAP_WARN_DAYS = 5                # warning threshold
GAP_FAIL_DAYS = 6                # fails usable_for_basis (auto series only)
COMPLETENESS_WINDOW_DAYS = 60    # rolling window for % weekday coverage
COMPLETENESS_MIN_PCT = 90.0      # below this triggers warning


# ============================================================================
# I/O HELPERS
# ============================================================================

def get_data_dir() -> Path:
    """Resolve data/ directory relative to this script."""
    return Path(__file__).resolve().parent.parent / "data"


def get_output_path() -> Path:
    return get_data_dir() / "validation_report.json"


def parse_date_yyyymmdd(date_str: str) -> Optional[datetime]:
    """Parse YYYYMMDD integer-format date. Returns None on invalid input."""
    try:
        s = str(date_str).strip()
        if len(s) != 8 or not s.isdigit():
            return None
        return datetime.strptime(s, "%Y%m%d")
    except (ValueError, TypeError):
        return None


def count_business_days(start: datetime, end: datetime) -> int:
    """Inclusive count of weekdays (Mon-Fri) between two dates."""
    if end < start:
        return 0
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


# ============================================================================
# SINGLE-FILE VALIDATOR
# ============================================================================

def validate_series(series_id: str, metadata: dict, csv_path: Path, today: datetime) -> dict:
    """
    Validate a single CSV file against its metadata contract.

    Returns dict with status, schema_ok, last_date, usable_for_basis,
    errors, warnings, stats.
    """
    cadence = metadata.get("cadence", "daily")
    is_manual = cadence == "manual"

    result = {
        "status": "pass",
        "schema_ok": False,
        "last_date": None,
        "usable_for_basis": False,
        "errors": [],
        "warnings": [],
        "stats": {
            "rows": 0,
            "max_gap_business_days": 0,
            "missing_count": 0,
            "duplicates_found": 0,
            "range_breaches": 0,
            "flatline_days": 0,
            "spikes_warn": 0,
            "spikes_severe": 0,
            "days_since_update": None,
        },
    }

    # File existence check
    if not csv_path.exists():
        result["status"] = "fail"
        result["errors"].append(f"file_not_found: {csv_path.name}")
        return result

    # Read CSV
    try:
        with open(csv_path, "r", newline="") as f:
            reader = csv.reader(f)
            rows = list(reader)
    except (OSError, UnicodeDecodeError) as e:
        result["status"] = "fail"
        result["errors"].append(f"read_error: {e}")
        return result

    if len(rows) < 2:
        result["status"] = "fail"
        result["errors"].append("empty_file_or_header_only")
        return result

    # Schema check: headers
    expected_headers = ["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]
    if rows[0] != expected_headers:
        result["status"] = "fail"
        result["errors"].append(f"schema_headers_mismatch: got {rows[0]}, expected {expected_headers}")
        return result

    # Parse all data rows
    parsed = []  # list of (datetime, open, high, low, close, volume)
    seen_dates = set()
    for i, row in enumerate(rows[1:], start=2):
        if len(row) != 6:
            result["errors"].append(f"row_{i}_wrong_column_count: {len(row)}")
            continue

        date = parse_date_yyyymmdd(row[0])
        if date is None:
            result["errors"].append(f"row_{i}_invalid_date: {row[0]!r}")
            continue

        if date in seen_dates:
            result["stats"]["duplicates_found"] += 1
            result["errors"].append(f"duplicate_date: {row[0]}")
            continue
        seen_dates.add(date)

        try:
            o = float(row[1]); h = float(row[2]); l = float(row[3])
            c = float(row[4]); v = float(row[5])
        except ValueError:
            result["errors"].append(f"row_{i}_non_numeric: {row}")
            continue

        if not (o == h == l == c):
            result["errors"].append(f"row_{i}_ohlc_not_equal: O={o} H={h} L={l} C={c}")
            continue

        if v != 0:
            result["warnings"].append(f"row_{i}_volume_not_zero: {v}")

        parsed.append((date, o, h, l, c, v))

    if not parsed:
        result["status"] = "fail"
        result["errors"].append("no_valid_rows_parsed")
        return result

    # Sort by date (defensive — most files are sorted, but verify)
    parsed.sort(key=lambda r: r[0])

    # Schema passed at this point
    result["schema_ok"] = True
    result["stats"]["rows"] = len(parsed)
    result["last_date"] = parsed[-1][0].strftime("%Y-%m-%d")

    # Monotonic check
    for i in range(1, len(parsed)):
        if parsed[i][0] <= parsed[i - 1][0]:
            result["errors"].append(f"dates_not_monotonic_at_row_{i + 2}")
            break

    # Gap analysis (business days between consecutive dates)
    # Severity depends on continuity_class:
    #   - "observed"     (cash bills, RFR): long gaps are suspicious -> FAIL
    #   - "fitted_curve" (BoE/ECB/MOF modeled curves): short-end gaps are
    #     legitimate (the curve is only fitted where reliable instruments
    #     exist) -> WARN
    #   - "manual"       (operator-updated): gaps expected -> WARN (informational)
    continuity_class = metadata.get("continuity_class", "observed")
    max_gap = 0
    for i in range(1, len(parsed)):
        gap = count_business_days(
            parsed[i - 1][0] + timedelta(days=1),
            parsed[i][0] - timedelta(days=1),
        )
        if gap > max_gap:
            max_gap = gap
        if gap > GAP_SILENT_DAYS:
            msg = (f"gap_{gap}_bizdays_between_"
                   f"{parsed[i-1][0].strftime('%Y-%m-%d')}_and_"
                   f"{parsed[i][0].strftime('%Y-%m-%d')}")
            if continuity_class in ("manual", "fitted_curve"):
                # Gaps are structurally expected -> informational warning only
                result["warnings"].append(msg)
            elif gap >= GAP_FAIL_DAYS:
                result["errors"].append(msg)
            elif gap >= GAP_WARN_DAYS:
                result["warnings"].append(msg)
    result["stats"]["max_gap_business_days"] = max_gap

    # Range sanity (per series)
    exp_min = metadata["exp_min"]
    exp_max = metadata["exp_max"]
    for date, _, _, _, c, _ in parsed:
        if c < exp_min or c > exp_max:
            result["stats"]["range_breaches"] += 1
            result["warnings"].append(
                f"range_breach: {date.strftime('%Y-%m-%d')} value={c} outside [{exp_min}, {exp_max}]"
            )

    # Flatline detection (consecutive identical values)
    # Manual series legitimately repeat values (operator may copy same yield
    # for stable days), so suppress the flatline warning for manual cadence.
    flatline_streak = 1
    max_flatline = 1
    for i in range(1, len(parsed)):
        if parsed[i][4] == parsed[i - 1][4]:
            flatline_streak += 1
            if flatline_streak > max_flatline:
                max_flatline = flatline_streak
        else:
            flatline_streak = 1
    result["stats"]["flatline_days"] = max_flatline
    if max_flatline > FLATLINE_WARN_DAYS and not is_manual:
        result["warnings"].append(
            f"flatline_detected: {max_flatline} consecutive identical values"
        )

    # Spike detection (rolling z-score on DAILY CHANGES, not levels)
    # Rationale: rates trend for months during hiking/cutting cycles. A z-score
    # on the LEVEL vs a trailing window flags every step of a sustained trend
    # as a "spike" (e.g. SOFR pinned near 0.05% then lifting off produced
    # z=82). A real data spike is an anomalous DAY-OVER-DAY jump, so we score
    # the first differences. A volatility floor prevents divide-by-near-zero
    # when a rate has been pinned flat (tiny stdev -> spurious huge z).
    closes = [r[4] for r in parsed]
    if len(closes) >= SPIKE_TRAILING_WINDOW + 2:
        # daily changes
        diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        # diffs[k] corresponds to the change INTO parsed[k+1]
        for i in range(SPIKE_TRAILING_WINDOW, len(diffs)):
            window = diffs[i - SPIKE_TRAILING_WINDOW:i]
            try:
                stdev = statistics.stdev(window)
            except statistics.StatisticsError:
                continue
            # Volatility floor: ignore spikes when recent daily moves are tiny.
            # SPIKE_MIN_STDEV is in percentage points; below it, day-to-day
            # noise is so small that z-scores are meaningless.
            if stdev < SPIKE_MIN_STDEV:
                continue
            mean = statistics.mean(window)
            z = abs((diffs[i] - mean) / stdev)
            spike_date = parsed[i + 1][0].strftime("%Y-%m-%d")
            if z >= SPIKE_SEVERE_SIGMA:
                result["stats"]["spikes_severe"] += 1
                result["warnings"].append(f"spike_severe: {spike_date} z={z:.2f}")
            elif z >= SPIKE_WARN_SIGMA:
                result["stats"]["spikes_warn"] += 1
                result["warnings"].append(f"spike_warn: {spike_date} z={z:.2f}")

    # Freshness check (PER-SERIES via cadence policy)
    last_date = parsed[-1][0]
    biz_days_old = count_business_days(last_date + timedelta(days=1), today)
    result["stats"]["days_since_update"] = biz_days_old

    policy = FRESHNESS_POLICY.get(cadence, FRESHNESS_POLICY["daily"])
    warn_thr = policy["warn_bizdays"]
    fail_thr = policy["fail_bizdays"]

    if fail_thr is not None and biz_days_old >= fail_thr:
        result["errors"].append(
            f"stale: last_date={last_date.strftime('%Y-%m-%d')} is {biz_days_old} "
            f"business days old (fail threshold {fail_thr}, cadence={cadence})"
        )
    elif warn_thr is not None and biz_days_old >= warn_thr:
        result["warnings"].append(
            f"stale: last_date={last_date.strftime('%Y-%m-%d')} is {biz_days_old} "
            f"business days old (warn threshold {warn_thr}, cadence={cadence})"
        )
    # manual cadence (both thresholds None): no staleness flag, report-only via
    # stats.days_since_update

    # Completeness check (last 60 calendar days, % weekday coverage)
    # Skipped for manual series — operator updates on demand, not every weekday.
    if len(parsed) >= 2 and not is_manual:
        cutoff = today - timedelta(days=COMPLETENESS_WINDOW_DAYS)
        recent_dates = {r[0] for r in parsed if r[0] >= cutoff}
        expected_weekdays = 0
        cursor = cutoff
        while cursor <= today:
            if cursor.weekday() < 5:
                expected_weekdays += 1
            cursor += timedelta(days=1)
        if expected_weekdays > 0:
            pct = (len(recent_dates) / expected_weekdays) * 100
            if pct < COMPLETENESS_MIN_PCT:
                result["warnings"].append(
                    f"completeness_low: {pct:.1f}% of weekdays in last {COMPLETENESS_WINDOW_DAYS}d "
                    f"(have {len(recent_dates)} of {expected_weekdays} expected)"
                )

    # Determine final status and usability
    if result["errors"]:
        result["status"] = "fail"
        result["usable_for_basis"] = False
    elif result["warnings"]:
        result["status"] = "warn"
        result["usable_for_basis"] = True
    else:
        result["status"] = "pass"
        result["usable_for_basis"] = True

    return result


# ============================================================================
# REPORT BUILDER
# ============================================================================

def build_report(today: datetime) -> dict:
    """Run validation across all SERIES_METADATA and build report dict."""
    data_dir = get_data_dir()
    report = {
        "schema_version": "1.1",
        "generated_at": today.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "global_status": "pass",
        "series": {},
        "summary": {
            "total_series": len(SERIES_METADATA),
            "pass": 0,
            "warn": 0,
            "fail": 0,
            "blocking": False,
        },
    }

    for series_id, metadata in SERIES_METADATA.items():
        csv_path = data_dir / f"{series_id}.csv"
        series_result = validate_series(series_id, metadata, csv_path, today)
        # Inject metadata for downstream consumers
        series_result["metadata"] = {
            "currency": metadata["currency"],
            "tenor": metadata["tenor"],
            "instrument_type": metadata["instrument_type"],
            "credit_nature": metadata["credit_nature"],
            "exp_min": metadata["exp_min"],
            "exp_max": metadata["exp_max"],
            "update_method": metadata.get("update_method", "auto"),
            "cadence": metadata.get("cadence", "daily"),
            "continuity_class": metadata.get("continuity_class", "observed"),
        }
        report["series"][series_id] = series_result

        if series_result["status"] == "pass":
            report["summary"]["pass"] += 1
        elif series_result["status"] == "warn":
            report["summary"]["warn"] += 1
        else:
            report["summary"]["fail"] += 1

    # Global status logic: any fail -> fail; any warn (no fails) -> warn; else pass
    if report["summary"]["fail"] > 0:
        report["global_status"] = "fail"
    elif report["summary"]["warn"] > 0:
        report["global_status"] = "warn"
    else:
        report["global_status"] = "pass"

    # Blocking flag: only block if literally zero usable series for basis
    usable_count = sum(
        1 for s in report["series"].values() if s["usable_for_basis"]
    )
    report["summary"]["blocking"] = usable_count == 0

    return report


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main() -> int:
    today = datetime.now(timezone.utc).replace(tzinfo=None)
    print(f"[validate_inputs] Starting validation at {today.isoformat()}Z")
    print(f"[validate_inputs] Data directory: {get_data_dir()}")
    print(f"[validate_inputs] Series in registry: {len(SERIES_METADATA)}")

    try:
        report = build_report(today)
    except Exception as e:
        print(f"[validate_inputs] FATAL: build_report crashed: {e}", file=sys.stderr)
        return 1

    output_path = get_output_path()
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)
    except OSError as e:
        print(f"[validate_inputs] FATAL: cannot write report: {e}", file=sys.stderr)
        return 1

    # Console summary
    s = report["summary"]
    print(f"[validate_inputs] Wrote report to {output_path}")
    print(f"[validate_inputs] Global status: {report['global_status']}")
    print(f"[validate_inputs] Pass: {s['pass']}  Warn: {s['warn']}  Fail: {s['fail']}")
    print(f"[validate_inputs] Blocking: {s['blocking']}")

    # Show fail details concisely
    if s["fail"] > 0:
        print(f"[validate_inputs] Failing series:")
        for sid, sr in report["series"].items():
            if sr["status"] == "fail":
                err_preview = sr["errors"][:2]
                print(f"  - {sid}: {err_preview}")

    # Exit code policy:
    #   0 = pipeline can proceed (pass or warn, with at least 1 usable series)
    #   1 = blocking failure (zero usable series, real bug)
    return 1 if s["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())


# ============================================================================
# OUTPUT JSON SCHEMA REFERENCE (schema_version 1.1)
# ============================================================================
# {
#   "schema_version": "1.1",
#   "generated_at": "ISO8601 UTC string",
#   "global_status": "pass" | "warn" | "fail",
#   "series": {
#     "<series_id>": {
#       "status": "pass" | "warn" | "fail",
#       "schema_ok": bool,
#       "last_date": "YYYY-MM-DD" | null,
#       "usable_for_basis": bool,
#       "errors": [str, ...],
#       "warnings": [str, ...],
#       "stats": {
#         "rows": int, "max_gap_business_days": int, "missing_count": int,
#         "duplicates_found": int, "range_breaches": int, "flatline_days": int,
#         "spikes_warn": int, "spikes_severe": int, "days_since_update": int | null
#       },
#       "metadata": {
#         "currency": str, "tenor": str, "instrument_type": str,
#         "credit_nature": str, "exp_min": float, "exp_max": float,
#         "update_method": "auto" | "manual", "cadence": "daily" | "manual"
#       }
#     }, ...
#   },
#   "summary": {
#     "total_series": int, "pass": int, "warn": int, "fail": int, "blocking": bool
#   }
# }
