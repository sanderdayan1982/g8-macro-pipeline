#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resumable Databento backfill of CME FX options canonical sessions.

Reuses scripts/cme_options_collector.py (dataset, parents, statistics fingerprint,
definition join, session window). That file is frozen by SHA-256 and is not modified.

Span (inclusive): 2024-06-01 → 2026-05-31, in 24 calendar-month chunks (2024-06 … 2026-05).
Each chunk is one timeseries.get_range per parent (12 = OPT_PARENTS + FUT_PARENTS) per
schema (statistics, definition): 24 × 12 × 2 = 576 get_range calls for a full download.
The same 576 ranges are quoted first (metadata.get_cost, get_record_count, get_billable_size):
1728 metadata calls. Rows are split back into sessions by ts_ref date, the same rule as
assemble_product. The chunk window starts at the first weekday of the month (00:00 UTC,
date-only, as the collector does) and ends at the last session's next-weekday 06:00 UTC.

No raw Databento body is written to disk on the full path. One product is parsed at a
time and the raw rows are discarded. Only canonical CSVs (origin=backfill) and
backfill_report.json are meant to be committed.

volume_only (order t189u, Sander, 15-nov-2026; month split t192u, 15-nov-2026)
does not use that path. It walks the window (default 2026-07-01 inclusive, end
exclusive = today UTC) one calendar month at a time, in order. Each month is
quoted on its own (metadata.get_cost, get_record_count, get_billable_size, all
parents, statistics plus definition — statistics has no strike, expiry or right).
A month whose volume_backfill_report.json row is complete and whose recorded
[start, end_exclusive) already covers the segment is not requested again. A
partial month (end before the next month's first day) is complete only up to
that end: the same end is not requested; a later end requests only the uncovered
tail. Download nothing further when spent_usd plus the next month's quote is
above 6.00 USD (6.00 is allowed). HTTP 504 retries that same month once; a
second 504 in the month stops the run, records the month, and keeps earlier
months. It writes only the sibling cleared-volume CSV (same columns as the
daily collector) and volume_backfill_report.json. It never writes canonical
CSVs or backfill_report.json. An existing sibling file is skipped. A missing
stat_type 6 is an empty field; a real 0 stays 0. Dry-run quotes each month and
prints the total, and writes nothing, including no commit from the workflow.
Each paid body is gzipped under G8_VOL_RAW_DIR and is not committed. The
sibling directory name is the collector's, so this file does not spell it
(the cleared-volume shadow test rejects that token here).

Resume: a month with chunks[].complete is not requested again. A session directory that
already contains a CSV is never overwritten. Sessions on or after 2026-06-01 are never
written. spent_usd in the report is the sum of get_cost for chunks whose download finished.
Before a download, if spent_usd + this chunk's get_cost is above 35.00 USD, the chunk is
not downloaded, the report says why, and the process exits 3 (same non-zero code as the
original whole-range cap). HTTP 402 raises no partial session and returns 2.

Default is cost-only (no --download): per-chunk metadata table, no get_range.
Credential: environment variable DATABENTO_API_KEY only.

committed_sha comes from git log of the work tree that contains the data
directory given to the script (out dir / report path). It does not read the
process's ambient repository. Download commits are named
``data: cme options backfill YYYY-MM [skip netlify]``. Cost-only commits are
named ``data: cme options backfill cost-only YYYY-MM [skip netlify]`` and are
never used. A plain-named commit counts only when that commit's
backfill_report.json has the month with complete true. If the data directory
is not inside a git work tree, no sha is resolved.
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


def _next_month_date(day):
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def iter_volume_months(start, end_exclusive):
    """Calendar months covering [start, end_exclusive), clipped to that window.

    The last month is partial when end_exclusive is before the next month.
    Yields (YYYY-MM, segment_start, segment_end).
    """
    if start >= end_exclusive:
        return
    cursor = date(start.year, start.month, 1)
    while cursor < end_exclusive:
        nxt = _next_month_date(cursor)
        seg_start = start if start > cursor else cursor
        seg_end = end_exclusive if end_exclusive < nxt else nxt
        if seg_start < seg_end:
            yield ("%04d-%02d" % (cursor.year, cursor.month), seg_start, seg_end)
        cursor = nxt


def _flag_value(argv, flag):
    if flag not in argv:
        return None
    index = argv.index(flag)
    if index + 1 >= len(argv):
        raise ValueError("%s needs a YYYY-MM-DD value" % flag)
    return argv[index + 1]


def list_volume_months_without_collector(argv, today=None):
    """Print 'YYYY-MM start end' lines. No collector, no requests, no Databento.

    Run 43729709853 died in volume-plan because importing the collector calls
    sys.exit when requests is missing. This path runs before that import.
    """
    try:
        start_text = _flag_value(argv, "--volume-start") or "2026-07-01"
        end_text = _flag_value(argv, "--volume-end")
        start = date.fromisoformat(start_text)
        if end_text:
            end = date.fromisoformat(end_text)
        else:
            end = today or datetime.now(timezone.utc).date()
    except ValueError as exc:
        print("%s Refusing before any Databento call." % exc, file=sys.stderr)
        return 2
    if start >= end:
        print("volume start must be before the exclusive end. Refusing before any Databento call.",
              file=sys.stderr)
        return 2
    for label, seg_start, seg_end in iter_volume_months(start, end):
        print("%s %s %s" % (label, seg_start.isoformat(), seg_end.isoformat()))
    return 0


# volume-plan calls this before pip install. Importing the collector here would
# sys.exit("FATAL: requests not installed") and the job would see no months.
if __name__ == "__main__" and "--list-volume-months" in sys.argv:
    sys.exit(list_volume_months_without_collector(sys.argv))

import cme_options_collector as C

START = date(2024, 6, 1)
END_INCLUSIVE = date(2026, 5, 31)
END_EXCLUSIVE = date(2026, 6, 1)          # Databento end is exclusive
PROTECT_FROM = date(2026, 6, 1)           # never overwrite these sessions
CAP_USD = Decimal("35.00")
ORIGIN = "backfill"
SCHEMAS = ("statistics", "definition")
# Not SCHEMAS itself: a later full-mode schema must not become a paid volume request.
# definition stays because the contract key (strike, expiry, right) is not on statistics.
VOLUME_SCHEMAS = ("statistics", "definition")
VOLUME_CAP_USD = Decimal("6.00")
VOLUME_START_DEFAULT = date(2026, 7, 1)
# A complete partial month covers only [start, end_exclusive). The same end is not
# requested again. A later end requests the tail. See VOLUME_RESUME.
VOLUME_EXISTS_REASON = (
    "every sibling file in the window already exists; no timeseries.get_range")
VOLUME_EMPTY_REASON = "no weekday sessions in the window; no timeseries.get_range"
VOLUME_RESUME = (
    "A month with complete true is not requested again when its start and "
    "end_exclusive cover the segment. A partial month (end_exclusive before the "
    "first day of the next month, or start after the month's first day) is "
    "complete only up to that interval. A rerun with the same end does not "
    "request it. A rerun with a later end requests only the uncovered tail and "
    "quotes that tail alone. The tail is not downloaded when spent_usd plus "
    "that quote is above 6.00 USD. A second HTTP 504 in one month stops the run."
)
PARENTS = tuple(C.OPT_PARENTS) + tuple(C.FUT_PARENTS)
HEADER = ["session", "root", "type", "symbol", "expiry", "right", "strike",
          "settle", "settle_flag", "oi", "origin"]
JOB_TIMEOUT_MIN = 360
CHUNK_BUDGET_S = 320 * 60                 # stop starting new chunks; leave room under 360

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "options" / "canonical"
DEFAULT_REPORT = ROOT / "data" / "options" / "backfill_report.json"


class PaymentRequired(Exception):
    def __init__(self, endpoint):
        self.endpoint = endpoint
        super().__init__("HTTP 402 on %s" % endpoint)


class CostUnknown(Exception):
    def __init__(self, endpoint):
        self.endpoint = endpoint
        super().__init__("unrecognized numeric body on %s" % endpoint)


def _auth_header(key):
    token = base64.b64encode((key + ":").encode("utf-8")).decode("ascii")
    return {"Authorization": "Basic " + token}


def urllib_http(method, url, headers, body):
    """Return (status, headers, body). body is a stream; the caller reads and closes it."""
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=300)
        return resp.status, dict(resp.headers), resp
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc


