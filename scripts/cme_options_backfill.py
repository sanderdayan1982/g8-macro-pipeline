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
That end stays at 06:00. Databento filters the range on ts_recv, and the collector
records the session's open interest at about 01:44 UTC on the next weekday, after 00:00.

No raw Databento body is written to disk. One product is parsed at a time and the raw
rows are discarded. Only canonical CSVs (origin=backfill) and backfill_report.json are
meant to be committed.

Resume: a month with chunks[].complete is not requested again. complete is true only
when every skipped row is exists, protected, or a CME no-settlement date (1 January
or 25 December). "no records" is not a closure. A session directory that already
contains a CSV is never overwritten. Sessions on or after 2026-06-01 are never
written. spent_usd in the report is the sum of get_cost for chunks whose download
finished. Before get_range, the chunk's get_cost is written as a pending
reserved_usd. The cap compares spent_usd + reserved_usd + this chunk's get_cost
with 35.00 USD. A pending reservation blocks another get_range until it is
reconciled (spent or release). Above the cap, the chunk is not downloaded, the
report says why, and the process exits 3 (same non-zero code as the original
whole-range cap). A pending reservation exits 4. HTTP 402 raises no partial
session and returns 2.

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

import cme_options_collector as C

START = date(2024, 6, 1)
END_INCLUSIVE = date(2026, 5, 31)
END_EXCLUSIVE = date(2026, 6, 1)          # Databento end is exclusive
PROTECT_FROM = date(2026, 6, 1)           # never overwrite these sessions
CAP_USD = Decimal("35.00")
ORIGIN = "backfill"
SCHEMAS = ("statistics", "definition")
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
    """[first weekday 00:00 UTC, last session's next-weekday 06:00 UTC].

    Not 00:00 of that next weekday. get_range filters on ts_recv when the schema
    has it (statistics does; definition does too). Open interest for the last
    session is received around 01:44 UTC the next weekday, so a 00:00 end drops
    it, and the next month only writes its own sessions. Orden REVISION-TRIPLE-01
    punto 4 was not applied for that reason.
    """
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


def market_closed(session):
    """True when CME disseminates no settlement for this trade date.

    A calendar rule, not an empty pull. Checked 14 Nov 2026:
    1 January has no settle file (CME Clearing advisory, New Year's Day 2025,
    https://www.cmegroup.com/tools-information/holiday-calendar/files/2025-new-years-advisory.pdf)
    and 25 December has no settlement prices (CME Group settlement advisory,
    https://www.cmegroup.com/tools-information/holiday-calendar/files/2025/christmas-holiday-settlement-times-2025.pdf).
    The rule is those two trade dates in every year. Both fall on a weekday in
    2024-06..2026-05, so no Saturday/Sunday observance is applied. Modified-hours
    sessions (Juneteenth, Good Friday, Thanksgiving, Monday holidays) are not
    closures: a settlement is still disseminated.
    """
    if isinstance(session, str):
        session = date.fromisoformat(session[:10])
    return (session.month, session.day) in ((1, 1), (12, 25))


def closure_skip(session):
    """A skipped row for a verified closure, or None when the calendar is open."""
    if not market_closed(session):
        return None
    iso = session.isoformat() if isinstance(session, date) else str(session)[:10]
    return {"session": iso, "reason": "closed", "calendar": "CME-no-settlement"}


_NO_RECORD_REASONS = frozenset(("no records", "no-records", "norecords", "holiday"))


def skipped_allows_complete(skipped):
    """True only when every skipped row may stand on a complete chunk.

    Allowed reasons are exists, protected, and closed when market_closed says
    the trade date has no settlement. "no records" is not that closure. A
    sanity failure or any other reason leaves the chunk incomplete.
    """
    for entry in skipped or []:
        if not isinstance(entry, dict):
            return False
        reason = entry.get("reason")
        if reason in _NO_RECORD_REASONS:
            return False
        if reason in ("exists", "protected"):
            continue
        if reason == "closed" and entry.get("session") and market_closed(entry["session"]):
            continue
        return False
    return True


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
                closure = closure_skip(session)
                if closure:
                    skipped.append(closure)
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


