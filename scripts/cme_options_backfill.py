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

No raw Databento body is written to disk. One product is parsed at a time and the raw
rows are discarded. Only canonical CSVs (origin=backfill) and backfill_report.json are
meant to be committed.

Resume: a month with chunks[].complete is not requested again. A session directory that
already contains a CSV is never overwritten. Sessions on or after 2026-06-01 are never
written. spent_usd in the report is the sum of get_cost for chunks whose download finished.
Before a download, if spent_usd + this chunk's get_cost is above 35.00 USD, the chunk is
not downloaded (clean exit 0, stop_loop, the report says why). HTTP 402 raises no partial
session and returns 2.

Default is cost-only (no --download): per-chunk metadata table, no get_range.
Credential: environment variable DATABENTO_API_KEY only.
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


def lookup_commit_shas():
    """Newest commit whose subject is 'data: cme options backfill YYYY-MM …'."""
    try:
        out = subprocess.run(
            ["git", "log", "--format=%H\t%s", "--grep", "data: cme options backfill "],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for line in (out.stdout or "").splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        parts = subject.split()
        if len(parts) < 5:
            continue
        token = parts[4]
        if len(token) == 7 and token[4] == "-" and token[:4].isdigit() and token[5:7].isdigit():
            found.setdefault(token, sha)
    return found


def apply_commit_shas(report, shas=None):
    shas = lookup_commit_shas() if shas is None else shas
    changed = False
    for chunk in report.get("chunks") or []:
        sha = shas.get(chunk.get("month"))
        if sha and chunk.get("committed_sha") != sha:
            chunk["committed_sha"] = sha
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
    args = parser.parse_args(argv)
    if args.list_months:
        for month in iter_chunks():
            print(month)
        return 0

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

    shas = lookup_commit_shas()
    apply_commit_shas(report, shas)
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
                print(reason, file=sys.stderr)
                _summary(reason)
                return 0
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