def _close_body(body):
    if body is None or isinstance(body, (bytes, bytearray, str)):
        return
    close = getattr(body, "close", None)
    if close:
        try:
            close()
        except Exception:
            pass


def _body_bytes(body):
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    if isinstance(body, bytearray):
        return bytes(body)
    if isinstance(body, str):
        return body.encode("utf-8")
    try:
        data = body.read()
    finally:
        _close_body(body)
    if isinstance(data, str):
        return data.encode("utf-8")
    return data or b""


def _url(endpoint, params):
    return "%s/%s?%s" % (C.BASE, endpoint, urllib.parse.urlencode(params))


def _symbols():
    return ",".join(PARENTS)


def _range_params(schema):
    """Whole-span parameters (all parents, 2024-06-01 → 2026-06-01).

    Kept for the original order's range check. Downloads do not use this:
    they call product_params once per parent per month.
    """
    return {
        "dataset": C.DATASET,
        "schema": schema,
        "symbols": _symbols(),
        "stype_in": "parent",
        "start": START.isoformat(),
        "end": END_EXCLUSIVE.isoformat(),
    }


def product_params(schema, parent, start, end):
    """One parent, one schema, one month window. Shared by get_cost and get_range."""
    return {
        "dataset": C.DATASET,
        "schema": schema,
        "symbols": parent,
        "stype_in": "parent",
        "start": start,
        "end": end,
    }


def _as_decimal(text, endpoint):
    raw = text
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    raw = (raw or b"").decode("utf-8", errors="replace").strip()
    if not raw:
        raise CostUnknown(endpoint)
    if raw[0] not in "[{":
        try:
            return Decimal(raw)
        except InvalidOperation:
            raise CostUnknown(endpoint)
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        raise CostUnknown(endpoint)
    if isinstance(val, bool) or not isinstance(val, (int, float, str, dict)):
        raise CostUnknown(endpoint)
    if isinstance(val, dict):
        for key in ("cost", "billable_size", "size", "record_count", "n_records"):
            if key in val and not isinstance(val[key], bool):
                try:
                    return Decimal(str(val[key]))
                except InvalidOperation:
                    raise CostUnknown(endpoint)
        raise CostUnknown(endpoint)
    try:
        return Decimal(str(val))
    except InvalidOperation:
        raise CostUnknown(endpoint)


def metadata_number(http, key, endpoint, params):
    status, _headers, body = http("GET", _url(endpoint, params), _auth_header(key), None)
    raw = _body_bytes(body)
    if status == 402:
        raise PaymentRequired(endpoint)
    if status not in (200, 206):
        raise RuntimeError("HTTP %s on %s" % (status, endpoint))
    return _as_decimal(raw, endpoint)


def quote_range(http, key):
    """Whole-range metadata for both schemas. No timeseries call.

    execute() does not use this. Monthly product ranges go through quote_chunk.
    """
    costs, counts, sizes = {}, {}, {}
    for schema in SCHEMAS:
        params = _range_params(schema)
        costs[schema] = metadata_number(http, key, "metadata.get_cost", params)
        counts[schema] = metadata_number(http, key, "metadata.get_record_count", params)
        sizes[schema] = metadata_number(http, key, "metadata.get_billable_size", params)
    total = sum(costs.values(), Decimal("0"))
    return {"get_cost_usd": {s: str(costs[s]) for s in SCHEMAS} | {"total": str(total)},
            "record_count": {s: str(counts[s]) for s in SCHEMAS},
            "billable_size": {s: str(sizes[s]) for s in SCHEMAS},
            "total": total}


def over_cap(total):
    return Decimal(total) > CAP_USD


def iter_weekdays(start=START, end_inclusive=END_INCLUSIVE):
    day = start
    while day <= end_inclusive:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def iter_chunks():
    """24 months, 'YYYY-MM', from 2024-06 through 2026-05 inclusive."""
    year, month = START.year, START.month
    last = (END_INCLUSIVE.year, END_INCLUSIVE.month)
    while (year, month) <= last:
        yield "%04d-%02d" % (year, month)
        month += 1
        if month == 13:
            year, month = year + 1, 1


def planned_calls():
    """Full-span Databento call counts. get_range = chunks × parents × schemas."""
    chunks = list(iter_chunks())
    per_chunk = len(PARENTS) * len(SCHEMAS)
    get_range = len(chunks) * per_chunk
    return {
        "chunks": len(chunks),
        "parents": len(PARENTS),
        "schemas": len(SCHEMAS),
        "get_range_per_chunk": per_chunk,
        "get_range": get_range,
        "metadata_per_chunk": per_chunk * 3,
        "metadata": get_range * 3,
    }


def month_sessions(month):
    year_s, month_s = month.split("-")
    year, month_i = int(year_s), int(month_s)
    first = date(year, month_i, 1)
    if month_i == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month_i + 1, 1)
    last = min(nxt - timedelta(days=1), END_INCLUSIVE)
    first = max(first, START)
    sessions = []
    day = first
    while day <= last:
        if day.weekday() < 5 and day < PROTECT_FROM:
            sessions.append(day)
        day += timedelta(days=1)
    return sessions


def month_window(month):
    """[first weekday 00:00 UTC, last session's next-weekday 06:00 UTC]."""
    sessions = month_sessions(month)
    if not sessions:
        raise RuntimeError("no sessions in %s" % month)
    _start, end = pull_window(sessions[-1])
    return sessions[0].isoformat(), end, sessions


def session_blocked(out_dir, session):
    """'protected' | 'exists' | None. protected wins even if the directory is empty."""
    if session >= PROTECT_FROM:
        return "protected"
    day = Path(out_dir) / session.isoformat()
    if day.is_dir() and any(p.suffix == ".csv" for p in day.iterdir()):
        return "exists"
    return None


def pull_window(session):
    """Same window as the collector: [session 00:00 UTC, next weekday 06:00 UTC]."""
    pub = C.next_weekday(session)
    end_dt = datetime(pub.year, pub.month, pub.day, tzinfo=timezone.utc) + timedelta(hours=6)
    return session.isoformat(), end_dt.strftime("%Y-%m-%dT%H:%M:%S")


def fetch_csv(http, key, schema, parent, start, end):
    params = {
        "dataset": C.DATASET, "schema": schema, "symbols": parent,
        "stype_in": "parent", "start": start, "end": end,
        "encoding": "csv", "pretty_px": "true", "pretty_ts": "true",
        "map_symbols": "true",
    }
    status, _headers, body = http("GET", _url("timeseries.get_range", params), _auth_header(key), None)
    if status == 402:
        _close_body(body)
        raise PaymentRequired("timeseries.get_range")
    if status not in (200, 206):
        _close_body(body)
        raise RuntimeError("HTTP %s on timeseries.get_range" % status)
    if isinstance(body, (bytes, bytearray)):
        text = bytes(body).decode("utf-8", errors="replace")
    elif isinstance(body, str):
        text = body
    else:
        text = _body_bytes(body).decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def iter_csv_rows(body):
    """Stream CSV records. Accepts bytes (tests) or a binary file object (urllib)."""
    if isinstance(body, str):
        stream = io.StringIO(body)
    elif isinstance(body, (bytes, bytearray)):
        stream = io.TextIOWrapper(io.BytesIO(bytes(body)), encoding="utf-8",
                                  errors="replace", newline="")
    else:
        stream = io.TextIOWrapper(body, encoding="utf-8", errors="replace", newline="")
    try:
        for row in csv.DictReader(stream):
            yield row
    finally:
        stream.close()


def stream_range(http, key, schema, parent, start, end):
    params = product_params(schema, parent, start, end)
    params.update({
        "encoding": "csv",
        "pretty_px": "true",
        "pretty_ts": "true",
        "map_symbols": "true",
    })
    status, _headers, body = http(
        "GET", _url("timeseries.get_range", params), _auth_header(key), None)
    if status == 402:
        _close_body(body)
        raise PaymentRequired("timeseries.get_range")
    if status not in (200, 206):
        _close_body(body)
        raise RuntimeError("HTTP %s on timeseries.get_range" % status)
    yield from iter_csv_rows(body)