def _summary(text):
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


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
        "reserved_usd": "0",
        "chunk_budget_s": CHUNK_BUDGET_S,
        "job_timeout_min": JOB_TIMEOUT_MIN,
        "planned_calls": plan,
        "actual_cost_usd": None,
        "actual_cost_note": (
            "metadata.get_cost is the estimate. timeseries.get_range does not return a billed amount. "
            "spent_usd sums get_cost of chunks whose download finished. "
            "reserved_usd sums get_cost written before get_range and not yet reconciled."
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
    body.setdefault("reserved_usd", "0")
    body.setdefault("chunks", [])
    body.setdefault("stop_loop", False)
    body.setdefault("blocked", False)
    _sync_reserved(body)
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
    _sync_reserved(report)
    return report


def pending_reserved(report):
    """Sum of get_cost still reserved, from the chunks, not the cached field."""
    total = Decimal("0")
    for chunk in report.get("chunks") or []:
        if chunk.get("reservation") == "pending":
            total += Decimal(str(chunk.get("reserved_usd") or "0"))
    return total


def _sync_reserved(report):
    """reserved_usd is the sum of chunks whose reservation is still pending."""
    report["reserved_usd"] = str(pending_reserved(report))
    return report


def _pending_chunk(report, month):
    for chunk in report.get("chunks") or []:
        if chunk.get("month") == month and chunk.get("reservation") == "pending":
            return chunk
    return None


def _pending_reason(month, amount):
    return ("pending reservation for %s of %s USD blocks re-download until reconciled. "
            "Use --reconcile-reservation %s --reconcile-as spent to count the charge "
            "(a later download is a new charge) or --reconcile-as release to drop it."
            % (month, amount, month))


def reconcile_reservation(report, month, action):
    """Lift a pending reservation so a later run may download again.

    spent: add reserved_usd to spent_usd once. The charge is counted. A later
    download reserves a new get_cost and, if it finishes, counts that too.
    release: drop the reservation. A later download may reserve again.
    A second call is a no-op failure so spent is not applied twice.
    """
    if action not in ("spent", "release"):
        raise ValueError("reconcile action must be spent or release, not %r" % (action,))
    chunk = None
    for item in report.get("chunks") or []:
        if item.get("month") == month:
            chunk = item
            break
    if chunk is None:
        return {"ok": False, "reason": "no chunk %s in the report" % month}
    if chunk.get("reservation") != "pending":
        return {
            "ok": False,
            "reason": "chunk %s has no pending reservation (reservation=%s)"
                      % (month, chunk.get("reservation")),
        }
    amount = Decimal(str(chunk.get("reserved_usd") or "0"))
    if action == "spent":
        spent = Decimal(str(report.get("spent_usd") or "0"))
        report["spent_usd"] = str(spent + amount)
        chunk["reservation"] = "spent"
    else:
        chunk["reservation"] = "released"
        chunk["reserved_usd"] = "0"
    _sync_reserved(report)
    return {
        "ok": True,
        "month": month,
        "action": action,
        "amount": str(amount),
        "reason": "reconciled %s reservation %s USD as %s" % (month, amount, action),
    }


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
    """Persist the report by writing a sibling .tmp file and os.replace.

    A crash before the replace leaves the previous report intact.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _recompute(body)
    payload = json.dumps(body, indent=1) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


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
        "reserved_usd": "0",
        "reservation": None,
        "committed_sha": None,
        "complete": False,
    }


def _cap_reason(spent, reserved, month, chunk_cost):
    return ("HARD CAP: cumulative spent %s USD + reserved %s USD + chunk %s get_cost %s USD "
            "is above %s USD. Stopped before downloading this chunk."
            % (spent, reserved, month, chunk_cost, CAP_USD))


def _offline_report_command(args, report_path):
    """Inspect or reconcile the report. Does not call Databento."""
    report = load_report(report_path)
    if args.list_skipped and args.reconcile_reservation:
        print("pass only one of --list-skipped and --reconcile-reservation", file=sys.stderr)
        return 2
    if args.list_skipped:
        for chunk in report.get("chunks") or []:
            skipped = chunk.get("skipped") or []
            month = chunk.get("month")
            if skipped and month:
                print(month)
        return 0
    if not args.reconcile_as:
        print("--reconcile-reservation needs --reconcile-as spent or release", file=sys.stderr)
        return 2
    month = args.reconcile_reservation
    if month not in list(iter_chunks()):
        print("unknown month %s" % month, file=sys.stderr)
        return 2
    result = reconcile_reservation(report, month, args.reconcile_as)
    if not result["ok"]:
        print(result["reason"], file=sys.stderr)
        return 2
    write_report(report_path, report)
    print(result["reason"])
    return 0


def execute(argv=None, http=None, env=None, out_dir=None, report_path=None,
            sessions=None, clock=None, budget_s=None):
    parser = argparse.ArgumentParser(description="CME options backfill, one calendar month at a time")
    parser.add_argument("--download", action="store_true",
                        help="download and write canonical files (default is cost-only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="per-chunk get_cost / record_count / billable_size only (this is the default)")
    parser.add_argument("--month", default=None, help="process one YYYY-MM chunk")
    parser.add_argument("--list-months", action="store_true", help="print the 24 chunk months and exit")
    parser.add_argument("--record-stop", default=None, help="record a stop reason and exit 0")
    parser.add_argument("--list-skipped", action="store_true",
                        help="print months whose chunk has a non-empty skipped list and exit (no Databento call)")
    parser.add_argument("--reconcile-reservation", default=None, metavar="YYYY-MM",
                        help="reconcile one pending reservation and exit (no Databento call)")
    parser.add_argument("--reconcile-as", choices=("spent", "release"), default=None,
                        help="with --reconcile-reservation: spent counts the charge; release drops it")
    args = parser.parse_args(argv)
    if args.list_months:
        for month in iter_chunks():
            print(month)
        return 0

    env = os.environ if env is None else env
    out_dir = Path(out_dir or DEFAULT_OUT)
    report_path = Path(report_path or DEFAULT_REPORT)
    if args.reconcile_as and not args.reconcile_reservation:
        print("--reconcile-as requires --reconcile-reservation", file=sys.stderr)
        return 2
    if args.list_skipped or args.reconcile_reservation:
        return _offline_report_command(args, report_path)
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
            pending = _pending_chunk(report, month)
            if pending is not None:
                report["reason"] = _pending_reason(month, pending.get("reserved_usd"))
                report["stop_loop"] = True
                report["blocked"] = True
                report["dry_run"] = dry_run
                write_report(report_path, report)
                print(report["reason"], file=sys.stderr)
                _summary(report["reason"])
                return 4
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
            reserved = pending_reserved(report)
            projected = spent + reserved + quote["get_cost"]
            _summary("\n".join([
                "### Databento backfill chunk %s" % month,
                "- window: %s → %s (last session's next weekday 06:00 UTC)" % (start, end),
                "- get_cost USD: %s" % entry["get_cost"],
                "- record_count: %s" % entry["record_count"],
                "- billable_size: %s" % entry["billable_size"],
                "- cumulative spent USD: %s" % spent,
                "- reserved USD: %s" % reserved,
                "- spent + reserved + chunk USD: %s" % projected,
                "- cap: %s USD" % CAP_USD,
            ]))
            if over_cap(projected):
                reason = _cap_reason(spent, reserved, month, quote["get_cost"])
                entry["complete"] = False
                _upsert_chunk(report, entry)
                report["blocked"] = True
                report["stop_loop"] = True
                report["dry_run"] = dry_run
                report["reason"] = reason
                write_report(report_path, report)
                print(reason, file=sys.stderr)
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
            entry["reserved_usd"] = str(quote["get_cost"])
            entry["reservation"] = "pending"
            _upsert_chunk(report, entry)
            write_report(report_path, report)
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
            entry["committed_sha"] = shas.get(month)
            # A finished download is counted in spent_usd only when the chunk
            # may be marked complete. Otherwise the reservation stays pending
            # so the next run does not call get_range again and pay twice.
            if skipped_allows_complete(result["skipped"]):
                reconciled = reconcile_reservation(report, month, "spent")
                if not reconciled["ok"]:
                    entry["complete"] = False
                    report["reason"] = reconciled["reason"]
                    report["stop_loop"] = True
                    report["dry_run"] = False
                    _upsert_chunk(report, entry)
                    write_report(report_path, report)
                    print(report["reason"], file=sys.stderr)
                    return 1
                entry["complete"] = True
                report["reason"] = ""
            else:
                entry["complete"] = False
                report["reason"] = _pending_reason(month, entry.get("reserved_usd"))
            report["blocked"] = False
            report["stop_loop"] = False
            report["dry_run"] = False
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