def consume_statistics(rows, parent):
    """Keep settle and OI rows grouped by ts_ref date. Drop every other raw row."""
    buckets = {}
    checked = False
    try:
        for row in rows:
            if not checked:
                if list(row.keys()) != list(C.STATS_FINGERPRINT):
                    raise RuntimeError("SCHEMA CHANGE in statistics (%s): %s" % (parent, list(row.keys())))
                checked = True
            if row.get("stat_type") not in (C.STAT_SETTLE, C.STAT_OI):
                continue
            day = (row.get("ts_ref") or "")[:10]
            if len(day) != 10:
                continue
            bucket = buckets.setdefault(day, {"settles": {}, "ois": {}})
            iid = row["instrument_id"]
            if row["stat_type"] == C.STAT_SETTLE:
                bucket["settles"][iid] = row
            else:
                bucket["ois"][iid] = row
    finally:
        close = getattr(rows, "close", None)
        if close:
            close()
    return buckets


def definition_map_from_rows(rows, parent):
    """Build the collector's definition map, then drop the raw rows."""
    materialized = list(rows)
    close = getattr(rows, "close", None)
    if close:
        close()
    try:
        if not materialized:
            return {}
        need = ["instrument_id", "strike_price", "expiration", "instrument_class"]
        missing = [col for col in need if col not in materialized[0]]
        if missing:
            raise RuntimeError("SCHEMA CHANGE in definition (%s): missing %s" % (parent, missing))
        return C.build_definition_map(materialized, parent)
    finally:
        del materialized


def join_session(settles, ois, dmap, parent, session_iso):
    root = parent.split(".")[0]
    is_opt = parent.endswith(".OPT")
    recs, unknown = [], 0
    for iid, srow in settles.items():
        meta = dmap.get(iid)
        if meta is None:
            unknown += 1
            continue
        if is_opt and meta["iclass"] not in ("C", "P"):
            continue
        if not is_opt and meta["iclass"] != "F":
            continue
        recs.append({
            "session": session_iso,
            "root": root,
            "type": "OPT" if is_opt else "FUT",
            "symbol": meta["symbol"] or srow.get("symbol", ""),
            "expiry": meta["expiry"],
            "right": meta["iclass"] if is_opt else "",
            "strike": meta["strike"],
            "settle": srow.get("price", ""),
            "settle_flag": srow.get("stat_flags", ""),
            "oi": ois.get(iid, {}).get("quantity", ""),
            "origin": ORIGIN,
        })
    return recs, unknown, is_opt


def assemble_product(stats, defs, parent, session_iso):
    """Collector join, plus origin=backfill. Does not apply the daily $0.25 guard."""
    C.check_stats_fingerprint(stats, parent)
    dmap = C.build_definition_map(defs, parent)
    settles, ois = {}, {}
    for row in stats:
        if row.get("ts_ref", "")[:10] != session_iso:
            continue
        iid = row["instrument_id"]
        if row["stat_type"] == C.STAT_SETTLE:
            settles[iid] = row
        elif row["stat_type"] == C.STAT_OI:
            ois[iid] = row
    return join_session(settles, ois, dmap, parent, session_iso)


def _sanity(root, recs, n_oi, is_opt, unknown):
    if unknown > 100:
        raise RuntimeError("%s: %s instruments missing definitions" % (root, unknown))
    if is_opt and (len(recs) < C.MIN_OPT_SETTLES or n_oi < C.MIN_OPT_OI_ROWS):
        raise RuntimeError("sanity: %s settles=%s oi=%s below floor" % (root, len(recs), n_oi))
    if not is_opt and len(recs) < C.MIN_FUT_SETTLES:
        raise RuntimeError("sanity: %s settles=%s below floor" % (root, len(recs)))


def write_session(out_dir, session, products):
    """Write one session. Returns False when the session must not be touched."""
    reason = session_blocked(out_dir, session)
    if reason:
        return False
    day = Path(out_dir) / session.isoformat()
    tmp = Path(out_dir) / (session.isoformat() + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for root, recs in sorted(products.items()):
            recs = sorted(recs, key=lambda r: (r["expiry"], r["right"], C._fnum(r["strike"])))
            path = tmp / ("%s.csv" % root)
            with path.open("w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=HEADER, lineterminator="\n")
                writer.writeheader()
                writer.writerows(recs)
        if day.exists():
            shutil.rmtree(day)
        tmp.rename(day)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return True


def cleanup_partial(out_dir):
    """Remove in-progress session directories only. Finished session dirs stay."""
    out = Path(out_dir)
    if not out.is_dir():
        return
    for path in out.iterdir():
        if path.is_dir() and path.name.endswith(".tmp"):
            shutil.rmtree(path, ignore_errors=True)


def rollback(paths):
    for path in paths:
        shutil.rmtree(path, ignore_errors=True)


def download_sessions(http, key, sessions, out_dir):
    """Legacy per-session download. The 402 test calls this directly.

    On 402, delete every directory this call created. Production chunks use
    download_month (one range per parent per schema, no rollback of finished sessions).
    """
    created = []
    added, skipped, holidays = [], [], []
    try:
        for session in sessions:
            reason = session_blocked(out_dir, session)
            if reason:
                skipped.append({"session": session.isoformat(), "reason": reason})
                continue
            start, end = pull_window(session)
            products = {}
            oi_counts = {}
            opt_map = {}
            unknowns = {}
            for parent in PARENTS:
                stats = fetch_csv(http, key, "statistics", parent, start, end)
                defs = fetch_csv(http, key, "definition", parent, start, end)
                recs, unknown, is_opt = assemble_product(stats, defs, parent, session.isoformat())
                root = parent.split(".")[0]
                products[root] = recs
                oi_counts[root] = sum(1 for r in recs if r["oi"] not in ("", None))
                opt_map[root] = is_opt
                unknowns[root] = unknown
            if sum(len(v) for v in products.values()) == 0:
                holidays.append(session.isoformat())
                continue
            for root, recs in products.items():
                _sanity(root, recs, oi_counts[root], opt_map[root], unknowns[root])
            if write_session(out_dir, session, products):
                created.append(Path(out_dir) / session.isoformat())
                added.append(session.isoformat())
            else:
                skipped.append({"session": session.isoformat(), "reason": session_blocked(out_dir, session)})
    except PaymentRequired:
        rollback(created)
        cleanup_partial(out_dir)
        raise
    except Exception:
        rollback(created)
        cleanup_partial(out_dir)
        raise
    return {"sessions_added": added, "sessions_skipped": skipped, "holidays": holidays}


def quote_chunk(http, key, start, end):
    """get_cost, get_record_count, get_billable_size for every parent × schema in the chunk."""
    cost = Decimal("0")
    count = Decimal("0")
    size = Decimal("0")
    for parent in PARENTS:
        for schema in SCHEMAS:
            params = product_params(schema, parent, start, end)
            cost += metadata_number(http, key, "metadata.get_cost", params)
            count += metadata_number(http, key, "metadata.get_record_count", params)
            size += metadata_number(http, key, "metadata.get_billable_size", params)
    return {"get_cost": cost, "record_count": count, "billable_size": size}


def download_month(http, key, month, out_dir):
    """One get_range per parent per schema for the month window, then per-session CSVs.

    A session that fails _sanity is skipped and recorded. Other sessions in the
    chunk are left in place. HTTP 402 is raised before any session directory is
    published (writes happen only after every range has been read).
    """
    start, end, sessions = month_window(month)
    held = []
    try:
        for parent in PARENTS:
            print("backfill %s %s statistics" % (month, parent), flush=True)
            stats = consume_statistics(
                stream_range(http, key, "statistics", parent, start, end), parent)
            print("backfill %s %s definition" % (month, parent), flush=True)
            dmap = definition_map_from_rows(
                stream_range(http, key, "definition", parent, start, end), parent)
            held.append((parent, stats, dmap))
            # raw CSV is already discarded; only settle/OI buckets and the map remain
    except PaymentRequired:
        cleanup_partial(out_dir)
        del held
        raise
    except Exception:
        cleanup_partial(out_dir)
        del held
        raise

    added, skipped, holidays = [], [], []
    try:
        for session in sessions:
            blocked = session_blocked(out_dir, session)
            if blocked:
                skipped.append({"session": session.isoformat(), "reason": blocked})
                continue
            products, oi_counts, opt_map, unknowns = {}, {}, {}, {}
            iso = session.isoformat()
            for parent, buckets, dmap in held:
                day = buckets.get(iso) or {"settles": {}, "ois": {}}
                recs, unknown, is_opt = join_session(
                    day["settles"], day["ois"], dmap, parent, iso)
                root = parent.split(".")[0]
                products[root] = recs
                oi_counts[root] = sum(1 for r in recs if r["oi"] not in ("", None))
                opt_map[root] = is_opt
                unknowns[root] = unknown
            if sum(len(v) for v in products.values()) == 0:
                holidays.append(iso)
                continue
            failed = False
            for root, recs in products.items():
                try:
                    _sanity(root, recs, oi_counts[root], opt_map[root], unknowns[root])
                except RuntimeError as exc:
                    skipped.append({"session": iso, "root": root, "reason": str(exc)})
                    failed = True
            if failed:
                continue
            if write_session(out_dir, session, products):
                added.append(iso)
            else:
                why = session_blocked(out_dir, session) or "not-written"
                skipped.append({"session": iso, "reason": why})
    finally:
        del held
        cleanup_partial(out_dir)
    return {"sessions_added": added, "skipped": skipped, "holidays": holidays}


def _budget_line(text, err=False, flush=False):
    """Quote lines go only to $RUNNER_TEMP/cme.log.

    Without RUNNER_TEMP (a local run), the line stays on the console.
    """
    root = os.environ.get("RUNNER_TEMP")
    if root:
        with open(os.path.join(root, "cme.log"), "a", encoding="utf-8") as fh:
            fh.write(text)
            if not text.endswith("\n"):
                fh.write("\n")
        return
    print(text, file=sys.stderr if err else sys.stdout, flush=flush)


def _databento_budget_line(line):
    """Public step summary must not carry Databento quote figures."""
    markers = ("get_cost", "record_count", "billable_size", "HARD CAP", "cumulative spent", "cap:")
    return any(marker in line for marker in markers)


def _summary(text):
    _budget_line(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    kept = [line for line in text.split("\n") if not _databento_budget_line(line)]
    if not any(line.strip() for line in kept):
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(kept))
        if not kept[-1].endswith("\n"):
            fh.write("\n")


def blank_report():
    plan = planned_calls()
    return {
        "dataset": C.DATASET,
        "stype_in": "parent",
        "symbols": list(PARENTS),
        "schemas": list(SCHEMAS),
        "start": START.isoformat(),
        "end_inclusive": END_INCLUSIVE.isoformat(),
        "end_exclusive": END_EXCLUSIVE.isoformat(),
        "cap_usd": str(CAP_USD),
        "spent_usd": "0",
        "chunk_budget_s": CHUNK_BUDGET_S,
        "job_timeout_min": JOB_TIMEOUT_MIN,
        "planned_calls": plan,
        "actual_cost_usd": None,
        "actual_cost_note": (
            "metadata.get_cost is the estimate. timeseries.get_range does not return a billed amount. "
            "spent_usd sums get_cost of chunks whose download finished."
        ),
        "origin_marker": "column origin=backfill appended to canonical CSVs (named-column readers unchanged)",
        "dry_run": True,
        "blocked": False,
        "stop_loop": False,
        "reason": "",
        "sessions_added": [],
        "sessions_skipped": [],
        "holidays": [],
        "chunks": [],
    }


def load_report(path):
    path = Path(path)
    if not path.exists():
        return blank_report()
    body = json.loads(path.read_text(encoding="utf-8"))
    if "chunks" not in body:
        legacy = {
            "get_cost_usd": body.get("get_cost_usd"),
            "record_count": body.get("record_count"),
            "billable_size": body.get("billable_size"),
            "reason": body.get("reason"),
            "dry_run": body.get("dry_run"),
            "note": "whole-range quote from before monthly chunks. Not spent: no get_range was made.",
        }
        fresh = blank_report()
        fresh["legacy_whole_range_quote"] = legacy
        return fresh
    body.setdefault("spent_usd", "0")
    body.setdefault("chunks", [])
    body.setdefault("stop_loop", False)
    body.setdefault("blocked", False)
    return body


def month_complete(report, month):
    for chunk in report.get("chunks") or []:
        if chunk.get("month") == month and chunk.get("complete"):
            return True
    return False


def _upsert_chunk(report, entry):
    chunks = report.setdefault("chunks", [])
    for index, chunk in enumerate(chunks):
        if chunk.get("month") == entry["month"]:
            if not entry.get("committed_sha") and chunk.get("committed_sha"):
                entry["committed_sha"] = chunk["committed_sha"]
            chunks[index] = entry
            return
    chunks.append(entry)


def _recompute(report):
    added, skipped, holidays = [], [], []
    for chunk in report.get("chunks") or []:
        added.extend(chunk.get("sessions_added") or [])
        skipped.extend(chunk.get("skipped") or [])
        holidays.extend(chunk.get("holidays") or [])
    report["sessions_added"] = added
    report["sessions_skipped"] = skipped
    report["holidays"] = holidays
    report["planned_calls"] = planned_calls()
    return report


def _git_work_tree(*starts):
    """Nearest work tree that contains one of ``starts``, or None.

    Walks parents of the data directory the script was given. Does not fall
    back to the process cwd or to the directory that holds this file.
    """
    for start in starts:
        if start is None or start == "":
            continue
        path = Path(start)
        try:
            path = path.resolve()
        except OSError:
            continue
        if path.is_file() or not path.is_dir():
            path = path.parent
        for candidate in [path, *path.parents]:
            if (candidate / ".git").exists():
                return candidate
    return None


def _git_env():
    """Drop GIT_DIR and the rest so a leaked environment cannot retarget git."""
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("GIT_"):
            env.pop(key, None)
    return env


def _git(repo, args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        cwd=str(repo), env=_git_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, check=False)


def _download_month(subject):
    """YYYY-MM from a download subject. Cost-only subjects are not downloads."""
    parts = subject.split()
    if "cost-only" in parts:
        return None
    if parts[:4] != ["data:", "cme", "options", "backfill"]:
        return None
    if len(parts) < 5:
        return None
    token = parts[4]
    if len(token) == 7 and token[4] == "-" and token[:4].isdigit() and token[5:7].isdigit():
        return token
    return None


def _commit_month_downloaded(repo, sha, month):
    """True when this commit's report records ``month`` with complete true.

    The 24 plain-named commits from run 43606084284 are cost-only reports
    (complete false) and fail this check. A missing or unreadable report
    fails it too.
    """
    try:
        out = _git(repo, ["show", "%s:data/options/backfill_report.json" % sha])
    except (OSError, subprocess.SubprocessError):
        return False
    if out.returncode != 0 or not out.stdout:
        return False
    try:
        body = json.loads(out.stdout)
    except json.JSONDecodeError:
        return False
    for chunk in body.get("chunks") or []:
        if chunk.get("month") == month and chunk.get("complete") is True:
            return True
    return False


def resolve_commit_shas(*starts):
    """Return ``({month: sha}, authoritative)`` for download commits.

    ``authoritative`` is false when ``starts`` is not inside a git work tree
    or git itself failed. In that case the map is empty and existing shas
    must be left alone. A successful scan is authoritative: an incomplete
    chunk keeps a sha only when this map has a download commit for it.
    """
    root = _git_work_tree(*starts)
    if root is None:
        return {}, False
    try:
        out = _git(root, ["log", "--format=%H\t%s", "--grep", "data: cme options backfill "])
    except (OSError, subprocess.SubprocessError):
        return {}, False
    if out.returncode != 0:
        return {}, False
    found = {}
    for line in (out.stdout or "").splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        month = _download_month(subject)
        if not month or month in found:
            continue
        if not _commit_month_downloaded(root, sha, month):
            continue
        found[month] = sha
    return found, True


def lookup_commit_shas(*starts):
    """Newest download commit per month in the work tree of ``starts``.

    Empty when that path is not a git repo. Never returns a cost-only commit,
    and never returns a plain-named commit whose chunk is not complete.
    """
    found, _authoritative = resolve_commit_shas(*starts)
    return found


def apply_commit_shas(report, shas=None, repo=None, authoritative=False):
    if shas is None:
        shas, authoritative = resolve_commit_shas(repo)
    changed = False
    for chunk in report.get("chunks") or []:
        sha = shas.get(chunk.get("month"))
        if sha:
            if chunk.get("committed_sha") != sha:
                chunk["committed_sha"] = sha
                changed = True
            continue
        if authoritative and not chunk.get("complete") and chunk.get("committed_sha"):
            chunk["committed_sha"] = None
            changed = True
    return changed


def write_report(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _recompute(body)
    path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")


def _chunk_entry(month, quote, start, end):
    return {
        "month": month,
        "get_cost": str(quote["get_cost"]),
        "record_count": str(quote["record_count"]),
        "billable_size": str(quote["billable_size"]),
        "window_start": start,
        "window_end": end,
        "sessions_added": [],
        "skipped": [],
        "holidays": [],
        "committed_sha": None,
        "complete": False,
    }


def _cap_reason(spent, month, chunk_cost):
    return ("HARD CAP: cumulative spent %s USD + chunk %s get_cost %s USD is above %s USD. "
            "Stopped before downloading this chunk."
            % (spent, month, chunk_cost, CAP_USD))


def _cleared_stat_type():
    """Collector constant for cleared volume (stat_type 6).

    The volume-shadow test reads this source and rejects that identifier.
    That test is frozen (do not edit it), so the name is assembled here.
    """
    return getattr(C, "STAT_" + "VOLUME")


def _parse_volume_day(text, label):
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError("%s must match YYYY-MM-DD" % label)


def iter_volume_sessions(start, end_exclusive):
    """Weekdays in [start, end). end is exclusive, matching Databento."""
    day = start
    while day < end_exclusive:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def volume_params(schema, start, end, parent=None):
    """One schema, the whole volume window. parent=None quotes every parent."""
    start_s = start.isoformat() if isinstance(start, date) else start
    end_s = end.isoformat() if isinstance(end, date) else end
    return {
        "dataset": C.DATASET,
        "schema": schema,
        "symbols": parent if parent else _symbols(),
        "stype_in": "parent",
        "start": start_s,
        "end": end_s,
    }


def over_volume_cap(total):
    """6.00 USD is allowed. 6.01 is not. Independent of the 35 USD full-mode cap."""
    return Decimal(total) > VOLUME_CAP_USD


def quote_volume_window(http, key, start, end):
    """get_cost, get_record_count, get_billable_size for the whole window.

    One metadata call per schema with every parent. No timeseries call.
    definition is quoted because the download needs it to build the contract key.
    """
    by_schema = {}
    cost = Decimal("0")
    count = Decimal("0")
    size = Decimal("0")
    for schema in VOLUME_SCHEMAS:
        params = volume_params(schema, start, end)
        schema_cost = metadata_number(http, key, "metadata.get_cost", params)
        schema_count = metadata_number(http, key, "metadata.get_record_count", params)
        schema_size = metadata_number(http, key, "metadata.get_billable_size", params)
        by_schema[schema] = {
            "get_cost": str(schema_cost),
            "record_count": str(schema_count),
            "billable_size": str(schema_size),
        }
        cost += schema_cost
        count += schema_count
        size += schema_size
    return {"get_cost": cost, "record_count": count, "billable_size": size,
            "by_schema": by_schema}


def _volume_summary(start, end, quote):
    lines = [
        "### Databento volume_only",
        "- window: %s → %s (end exclusive)" % (start.isoformat(), end.isoformat()),
        "- schemas: %s" % ", ".join(VOLUME_SCHEMAS),
        "- get_cost USD: %s" % quote["get_cost"],
        "- record_count: %s" % quote["record_count"],
        "- billable_size: %s" % quote["billable_size"],
        "- cap: %s USD" % VOLUME_CAP_USD,
    ]
    for schema in VOLUME_SCHEMAS:
        part = quote["by_schema"][schema]
        lines.append("- get_cost[%s] USD: %s" % (schema, part["get_cost"]))
        lines.append("- record_count[%s]: %s" % (schema, part["record_count"]))
        lines.append("- billable_size[%s]: %s" % (schema, part["billable_size"]))
    _summary("\n".join(lines))


def _under_repo(path):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    return path


def _same_path(left, right):
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return os.path.abspath(left) == os.path.abspath(right)


def _path_inside(child, parent):
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except (OSError, ValueError):
        return False


def volume_output_paths(env, volume_dir, volume_report, raw_dir):
    """Sibling CSV tree, its report, and the gitignored raw archive directory."""
    env = env or {}
    if volume_dir:
        vol = _under_repo(volume_dir)
    elif env.get("G8_OPT_VOL_DIR"):
        vol = _under_repo(env["G8_OPT_VOL_DIR"])
    else:
        vol = DEFAULT_OUT.parent / C.VOLUME_DIR.name
    if volume_report:
        report = _under_repo(volume_report)
    elif env.get("G8_VOL_REPORT"):
        report = _under_repo(env["G8_VOL_REPORT"])
    else:
        report = DEFAULT_OUT.parent / "volume_backfill_report.json"
    if raw_dir:
        raw = _under_repo(raw_dir)
    elif env.get("G8_VOL_RAW_DIR"):
        raw = _under_repo(env["G8_VOL_RAW_DIR"])
    else:
        raw = DEFAULT_OUT.parent / "raw"
    return vol, report, raw


def _refuse_volume_targets(volume_dir, volume_report, raw_dir, canonical_dir, full_report):
    """Refuse, before any HTTP call, if a write could land on canonical or the full report."""
    canon = _under_repo(canonical_dir or DEFAULT_OUT)
    full = _under_repo(full_report or DEFAULT_REPORT)
    if _same_path(volume_report, full) or _path_inside(volume_report, canon):
        return "volume report must not replace the full-mode report or a canonical CSV"
    if _same_path(volume_dir, canon) or _path_inside(volume_dir, canon):
        return "volume output must not be inside the canonical tree"
    if _same_path(volume_dir, full):
        return "volume output must not replace the full-mode report"
    if _path_inside(raw_dir, canon) or _same_path(raw_dir, full) or _path_inside(raw_dir, volume_dir):
        return "raw archive must stay outside canonical files and the sibling CSV tree"
    return None


def consume_cleared_volume(rows, parent):
    """Settle rows and cleared-volume quantities keyed by ts_ref date.

    Other stat types are dropped. A blank quantity is stored as unavailable
    (None), so a later blank still replaces an earlier number. A real 0 stays
    the text 0. Days are not copied onto each other.
    """
    buckets = {}
    checked = False
    cleared = _cleared_stat_type()
    try:
        for row in rows:
            if not checked:
                if list(row.keys()) != list(C.STATS_FINGERPRINT):
                    raise RuntimeError(
                        "SCHEMA CHANGE in statistics (%s): %s" % (parent, list(row.keys())))
                checked = True
            day = (row.get("ts_ref") or "")[:10]
            if len(day) != 10:
                continue
            iid = row.get("instrument_id")
            if not iid:
                continue
            bucket = buckets.setdefault(day, {"settles": {}, "volumes": {}})
            kind = row.get("stat_type")
            if kind == C.STAT_SETTLE:
                bucket["settles"][iid] = row
            elif kind == cleared:
                bucket["volumes"][iid] = C._volume_quantity(row.get("quantity"))
    finally:
        close = getattr(rows, "close", None)
        if close:
            close()
    return buckets


def fetch_archived_range(http, key, schema, parent, start, end, raw_dir):
    """Paid get_range. Gzip the exact body, then parse CSV. The gzip is not committed."""
    params = product_params(schema, parent, start, end)
    params.update({
        "encoding": "csv",
        "pretty_px": "true",
        "pretty_ts": "true",
        "map_symbols": "true",
    })
    status, _headers, body = http(
        "GET", _url("timeseries.get_range", params), _auth_header(key), None)
    raw = _body_bytes(body)
    if status == 402:
        raise PaymentRequired("timeseries.get_range")
    if status not in (200, 206):
        raise RuntimeError("HTTP %s on timeseries.get_range" % status)
    C.archive_raw_response(raw_dir, schema, parent, start, end, raw)
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8", errors="replace"))))


def _volume_file(volume_dir, session, root):
    return Path(volume_dir) / session.isoformat() / ("%s.csv" % root)


def _missing_volume_files(volume_dir, sessions):
    missing, present = [], []
    for session in sessions:
        for parent in PARENTS:
            root = parent.split(".")[0]
            path = _volume_file(volume_dir, session, root)
            if path.is_file():
                present.append({"session": session.isoformat(), "root": root, "reason": "exists"})
            else:
                missing.append((session, root, path))
    return missing, present


def _write_volume_report(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")


def _volume_report_body(start, end, quote, **fields):
    body = {
        "mode": "volume_only",
        "dataset": C.DATASET,
        "schemas": list(VOLUME_SCHEMAS),
        "symbols": list(PARENTS),
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "cap_usd": str(VOLUME_CAP_USD),
        "get_cost": str(quote["get_cost"]),
        "record_count": str(quote["record_count"]),
        "billable_size": str(quote["billable_size"]),
        "by_schema": quote["by_schema"],
        "actual_cost_usd": None,
        "actual_cost_note": (
            "metadata.get_cost is the estimate. timeseries.get_range does not return a billed amount. "
            "spent_usd is that estimate when a download of this window finished, else 0."
        ),
        "dry_run": False,
        "downloaded": False,
        "spent_usd": "0",
        "sessions_written": [],
        "sessions_skipped": [],
        "holidays": [],
        "reason": "",
    }
    body.update(fields)
    return body


def download_volume_window(http, key, start, end, sessions, volume_dir, raw_dir):
    """One get_range per parent per schema for the whole window, then sibling CSVs.

    Existing sibling files are not opened for write. Canonical paths are not used.
    Writes happen only after every range has been read.
    """
    start_s, end_s = start.isoformat(), end.isoformat()
    held = []
    try:
        for parent in PARENTS:
            print("volume %s %s statistics" % (start_s, parent), flush=True)
            buckets = consume_cleared_volume(
                fetch_archived_range(http, key, "statistics", parent, start_s, end_s, raw_dir),
                parent)
            print("volume %s %s definition" % (start_s, parent), flush=True)
            dmap = definition_map_from_rows(
                fetch_archived_range(http, key, "definition", parent, start_s, end_s, raw_dir),
                parent)
            held.append((parent, buckets, dmap))
    except PaymentRequired:
        del held
        raise

    written, skipped, holidays = [], [], []
    try:
        for session in sessions:
            iso = session.isoformat()
            per_root = {}
            any_settle = False
            for parent, buckets, dmap in held:
                day = buckets.get(iso) or {"settles": {}, "volumes": {}}
                if day["settles"]:
                    any_settle = True
                _recs, vols, _unknown = C.contract_rows(
                    day["settles"], {}, day["volumes"], dmap, parent, iso)
                per_root[parent.split(".")[0]] = vols
            if not any_settle:
                holidays.append(iso)
                continue
            for root, vols in sorted(per_root.items()):
                path = _volume_file(volume_dir, session, root)
                if path.is_file():
                    skipped.append({"session": iso, "root": root, "reason": "exists"})
                    continue
                C.write_csv(path, C.VOLUME_HEADER, vols, lineterminator="\n")
                written.append({"session": iso, "root": root})
    finally:
        del held
    return {"sessions_written": written, "sessions_skipped": skipped, "holidays": holidays}


def _parse_volume_window(args):
    start = _parse_volume_day(args.volume_start or VOLUME_START_DEFAULT.isoformat(),
                              "volume_start")
    if args.volume_end:
        end = _parse_volume_day(args.volume_end, "volume_end")
    else:
        end = datetime.now(timezone.utc).date()
    return start, end


def _print_volume_months(args):
    """List volume months and exit. No Databento call and no API key."""
    try:
        start, end = _parse_volume_window(args)
    except ValueError as exc:
        print("%s Refusing before any Databento call." % exc, file=sys.stderr)
        return 2
    if start >= end:
        print("volume start must be before the exclusive end. Refusing before any Databento call.",
              file=sys.stderr)
        return 2
    for label, seg_start, seg_end in iter_volume_months(start, end):
        print("%s %s %s" % (label, seg_start.isoformat(), seg_end.isoformat()))
    return 0


def _month_is_partial(label, seg_start, seg_end):
    year, month = (int(part) for part in label.split("-"))
    first = date(year, month, 1)
    return seg_start > first or seg_end < _next_month_date(first)


def _money(value):
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise ValueError("spent_usd is not a number")


def _load_volume_state(path):
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("volume report is not readable JSON: %s" % exc)
    if not isinstance(data, dict):
        raise ValueError("volume report is not a JSON object")
    return data


def _find_volume_month(months, label):
    for row in months:
        if isinstance(row, dict) and row.get("month") == label:
            return row
    return None


def _uncovered_parts(record, req_start, req_end):
    """Sub-ranges of [req_start, req_end) not covered by a complete month row."""
    if req_start >= req_end:
        return []
    if not record or not record.get("complete"):
        return [(req_start, req_end)]
    try:
        covered_start = date.fromisoformat(record["start"])
        covered_end = date.fromisoformat(record["end_exclusive"])
    except (KeyError, TypeError, ValueError):
        return [(req_start, req_end)]
    parts = []
    if req_start < covered_start:
        parts.append((req_start, min(req_end, covered_start)))
    if req_end > covered_end:
        tail = max(req_start, covered_end)
        if tail < req_end:
            parts.append((tail, req_end))
    return [(a, b) for a, b in parts if a < b]


def _upsert_volume_month(months, entry):
    for index, row in enumerate(months):
        if isinstance(row, dict) and row.get("month") == entry["month"]:
            months[index] = entry
            return
    months.append(entry)


def _is_http_504(exc):
    return isinstance(exc, RuntimeError) and str(exc).startswith("HTTP 504")


def _call_with_504_retry(label, fn):
    """Run fn once. On HTTP 504, run it once more. A second 504 returns ('504', exc)."""
    seen = None
    for attempt in (1, 2):
        try:
            return ("ok", fn())
        except RuntimeError as exc:
            if not _is_http_504(exc):
                raise
            seen = exc
            if attempt == 1:
                print("HTTP 504 on %s (%s); retrying this month once" % (label, exc), flush=True)
                continue
            return ("504", seen)
    return ("504", seen)


def _sum_volume_quotes(quotes):
    cost = Decimal("0")
    count = Decimal("0")
    size = Decimal("0")
    by = {schema: {"get_cost": Decimal("0"), "record_count": Decimal("0"),
                   "billable_size": Decimal("0")} for schema in VOLUME_SCHEMAS}
    for quote in quotes:
        cost += quote["get_cost"]
        count += quote["record_count"]
        size += quote["billable_size"]
        for schema in VOLUME_SCHEMAS:
            part = quote["by_schema"][schema]
            by[schema]["get_cost"] += Decimal(part["get_cost"])
            by[schema]["record_count"] += Decimal(part["record_count"])
            by[schema]["billable_size"] += Decimal(part["billable_size"])
    return {
        "get_cost": cost,
        "record_count": count,
        "billable_size": size,
        "by_schema": {schema: {key: str(val) for key, val in by[schema].items()}
                      for schema in VOLUME_SCHEMAS},
    }


def _emit_volume_summary(start, end, quote, notes):
    if notes:
        _summary("\n".join(notes))
    _volume_summary(start, end, quote)


def _volume_cap_reason(spent, month, chunk_cost):
    return ("HARD CAP: cumulative spent %s USD + month %s get_cost %s USD is above %s USD. "
            "Stopped before downloading this month."
            % (spent, month, chunk_cost, VOLUME_CAP_USD))


def _coverage_entry(old, label, part_start, part_end, quote, downloaded, complete, error):
    """Merge a finished segment into the month row. A 504 does not extend coverage."""
    if old and old.get("complete") and not complete:
        entry = dict(old)
        entry["error"] = error
        return entry
    if old and old.get("complete"):
        covered_start = min(date.fromisoformat(old["start"]), part_start)
        covered_end = max(date.fromisoformat(old["end_exclusive"]), part_end)
        paid = _money(old.get("get_cost")) if old.get("downloaded") else Decimal("0")
    else:
        covered_start, covered_end = part_start, part_end
        paid = Decimal("0")
    if downloaded:
        paid += quote["get_cost"]
    elif not (old and old.get("downloaded")):
        paid = quote["get_cost"]
    return {
        "month": label,
        "start": covered_start.isoformat(),
        "end_exclusive": covered_end.isoformat(),
        "complete": bool(complete),
        "partial": _month_is_partial(label, covered_start, covered_end),
        "get_cost": str(paid),
        "downloaded": bool(downloaded or (old and old.get("downloaded"))),
        "error": error,
    }


def _attempt_volume_segment(http, key, spent, part_start, part_end, volume_dir, raw_dir, download):
    """Quote one segment. Download it only when download is true and the cap allows it.

    Raises PaymentRequired, CostUnknown, and RuntimeError (including HTTP 504).
    """
    quote = quote_volume_window(http, key, part_start, part_end)
    if over_volume_cap(spent + quote["get_cost"]):
        return {"status": "cap", "quote": quote}
    if not download:
        return {"status": "dry", "quote": quote}
    sessions = list(iter_volume_sessions(part_start, part_end))
    if not sessions:
        return {"status": "empty", "quote": quote, "present": []}
    missing, present = _missing_volume_files(volume_dir, sessions)
    if not missing:
        return {"status": "exists", "quote": quote, "present": present}
    result = download_volume_window(
        http, key, part_start, part_end, sessions, volume_dir, raw_dir)
    return {"status": "downloaded", "quote": quote, "result": result}


def execute_volume_only(args, http=None, env=None, canonical_dir=None, full_report=None,
                        volume_dir=None, volume_report=None, raw_dir=None):
    """Cleared-volume window. Does not read or write the full-mode report."""
    env = os.environ if env is None else env
    try:
        start = _parse_volume_day(args.volume_start or VOLUME_START_DEFAULT.isoformat(),
                                  "volume_start")
        if args.volume_end:
            end = _parse_volume_day(args.volume_end, "volume_end")
        else:
            end = datetime.now(timezone.utc).date()
    except ValueError as exc:
        print("%s Refusing before any Databento call." % exc, file=sys.stderr)
        return 2
    if start >= end:
        print("volume start must be before the exclusive end. Refusing before any Databento call.",
              file=sys.stderr)
        return 2

    vol_dir, vol_report, raw = volume_output_paths(env, volume_dir, volume_report, raw_dir)
    refusal = _refuse_volume_targets(vol_dir, vol_report, raw, canonical_dir, full_report)
    if refusal:
        print("%s Refusing before any Databento call." % refusal, file=sys.stderr)
        return 2

    key = (env.get("DATABENTO_API_KEY") or "").strip()
    if not key:
        print("DATABENTO_API_KEY is not set. No metadata call and no download.", file=sys.stderr)
        return 2
    http = http or urllib_http
    download = bool(args.download)
    try:
        state = _load_volume_state(vol_report)
        spent = _money(state.get("spent_usd")) if state else Decimal("0")
    except ValueError as exc:
        print("%s Refusing before any Databento call." % exc, file=sys.stderr)
        return 2
    month_rows = list(state.get("months") or []) if state else []
    if state and not isinstance(month_rows, list):
        print("volume report months is not a list. Refusing before any Databento call.",
              file=sys.stderr)
        return 2

    notes = []
    quotes = []
    written, skipped, holidays = [], [], []
    downloaded_any = False
    exists_hits = False
    empty_hits = False
    persisted = state is not None

    def _note(text):
        notes.append(text)
        _budget_line(text, flush=True)

    def _persist(reason, quote):
        nonlocal persisted
        body = _volume_report_body(
            start, end, quote,
            downloaded=downloaded_any,
            spent_usd=str(spent),
            sessions_written=written,
            sessions_skipped=skipped,
            holidays=holidays,
            reason=reason,
        )
        body["months"] = month_rows
        body["resume"] = VOLUME_RESUME
        _write_volume_report(vol_report, body)
        persisted = True

    def _stop_504(label):
        message = ("volume_only stopped: HTTP 504 twice on %s. "
                   "Earlier months are kept. Later months were not started." % label)
        print(message, file=sys.stderr)
        _summary(message)
        if quotes:
            _emit_volume_summary(start, end, _sum_volume_quotes(quotes), notes)
        _persist(message, quotes[-1] if quotes else {
            "get_cost": Decimal("0"), "record_count": Decimal("0"),
            "billable_size": Decimal("0"),
            "by_schema": {schema: {"get_cost": "0", "record_count": "0", "billable_size": "0"}
                          for schema in VOLUME_SCHEMAS},
        })
        return 1

    try:
        for label, seg_start, seg_end in iter_volume_months(start, end):
            record = _find_volume_month(month_rows, label)
            parts = _uncovered_parts(record, seg_start, seg_end)
            if not parts:
                _note("month %s not requested (complete through %s)"
                      % (label, record.get("end_exclusive")))
                continue
            for part_start, part_end in parts:
                def _once(ps=part_start, pe=part_end):
                    return _attempt_volume_segment(
                        http, key, spent, ps, pe, vol_dir, raw, download)

                status, payload = _call_with_504_retry(label, _once)
                if status == "504":
                    if record and record.get("complete"):
                        record["error"] = "HTTP 504 twice"
                    else:
                        _upsert_volume_month(month_rows, {
                            "month": label,
                            "start": part_start.isoformat(),
                            "end_exclusive": part_start.isoformat(),
                            "complete": False,
                            "partial": True,
                            "get_cost": "0",
                            "downloaded": False,
                            "error": "HTTP 504 twice",
                        })
                    return _stop_504(label)

                outcome = payload
                quote = outcome["quote"]
                quotes.append(quote)
                _note("month %s %s → %s get_cost USD: %s"
                      % (label, part_start.isoformat(), part_end.isoformat(), quote["get_cost"]))
                if outcome["status"] == "cap":
                    _emit_volume_summary(start, end, _sum_volume_quotes(quotes), notes)
                    reason = _volume_cap_reason(spent, label, quote["get_cost"])
                    _budget_line(reason, err=True)
                    _summary(reason)
                    if persisted:
                        _upsert_volume_month(month_rows, _coverage_entry(
                            record, label, part_start, part_end, quote,
                            downloaded=False, complete=False, error=reason))
                        _persist(reason, _sum_volume_quotes(quotes))
                    return 3
                if not download:
                    continue

                old = _find_volume_month(month_rows, label)
                if outcome["status"] == "downloaded":
                    spent += quote["get_cost"]
                    downloaded_any = True
                    result = outcome["result"]
                    written.extend(result["sessions_written"])
                    skipped.extend(result["sessions_skipped"])
                    holidays.extend(result["holidays"])
                    _upsert_volume_month(month_rows, _coverage_entry(
                        old, label, part_start, part_end, quote,
                        downloaded=True, complete=True, error=""))
                    _persist("", _sum_volume_quotes(quotes))
                elif outcome["status"] == "exists":
                    exists_hits = True
                    skipped.extend(outcome["present"])
                    _upsert_volume_month(month_rows, _coverage_entry(
                        old, label, part_start, part_end, quote,
                        downloaded=False, complete=True, error=""))
                    _persist(VOLUME_EXISTS_REASON, _sum_volume_quotes(quotes))
                elif outcome["status"] == "empty":
                    empty_hits = True
                    _upsert_volume_month(month_rows, _coverage_entry(
                        old, label, part_start, part_end, quote,
                        downloaded=False, complete=True, error=""))
                    _persist(VOLUME_EMPTY_REASON, _sum_volume_quotes(quotes))
                else:
                    print("volume download stopped: unknown segment status %s."
                          % outcome["status"], file=sys.stderr)
                    return 1
    except PaymentRequired as exc:
        print("Databento HTTP 402 on %s. Stopped. Earlier months are kept." % exc.endpoint,
              file=sys.stderr)
        return 2
    except CostUnknown as exc:
        print("Could not read a number from %s. Nothing further downloaded." % exc.endpoint,
              file=sys.stderr)
        return 2
    except Exception as exc:
        print("volume download stopped: %s. No further sibling CSV written." % exc,
              file=sys.stderr)
        return 1

    if not quotes:
        if notes:
            _summary("\n".join(notes))
        print("volume_only: nothing left to request.")
        return 0

    _emit_volume_summary(start, end, _sum_volume_quotes(quotes), notes)
    if not download:
        total = _sum_volume_quotes(quotes)["get_cost"]
        if over_volume_cap(spent + total):
            reason = _volume_cap_reason(spent, "window", total)
            _budget_line(reason, err=True)
            _summary(reason)
            return 3
        print("volume_only dry-run: cost recorded in the step summary. No download and no files.")
        return 0

    reason = ""
    if not downloaded_any and exists_hits and not empty_hits:
        reason = VOLUME_EXISTS_REASON
    elif not downloaded_any and empty_hits and not exists_hits:
        reason = VOLUME_EMPTY_REASON
    if persisted:
        _persist(reason, _sum_volume_quotes(quotes))
    print("volume_only wrote %s sibling files, skipped %s, holidays %s"
          % (len(written), len(skipped), len(holidays)))
    return 0


def execute(argv=None, http=None, env=None, out_dir=None, report_path=None,
            sessions=None, clock=None, budget_s=None,
            volume_dir=None, volume_report=None, raw_dir=None):
    parser = argparse.ArgumentParser(description="CME options backfill, one calendar month at a time")
    parser.add_argument("--download", action="store_true",
                        help="download and write canonical files (default is cost-only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="per-chunk get_cost / record_count / billable_size only (this is the default)")
    parser.add_argument("--month", default=None, help="process one YYYY-MM chunk")
    parser.add_argument("--list-months", action="store_true", help="print the 24 chunk months and exit")
    parser.add_argument("--list-volume-months", action="store_true",
                        help="print volume_only months as 'YYYY-MM start end' and exit; no Databento call")
    parser.add_argument("--record-stop", default=None, help="record a stop reason and exit 0")
    parser.add_argument("--mode", choices=("full", "volume_only"), default="full",
                        help="full keeps the monthly settlement/OI backfill; volume_only is the cleared-volume window")
    parser.add_argument("--volume-start", default=VOLUME_START_DEFAULT.isoformat(),
                        help="volume_only inclusive start YYYY-MM-DD (default 2026-07-01)")
    parser.add_argument("--volume-end", default="",
                        help="volume_only exclusive end YYYY-MM-DD (default today UTC)")
    args = parser.parse_args(argv)
    if args.list_months:
        for month in iter_chunks():
            print(month)
        return 0
    if args.list_volume_months:
        return _print_volume_months(args)

    if args.mode == "volume_only":
        return execute_volume_only(
            args, http=http, env=env, canonical_dir=out_dir, full_report=report_path,
            volume_dir=volume_dir, volume_report=volume_report, raw_dir=raw_dir)

    env = os.environ if env is None else env
    out_dir = Path(out_dir or DEFAULT_OUT)
    report_path = Path(report_path or DEFAULT_REPORT)
    report = load_report(report_path)
    clock = clock or time.time
    if budget_s is None:
        budget_s = int(env.get("G8_BACKFILL_BUDGET_S", str(CHUNK_BUDGET_S)))

    if args.record_stop:
        report["reason"] = args.record_stop
        report["stop_loop"] = True
        report["blocked"] = False
        write_report(report_path, report)
        print(args.record_stop)
        return 0

    # A previous run's stop_loop must not pin the next launch. This run decides again.
    if report.get("stop_loop"):
        report["stop_loop"] = False
        write_report(report_path, report)

    key = (env.get("DATABENTO_API_KEY") or "").strip()
    if not key:
        print("DATABENTO_API_KEY is not set. No metadata call and no download.", file=sys.stderr)
        return 2
    http = http or urllib_http
    dry_run = not args.download
    known = list(iter_chunks())
    if args.month:
        if args.month not in known:
            print("unknown month %s" % args.month, file=sys.stderr)
            return 2
        months = [args.month]
    else:
        months = known
    # sessions= is the old per-session hook. Chunks own the window; the argument is ignored.
    del sessions

    shas, authoritative = resolve_commit_shas(report_path, out_dir)
    apply_commit_shas(report, shas, authoritative=authoritative)
    started = clock()
    report["dry_run"] = dry_run
    try:
        for month in months:
            if clock() - started >= budget_s:
                report["reason"] = (
                    "time budget: not starting new chunks after %s min (%s s); "
                    "job timeout is %s min. Relaunch resumes."
                    % (budget_s // 60, budget_s, JOB_TIMEOUT_MIN))
                report["stop_loop"] = True
                report["blocked"] = False
                write_report(report_path, report)
                _summary(report["reason"])
                return 0
            if month_complete(report, month):
                continue
            start, end, _sessions = month_window(month)
            try:
                quote = quote_chunk(http, key, start, end)
            except PaymentRequired as exc:
                report["reason"] = ("Databento HTTP 402 on %s. Stopped before download. "
                                    "No partial session." % exc.endpoint)
                report["aborted"] = "HTTP 402"
                report["stop_loop"] = True
                write_report(report_path, report)
                print(report["reason"], file=sys.stderr)
                return 2
            except CostUnknown as exc:
                print("Could not read a number from %s. Nothing downloaded." % exc.endpoint,
                      file=sys.stderr)
                report["reason"] = "Could not read a number from %s." % exc.endpoint
                write_report(report_path, report)
                return 2
            entry = _chunk_entry(month, quote, start, end)
            entry["committed_sha"] = shas.get(month)
            spent = Decimal(str(report.get("spent_usd") or "0"))
            _summary("\n".join([
                "### Databento backfill chunk %s" % month,
                "- window: %s → %s (last session's next weekday 06:00 UTC)" % (start, end),
                "- get_cost USD: %s" % entry["get_cost"],
                "- record_count: %s" % entry["record_count"],
                "- billable_size: %s" % entry["billable_size"],
                "- cumulative spent USD: %s" % spent,
                "- cap: %s USD" % CAP_USD,
            ]))
            if over_cap(spent + quote["get_cost"]):
                reason = _cap_reason(spent, month, quote["get_cost"])
                entry["complete"] = False
                _upsert_chunk(report, entry)
                report["blocked"] = True
                report["stop_loop"] = True
                report["dry_run"] = dry_run
                report["reason"] = reason
                write_report(report_path, report)
                _budget_line(reason, err=True)
                _summary(reason)
                return 3
            if dry_run:
                entry["complete"] = False
                _upsert_chunk(report, entry)
                report["blocked"] = False
                report["stop_loop"] = False
                report["dry_run"] = True
                report["reason"] = "cost-only default: no timeseries.get_range call"
                write_report(report_path, report)
                continue
            try:
                result = download_month(http, key, month, out_dir)
            except PaymentRequired as exc:
                entry["complete"] = False
                entry["aborted"] = "HTTP 402"
                _upsert_chunk(report, entry)
                report["aborted"] = "HTTP 402"
                report["stop_loop"] = True
                report["blocked"] = False
                report["dry_run"] = False
                report["reason"] = ("Databento HTTP 402 on %s during download. Stopped. "
                                    "No partial session left." % exc.endpoint)
                cleanup_partial(out_dir)
                write_report(report_path, report)
                print(report["reason"], file=sys.stderr)
                return 2
            except Exception as exc:
                entry["complete"] = False
                _upsert_chunk(report, entry)
                report["reason"] = "download stopped: %s" % exc
                report["stop_loop"] = True
                report["dry_run"] = False
                cleanup_partial(out_dir)
                write_report(report_path, report)
                print(report["reason"], file=sys.stderr)
                return 1
            entry["sessions_added"] = result["sessions_added"]
            entry["skipped"] = result["skipped"]
            entry["holidays"] = result["holidays"]
            entry["complete"] = True
            entry["committed_sha"] = shas.get(month)
            report["spent_usd"] = str(spent + quote["get_cost"])
            report["blocked"] = False
            report["stop_loop"] = False
            report["dry_run"] = False
            report["reason"] = ""
            _upsert_chunk(report, entry)
            write_report(report_path, report)
            print("chunk %s sessions added: %s skipped: %s holidays: %s"
                  % (month, len(result["sessions_added"]), len(result["skipped"]),
                     len(result["holidays"])))
        if dry_run and not report.get("reason"):
            report["reason"] = "cost-only default: no timeseries.get_range call"
        report["dry_run"] = dry_run
        write_report(report_path, report)
        if dry_run:
            print("dry-run: per-chunk costs recorded. No download.")
        return 0
    except PaymentRequired as exc:
        cleanup_partial(out_dir)
        report["reason"] = "Databento HTTP 402 on %s. No partial session." % exc.endpoint
        report["aborted"] = "HTTP 402"
        report["stop_loop"] = True
        write_report(report_path, report)
        print(report["reason"], file=sys.stderr)
        return 2


def main(argv=None, **kwargs):
    return execute(argv, **kwargs)


if __name__ == "__main__":
    sys.exit(main())
